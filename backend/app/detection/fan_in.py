"""
app/detection/fan_in.py
------------------------
Fan-in (collector) detection — Module B Stage E.

Detects accounts receiving funds from many distinct counterparties,
potentially acting as collectors in a mule ring.

Thresholds are percentile-based: an account is flagged if its unique_senders
exceeds the P90 across all accounts — not a fixed absolute number.
This handles the uniform activity distribution in the competition dataset.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import duckdb

from app.schemas.detection import FanInFinding

logger = logging.getLogger(__name__)

DEFAULT_UNIQUE_SENDER_PERCENTILE = 0.90   # flag if unique_senders > P90
DEFAULT_MIN_UNIQUE_SENDERS = 5            # absolute floor regardless of percentile


def detect_fan_in(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    percentile_threshold: float = DEFAULT_UNIQUE_SENDER_PERCENTILE,
    min_unique_senders: int = DEFAULT_MIN_UNIQUE_SENDERS,
) -> FanInFinding:
    """
    Run fan-in detection for *account_id*.

    An account is flagged if its unique_senders exceeds the configured
    percentile across all accounts (or min_unique_senders, whichever is higher).
    """
    account_id = account_id.upper()

    # Get account's own stats (fast — indexed lookup)
    row = conn.execute(
        """
        SELECT incoming_count, unique_senders,
               CAST(incoming_amount AS DOUBLE) AS inc_amt
        FROM accounts WHERE normalized_account_id = ?
        """,
        [account_id],
    ).fetchone()

    if row is None:
        return FanInFinding(account_id=account_id, detected=False,
                            explanation="Account not found.")

    inc_count, uniq_senders, inc_amt = row[0], row[1], row[2]

    # Compute percentile threshold across all accounts
    pct_row = conn.execute(
        f"SELECT percentile_cont({percentile_threshold}) WITHIN GROUP "
        f"(ORDER BY unique_senders) FROM accounts"
    ).fetchone()
    threshold = max(int(pct_row[0]), min_unique_senders) if pct_row and pct_row[0] else min_unique_senders

    detected = uniq_senders > threshold

    # Peak hourly incoming (busiest 1-hour window)
    peak_row = conn.execute(
        """
        SELECT MAX(hour_count)
        FROM (
            SELECT DATE_TRUNC('hour', ts) AS hour, COUNT(*) AS hour_count
            FROM transactions
            WHERE receiver_account = ?
            GROUP BY hour
        )
        """,
        [account_id],
    ).fetchone()
    peak_hourly = int(peak_row[0]) if peak_row and peak_row[0] else None

    # Sender concentration (HHI)
    hhi_row = conn.execute(
        """
        SELECT SUM(share * share) FROM (
            SELECT COUNT(*)::DOUBLE / SUM(COUNT(*)) OVER () AS share
            FROM transactions WHERE receiver_account = ?
            GROUP BY sender_account
        )
        """,
        [account_id],
    ).fetchone()
    sender_hhi = float(hhi_row[0]) if hhi_row and hhi_row[0] else None

    # Supporting transactions (bounded to 50)
    txids = conn.execute(
        """
        SELECT transaction_id FROM transactions
        WHERE receiver_account = ?
        ORDER BY ts ASC LIMIT 50
        """,
        [account_id],
    ).fetchall()

    explanation = (
        f"Account has {uniq_senders} unique senders "
        f"(P{int(percentile_threshold*100)} threshold={threshold}). "
        f"Incoming transactions: {inc_count}. "
        f"Total incoming: {inc_amt:.2f} INR. "
        f"Peak hourly: {peak_hourly}. "
        f"Sender HHI concentration: {f'{sender_hhi:.3f}' if sender_hhi is not None else 'N/A'}. "
        f"{'TRIGGERED' if detected else 'NOT triggered'}."
    )

    return FanInFinding(
        account_id=account_id,
        detected=detected,
        unique_senders=uniq_senders,
        in_degree=uniq_senders,
        incoming_tx_count=inc_count,
        incoming_amount=str(Decimal(str(inc_amt)).quantize(Decimal("0.01"))),
        sender_concentration=sender_hhi,
        peak_hourly_incoming=float(peak_hourly) if peak_hourly else None,
        supporting_tx=[r[0] for r in txids],
        explanation=explanation,
    )
