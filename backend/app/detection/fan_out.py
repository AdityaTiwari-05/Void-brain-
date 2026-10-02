"""
app/detection/fan_out.py
-------------------------
Fan-out (distributor) detection — Module B Stage E.

Detects accounts distributing funds to multiple downstream accounts,
potentially acting as distributors in a mule ring.

Splitting behavior detection:
  An account shows splitting when it sends many outgoing transactions
  of similar amounts to distinct receivers in a short time window.
  We detect this via coefficient of variation (CV = std/mean) of outgoing
  amounts: low CV with high receiver count = splitting pattern.
"""

from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import Optional

import duckdb

from app.schemas.detection import FanOutFinding

logger = logging.getLogger(__name__)

DEFAULT_UNIQUE_RECEIVER_PERCENTILE = 0.90
DEFAULT_MIN_UNIQUE_RECEIVERS = 5
DEFAULT_SPLITTING_CV_THRESHOLD = 0.30   # CV below this = amounts are similar


def detect_fan_out(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    percentile_threshold: float = DEFAULT_UNIQUE_RECEIVER_PERCENTILE,
    min_unique_receivers: int = DEFAULT_MIN_UNIQUE_RECEIVERS,
    splitting_cv_threshold: float = DEFAULT_SPLITTING_CV_THRESHOLD,
) -> FanOutFinding:
    """Run fan-out detection for *account_id*."""
    account_id = account_id.upper()

    row = conn.execute(
        """
        SELECT outgoing_count, unique_receivers,
               CAST(outgoing_amount AS DOUBLE) AS out_amt
        FROM accounts WHERE normalized_account_id = ?
        """,
        [account_id],
    ).fetchone()

    if row is None:
        return FanOutFinding(account_id=account_id, detected=False,
                             explanation="Account not found.")

    out_count, uniq_recv, out_amt = row[0], row[1], row[2]

    # Percentile threshold
    pct_row = conn.execute(
        f"SELECT percentile_cont({percentile_threshold}) WITHIN GROUP "
        f"(ORDER BY unique_receivers) FROM accounts"
    ).fetchone()
    threshold = max(int(pct_row[0]), min_unique_receivers) if pct_row and pct_row[0] else min_unique_receivers

    detected = uniq_recv > threshold

    # Splitting behavior: CV of outgoing amounts
    cv_row = conn.execute(
        """
        SELECT STDDEV_POP(amount), AVG(amount)
        FROM transactions WHERE sender_account = ?
        """,
        [account_id],
    ).fetchone()
    splitting = False
    if cv_row and cv_row[1] is not None and float(cv_row[1]) > 0:
        std = float(cv_row[0]) if cv_row[0] is not None else 0.0
        cv = std / float(cv_row[1])
        splitting = (cv < splitting_cv_threshold) and (uniq_recv >= min_unique_receivers)

    # Receiver concentration (HHI)
    hhi_row = conn.execute(
        """
        SELECT SUM(share * share) FROM (
            SELECT COUNT(*)::DOUBLE / SUM(COUNT(*)) OVER () AS share
            FROM transactions WHERE sender_account = ?
            GROUP BY receiver_account
        )
        """,
        [account_id],
    ).fetchone()
    recv_hhi = float(hhi_row[0]) if hhi_row and hhi_row[0] else None

    # Supporting transactions (bounded)
    txids = conn.execute(
        """
        SELECT transaction_id FROM transactions
        WHERE sender_account = ?
        ORDER BY ts ASC LIMIT 50
        """,
        [account_id],
    ).fetchall()

    explanation = (
        f"Account has {uniq_recv} unique receivers "
        f"(P{int(percentile_threshold*100)} threshold={threshold}). "
        f"Outgoing transactions: {out_count}. "
        f"Total outgoing: {out_amt:.2f} INR. "
        f"Splitting pattern: {'YES' if splitting else 'NO'}. "
        f"Receiver HHI concentration: {f'{recv_hhi:.3f}' if recv_hhi is not None else 'N/A'}. "
        f"{'TRIGGERED' if detected else 'NOT triggered'}."
    )

    return FanOutFinding(
        account_id=account_id,
        detected=detected,
        unique_receivers=uniq_recv,
        out_degree=uniq_recv,
        outgoing_tx_count=out_count,
        outgoing_amount=str(Decimal(str(out_amt)).quantize(Decimal("0.01"))),
        receiver_concentration=recv_hhi,
        splitting_detected=splitting,
        supporting_tx=[r[0] for r in txids],
        explanation=explanation,
    )
