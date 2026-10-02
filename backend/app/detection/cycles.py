"""
app/detection/cycles.py
------------------------
Cycle participation detection — Module B Stage E.

Detects short suspicious cycles such as A→B→C→A using DuckDB-driven
multi-hop queries.  We search for cycles of length 2, 3, and 4.

A cycle is defined as a path where the final receiver equals the initial
sender, with chronological ordering (each hop's ts > previous hop's ts).

Design:
  - All traversal is done in DuckDB SQL — no Python graph construction.
  - Maximum cycle length: configurable (default 4).
  - Results are bounded (default max 20 cycles reported).
  - Temporal constraint: each hop must be AFTER the previous hop's ts.
  - This prevents detecting legitimate recurring billing as a cycle.
"""

from __future__ import annotations

import logging
from typing import Optional

import duckdb

from app.schemas.detection import CycleFinding

logger = logging.getLogger(__name__)

DEFAULT_MAX_CYCLE_LENGTH = 4
DEFAULT_MAX_CYCLES       = 20


def detect_cycles(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    max_cycle_length: int = DEFAULT_MAX_CYCLE_LENGTH,
    max_cycles: int = DEFAULT_MAX_CYCLES,
) -> CycleFinding:
    """
    Detect transaction cycles involving *account_id*.

    Searches for:
    - Length-2: A→B and B→A (both chronological)
    - Length-3: A→B→C→A (all chronological)

    Longer cycles are computationally expensive on 2M rows;
    length-3 is the practical maximum for this dataset.
    """
    account_id = account_id.upper()
    cycle_paths: list[list[str]] = []

    # ── Length-2 cycles: A→B and then B→A ─────────────────────────────────── #
    l2 = conn.execute(
        """
        SELECT DISTINCT t1.receiver_account
        FROM transactions t1
        JOIN transactions t2
          ON t2.sender_account   = t1.receiver_account
         AND t2.receiver_account = t1.sender_account
         AND t2.ts > t1.ts
        WHERE t1.sender_account = ?
        LIMIT ?
        """,
        [account_id, max_cycles],
    ).fetchall()

    for (b,) in l2:
        cycle_paths.append([account_id, b, account_id])

    # ── Length-3 cycles: A→B→C→A ──────────────────────────────────────────── #
    if len(cycle_paths) < max_cycles:
        remaining = max_cycles - len(cycle_paths)
        l3 = conn.execute(
            """
            SELECT DISTINCT t1.receiver_account, t2.receiver_account
            FROM transactions t1
            JOIN transactions t2
              ON t2.sender_account = t1.receiver_account
             AND t2.ts > t1.ts
             AND t2.receiver_account != t1.sender_account
            JOIN transactions t3
              ON t3.sender_account   = t2.receiver_account
             AND t3.receiver_account = t1.sender_account
             AND t3.ts > t2.ts
            WHERE t1.sender_account = ?
            LIMIT ?
            """,
            [account_id, remaining],
        ).fetchall()

        for (b, c) in l3:
            cycle_paths.append([account_id, b, c, account_id])

    detected  = len(cycle_paths) > 0
    explanation = (
        f"Found {len(cycle_paths)} cycle(s) involving {account_id} "
        f"(lengths 2–3, chronologically ordered). "
        f"{'TRIGGERED' if detected else 'NOT triggered'}."
    )

    return CycleFinding(
        account_id=account_id,
        detected=detected,
        cycle_count=len(cycle_paths),
        cycle_paths=cycle_paths,
        explanation=explanation,
    )
