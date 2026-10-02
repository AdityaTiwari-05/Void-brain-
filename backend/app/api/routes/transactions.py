"""
app/api/routes/transactions.py
-------------------------------
Transaction-level query endpoints for Module C.

GET  /v1/transactions                  - server-side paginated/filtered
GET  /v1/transactions/{id}             - single transaction detail
GET  /v1/transactions/stats            - aggregated stats (dashboard)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.analytics import get_analytics_db
from app.ingestion.query import get_transaction_by_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/transactions", tags=["transactions"])


def _ok(data, **meta):
    return {"success": True, "data": data, **meta}


def _safe_dec(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(Decimal(str(v)))
    except Exception:
        return None


# ── List / filter ──────────────────────────────────────────────────────────── #

@router.get("")
def list_transactions(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sender: Optional[str] = Query(None, max_length=64),
    receiver: Optional[str] = Query(None, max_length=64),
    transaction_id: Optional[str] = Query(None, max_length=64),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    payment_mode: Optional[str] = Query(None, regex="^(UPI|IMPS|NEFT|RTGS)$"),
    device_type: Optional[str] = Query(None),
    min_amount: Optional[float] = Query(None, ge=0),
    max_amount: Optional[float] = Query(None, ge=0),
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """
    Server-side paginated transaction list with filters.
    Never returns all 2M rows — always bounded by limit.
    """
    where_parts: list[str] = []
    params: list = []

    if sender:
        where_parts.append("sender_account = ?")
        params.append(sender.upper())
    if receiver:
        where_parts.append("receiver_account = ?")
        params.append(receiver.upper())
    if transaction_id:
        where_parts.append("transaction_id = ?")
        params.append(transaction_id)
    if start_time:
        where_parts.append("ts >= ?")
        params.append(start_time)
    if end_time:
        where_parts.append("ts <= ?")
        params.append(end_time)
    if payment_mode:
        where_parts.append("payment_mode = ?")
        params.append(payment_mode)
    if device_type:
        where_parts.append("device_type = ?")
        params.append(device_type)
    if min_amount is not None:
        where_parts.append("amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        where_parts.append("amount <= ?")
        params.append(max_amount)

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    try:
        rows = adb.execute(
            f"""
            SELECT
                transaction_id, sender_account, receiver_account,
                sender_ifsc, receiver_ifsc,
                CAST(amount AS DOUBLE) AS amount,
                ts::VARCHAR AS ts,
                payment_mode, device_type,
                narration, ip_address,
                source_row, run_id
            FROM transactions
            {where_sql}
            ORDER BY ts ASC, transaction_id ASC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        total_row = adb.execute(
            f"SELECT COUNT(*) FROM transactions {where_sql}", params
        ).fetchone()
        total = int(total_row[0]) if total_row else 0

    except Exception as exc:
        logger.error("list_transactions error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to query transactions.")

    keys = [
        "transaction_id", "sender_account", "receiver_account",
        "sender_ifsc", "receiver_ifsc", "amount", "ts",
        "payment_mode", "device_type", "narration", "ip_address",
        "source_row", "run_id",
    ]
    data = [dict(zip(keys, r)) for r in rows]

    return _ok(data, total=total, limit=limit, offset=offset,
               filters_applied=bool(where_parts))


# ── Single transaction ─────────────────────────────────────────────────────── #

@router.get("/stats")
def transaction_stats(
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Aggregated stats for dashboard cards."""
    try:
        row = adb.execute("""
            SELECT
                COUNT(*) AS total_transactions,
                COUNT(DISTINCT sender_account) AS unique_senders,
                COUNT(DISTINCT receiver_account) AS unique_receivers,
                CAST(SUM(amount) AS DOUBLE) AS total_volume,
                CAST(AVG(amount) AS DOUBLE) AS avg_amount,
                CAST(MAX(amount) AS DOUBLE) AS max_amount,
                MIN(ts)::VARCHAR AS earliest,
                MAX(ts)::VARCHAR AS latest,
                COUNT(DISTINCT payment_mode) AS payment_modes,
                COUNT(DISTINCT device_type) AS device_types
            FROM transactions
        """).fetchone()
    except Exception as exc:
        logger.error("transaction_stats error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to retrieve stats.")

    if row is None:
        return _ok({})

    keys = [
        "total_transactions", "unique_senders", "unique_receivers",
        "total_volume", "avg_amount", "max_amount",
        "earliest", "latest", "payment_modes", "device_types",
    ]
    return _ok(dict(zip(keys, row)))


@router.get("/{txn_id}")
def get_transaction(
    txn_id: str,
    adb: duckdb.DuckDBPyConnection = Depends(get_analytics_db),
):
    """Return full detail for a single transaction."""
    try:
        txn = get_transaction_by_id(adb, txn_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("get_transaction error: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to retrieve transaction.")
    if txn is None:
        raise HTTPException(404, f"Transaction {txn_id} not found.")
    return _ok(txn)
