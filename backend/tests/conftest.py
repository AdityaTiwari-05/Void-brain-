"""
pytest fixtures shared across all test modules.

All tests use in-memory DuckDB connections and temporary files;
they never touch the real database or the filesystem outside /tmp.
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path
from typing import Generator

import duckdb
import pytest

# Ensure backend/ is on the path when pytest is run from the project root
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# Also ensure tests/ directory itself is importable (so conftest can be imported directly)
_tests = Path(__file__).resolve().parent
if str(_tests) not in sys.path:
    sys.path.insert(0, str(_tests))

# Override settings so tests never touch real data dirs or DB
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("DATA_RAW_DIR", tempfile.gettempdir())
os.environ.setdefault("DATA_PROCESSED_DIR", tempfile.gettempdir())
os.environ.setdefault("DATA_PARQUET_DIR", tempfile.gettempdir())
os.environ.setdefault("DATA_BENCHMARKS_DIR", tempfile.gettempdir())
os.environ.setdefault("LOG_LEVEL", "WARNING")


# ── Helpers ───────────────────────────────────────────────────────────────── #

VALID_ROW = {
    "Transaction_ID":   "TXN000000000001",
    "Sender_Account":   "012345678901",
    "Receiver_Account": "987654321012",
    "Sender_IFSC":      "HDFC0ABCDEF",
    "Receiver_IFSC":    "ICIC0XYZ123",
    "Amount":           "50000.00",
    "Timestamp":        "2026-09-05 10:23:45",
    "Payment_Mode":     "UPI",
    "Narration":        "Fund transfer",
    "IP_Address":       "192.168.1.1",
    "Device_Type":      "Android",
}

COLUMNS = list(VALID_ROW.keys())


def make_csv(rows: list[dict], tmp_path: Path, filename: str = "test.csv") -> Path:
    """Write a list of row dicts to a temporary CSV file and return its path."""
    out = tmp_path / filename
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out


def mem_conn() -> duckdb.DuckDBPyConnection:
    """Open a fresh in-memory DuckDB connection with schema initialised."""
    # Clear the settings cache so each test gets a fresh Settings with env overrides
    from app.config.settings import get_settings
    get_settings.cache_clear()

    conn = duckdb.connect(":memory:")
    from app.database.schema import initialize_database
    initialize_database(conn)
    return conn


@pytest.fixture
def tmp_csv(tmp_path: Path):
    """Factory fixture: returns make_csv(rows, tmp_path) partial."""
    def _make(rows: list[dict], filename: str = "test.csv") -> Path:
        return make_csv(rows, tmp_path, filename)
    return _make


@pytest.fixture
def db_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Yield a fresh in-memory DuckDB connection, close after test."""
    conn = mem_conn()
    yield conn
    try:
        conn.close()
    except Exception:
        pass
