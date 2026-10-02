"""
scripts/generate_synthetic.py
------------------------------
Generate a synthetic transaction CSV for benchmarking and testing.

Usage
-----
    python scripts/generate_synthetic.py --rows 2000000 --output data/raw/synthetic_2m.csv
    python scripts/generate_synthetic.py --rows 50000   --output data/raw/synthetic_50k.csv --seed 42

The generator:
  - Produces structurally valid rows matching the required schema.
  - Injects a configurable fraction of bad rows (bad amounts, timestamps,
    accounts, missing TIDs) and duplicate Transaction_IDs.
  - Randomises Payment_Mode, Device_Type, IFSC, Narration, IP_Address.
  - Uses a 15-day timestamp window matching the hackathon dataset spec.
  - Writes via csv.writer in 64 k-row chunks to avoid large in-memory buffers.
  - Labels output as SYNTHETIC — never claimed as real data.

Injected error / edge-case rows (controlled by --error-rate, default 0.5 %)
  BAD_AMOUNT         : "abc", "-100", "0", ""
  BAD_TIMESTAMP      : "31/12/2026", "not-a-date", ""
  BAD_ACCOUNT        : too short, letters, empty
  MISSING_TID        : empty Transaction_ID
  DUPLICATE_TID      : repeated Transaction_ID from an earlier row
  BAD_IFSC (WARNING) : malformed IFSC codes  (row still valid otherwise)
  UNKNOWN_PAYMENT    : "WIRE", "CASH"        (WARNING)
  UNKNOWN_DEVICE     : "SmartTV", "Unknown"  (WARNING)

NOTE: This script uses only stdlib + random; it has zero external deps so it
      can run before the full requirements.txt is installed.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


# ── Constants ─────────────────────────────────────────────────────────────── #

PAYMENT_MODES = ["UPI", "IMPS", "NEFT", "RTGS"]
DEVICE_TYPES  = ["Android", "iOS", "Windows_Browser", "Web_Emulator", "Linux_Script"]
BANKS         = ["HDFC", "ICIC", "SBIN", "AXIS", "KOTAK", "PUNB", "UBIN", "BARB"]
NARRATIONS    = [
    "Payment for services", "Fund transfer", "Reimbursement",
    "Online purchase", "EMI payment", "Salary credit",
    "Rent payment", "Investment transfer", "Refund",
    "Medical expenses", "Travel booking", "Utility bill",
]

HEADER = [
    "Transaction_ID", "Sender_Account", "Receiver_Account",
    "Sender_IFSC", "Receiver_IFSC", "Amount", "Timestamp",
    "Payment_Mode", "Narration", "IP_Address", "Device_Type",
]

BASE_DATE = datetime(2026, 9, 1, 0, 0, 0)
WINDOW_SECONDS = 15 * 24 * 3600  # 15 days

# ── Helpers ───────────────────────────────────────────────────────────────── #

def _account(rng: random.Random) -> str:
    """Generate a valid 12-digit account number preserving leading zeros."""
    # ~10 % chance of leading zero
    if rng.random() < 0.10:
        return "0" + "".join(str(rng.randint(0, 9)) for _ in range(11))
    return "".join(str(rng.randint(0, 9)) for _ in range(12))


def _ifsc(rng: random.Random) -> str:
    bank = rng.choice(BANKS)
    branch = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(6))
    return f"{bank}0{branch}"


def _ip(rng: random.Random) -> str:
    return ".".join(str(rng.randint(1, 254)) for _ in range(4))


def _timestamp(rng: random.Random) -> str:
    offset = rng.randint(0, WINDOW_SECONDS)
    dt = BASE_DATE + timedelta(seconds=offset)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _txn_id(idx: int) -> str:
    return f"TXN{idx:012d}"


def _bad_account(rng: random.Random) -> str:
    choices = [
        "12345",              # too short
        "ABCDEF123456",       # letters
        "",                   # empty
        "12345678901234567",  # too long
    ]
    return rng.choice(choices)


def _bad_amount(rng: random.Random) -> str:
    choices = ["abc", "-100.00", "0", "", "1,000.00", "NaN"]
    return rng.choice(choices)


def _bad_timestamp(rng: random.Random) -> str:
    choices = [
        "31/12/2026",
        "not-a-date",
        "2026-13-01 00:00:00",  # invalid month
        "",
        "01-01-2026",
    ]
    return rng.choice(choices)


def _bad_ifsc(rng: random.Random) -> str:
    choices = ["BADIFSC", "1234567890", "", "HDFC00000"]
    return rng.choice(choices)


# ── Generator ─────────────────────────────────────────────────────────────── #

def generate(
    rows: int,
    output: Path,
    seed: int = 42,
    error_rate: float = 0.005,
    chunk_size: int = 65_536,
) -> dict:
    """
    Write *rows* synthetic rows to *output*.

    Returns a summary dict with injection counts.
    """
    rng = random.Random(seed)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Pre-compute how many error rows of each type to inject
    total_bad   = max(1, int(rows * error_rate))
    n_bad_amt   = total_bad // 6
    n_bad_ts    = total_bad // 6
    n_bad_acct  = total_bad // 6
    n_missing   = total_bad // 6
    n_dupes     = total_bad // 6
    n_bad_ifsc  = total_bad - (n_bad_amt + n_bad_ts + n_bad_acct + n_missing + n_dupes)

    # Build a set of row indices that will be injected with each error type
    all_indices = list(range(rows))
    rng.shuffle(all_indices)
    bad_amt_idx    = set(all_indices[:n_bad_amt])
    bad_ts_idx     = set(all_indices[n_bad_amt:n_bad_amt+n_bad_ts])
    bad_acct_idx   = set(all_indices[n_bad_amt+n_bad_ts:n_bad_amt+n_bad_ts+n_bad_acct])
    missing_idx    = set(all_indices[n_bad_amt+n_bad_ts+n_bad_acct:n_bad_amt+n_bad_ts+n_bad_acct+n_missing])
    dupe_idx       = set(all_indices[n_bad_amt+n_bad_ts+n_bad_acct+n_missing:n_bad_amt+n_bad_ts+n_bad_acct+n_missing+n_dupes])
    bad_ifsc_idx   = set(all_indices[n_bad_amt+n_bad_ts+n_bad_acct+n_missing+n_dupes:n_bad_amt+n_bad_ts+n_bad_acct+n_missing+n_dupes+n_bad_ifsc])

    # TIDs emitted so far (for duplicate injection)
    emitted_tids: list[str] = []

    counters = {
        "bad_amount": 0, "bad_timestamp": 0, "bad_account": 0,
        "missing_tid": 0, "duplicate": 0, "bad_ifsc": 0,
        "clean": 0,
    }

    t0 = time.perf_counter()

    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)

        buffer: list[list[str]] = []

        for i in range(rows):
            tid = _txn_id(i + 1)

            # Determine injections for this row
            is_dupe      = i in dupe_idx and len(emitted_tids) > 10
            is_missing   = i in missing_idx
            is_bad_amt   = i in bad_amt_idx
            is_bad_ts    = i in bad_ts_idx
            is_bad_acct  = i in bad_acct_idx
            is_bad_ifsc  = i in bad_ifsc_idx

            # TID
            if is_missing:
                actual_tid = ""
                counters["missing_tid"] += 1
            elif is_dupe:
                actual_tid = rng.choice(emitted_tids[-500:])  # pick recent emitted
                counters["duplicate"] += 1
            else:
                actual_tid = tid

            # Accounts
            if is_bad_acct:
                sender   = _bad_account(rng)
                receiver = _bad_account(rng)
                counters["bad_account"] += 1
            else:
                sender   = _account(rng)
                receiver = _account(rng)

            # IFSC
            if is_bad_ifsc:
                s_ifsc = _bad_ifsc(rng)
                r_ifsc = _bad_ifsc(rng)
                counters["bad_ifsc"] += 1
            else:
                s_ifsc = _ifsc(rng)
                r_ifsc = _ifsc(rng)

            # Amount
            if is_bad_amt:
                amount = _bad_amount(rng)
                counters["bad_amount"] += 1
            else:
                amount = f"{rng.uniform(100.0, 5_000_000.0):.2f}"

            # Timestamp
            if is_bad_ts:
                ts = _bad_timestamp(rng)
                counters["bad_timestamp"] += 1
            else:
                ts = _timestamp(rng)

            payment = rng.choice(PAYMENT_MODES)
            narration = rng.choice(NARRATIONS)
            ip = _ip(rng)
            device = rng.choice(DEVICE_TYPES)

            row = [actual_tid, sender, receiver, s_ifsc, r_ifsc,
                   amount, ts, payment, narration, ip, device]
            buffer.append(row)

            if not is_missing and not is_dupe:
                emitted_tids.append(actual_tid)

            if not any([is_missing, is_dupe, is_bad_amt, is_bad_ts, is_bad_acct]):
                counters["clean"] += 1

            if len(buffer) >= chunk_size:
                writer.writerows(buffer)
                buffer.clear()

        if buffer:
            writer.writerows(buffer)

    elapsed = time.perf_counter() - t0
    size_bytes = output.stat().st_size

    summary = {
        "label": "SYNTHETIC",
        "output": str(output),
        "rows_requested": rows,
        "rows_written": rows + 1,   # +1 for header (the CSV has rows data rows)
        "file_size_bytes": size_bytes,
        "file_size_mb": round(size_bytes / 1_048_576, 2),
        "generation_seconds": round(elapsed, 2),
        "injected": counters,
        "seed": seed,
        "error_rate": error_rate,
    }
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────── #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic transaction CSV for Abhedya-Chakra benchmarks."
    )
    parser.add_argument("--rows",       type=int,   default=100_000,
                        help="Number of data rows to generate (default 100 000).")
    parser.add_argument("--output",     type=str,   default="data/raw/synthetic.csv",
                        help="Output CSV path.")
    parser.add_argument("--seed",       type=int,   default=42,
                        help="Random seed for reproducibility.")
    parser.add_argument("--error-rate", type=float, default=0.005,
                        help="Fraction of rows injected with errors (default 0.005 = 0.5 %%).")
    parser.add_argument("--chunk-size", type=int,   default=65_536,
                        help="Write buffer rows (default 65536).")
    args = parser.parse_args()

    output = Path(args.output)
    print(f"Generating {args.rows:,} rows → {output} …", flush=True)
    summary = generate(
        rows=args.rows,
        output=output,
        seed=args.seed,
        error_rate=args.error_rate,
        chunk_size=args.chunk_size,
    )

    import json
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
