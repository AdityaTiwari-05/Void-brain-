"""
app/api/routes/ingestion.py
----------------------------
Ingestion trigger endpoint — Module A API surface.

POST /v1/ingestion/ingest        - trigger ingestion of a file on the server
GET  /v1/ingestion/runs          - list ingestion runs
GET  /v1/ingestion/runs/{run_id} - single run status
GET  /v1/ingestion/status        - overall readiness
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import duckdb
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.analytics import get_analytics_db
from app.database.schema import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ingestion", tags=["ingestion"])

# Simple in-memory lock so two ingestion jobs don't run simultaneously
_ingestion_lock = threading.Lock()
_current_job: Optional[dict] = None


def _ok(data, **meta):
    return {"success": True, "data": data, **meta}


class IngestRequest(BaseModel):
    file_path: str = Field(..., description="Server-side path to the CSV file to ingest.")
    force: bool = Field(False, description="If True, re-ingest even if already loaded.")


# ── Status ─────────────────────────────────────────────────────────────────── #

@router.get("/status")
def ingestion_status():
    """Check if data has been ingested. Works even before first ingestion."""
    settings = get_settings()
    db_path = Path(settings.analytics_db_path)

    if not db_path.exists():
        return _ok({
            "run_count": 0,
            "total_rows_loaded": 0,
            "last_run_at": None,
            "last_status": None,
            "transaction_count": 0,
            "account_count": 0,
            "ready": False,
        })

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            row = conn.execute("""
                SELECT COUNT(*) AS runs,
                       SUM(rows_loaded) AS total_loaded,
                       MAX(ended_at)::VARCHAR AS last_run,
                       MAX(status) AS last_status
                FROM ingestion_runs
            """).fetchone()
            txn_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            acct_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:
        logger.error("ingestion_status error: %s", exc, exc_info=True)
        return _ok({
            "run_count": 0, "total_rows_loaded": 0,
            "last_run_at": None, "last_status": None,
            "transaction_count": 0, "account_count": 0,
            "ready": False, "error": str(exc),
        })

    return _ok({
        "run_count": int(row[0]) if row else 0,
        "total_rows_loaded": int(row[1]) if row and row[1] else 0,
        "last_run_at": row[2] if row else None,
        "last_status": row[3] if row else None,
        "transaction_count": int(txn_count),
        "account_count": int(acct_count),
        "ready": int(txn_count) > 0,
    })


# ── Ingest ─────────────────────────────────────────────────────────────────── #

def _materialise_accounts(conn: duckdb.DuckDBPyConnection) -> None:
    """
    Rebuild the accounts table from transactions.
    Called after ingestion completes.
    """
    try:
        conn.execute("DELETE FROM accounts")
        conn.execute("""
            INSERT INTO accounts
                (account_node_id, normalized_account_id,
                 first_seen, last_seen,
                 incoming_count, outgoing_count,
                 incoming_amount, outgoing_amount,
                 unique_senders, unique_receivers)
            SELECT
                nextval('account_node_id_seq'),
                acct,
                MIN(ts), MAX(ts),
                COUNT(*) FILTER (WHERE receiver_account = acct),
                COUNT(*) FILTER (WHERE sender_account   = acct),
                COALESCE(SUM(amount) FILTER (WHERE receiver_account = acct), 0),
                COALESCE(SUM(amount) FILTER (WHERE sender_account   = acct), 0),
                COUNT(DISTINCT sender_account)   FILTER (WHERE receiver_account = acct),
                COUNT(DISTINCT receiver_account) FILTER (WHERE sender_account   = acct)
            FROM (
                SELECT sender_account   AS acct, ts, amount, sender_account, receiver_account
                FROM transactions
                UNION ALL
                SELECT receiver_account AS acct, ts, amount, sender_account, receiver_account
                FROM transactions
            )
            GROUP BY acct
        """)
        logger.info("Accounts materialised: %d accounts",
                    conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
    except Exception as exc:
        logger.error("Account materialisation failed: %s", exc, exc_info=True)


def _run_ingestion_job(file_path: str) -> None:
    global _current_job
    from app.ingestion.engine import run_ingestion

    settings = get_settings()
    conn = get_connection()

    try:
        _current_job = {"status": "running", "file": file_path}
        report = run_ingestion(input_file=file_path, conn=conn)
        _materialise_accounts(conn)
        _current_job = {
            "status": "complete",
            "file": file_path,
            "rows_loaded": report.rows_loaded,
            "rows_rejected": report.rows_rejected,
            "duration_seconds": report.duration_seconds,
        }
    except Exception as exc:
        logger.error("Background ingestion failed: %s", exc, exc_info=True)
        _current_job = {"status": "failed", "file": file_path, "error": str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
        _ingestion_lock.release()


@router.post("/ingest")
def ingest_file(
    body: IngestRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger ingestion of a CSV file (server-side path).
    Runs in background. Poll /v1/ingestion/status or /v1/ingestion/runs for progress.
    """
    global _current_job

    file_path = body.file_path.strip()
    if not Path(file_path).exists():
        raise HTTPException(400, f"File not found: {file_path}")
    if not file_path.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV files are supported.")

    if not _ingestion_lock.acquire(blocking=False):
        raise HTTPException(409, "Ingestion already in progress. Wait for it to complete.")

    background_tasks.add_task(_run_ingestion_job, file_path)

    return _ok({
        "message": "Ingestion started.",
        "file": file_path,
        "poll_url": "/v1/ingestion/status",
    })


@router.get("/job-status")
def job_status():
    """Return current background job status."""
    return _ok(_current_job or {"status": "idle"})


# ── Runs ───────────────────────────────────────────────────────────────────── #

@router.get("/runs")
def list_runs(limit: int = 20):
    """List recent ingestion runs."""
    settings = get_settings()
    db_path = Path(settings.analytics_db_path)
    if not db_path.exists():
        return _ok([])
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT run_id, started_at::VARCHAR, ended_at::VARCHAR,
                       duration_seconds, input_file,
                       rows_seen, rows_loaded, rows_rejected, rows_duplicate,
                       warning_count, error_count, status
                FROM ingestion_runs
                ORDER BY started_at DESC LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        raise HTTPException(500, f"Failed to list runs: {exc}")

    keys = [
        "run_id", "started_at", "ended_at", "duration_seconds", "input_file",
        "rows_seen", "rows_loaded", "rows_rejected", "rows_duplicate",
        "warning_count", "error_count", "status",
    ]
    return _ok([dict(zip(keys, r)) for r in rows])
