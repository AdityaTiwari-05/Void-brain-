"""
GET /health endpoint — Phase 1 only.

Returns a minimal JSON status object.  Database availability is checked
by opening (not creating) the DuckDB file and running a trivial query.
No sensitive data is returned.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    """
    Health check endpoint.

    Returns
    -------
    JSON with status, phase, and database availability.
    """
    db_status = "unavailable"
    try:
        from app.config import get_settings
        from app.database.schema import get_connection

        settings = get_settings()
        if Path(settings.db_path).exists():
            conn = get_connection()
            conn.execute("SELECT 1").fetchone()
            conn.close()
            db_status = "available"
        else:
            db_status = "not_initialised"
    except Exception as exc:
        logger.warning("Health check DB probe failed: %s", exc)
        db_status = "error"

    return {
        "status": "ok",
        "phase": "phase-1",
        "database": db_status,
    }
