"""
app/detection/terminal.py
--------------------------
Terminal / cash-out indicator detection — Module B Stage E.
Adapted for Void-brain- Phase 1 schema (narration column, no narration_markers).

Terminal indicators are behavioral signals, NOT proof of cash-out or criminality.
"""

from __future__ import annotations

import logging

import duckdb

from app.schemas.detection import TerminalIndicatorFinding

logger = logging.getLogger(__name__)


def detect_terminal_indicators(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    downstream_position_score: float = 0.0,
) -> TerminalIndicatorFinding:
    """
    Compute terminal indicator signals for *account_id*.
    Uses narration column (Phase 1 schema) for keyword matching.
    """
    account_id = account_id.upper()

    row = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE device_type = 'Linux_Script')   AS linux_count,
            COUNT(*) FILTER (WHERE device_type = 'Web_Emulator')   AS web_eml_count,
            COUNT(*) FILTER (WHERE lower(narration) LIKE '%crypto%'
                                OR lower(narration) LIKE '%bitcoin%'
                                OR lower(narration) LIKE '%usdt%')  AS crypto_count,
            COUNT(*) FILTER (WHERE lower(narration) LIKE '%p2p%'
                                OR lower(narration) LIKE '%peer%')  AS p2p_count,
            COUNT(*) FILTER (WHERE lower(narration) LIKE '%wallet%'
                                OR lower(narration) LIKE '%paytm%'
                                OR lower(narration) LIKE '%phonepe%') AS wallet_count,
            COUNT(*) FILTER (WHERE lower(narration) LIKE '%earning%'
                                OR lower(narration) LIKE '%task%')  AS earning_count,
            COUNT(*)                                               AS total_count
        FROM transactions
        WHERE sender_account = ? OR receiver_account = ?
        """,
        [account_id, account_id],
    ).fetchone()

    if row is None or row[6] == 0:
        return TerminalIndicatorFinding(
            account_id=account_id, detected=False,
            explanation="No transactions found.",
        )

    (linux_c, webeml_c, crypto_c, p2p_c, wallet_c, earning_c, total_c) = row
    unusual_ratio = (linux_c + webeml_c) / total_c if total_c > 0 else 0.0
    detected = (linux_c > 0 or webeml_c > 0 or crypto_c > 0)

    signals = []
    if linux_c > 0:    signals.append(f"Linux_Script device in {linux_c} transactions")
    if webeml_c > 0:   signals.append(f"Web_Emulator device in {webeml_c} transactions")
    if crypto_c > 0:   signals.append(f"CRYPTO narration in {crypto_c} transactions")
    if p2p_c > 0:      signals.append(f"P2P narration in {p2p_c} transactions")
    if wallet_c > 0:   signals.append(f"WALLET narration in {wallet_c} transactions")

    txids = conn.execute(
        """
        SELECT transaction_id FROM transactions
        WHERE (sender_account = ? OR receiver_account = ?)
          AND (device_type IN ('Linux_Script','Web_Emulator')
               OR lower(narration) LIKE '%crypto%'
               OR lower(narration) LIKE '%p2p%')
        ORDER BY ts ASC LIMIT 50
        """,
        [account_id, account_id],
    ).fetchall()

    explanation = (
        f"Terminal signals: {'; '.join(signals) if signals else 'none detected'}. "
        f"Unusual device ratio: {unusual_ratio*100:.1f}%. "
        f"{'TRIGGERED' if detected else 'NOT triggered'}. "
        "NOTE: Terminal indicators are signals for investigation, not proof of criminality."
    )

    return TerminalIndicatorFinding(
        account_id=account_id,
        detected=detected,
        linux_script_tx_count=linux_c,
        web_emulator_tx_count=webeml_c,
        crypto_narration_count=crypto_c,
        p2p_narration_count=p2p_c,
        wallet_narration_count=wallet_c,
        unusual_device_ratio=unusual_ratio,
        downstream_position_score=downstream_position_score,
        supporting_tx=[r[0] for r in txids],
        explanation=explanation,
        limitation=(
            "Terminal indicators are behavioral signals only. "
            "Device type and narration patterns do not prove criminality."
        ),
    )
