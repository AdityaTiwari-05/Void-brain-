"""
CLI entry point for Operation Abhedya-Chakra — Phase 1 ingestion.

Usage
-----
    python -m app.ingestion ingest    --input FILE [--db-path PATH]
    python -m app.ingestion validate  --input FILE
    python -m app.ingestion benchmark --input FILE [--db-path PATH]

Commands
--------
ingest      Full pipeline: normalise → load → report.
validate    Schema-check only (column presence); does NOT load data.
benchmark   Same as ingest but prints rows/sec and memory stats.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Ensure backend/ is on sys.path when invoked as `python -m app.ingestion`
_here = Path(__file__).resolve().parent.parent.parent  # → backend/
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
    )


def cmd_validate(args: argparse.Namespace) -> int:
    """Column-presence check only — no DB, no row loading."""
    from app.validation.schema_check import validate_columns

    src = Path(args.input)
    if not src.exists():
        print(f"ERROR: File not found: {src}", file=sys.stderr)
        return 1

    # Read header without duckdb to keep this truly dependency-light
    import csv

    with src.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            print("ERROR: File is empty.", file=sys.stderr)
            return 1

    result = validate_columns(header)

    if result.warning_message:
        print(f"WARNING: {result.warning_message}")
    if result.ok:
        print("OK: All required columns present.")
        return 0
    else:
        print(f"FAIL: {result.error_message}", file=sys.stderr)
        return 2


def cmd_ingest(args: argparse.Namespace, benchmark_mode: bool = False) -> int:
    """Run the full ingestion pipeline."""
    from app.ingestion.engine import run_ingestion

    db_path = Path(args.db_path) if getattr(args, "db_path", None) else None

    t0 = time.perf_counter()
    report = run_ingestion(input_file=args.input, db_path=db_path)
    elapsed = time.perf_counter() - t0

    # Print summary
    rows_per_sec = report.rows_seen / elapsed if elapsed > 0 else 0
    print(json.dumps(
        {
            "run_id":           report.run_id,
            "status":           report.status,
            "rows_seen":        report.rows_seen,
            "rows_loaded":      report.rows_loaded,
            "rows_rejected":    report.rows_rejected,
            "rows_duplicate":   report.rows_duplicate,
            "warning_count":    report.warning_count,
            "error_count":      report.error_count,
            "duration_seconds": report.duration_seconds,
            "rows_per_sec":     round(rows_per_sec, 1),
            "accounting_ok":    report.accounting_ok,
            "report_path":      report.report_path,
        },
        indent=2,
    ))

    if benchmark_mode:
        threshold = None
        try:
            from app.config import get_settings
            threshold = get_settings().benchmark_threshold_rows_per_sec
        except Exception:
            pass

        verdict = "N/A"
        if threshold is not None:
            verdict = "PASS" if rows_per_sec >= threshold else "FAIL"
        print(f"\nBENCHMARK  rows/sec={rows_per_sec:.0f}  threshold={threshold}  verdict={verdict}")

    if not report.accounting_ok:
        print("ACCOUNTING INVARIANT FAILED", file=sys.stderr)
        return 3

    return 0 if report.status == "COMPLETE" else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion",
        description="Abhedya-Chakra Phase 1 — Ingestion CLI",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    p_val = sub.add_parser("validate", help="Column-presence check only.")
    p_val.add_argument("--input", required=True, help="Path to CSV file.")

    # ingest
    p_ing = sub.add_parser("ingest", help="Full ingest pipeline.")
    p_ing.add_argument("--input", required=True, help="Path to CSV file.")
    p_ing.add_argument("--db-path", default=None, help="Override DB path.")

    # benchmark
    p_bnk = sub.add_parser("benchmark", help="Ingest + print performance stats.")
    p_bnk.add_argument("--input", required=True, help="Path to CSV file.")
    p_bnk.add_argument("--db-path", default=None, help="Override DB path.")

    args = parser.parse_args()
    _setup_logging(args.log_level)

    if args.command == "validate":
        sys.exit(cmd_validate(args))
    elif args.command == "ingest":
        sys.exit(cmd_ingest(args, benchmark_mode=False))
    elif args.command == "benchmark":
        sys.exit(cmd_ingest(args, benchmark_mode=True))


if __name__ == "__main__":
    main()
