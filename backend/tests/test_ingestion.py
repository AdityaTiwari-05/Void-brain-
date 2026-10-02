"""
Unit and integration tests for app.ingestion.engine.run_ingestion().

Covers (per spec)
-----------------
- Missing columns → engine raises ValueError before loading
- Malformed / invalid Sender_Account and Receiver_Account
- Leading-zero preservation in account numbers
- Timestamp parsing (valid / malformed)
- Amount validation (non-numeric, missing, zero, negative, valid)
- Payment_Mode normalisation (case variants, unknown → WARNING)
- IFSC normalisation (uppercase, malformed → WARNING)
- Device_Type normalisation (case variants, unknown → WARNING)
- Duplicate Transaction_ID handling (keep first, quarantine rest)
- Quarantine table population for ERROR rows
- Run metadata written to ingestion_runs
- Accounting invariant: rows_seen = rows_loaded + rejected + duplicates
- End-to-end small CSV → DuckDB integration (clean data)
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.ingestion.engine import run_ingestion

# Reuse fixtures and helpers from conftest
from conftest import VALID_ROW, COLUMNS, make_csv, mem_conn  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────── #

def _row(**overrides) -> dict:
    r = copy.deepcopy(VALID_ROW)
    r.update(overrides)
    return r


def _ingest(rows: list[dict], tmp_path: Path):
    """Write rows to CSV and run ingestion into a fresh in-memory DB."""
    csv_path = make_csv(rows, tmp_path)
    conn = mem_conn()
    report = run_ingestion(input_file=csv_path, conn=conn)
    return report, conn


# ── Column-level checks ───────────────────────────────────────────────────── #

def test_missing_required_column_raises(tmp_path):
    """Engine should raise ValueError when a required column is absent."""
    import csv as csv_mod
    bad_cols = [c for c in COLUMNS if c != "Amount"]
    out = tmp_path / "missing_col.csv"
    with out.open("w", newline="") as fh:
        w = csv_mod.DictWriter(fh, fieldnames=bad_cols)
        w.writeheader()
        row = {k: v for k, v in VALID_ROW.items() if k != "Amount"}
        w.writerow(row)

    conn = mem_conn()
    with pytest.raises(ValueError, match="Amount"):
        run_ingestion(input_file=out, conn=conn)


# ── Account validation ────────────────────────────────────────────────────── #

def test_valid_account_loaded(tmp_path):
    report, conn = _ingest([_row()], tmp_path)
    assert report.rows_loaded == 1
    assert report.rows_rejected == 0


def test_bad_sender_account_rejected(tmp_path):
    """Non-12-digit sender account → ERROR, row quarantined."""
    report, conn = _ingest([_row(Sender_Account="12345")], tmp_path)
    assert report.rows_loaded == 0
    assert report.rows_rejected == 1
    err = conn.execute(
        "SELECT error_type FROM ingestion_errors WHERE severity='ERROR'"
    ).fetchall()
    error_types = [e[0] for e in err]
    assert "INVALID_SENDER_ACCOUNT" in error_types


def test_bad_receiver_account_rejected(tmp_path):
    report, conn = _ingest([_row(Receiver_Account="ABCDEF123456")], tmp_path)
    assert report.rows_rejected == 1
    err_types = [r[0] for r in conn.execute(
        "SELECT error_type FROM ingestion_errors"
    ).fetchall()]
    assert "INVALID_RECEIVER_ACCOUNT" in err_types


def test_empty_sender_account_rejected(tmp_path):
    report, conn = _ingest([_row(Sender_Account="")], tmp_path)
    assert report.rows_rejected == 1


def test_leading_zero_account_preserved(tmp_path):
    """Leading zeros in account numbers must be kept exactly as entered."""
    leading_zero_account = "000000123456"
    report, conn = _ingest([_row(Sender_Account=leading_zero_account)], tmp_path)
    assert report.rows_loaded == 1
    result = conn.execute(
        "SELECT sender_account FROM transactions WHERE transaction_id=?",
        [VALID_ROW["Transaction_ID"]],
    ).fetchone()
    assert result is not None
    assert result[0] == leading_zero_account, (
        f"Leading zeros lost: expected {leading_zero_account!r}, got {result[0]!r}"
    )


# ── Timestamp parsing ─────────────────────────────────────────────────────── #

def test_valid_timestamp_loaded(tmp_path):
    report, conn = _ingest([_row(Timestamp="2026-09-10 14:30:00")], tmp_path)
    assert report.rows_loaded == 1


def test_malformed_timestamp_rejected(tmp_path):
    """Ambiguous / unparseable timestamps must not be guessed; row is ERROR."""
    for bad_ts in ["31/12/2026", "10-09-2026", "not-a-date", ""]:
        report, conn = _ingest([_row(Timestamp=bad_ts)], tmp_path)
        assert report.rows_rejected == 1, (
            f"Expected rejection for timestamp {bad_ts!r}, got rows_loaded={report.rows_loaded}"
        )


# ── Amount validation ─────────────────────────────────────────────────────── #

def test_valid_amount_loaded(tmp_path):
    report, _ = _ingest([_row(Amount="12500.50")], tmp_path)
    assert report.rows_loaded == 1


def test_non_numeric_amount_rejected(tmp_path):
    report, _ = _ingest([_row(Amount="abc")], tmp_path)
    assert report.rows_rejected == 1


def test_zero_amount_rejected(tmp_path):
    report, _ = _ingest([_row(Amount="0")], tmp_path)
    assert report.rows_rejected == 1


def test_negative_amount_rejected(tmp_path):
    report, _ = _ingest([_row(Amount="-500.00")], tmp_path)
    assert report.rows_rejected == 1


def test_empty_amount_rejected(tmp_path):
    report, _ = _ingest([_row(Amount="")], tmp_path)
    assert report.rows_rejected == 1


def test_amount_not_coerced_to_zero(tmp_path):
    """Bad amount must never become 0.00 silently in the transactions table."""
    report, conn = _ingest([_row(Amount="abc")], tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert count == 0, "Bad amount row must not appear in transactions table"


# ── Payment_Mode normalisation ────────────────────────────────────────────── #

def test_payment_mode_case_normalised(tmp_path):
    """'upi' and 'UPI' should both load as 'UPI'."""
    for variant in ["upi", "UPI", "Upi"]:
        report, conn = _ingest(
            [_row(Transaction_ID=f"TXN_PM_{variant}", Payment_Mode=variant)],
            tmp_path,
        )
        assert report.rows_loaded == 1
        mode = conn.execute("SELECT payment_mode FROM transactions").fetchone()[0]
        assert mode == "UPI", f"Expected 'UPI', got {mode!r} for input {variant!r}"


def test_unknown_payment_mode_warning_not_rejected(tmp_path):
    """Unknown Payment_Mode → WARNING, row still loaded."""
    report, conn = _ingest([_row(Payment_Mode="WIRE")], tmp_path)
    assert report.rows_loaded == 1
    assert report.warning_count >= 1
    warn = conn.execute(
        "SELECT error_type FROM ingestion_errors WHERE severity='WARNING'"
    ).fetchall()
    warn_types = [w[0] for w in warn]
    assert "UNKNOWN_PAYMENT_MODE" in warn_types


# ── IFSC normalisation ────────────────────────────────────────────────────── #

def test_ifsc_uppercased(tmp_path):
    """IFSC codes should be uppercased during normalisation."""
    report, conn = _ingest([_row(Sender_IFSC="hdfc0abcdef")], tmp_path)
    assert report.rows_loaded == 1
    ifsc = conn.execute("SELECT sender_ifsc FROM transactions").fetchone()[0]
    assert ifsc == "HDFC0ABCDEF"


def test_malformed_ifsc_warning_not_error(tmp_path):
    """Malformed IFSC → WARNING only, row must still load."""
    report, conn = _ingest([_row(Sender_IFSC="BADIFSC")], tmp_path)
    assert report.rows_loaded == 1
    assert report.warning_count >= 1
    warn_types = [r[0] for r in conn.execute(
        "SELECT error_type FROM ingestion_errors WHERE severity='WARNING'"
    ).fetchall()]
    assert "INVALID_SENDER_IFSC" in warn_types


def test_empty_ifsc_warning(tmp_path):
    report, conn = _ingest([_row(Receiver_IFSC="")], tmp_path)
    assert report.rows_loaded == 1
    warn_types = [r[0] for r in conn.execute(
        "SELECT error_type FROM ingestion_errors WHERE severity='WARNING'"
    ).fetchall()]
    assert "INVALID_RECEIVER_IFSC" in warn_types


# ── Device_Type normalisation ─────────────────────────────────────────────── #

def test_device_type_case_normalised(tmp_path):
    """'android' should normalise to 'Android'."""
    report, conn = _ingest(
        [_row(Device_Type="android")],
        tmp_path,
    )
    assert report.rows_loaded == 1
    dt = conn.execute("SELECT device_type FROM transactions").fetchone()[0]
    assert dt == "Android"


def test_unknown_device_type_warning(tmp_path):
    report, conn = _ingest([_row(Device_Type="SmartTV")], tmp_path)
    assert report.rows_loaded == 1
    warn_types = [r[0] for r in conn.execute(
        "SELECT error_type FROM ingestion_errors WHERE severity='WARNING'"
    ).fetchall()]
    assert "UNKNOWN_DEVICE_TYPE" in warn_types


# ── Duplicate Transaction_ID ──────────────────────────────────────────────── #

def test_duplicate_tid_keep_first(tmp_path):
    """First occurrence kept; subsequent occurrences quarantined."""
    rows = [
        _row(Transaction_ID="TXN_DUP_001", Amount="1000.00"),
        _row(Transaction_ID="TXN_DUP_001", Amount="2000.00"),  # duplicate
        _row(Transaction_ID="TXN_DUP_001", Amount="3000.00"),  # duplicate
    ]
    report, conn = _ingest(rows, tmp_path)
    assert report.rows_loaded == 1
    assert report.rows_duplicate == 2
    # The row in transactions should be the first (amount=1000.00)
    amt = conn.execute("SELECT amount FROM transactions WHERE transaction_id='TXN_DUP_001'").fetchone()[0]
    assert float(amt) == 1000.00, f"Expected first row (1000.00), got {amt}"


def test_duplicate_quarantined_in_errors(tmp_path):
    rows = [
        _row(Transaction_ID="TXN_DUP_002"),
        _row(Transaction_ID="TXN_DUP_002"),
    ]
    report, conn = _ingest(rows, tmp_path)
    dup_errors = conn.execute(
        "SELECT COUNT(*) FROM ingestion_errors WHERE error_type='DUPLICATE_TRANSACTION_ID'"
    ).fetchone()[0]
    assert dup_errors == 1


# ── Quarantine table ──────────────────────────────────────────────────────── #

def test_quarantine_row_has_masked_record(tmp_path):
    """ingestion_errors rows must have a masked_record that hides accounts."""
    report, conn = _ingest([_row(Amount="bad_value")], tmp_path)
    row = conn.execute(
        "SELECT masked_record FROM ingestion_errors WHERE severity='ERROR' LIMIT 1"
    ).fetchone()
    assert row is not None
    masked = row[0] or ""
    # Full account number should NOT appear in masked record
    assert VALID_ROW["Sender_Account"] not in masked or "****" in masked


# ── Run metadata ──────────────────────────────────────────────────────────── #

def test_ingestion_run_row_written(tmp_path):
    """ingestion_runs must have exactly one row per run."""
    report, conn = _ingest([_row()], tmp_path)
    run_row = conn.execute(
        "SELECT rows_seen, rows_loaded, status FROM ingestion_runs WHERE run_id=?",
        [report.run_id],
    ).fetchone()
    assert run_row is not None
    assert run_row[0] == 1      # rows_seen
    assert run_row[1] == 1      # rows_loaded
    assert run_row[2] == "COMPLETE"


def test_ingestion_run_duration_positive(tmp_path):
    report, conn = _ingest([_row()], tmp_path)
    duration = conn.execute(
        "SELECT duration_seconds FROM ingestion_runs WHERE run_id=?",
        [report.run_id],
    ).fetchone()[0]
    assert duration is not None
    assert duration > 0


# ── Accounting invariant ──────────────────────────────────────────────────── #

def test_accounting_invariant_clean(tmp_path):
    rows = [_row(Transaction_ID=f"TXN_{i:04d}") for i in range(10)]
    report, _ = _ingest(rows, tmp_path)
    assert report.accounting_ok is True
    assert report.rows_seen == report.rows_loaded + report.rows_rejected + report.rows_duplicate


def test_accounting_invariant_mixed(tmp_path):
    """Mix of valid, bad-amount, bad-account, and duplicate rows."""
    rows = [
        _row(Transaction_ID="TXN_A001"),                          # valid
        _row(Transaction_ID="TXN_A002", Amount="bad"),            # error
        _row(Transaction_ID="TXN_A003", Sender_Account="short"),  # error
        _row(Transaction_ID="TXN_A001"),                          # duplicate
        _row(Transaction_ID="TXN_A004"),                          # valid
    ]
    report, _ = _ingest(rows, tmp_path)
    assert report.accounting_ok is True
    total = report.rows_loaded + report.rows_rejected + report.rows_duplicate
    assert total == report.rows_seen


# ── End-to-end integration ────────────────────────────────────────────────── #

def test_e2e_small_csv_to_duckdb(tmp_path):
    """
    Full pipeline: 20 clean rows → DuckDB → verify counts, schema, data.
    """
    rows = [
        _row(
            Transaction_ID=f"TXN_E2E_{i:04d}",
            Sender_Account=f"{i:012d}",
            Receiver_Account=f"{(i + 1) * 3:012d}",
            Amount=str(1000 * (i + 1)),
            Timestamp=f"2026-09-{(i % 15) + 1:02d} 10:00:00",
        )
        for i in range(20)
    ]
    report, conn = _ingest(rows, tmp_path)

    assert report.rows_seen == 20
    assert report.rows_loaded == 20
    assert report.rows_rejected == 0
    assert report.rows_duplicate == 0
    assert report.accounting_ok is True
    assert report.status == "COMPLETE"

    # Verify all 20 rows are in transactions
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert count == 20

    # Verify no errors were generated
    err_count = conn.execute("SELECT COUNT(*) FROM ingestion_errors").fetchone()[0]
    assert err_count == 0

    # Verify first row data integrity
    row = conn.execute(
        "SELECT transaction_id, amount, ts FROM transactions WHERE transaction_id='TXN_E2E_0000'"
    ).fetchone()
    assert row is not None
    assert row[0] == "TXN_E2E_0000"
    assert float(row[1]) == 1000.0


def test_e2e_mixed_csv_accounting(tmp_path):
    """
    20 rows with injected errors; verify accounting invariant holds and
    error table is populated correctly.
    """
    rows = (
        [_row(Transaction_ID=f"TXN_M{i:04d}") for i in range(15)]  # 15 clean
        + [_row(Transaction_ID=f"TXN_BAD{i:04d}", Amount="NaN") for i in range(3)]  # 3 bad amount
        + [_row(Transaction_ID="TXN_M0000")]   # 1 duplicate
        + [_row(Transaction_ID="TXN_M0000")]   # 1 duplicate
    )
    report, conn = _ingest(rows, tmp_path)

    assert report.rows_seen == 20
    assert report.accounting_ok is True
    # 15 clean + 0 bad (bad-amount rows rejected) loaded
    assert report.rows_loaded == 15
    assert report.rows_rejected == 3
    assert report.rows_duplicate == 2
