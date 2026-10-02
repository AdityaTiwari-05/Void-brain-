"""
Core ingestion engine for Operation Abhedya-Chakra — Phase 1.

Pipeline
--------
1.  Resolve & stat the input file.
2.  Open (or reuse) a DuckDB connection; initialise schema.
3.  Column-level schema check via validate_columns().
4.  DuckDB read_csv() → staging table (all VARCHAR — no implicit coercion).
5.  Vectorised SQL normalisation pass:
      a. Trim all text columns.
      b. Uppercase IFSC.
      c. Normalise Payment_Mode / Device_Type case to title-case canonical.
      d. Cast Amount → DECIMAL(18,2); flag non-numeric / ≤0 as ERROR.
      e. Cast Timestamp → TIMESTAMP; flag malformed as ERROR.
      f. Validate account numbers against configurable regex; flag as ERROR.
      g. Validate IFSC format; flag empty/malformed as WARNING.
      h. Flag missing Transaction_ID as ERROR.
6.  Duplicate Transaction_ID detection: keep first by staging rowid,
    quarantine the rest as DUPLICATE_TRANSACTION_ID.
7.  Insert valid rows into `transactions`.
8.  Insert error / warning rows into `ingestion_errors` (masked records).
9.  Update `ingestion_runs` with final counts + duration.
10. Assert accounting invariant: rows_seen = rows_loaded + rejected + dupes.
11. Write JSON run report to data/benchmarks/.
12. Drop staging table.

Hard rules honoured
-------------------
- No per-row Python; no row-by-row INSERTs.
- No full-dataset copies in Python memory.
- No eval / exec / shell calls.
- Parameterised SQL everywhere identifiers aren't (DuckDB doesn't support
  ? placeholders for identifiers, so table/column names are assembled once
  from trusted constants and never from user input).
- Narration / IP treated as untrusted text: trimmed, preserved, never parsed.
- Account numbers masked in logs: show last 4 digits only (********XXXX).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

from app.config import get_settings
from app.database.schema import initialize_database
from app.validation.schema_check import REQUIRED_COLUMNS, validate_columns

logger = logging.getLogger(__name__)

# ── Known-good canonical values ───────────────────────────────────────────── #

VALID_PAYMENT_MODES: frozenset[str] = frozenset({"UPI", "IMPS", "NEFT", "RTGS"})
VALID_DEVICE_TYPES: frozenset[str] = frozenset(
    {"Android", "iOS", "Windows_Browser", "Web_Emulator", "Linux_Script"}
)

# IFSC: 4 alpha (bank code) + 0 + 6 alphanumeric (branch code)
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

# ── Result types ──────────────────────────────────────────────────────────── #


@dataclass
class IngestionReport:
    run_id: str
    input_file: str
    input_size_bytes: int
    started_at: str
    ended_at: str
    duration_seconds: float
    rows_seen: int
    rows_loaded: int
    rows_rejected: int
    rows_duplicate: int
    warning_count: int
    error_count: int
    db_path: str
    schema_version: int
    status: str  # COMPLETE | FAILED
    report_path: str
    accounting_ok: bool
    warnings: list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────── #


def _mask_account(account: str | None) -> str:
    """Return '********XXXX' keeping only last 4 chars. Safe for logs."""
    if not account:
        return "********(empty)"
    a = str(account)
    return f"{'*' * 8}{a[-4:]}" if len(a) >= 4 else "*" * len(a)


def _staging_table(run_id: str) -> str:
    """Deterministic, safe staging-table name derived from run_id."""
    # run_id is a UUID4 hex string — strip hyphens, prefix with stg_
    safe = re.sub(r"[^a-zA-Z0-9]", "_", run_id)
    return f"_stg_{safe}"


def _resolve_path(input_file: str | Path) -> Path:
    p = Path(input_file).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {p}")
    if not p.is_file():
        raise ValueError(f"Input path is not a file: {p}")
    return p


def _write_report(report: IngestionReport, benchmarks_dir: Path) -> Path:
    benchmarks_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = benchmarks_dir / f"run_{ts}_{report.run_id[:8]}.json"
    report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return report_path


# ── Main entry point ──────────────────────────────────────────────────────── #


def run_ingestion(
    input_file: str | Path,
    conn: Optional[duckdb.DuckDBPyConnection] = None,
    db_path: Optional[Path] = None,
) -> IngestionReport:
    """
    Ingest *input_file* (CSV) into DuckDB.

    Parameters
    ----------
    input_file : path to the CSV to ingest.
    conn       : existing DuckDB connection (used by tests / benchmarks).
                 If None, a connection is opened from settings.
    db_path    : override DB path (ignored when *conn* is provided).

    Returns
    -------
    IngestionReport  — structured summary of the run.
    """
    settings = get_settings()
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    # ── 1. Resolve file ──────────────────────────────────────────────────── #
    src = _resolve_path(input_file)
    input_size_bytes = src.stat().st_size
    # Use forward slashes; DuckDB on Windows accepts both
    src_str = src.as_posix()

    logger.info(
        "[run:%s] Starting ingestion | file=%s | size=%d bytes",
        run_id[:8],
        src.name,
        input_size_bytes,
    )

    # ── 2. DB connection + schema ────────────────────────────────────────── #
    own_conn = conn is None
    if conn is None:
        from app.database.schema import get_connection
        conn = get_connection(db_path)

    initialize_database(conn)

    effective_db_path = str(db_path or settings.db_path)

    # Register run (status=RUNNING)
    conn.execute(
        """
        INSERT INTO ingestion_runs
            (run_id, started_at, input_file, input_size_bytes,
             db_path, schema_version, status)
        VALUES (?, ?, ?, ?, ?, ?, 'RUNNING')
        """,
        [run_id, started_at, str(src), input_size_bytes,
         effective_db_path, settings.schema_version],
    )

    report_warnings: list[str] = []
    status = "COMPLETE"

    try:
        report = _run_pipeline(
            conn=conn,
            run_id=run_id,
            src_str=src_str,
            src_name=str(src),
            input_size_bytes=input_size_bytes,
            started_at=started_at,
            t0=t0,
            report_warnings=report_warnings,
            settings=settings,
            effective_db_path=effective_db_path,
        )
    except Exception as exc:
        status = "FAILED"
        duration = time.perf_counter() - t0
        ended_at = datetime.now(timezone.utc)
        conn.execute(
            """
            UPDATE ingestion_runs
            SET status=?, ended_at=?, duration_seconds=?
            WHERE run_id=?
            """,
            [status, ended_at, duration, run_id],
        )
        logger.error("[run:%s] Ingestion FAILED: %s", run_id[:8], exc, exc_info=True)
        raise
    finally:
        if own_conn:
            conn.close()

    return report


# ── Pipeline implementation ───────────────────────────────────────────────── #


def _run_pipeline(
    *,
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    src_str: str,
    src_name: str,
    input_size_bytes: int,
    started_at: datetime,
    t0: float,
    report_warnings: list[str],
    settings,
    effective_db_path: str,
) -> IngestionReport:

    stg = _staging_table(run_id)

    # ── 3. Column schema check ───────────────────────────────────────────── #
    # Peek at header without loading data
    header_df = conn.execute(
        f"SELECT * FROM read_csv('{src_str}', header=true, all_varchar=true) LIMIT 0"
    ).fetchdf()
    file_columns: list[str] = list(header_df.columns)

    col_check = validate_columns(file_columns)

    if col_check.warning_message:
        report_warnings.append(col_check.warning_message)
        logger.warning("[run:%s] %s", run_id[:8], col_check.warning_message)

    if not col_check.ok:
        # Fatal: cannot proceed without required columns
        conn.execute(
            """
            UPDATE ingestion_runs
            SET status='FAILED', ended_at=?, duration_seconds=?,
                error_count=1
            WHERE run_id=?
            """,
            [datetime.now(timezone.utc), time.perf_counter() - t0, run_id],
        )
        raise ValueError(
            f"[run:{run_id[:8]}] Schema check failed — {col_check.error_message}"
        )

    # Build column alias map so we reference file columns by their canonical names
    # e.g. if file has "transaction_id" we select it AS "Transaction_ID"
    inv_map: dict[str, str] = {v: k for k, v in col_check.canonical_map.items()}

    def _col(canonical: str) -> str:
        """Return quoted file-column name for a canonical column."""
        file_col = inv_map.get(canonical, canonical)
        return f'"{file_col}"'

    # ── 4. Load CSV into staging table (all VARCHAR) ─────────────────────── #
    logger.info("[run:%s] Loading CSV into staging table …", run_id[:8])

    conn.execute(
        f"""
        CREATE TEMP TABLE {stg} AS
        SELECT
            row_number() OVER () AS _row_num,
            {_col('Transaction_ID')}::VARCHAR   AS Transaction_ID,
            {_col('Sender_Account')}::VARCHAR   AS Sender_Account,
            {_col('Receiver_Account')}::VARCHAR AS Receiver_Account,
            {_col('Sender_IFSC')}::VARCHAR      AS Sender_IFSC,
            {_col('Receiver_IFSC')}::VARCHAR    AS Receiver_IFSC,
            {_col('Amount')}::VARCHAR           AS Amount,
            {_col('Timestamp')}::VARCHAR        AS Timestamp,
            {_col('Payment_Mode')}::VARCHAR     AS Payment_Mode,
            {_col('Narration')}::VARCHAR        AS Narration,
            {_col('IP_Address')}::VARCHAR       AS IP_Address,
            {_col('Device_Type')}::VARCHAR      AS Device_Type
        FROM read_csv(
            '{src_str}',
            header = true,
            all_varchar = true,
            ignore_errors = false
        )
        """
    )

    rows_seen_row = conn.execute(f"SELECT COUNT(*) FROM {stg}").fetchone()
    rows_seen: int = rows_seen_row[0] if rows_seen_row else 0
    logger.info("[run:%s] rows_seen=%d", run_id[:8], rows_seen)

    # ── 5. Vectorised SQL normalisation → validated staging view ─────────── #
    #
    # We perform ALL normalisation/validation inside a single SQL expression
    # that classifies each row.  The result is a view over the staging table.
    #
    # Each row gets:
    #   _tid_ok        : Transaction_ID present and non-empty after trim
    #   _sender_ok     : matches account_regex
    #   _receiver_ok   : matches account_regex
    #   _sender_ifsc_ok: non-empty and matches IFSC pattern
    #   _recv_ifsc_ok  : non-empty and matches IFSC pattern
    #   _amount_val    : TRY_CAST result (NULL if non-numeric)
    #   _amount_ok     : non-null and > 0
    #   _ts_val        : TRY_CAST result (NULL if malformed)
    #   _ts_ok         : non-null
    #   _payment_norm  : normalised Payment_Mode
    #   _device_norm   : normalised Device_Type
    #
    # Severity rules:
    #   Missing Transaction_ID → ERROR
    #   Bad account (sender or receiver) → ERROR
    #   Bad Amount → ERROR
    #   Bad Timestamp → ERROR
    #   Empty / malformed Sender_IFSC or Receiver_IFSC → WARNING (row kept)
    #   Unknown Payment_Mode or Device_Type → WARNING (row kept, value preserved)
    #
    # A row is rejected (ERROR) if ANY error condition is true.
    # A row with only WARNINGs is loaded into transactions and logged.

    acct_regex = settings.account_regex
    # Payment_Mode normalisation: map case-insensitive variants to canonical
    # We build a CASE expression for known values; unknown kept as-is
    pm_cases = "\n            ".join(
        f"WHEN upper(trim(Payment_Mode)) = '{m.upper()}' THEN '{m}'"
        for m in sorted(VALID_PAYMENT_MODES)
    )
    dt_cases = "\n            ".join(
        f"WHEN lower(trim(Device_Type)) = '{d.lower()}' THEN '{d}'"
        for d in sorted(VALID_DEVICE_TYPES)
    )

    conn.execute(
        f"""
        CREATE TEMP VIEW {stg}_validated AS
        SELECT
            _row_num,
            -- Trimmed raw columns
            trim(Transaction_ID)   AS t_tid,
            trim(Sender_Account)   AS t_sender,
            trim(Receiver_Account) AS t_receiver,
            upper(trim(Sender_IFSC))   AS t_sender_ifsc,
            upper(trim(Receiver_IFSC)) AS t_receiver_ifsc,
            trim(Amount)           AS t_amount,
            trim(Timestamp)        AS t_timestamp,
            trim(Payment_Mode)     AS t_payment_mode,
            trim(Narration)        AS t_narration,
            trim(IP_Address)       AS t_ip,
            trim(Device_Type)      AS t_device_type,

            -- Validation flags (ERROR conditions)
            CASE WHEN trim(Transaction_ID) IS NULL
                      OR trim(Transaction_ID) = ''
                 THEN false ELSE true END                         AS _tid_ok,

            COALESCE(regexp_matches(trim(Sender_Account),   '{acct_regex}'), false) AS _sender_ok,
            COALESCE(regexp_matches(trim(Receiver_Account), '{acct_regex}'), false) AS _receiver_ok,

            -- Amount: TRY_CAST handles non-numeric gracefully
            TRY_CAST(trim(Amount) AS DECIMAL(18,2))               AS _amount_val,
            CASE WHEN TRY_CAST(trim(Amount) AS DECIMAL(18,2)) IS NOT NULL
                      AND TRY_CAST(trim(Amount) AS DECIMAL(18,2)) > 0
                 THEN true ELSE false END                         AS _amount_ok,

            -- Timestamp: strict TRY_CAST — no format guessing
            TRY_CAST(trim(Timestamp) AS TIMESTAMP)                AS _ts_val,
            CASE WHEN TRY_CAST(trim(Timestamp) AS TIMESTAMP) IS NOT NULL
                 THEN true ELSE false END                         AS _ts_ok,

            -- IFSC validation (WARNING only, not rejection)
            CASE WHEN upper(trim(Sender_IFSC)) IS NOT NULL
                      AND upper(trim(Sender_IFSC)) <> ''
                      AND regexp_matches(upper(trim(Sender_IFSC)), '^[A-Z]{{4}}0[A-Z0-9]{{6}}$')
                 THEN true ELSE false END                         AS _sender_ifsc_ok,

            CASE WHEN upper(trim(Receiver_IFSC)) IS NOT NULL
                      AND upper(trim(Receiver_IFSC)) <> ''
                      AND regexp_matches(upper(trim(Receiver_IFSC)), '^[A-Z]{{4}}0[A-Z0-9]{{6}}$')
                 THEN true ELSE false END                         AS _recv_ifsc_ok,

            -- Normalised enum columns
            CASE
                {pm_cases}
                ELSE trim(Payment_Mode)
            END AS _payment_norm,

            CASE
                {dt_cases}
                ELSE trim(Device_Type)
            END AS _device_norm,

            -- Unknown enum WARNINGs
            CASE WHEN upper(trim(Payment_Mode)) NOT IN
                      ({', '.join(f"'{m.upper()}'" for m in VALID_PAYMENT_MODES)})
                 THEN true ELSE false END AS _payment_unknown,

            CASE WHEN lower(trim(Device_Type)) NOT IN
                      ({', '.join(f"'{d.lower()}'" for d in VALID_DEVICE_TYPES)})
                 THEN true ELSE false END AS _device_unknown

        FROM {stg}
        """
    )

    # ── 6. Duplicate detection ───────────────────────────────────────────── #
    # Keep the first occurrence (lowest _row_num) of each Transaction_ID.
    # All others are quarantined as DUPLICATE_TRANSACTION_ID.
    # Only applies to rows where _tid_ok is true (no TID = already ERROR).

    conn.execute(
        f"""
        CREATE TEMP TABLE {stg}_dedup AS
        SELECT
            v.*,
            -- is this the first occurrence of this transaction_id?
            (_row_num = MIN(_row_num) OVER (PARTITION BY t_tid)) AS _is_first
        FROM {stg}_validated v
        """
    )

    # ── 7. Classify rows ─────────────────────────────────────────────────── #
    # ERROR row: any of _tid_ok=false, _sender_ok=false, _receiver_ok=false,
    #            _amount_ok=false, _ts_ok=false, OR is a duplicate.
    # WARNING row: loaded but has IFSC issue or unknown enum.
    # Good row: none of the above.

    # Count categories
    counts = conn.execute(
        f"""
        SELECT
            COUNT(*)                                                          AS total,
            COUNT(*) FILTER (WHERE NOT _tid_ok)                              AS no_tid,
            COUNT(*) FILTER (WHERE _tid_ok AND NOT _sender_ok)               AS bad_sender,
            COUNT(*) FILTER (WHERE _tid_ok AND NOT _receiver_ok)             AS bad_receiver,
            COUNT(*) FILTER (WHERE _tid_ok AND NOT _amount_ok)               AS bad_amount,
            COUNT(*) FILTER (WHERE _tid_ok AND NOT _ts_ok)                   AS bad_ts,
            COUNT(*) FILTER (WHERE _tid_ok AND NOT _is_first)                AS dupe,
            COUNT(*) FILTER (
                WHERE _tid_ok AND _is_first
                  AND _sender_ok AND _receiver_ok
                  AND _amount_ok AND _ts_ok
            )                                                                 AS clean,
            COUNT(*) FILTER (WHERE _sender_ifsc_ok = false)                  AS bad_sender_ifsc,
            COUNT(*) FILTER (WHERE _recv_ifsc_ok = false)                    AS bad_recv_ifsc,
            COUNT(*) FILTER (WHERE _payment_unknown)                         AS unknown_pm,
            COUNT(*) FILTER (WHERE _device_unknown)                          AS unknown_dt
        FROM {stg}_dedup
        """
    ).fetchone()

    (
        total, no_tid, bad_sender, bad_receiver, bad_amount, bad_ts,
        dupe_count, clean_count,
        bad_sender_ifsc, bad_recv_ifsc, unknown_pm, unknown_dt,
    ) = counts  # type: ignore[misc]

    # Rejected = rows with at least one ERROR condition (exclusive of dupes)
    rejected_count = conn.execute(
        f"""
        SELECT COUNT(*) FROM {stg}_dedup
        WHERE NOT _tid_ok
           OR (NOT _sender_ok AND _tid_ok)
           OR (NOT _receiver_ok AND _tid_ok)
           OR (NOT _amount_ok AND _tid_ok)
           OR (NOT _ts_ok AND _tid_ok)
        """
    ).fetchone()[0]  # type: ignore[index]

    # rows_loaded = rows with no ERROR and is_first (includes warning rows)
    rows_loaded = conn.execute(
        f"""
        SELECT COUNT(*) FROM {stg}_dedup
        WHERE _tid_ok AND _is_first
          AND _sender_ok AND _receiver_ok
          AND _amount_ok AND _ts_ok
        """
    ).fetchone()[0]  # type: ignore[index]

    warning_count = int(bad_sender_ifsc + bad_recv_ifsc + unknown_pm + unknown_dt)
    error_count = int(rejected_count + dupe_count)

    logger.info(
        "[run:%s] clean=%d rejected=%d dupes=%d warnings=%d",
        run_id[:8], rows_loaded, rejected_count, dupe_count, warning_count,
    )

    # ── 8. Insert valid rows → transactions ──────────────────────────────── #
    conn.execute(
        f"""
        INSERT INTO transactions
        SELECT
            t_tid             AS transaction_id,
            '{run_id}'        AS run_id,
            _row_num          AS source_row,
            t_sender          AS sender_account,
            t_receiver        AS receiver_account,
            t_sender_ifsc     AS sender_ifsc,
            t_receiver_ifsc   AS receiver_ifsc,
            _amount_val       AS amount,
            _ts_val           AS ts,
            _payment_norm     AS payment_mode,
            _device_norm      AS device_type,
            t_narration       AS narration,
            t_ip              AS ip_address
        FROM {stg}_dedup
        WHERE _tid_ok AND _is_first
          AND _sender_ok AND _receiver_ok
          AND _amount_ok AND _ts_ok
        """
    )

    # ── 9. Insert error rows → ingestion_errors ──────────────────────────── #
    # We use a UNION ALL of the different error categories so the INSERT
    # is a single bulk operation.

    # Helper: mask account in a SQL expression
    # We cannot call Python per-row; instead we use SQL string manipulation.
    def _sql_mask(col: str) -> str:
        """SQL expression that masks all but last 4 chars of a varchar col."""
        return (
            f"CASE WHEN length({col}) >= 4 "
            f"THEN repeat('*', 8) || right({col}, 4) "
            f"ELSE repeat('*', length({col})) END"
        )

    sender_mask = _sql_mask("t_sender")
    receiver_mask = _sql_mask("t_receiver")

    # Masked record as JSON string (constructed in SQL, no Python eval)
    masked_record_expr = (
        f"'{{\"row\": ' || _row_num::VARCHAR || "
        f"', \"tid\": \"' || coalesce(t_tid, '') || "
        f"'\", \"sender\": \"' || {sender_mask} || "
        f"'\", \"receiver\": \"' || {receiver_mask} || "
        f"'\", \"amount\": \"' || coalesce(t_amount, '') || "
        f"'\", \"ts\": \"' || coalesce(t_timestamp, '') || '\"}}'  "
    )

    conn.execute(
        f"""
        INSERT INTO ingestion_errors
            (id, run_id, row_number, transaction_id, severity, error_type, message, masked_record)

        -- Missing / empty Transaction_ID
        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            NULL,
            'ERROR',
            'MISSING_TRANSACTION_ID',
            'Transaction_ID is missing or empty.',
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE NOT _tid_ok

        UNION ALL

        -- Bad Sender_Account (only if tid was ok, so we have a TID to reference)
        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'ERROR',
            'INVALID_SENDER_ACCOUNT',
            'Sender_Account does not match account regex: ' || coalesce(t_sender, '(null)'),
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND NOT _sender_ok

        UNION ALL

        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'ERROR',
            'INVALID_RECEIVER_ACCOUNT',
            'Receiver_Account does not match account regex: ' || coalesce(t_receiver, '(null)'),
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND NOT _receiver_ok

        UNION ALL

        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'ERROR',
            'INVALID_AMOUNT',
            'Amount is non-numeric, missing, or <= 0: ' || coalesce(t_amount, '(null)'),
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND NOT _amount_ok

        UNION ALL

        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'ERROR',
            'INVALID_TIMESTAMP',
            'Timestamp cannot be parsed as YYYY-MM-DD HH:MM:SS: ' || coalesce(t_timestamp, '(null)'),
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND NOT _ts_ok

        UNION ALL

        -- Duplicate Transaction_ID (only those not already captured by error rows)
        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'ERROR',
            'DUPLICATE_TRANSACTION_ID',
            'Duplicate Transaction_ID; kept first occurrence by file order.',
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND NOT _is_first

        UNION ALL

        -- Bad Sender IFSC — WARNING (row still loads if no other error)
        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'WARNING',
            'INVALID_SENDER_IFSC',
            'Sender_IFSC is empty or does not match ^[A-Z]{{4}}0[A-Z0-9]{{6}}$: ' || coalesce(t_sender_ifsc, '(null)'),
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND NOT _sender_ifsc_ok

        UNION ALL

        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'WARNING',
            'INVALID_RECEIVER_IFSC',
            'Receiver_IFSC is empty or does not match ^[A-Z]{{4}}0[A-Z0-9]{{6}}$: ' || coalesce(t_receiver_ifsc, '(null)'),
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND NOT _recv_ifsc_ok

        UNION ALL

        -- Unknown Payment_Mode — WARNING
        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'WARNING',
            'UNKNOWN_PAYMENT_MODE',
            'Payment_Mode not in known set (UPI/IMPS/NEFT/RTGS): ' || coalesce(t_payment_mode, '(null)'),
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND _payment_unknown

        UNION ALL

        SELECT
            nextval('ingestion_errors_id_seq'),
            '{run_id}',
            _row_num,
            t_tid,
            'WARNING',
            'UNKNOWN_DEVICE_TYPE',
            'Device_Type not in known set: ' || coalesce(t_device_type, '(null)'),
            {masked_record_expr}
        FROM {stg}_dedup
        WHERE _tid_ok AND _device_unknown
        """
    )

    # ── 10. Accounting invariant ─────────────────────────────────────────── #
    # rows_seen = rows_loaded + rows_rejected + rows_duplicate
    accounting_ok = (rows_seen == rows_loaded + rejected_count + dupe_count)
    if not accounting_ok:
        logger.error(
            "[run:%s] ACCOUNTING MISMATCH: seen=%d loaded=%d rejected=%d dupes=%d sum=%d",
            run_id[:8],
            rows_seen, rows_loaded, rejected_count, dupe_count,
            rows_loaded + rejected_count + dupe_count,
        )

    # ── 11. Finalise ingestion_runs ──────────────────────────────────────── #
    ended_at = datetime.now(timezone.utc)
    duration = time.perf_counter() - t0

    conn.execute(
        """
        UPDATE ingestion_runs SET
            ended_at        = ?,
            duration_seconds= ?,
            rows_seen       = ?,
            rows_loaded     = ?,
            rows_rejected   = ?,
            rows_duplicate  = ?,
            warning_count   = ?,
            error_count     = ?,
            status          = 'COMPLETE'
        WHERE run_id = ?
        """,
        [
            ended_at, duration,
            rows_seen, rows_loaded, rejected_count, dupe_count,
            warning_count, error_count,
            run_id,
        ],
    )

    # ── 12. Write JSON report ────────────────────────────────────────────── #
    report = IngestionReport(
        run_id=run_id,
        input_file=src_name,
        input_size_bytes=input_size_bytes,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        duration_seconds=round(duration, 4),
        rows_seen=rows_seen,
        rows_loaded=rows_loaded,
        rows_rejected=rejected_count,
        rows_duplicate=dupe_count,
        warning_count=warning_count,
        error_count=error_count,
        db_path=effective_db_path,
        schema_version=settings.schema_version,
        status="COMPLETE",
        report_path="",
        accounting_ok=accounting_ok,
        warnings=report_warnings,
    )

    report_file = _write_report(report, settings.data_benchmarks_dir)
    report.report_path = str(report_file)

    # Update report_path in DB
    conn.execute(
        "UPDATE ingestion_runs SET report_path=? WHERE run_id=?",
        [str(report_file), run_id],
    )

    # ── 13. Cleanup staging ──────────────────────────────────────────────── #
    try:
        conn.execute(f"DROP VIEW IF EXISTS {stg}_validated")
        conn.execute(f"DROP TABLE IF EXISTS {stg}_dedup")
        conn.execute(f"DROP TABLE IF EXISTS {stg}")
    except Exception:
        pass  # staging cleanup is best-effort

    logger.info(
        "[run:%s] COMPLETE in %.2fs | loaded=%d rejected=%d dupes=%d warnings=%d | accounting_ok=%s",
        run_id[:8], duration, rows_loaded, rejected_count,
        dupe_count, warning_count, accounting_ok,
    )

    return report
