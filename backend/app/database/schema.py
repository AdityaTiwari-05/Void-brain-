"""
DuckDB schema initialisation for Operation Abhedya-Chakra — Phase 1.

Tables
------
transactions        Clean, normalised rows that passed all validation.
ingestion_errors    Quarantined rows (ERROR severity) + warning log rows.
ingestion_runs      One row per ingest execution — audit trail.

Design notes on indexes / zonemaps
-----------------------------------
DuckDB uses min/max zonemaps automatically on every column.  Adding an
explicit ART index speeds up exact-match look-ups but costs memory and
insert time.  For Phase 1 the priority is *load throughput*, so we
deliberately add NO secondary indexes at creation time.

The columns most likely to be queried in Phase 2 (graph traversal) are:
  Sender_Account, Receiver_Account  — equality / IN lookups
  Timestamp                          — range scans
  Transaction_ID                     — PK exact match

Recommendation (to be applied before Phase 2 query work):
  CREATE INDEX idx_txn_sender   ON transactions (sender_account);
  CREATE INDEX idx_txn_receiver ON transactions (receiver_account);
  CREATE INDEX idx_txn_ts       ON transactions (ts);
DuckDB's zonemaps already cover Timestamp range scans well when rows are
loaded in time-order; an explicit index adds value only for account
equality sweeps over large fan-in/-out sets.  Benchmark both before
committing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── DDL ──────────────────────────────────────────────────────────────────── #

_DDL_TRANSACTIONS = """
CREATE TABLE IF NOT EXISTS transactions (
    -- Identity
    transaction_id   VARCHAR        NOT NULL,  -- trimmed, unique (keep-first policy)
    run_id           VARCHAR        NOT NULL,  -- FK → ingestion_runs.run_id
    source_row       BIGINT         NOT NULL,  -- 1-based row number in source CSV

    -- Parties
    sender_account   VARCHAR        NOT NULL,
    receiver_account VARCHAR        NOT NULL,
    sender_ifsc      VARCHAR        NOT NULL,
    receiver_ifsc    VARCHAR        NOT NULL,

    -- Financial
    amount           DECIMAL(18,2)  NOT NULL,

    -- Temporal
    ts               TIMESTAMP      NOT NULL,  -- parsed from Timestamp column

    -- Classification
    payment_mode     VARCHAR        NOT NULL,  -- normalised case; unknown values kept as-is
    device_type      VARCHAR        NOT NULL,  -- normalised case; unknown values kept as-is

    -- Free-text (untrusted, preserved verbatim after trim)
    narration        VARCHAR,
    ip_address       VARCHAR,

    PRIMARY KEY (transaction_id)
);
"""

_DDL_INGESTION_ERRORS = """
CREATE TABLE IF NOT EXISTS ingestion_errors (
    id               BIGINT         PRIMARY KEY,  -- auto-increment via sequence
    run_id           VARCHAR        NOT NULL,
    row_number       BIGINT         NOT NULL,     -- 1-based
    transaction_id   VARCHAR,                     -- NULL if Transaction_ID itself was bad
    severity         VARCHAR        NOT NULL,     -- ERROR | WARNING | INFO
    error_type       VARCHAR        NOT NULL,     -- e.g. MISSING_TRANSACTION_ID, BAD_AMOUNT …
    message          VARCHAR        NOT NULL,
    -- Masked snapshot of the raw record (accounts → ********XXXX, no full account)
    masked_record    VARCHAR
);
"""

_DDL_INGESTION_ERRORS_SEQ = """
CREATE SEQUENCE IF NOT EXISTS ingestion_errors_id_seq START 1;
"""

_DDL_INGESTION_RUNS = """
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id           VARCHAR        PRIMARY KEY,
    started_at       TIMESTAMP      NOT NULL,
    ended_at         TIMESTAMP,
    duration_seconds DOUBLE,
    input_file       VARCHAR        NOT NULL,
    input_size_bytes BIGINT,
    rows_seen        BIGINT         DEFAULT 0,
    rows_loaded      BIGINT         DEFAULT 0,
    rows_rejected    BIGINT         DEFAULT 0,
    rows_duplicate   BIGINT         DEFAULT 0,
    warning_count    BIGINT         DEFAULT 0,
    error_count      BIGINT         DEFAULT 0,
    db_path          VARCHAR        NOT NULL,
    schema_version   INTEGER        NOT NULL,
    status           VARCHAR        DEFAULT 'RUNNING',  -- RUNNING | COMPLETE | FAILED
    report_path      VARCHAR
);
"""

_ALL_DDL = [
    _DDL_INGESTION_RUNS,
    _DDL_INGESTION_ERRORS_SEQ,
    _DDL_INGESTION_ERRORS,
    _DDL_TRANSACTIONS,
]

# ── Public API ────────────────────────────────────────────────────────────── #


def get_connection(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """
    Open (or create) the DuckDB database and apply all session pragmas.

    Parameters
    ----------
    db_path:
        Override the path from settings.  Useful in tests to pass
        ':memory:' or a temp file.
    """
    settings = get_settings()
    resolved = str(db_path) if db_path is not None else str(settings.db_path)
    conn = duckdb.connect(resolved)
    for stmt in settings.duckdb_pragmas():
        conn.execute(stmt)
    return conn


def initialize_database(
    conn: duckdb.DuckDBPyConnection | None = None,
    db_path: Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """
    Create all tables / sequences if they do not exist.

    Pass an existing *conn* (e.g. an in-memory connection for tests) or
    let the function open one from settings.

    Returns the connection so callers can keep using it.
    """
    if conn is None:
        conn = get_connection(db_path)

    for ddl in _ALL_DDL:
        conn.execute(ddl)

    logger.info("Database schema initialised at %s", conn.execute("PRAGMA database_list").fetchdf()["file"].iloc[0] if db_path != Path(":memory:") else ":memory:")
    return conn


def table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """Return True if *table_name* exists in the current DuckDB connection."""
    result = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table_name],
    ).fetchone()
    return bool(result and result[0] > 0)
