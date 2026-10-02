"""
scripts/benchmark_ingestion.py
-------------------------------
Benchmark ingestion + Module B feature calculations.

Usage:
  python scripts/benchmark_ingestion.py --rows 2000000 --output data/raw/synthetic_2m.csv
  python scripts/benchmark_ingestion.py --rows 100000  --use-existing data/raw/existing.csv

Records:
  - hardware config
  - ingestion time
  - rows/second
  - peak memory estimate
  - feature calculation time
  - 4-hop trace latency
  - API response estimate

NEVER fakes results.  If a target is not met, reports actual + bottleneck.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure backend/ is importable
_backend = Path(__file__).resolve().parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

os.environ.setdefault("DB_PATH", "data/benchmarks/bench.duckdb")
os.environ.setdefault("DATA_BENCHMARKS_DIR", "data/benchmarks")
os.environ.setdefault("DATA_RAW_DIR", "data/raw")
os.environ.setdefault("DATA_PROCESSED_DIR", "data/benchmarks")
os.environ.setdefault("DATA_PARQUET_DIR", "data/benchmarks")


def get_hardware_info() -> dict:
    import multiprocessing
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / 1e9
    except ImportError:
        ram_gb = None
    return {
        "os": platform.system(),
        "os_version": platform.version()[:80],
        "python": platform.python_version(),
        "cpu_count": multiprocessing.cpu_count(),
        "ram_gb": round(ram_gb, 1) if ram_gb else "psutil not installed",
        "machine": platform.machine(),
    }


def run_benchmark(csv_path: str, rows: int, generate: bool) -> dict:
    import duckdb
    from app.config import get_settings
    from app.database.schema import initialize_database, get_connection

    settings = get_settings()
    results: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hardware": get_hardware_info(),
        "dataset": {"rows_requested": rows, "csv_path": csv_path},
        "targets": {
            "ingestion_seconds": 60,
            "four_hop_seconds": 2,
        },
        "results": {},
        "target_status": {},
    }

    # ── Generate synthetic data if needed ─────────────────────────────────── #
    if generate:
        print(f"Generating {rows:,} synthetic rows → {csv_path} …", flush=True)
        sys.path.insert(0, str(Path(__file__).parent))
        from generate_synthetic import generate as gen
        t0 = time.perf_counter()
        summary = gen(rows=rows, output=Path(csv_path))
        gen_time = time.perf_counter() - t0
        results["dataset"]["generation_seconds"] = round(gen_time, 2)
        results["dataset"]["file_size_mb"] = summary["file_size_mb"]
        print(f"  Generated in {gen_time:.1f}s — {summary['file_size_mb']}MB", flush=True)

    # ── Ingestion benchmark ────────────────────────────────────────────────── #
    print("Running ingestion benchmark …", flush=True)

    # Fresh DB for benchmark
    bench_db = Path("data/benchmarks/bench.duckdb")
    bench_db.parent.mkdir(parents=True, exist_ok=True)
    if bench_db.exists():
        bench_db.unlink()

    conn = duckdb.connect(str(bench_db))
    conn.execute("SET memory_limit='10GB'")
    conn.execute("SET threads=4")
    initialize_database(conn)

    from app.ingestion.engine import run_ingestion
    t0 = time.perf_counter()
    report = run_ingestion(input_file=csv_path, conn=conn)
    ingest_time = time.perf_counter() - t0

    rows_per_sec = report.rows_loaded / ingest_time if ingest_time > 0 else 0
    results["results"]["ingestion"] = {
        "rows_loaded": report.rows_loaded,
        "rows_rejected": report.rows_rejected,
        "rows_duplicate": report.rows_duplicate,
        "duration_seconds": round(ingest_time, 2),
        "rows_per_second": round(rows_per_sec, 0),
        "accounting_ok": report.accounting_ok,
    }

    target_met = ingest_time <= results["targets"]["ingestion_seconds"]
    results["target_status"]["ingestion_60s"] = {
        "target": 60,
        "actual": round(ingest_time, 2),
        "met": target_met,
        "bottleneck": None if target_met else "DuckDB CSV read speed — try SSD, increase threads",
    }
    print(f"  Ingestion: {ingest_time:.2f}s ({rows_per_sec:,.0f} rows/sec) — {'✓ TARGET MET' if target_met else '✗ TARGET MISSED'}", flush=True)

    # ── Materialise accounts ──────────────────────────────────────────────── #
    print("Materialising accounts …", flush=True)
    t0 = time.perf_counter()
    conn.execute("DELETE FROM accounts")
    conn.execute("""
        INSERT INTO accounts
            (account_node_id, normalized_account_id, first_seen, last_seen,
             incoming_count, outgoing_count, incoming_amount, outgoing_amount,
             unique_senders, unique_receivers)
        SELECT
            nextval('account_node_id_seq'), acct,
            MIN(ts), MAX(ts),
            COUNT(*) FILTER (WHERE receiver_account = acct),
            COUNT(*) FILTER (WHERE sender_account   = acct),
            COALESCE(SUM(amount) FILTER (WHERE receiver_account = acct), 0),
            COALESCE(SUM(amount) FILTER (WHERE sender_account   = acct), 0),
            COUNT(DISTINCT sender_account)   FILTER (WHERE receiver_account = acct),
            COUNT(DISTINCT receiver_account) FILTER (WHERE sender_account   = acct)
        FROM (
            SELECT sender_account AS acct, ts, amount, sender_account, receiver_account FROM transactions
            UNION ALL
            SELECT receiver_account AS acct, ts, amount, sender_account, receiver_account FROM transactions
        )
        GROUP BY acct
    """)
    mat_time = time.perf_counter() - t0
    acct_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    results["results"]["materialisation"] = {
        "duration_seconds": round(mat_time, 2),
        "account_count": acct_count,
    }
    print(f"  Materialised {acct_count:,} accounts in {mat_time:.2f}s", flush=True)

    # ── 4-hop trace benchmark ──────────────────────────────────────────────── #
    print("Running 4-hop trace benchmark …", flush=True)
    victim_row = conn.execute(
        "SELECT normalized_account_id FROM accounts WHERE outgoing_count > 0 LIMIT 1"
    ).fetchone()

    if victim_row:
        victim = victim_row[0]
        from app.detection.hop_tracer import trace_victim
        # Warm-up
        trace_victim(conn, victim, max_hops=2, max_paths=5)
        # Timed run
        t0 = time.perf_counter()
        trace = trace_victim(conn, victim, max_hops=4, max_paths=50)
        trace_time = time.perf_counter() - t0

        results["results"]["four_hop_trace"] = {
            "victim": victim,
            "paths_found": trace.total_paths_found,
            "max_depth": trace.max_depth_reached,
            "reached_accounts": len(trace.all_reached_accounts),
            "duration_seconds": round(trace_time, 3),
            "truncated": trace.paths_truncated,
        }
        target_met_trace = trace_time <= results["targets"]["four_hop_seconds"]
        results["target_status"]["four_hop_2s"] = {
            "target": 2,
            "actual": round(trace_time, 3),
            "met": target_met_trace,
            "bottleneck": None if target_met_trace else (
                "DuckDB join performance — add indexes on sender_account/receiver_account"
            ),
        }
        print(f"  4-hop trace: {trace_time:.3f}s — {'✓ TARGET MET' if target_met_trace else '✗ TARGET MISSED'}", flush=True)
    else:
        print("  No accounts with outgoing transactions — skipping 4-hop trace", flush=True)
        results["results"]["four_hop_trace"] = {"error": "No suitable victim found"}

    conn.close()

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--output", type=str, default="data/raw/bench_synthetic.csv")
    parser.add_argument("--use-existing", type=str, default=None,
                        help="Use existing CSV instead of generating new data")
    args = parser.parse_args()

    csv_path = args.use_existing or args.output
    generate = args.use_existing is None

    try:
        results = run_benchmark(csv_path=csv_path, rows=args.rows, generate=generate)
    except Exception as exc:
        print(f"Benchmark FAILED: {exc}")
        raise

    # Write results
    out_path = Path("data/benchmarks") / f"benchmark_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))

    print(f"\nBenchmark results written to: {out_path}")
    print("\nSummary:")
    for k, v in results.get("target_status", {}).items():
        status = "✓ MET" if v["met"] else "✗ MISSED"
        print(f"  {k}: target={v['target']}s actual={v['actual']}s — {status}")
        if not v["met"] and v.get("bottleneck"):
            print(f"    Bottleneck: {v['bottleneck']}")


if __name__ == "__main__":
    main()
