"""
app/detection/pass_through.py
------------------------------
High-velocity pass-through detection — Module B Stage E.

Detects accounts that receive funds and rapidly disperse them to multiple
downstream accounts within a configurable time window.

Allocation policy
-----------------
We implement an AGGREGATE BEHAVIORAL model (not exact rupee attribution):

  For each incoming transaction to the account in the observation period,
  identify all outgoing transactions from the account within [ts_in, ts_in + window].
  Sum the outgoing value and divide by total incoming value.

This is a behavioral indicator — it does NOT prove that specific incoming
rupees funded specific outgoing transfers.  When funds are pooled, exact
attribution is impossible.  The limitation is documented in every result.

We prevent double-counting of outgoing transactions by:
  1. Using DISTINCT outgoing transaction_ids across all windows.
  2. Reporting the aggregate ratio, not per-incoming attribution.

Configurable thresholds (all can be overridden per call):
  DEFAULT_RATIO_THRESHOLD   0.90   (90% of incoming dispersed)
  DEFAULT_WINDOW_MINUTES    15
  DEFAULT_MIN_RECEIVERS     2      (must go to multiple accounts, not one)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import duckdb

from app.schemas.detection import PassThroughFinding

logger = logging.getLogger(__name__)

DEFAULT_RATIO_THRESHOLD = 0.90
DEFAULT_WINDOW_MINUTES  = 15
DEFAULT_MIN_RECEIVERS   = 2


def detect_pass_through(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    min_receivers: int = DEFAULT_MIN_RECEIVERS,
) -> PassThroughFinding:
    """
    Run pass-through detection for *account_id*.

    Returns a PassThroughFinding with detected=True only if:
      - pass-through ratio >= ratio_threshold
      - unique receivers within window >= min_receivers

    All SQL uses parameterised queries.  No per-row Python.
    """
    account_id = account_id.upper()

    # Step 1: total incoming amount for this account
    inc_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE receiver_account = ?",
        [account_id],
    ).fetchone()
    total_incoming = Decimal(str(inc_row[0])) if inc_row else Decimal("0")

    if total_incoming == 0:
        return PassThroughFinding(
            account_id=account_id,
            detected=False,
            explanation="No incoming transactions found.",
        )

    # Step 2: for each incoming transaction, find DISTINCT outgoing within window
    result = conn.execute(
        f"""
        SELECT
            SUM(DISTINCT_OUT.amount)                AS total_out_in_window,
            COUNT(DISTINCT DISTINCT_OUT.transaction_id)  AS out_tx_count,
            COUNT(DISTINCT DISTINCT_OUT.receiver_account) AS unique_receivers
        FROM (
            -- Distinct outgoing transactions that fall within ANY incoming window
            SELECT DISTINCT t_out.transaction_id, t_out.amount, t_out.receiver_account
            FROM transactions t_in
            JOIN transactions t_out
              ON t_out.sender_account = ?
             AND t_out.ts BETWEEN t_in.ts
                              AND t_in.ts + INTERVAL '{window_minutes} minutes'
            WHERE t_in.receiver_account = ?
        ) AS DISTINCT_OUT
        """,
        [account_id, account_id],
    ).fetchone()

    out_in_window = Decimal(str(result[0])) if result[0] else Decimal("0")
    out_tx_count  = int(result[1]) if result[1] else 0
    uniq_recv     = int(result[2]) if result[2] else 0

    # Ratio: capped at 1.0 (outgoing can exceed incoming due to pre-existing balance)
    ratio = min(1.0, float(out_in_window / total_incoming)) if total_incoming > 0 else 0.0

    detected = (ratio >= ratio_threshold) and (uniq_recv >= min_receivers)

    # Step 3: collect supporting transaction IDs (bounded to 100)
    in_txids = conn.execute(
        """
        SELECT transaction_id FROM transactions
        WHERE receiver_account = ?
        ORDER BY ts ASC LIMIT 100
        """,
        [account_id],
    ).fetchall()

    out_txids = conn.execute(
        f"""
        SELECT DISTINCT t_out.transaction_id
        FROM transactions t_in
        JOIN transactions t_out
          ON t_out.sender_account = ?
         AND t_out.ts BETWEEN t_in.ts
                          AND t_in.ts + INTERVAL '{window_minutes} minutes'
        WHERE t_in.receiver_account = ?
        ORDER BY t_out.ts ASC LIMIT 100
        """,
        [account_id, account_id],
    ).fetchall()

    explanation = (
        f"Account received {total_incoming} INR total. "
        f"Within {window_minutes}-minute windows after each incoming transaction, "
        f"{out_in_window} INR ({ratio*100:.1f}%) was dispersed outward "
        f"via {out_tx_count} transactions to {uniq_recv} unique receivers. "
        f"Threshold: {ratio_threshold*100:.0f}% to {min_receivers}+ receivers. "
        f"{'TRIGGERED' if detected else 'NOT triggered'}."
    )

    return PassThroughFinding(
        account_id=account_id,
        detected=detected,
        pass_through_ratio=ratio,
        pct_dispersed_within_window=ratio,
        window_minutes=window_minutes,
        incoming_amount_in_window=str(total_incoming),
        outgoing_amount_in_window=str(out_in_window),
        outgoing_tx_count=out_tx_count,
        unique_receivers_in_window=uniq_recv,
        supporting_incoming_tx=[r[0] for r in in_txids],
        supporting_outgoing_tx=[r[0] for r in out_txids],
        explanation=explanation,
        limitation=(
            "This is an aggregate behavioral indicator. "
            "Exact source-to-destination rupee attribution is not possible "
            "when funds are pooled at an intermediate account."
        ),
    )
