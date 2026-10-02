"""
app/core/analytics.py
---------------------
DuckDB read-only connection factory — Module B analytics query layer.
Adapted for Void-brain- project structure (uses app.config.get_settings).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import duckdb
from fastapi import HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)


class AnalyticsNotReadyError(Exception):
    """Raised when the analytics database file does not exist yet."""


def _db_path() -> Path:
    return Path(get_settings().analytics_db_path)


def get_analytics_conn() -> duckdb.DuckDBPyConnection:
    """
    Open a read-only DuckDB connection to the analytics store.
    Raises AnalyticsNotReadyError if the DB file has not been created yet.
    """
    db_path = _db_path()
    if not db_path.exists():
        raise AnalyticsNotReadyError(
            f"Analytics database not found at {db_path}. "
            "Run ingestion first: python -m app.ingestion ingest --input <file>"
        )
    s = get_settings()
    conn = duckdb.connect(str(db_path), read_only=True)
    conn.execute(f"SET memory_limit='{s.analytics_memory_limit}'")
    conn.execute(f"SET threads={s.analytics_threads}")
    return conn


@contextmanager
def analytics_conn_ctx() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Context-manager wrapper for use outside FastAPI (scripts, tests)."""
    conn = get_analytics_conn()
    try:
        yield conn
    finally:
        conn.close()


def get_analytics_db() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """
    FastAPI dependency — yields a read-only DuckDB connection per request.
    Returns HTTP 503 when the DB is not ready; HTTP 500 on any other error.
    """
    try:
        conn = get_analytics_conn()
    except AnalyticsNotReadyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "ANALYTICS_NOT_READY", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Failed to open analytics DB: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "ANALYTICS_CONNECTION_ERROR",
                    "message": "Failed to connect to analytics database."},
        )
    try:
        yield conn
    finally:
        conn.close()
