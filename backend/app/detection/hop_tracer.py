"""
app/detection/hop_tracer.py
----------------------------
Four-hop downstream victim trace — Module B Stage G.
Adapted for Void-brain- Phase 1 transaction schema
(uses narration/ip_address column names instead of narration_raw/source_ip).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from decimal import Decimal
from typing import Optional

import duckdb

from app.ingestion.query import _validate_account_id
from app.schemas.detection import FourHopTrace, HopPath, HopNode, HopEdge

logger = logging.getLogger(__name__)

MAX_HOPS = 4
DEFAULT_MAX_PATHS = 50
DEFAULT_MAX_ACCOUNTS_PER_HOP = 500


def trace_victim(
    conn: duckdb.DuckDBPyConnection,
    victim_account: str,
    *,
    max_hops: int = MAX_HOPS,
    max_paths: int = DEFAULT_MAX_PATHS,
    time_window_hours: Optional[float] = None,
    max_accounts_per_hop: int = DEFAULT_MAX_ACCOUNTS_PER_HOP,
) -> FourHopTrace:
    """Trace fund flow from victim_account up to max_hops downstream hops."""
    t0 = time.perf_counter()
    victim_account = _validate_account_id(victim_account)

    victim_row = conn.execute(
        "SELECT account_node_id FROM accounts WHERE normalized_account_id = ?",
        [victim_account],
    ).fetchone()
    victim_node_id = int(victim_row[0]) if victim_row else None

    visited_globally: set[str] = {victim_account}
    frontier: list[tuple[list[HopEdge], str, Optional[str]]] = [
        ([], victim_account, None)
    ]
    completed_paths: list[list[HopEdge]] = []
    paths_truncated = False
    all_reached: set[str] = {victim_account}

    for hop_num in range(1, max_hops + 1):
        if not frontier:
            break

        next_frontier: list[tuple[list[HopEdge], str, Optional[str]]] = []
        sources = list({entry[1] for entry in frontier})
        if len(sources) > max_accounts_per_hop:
            sources = sources[:max_accounts_per_hop]
            paths_truncated = True

        placeholders = ", ".join("?" * len(sources))
        min_ts_map: dict[str, str] = {}
        for (edges, acct, min_ts) in frontier:
            if acct in sources and min_ts:
                existing = min_ts_map.get(acct)
                if existing is None or min_ts < existing:
                    min_ts_map[acct] = min_ts

        # Phase 1 schema: uses narration and ip_address column names
        hop_edges = conn.execute(
            f"""
            SELECT
                t.transaction_id, t.sender_account, t.receiver_account,
                a_s.account_node_id AS sender_node_id,
                a_r.account_node_id AS receiver_node_id,
                CAST(t.amount AS VARCHAR) AS amount,
                t.ts::VARCHAR AS ts,
                t.payment_mode, t.sender_ifsc, t.receiver_ifsc,
                t.narration        AS narration_raw,
                NULL               AS narration_markers,
                t.device_type,
                t.ip_address       AS source_ip
            FROM transactions t
            LEFT JOIN accounts a_s ON a_s.normalized_account_id = t.sender_account
            LEFT JOIN accounts a_r ON a_r.normalized_account_id = t.receiver_account
            WHERE t.sender_account IN ({placeholders})
            ORDER BY t.ts ASC, t.transaction_id ASC
            LIMIT {max_accounts_per_hop * 20}
            """,
            sources,
        ).fetchall()

        edges_by_sender: dict[str, list[tuple]] = defaultdict(list)
        for row in hop_edges:
            edges_by_sender[row[1]].append(row)

        for (path_so_far, current_acct, min_ts) in frontier:
            outgoing = edges_by_sender.get(current_acct, [])
            for row in outgoing:
                (txid, sender, receiver, s_nid, r_nid, amt, ts_str,
                 pm, s_ifsc, r_ifsc, narr, markers, device, ip) = row

                if min_ts and ts_str < min_ts:
                    continue
                if receiver in visited_globally:
                    continue

                edge = HopEdge(
                    transaction_id=txid,
                    sender_account=sender,
                    receiver_account=receiver,
                    sender_node_id=int(s_nid) if s_nid is not None else None,
                    receiver_node_id=int(r_nid) if r_nid is not None else None,
                    amount=amt,
                    ts=ts_str,
                    payment_mode=pm,
                    sender_ifsc=s_ifsc,
                    receiver_ifsc=r_ifsc,
                    narration_raw=narr,
                    narration_markers=markers,
                    device_type=device,
                    source_ip=ip,
                    hop_number=hop_num,
                )
                new_path = path_so_far + [edge]
                all_reached.add(receiver)

                if len(completed_paths) < max_paths:
                    completed_paths.append(new_path)

                if len(completed_paths) >= max_paths:
                    paths_truncated = True
                    break

                next_frontier.append((new_path, receiver, ts_str))

            if paths_truncated:
                break

        visited_globally.update(
            edge.receiver_account
            for path in completed_paths
            for edge in path
            if edge.hop_number == hop_num
        )
        frontier = next_frontier

        if not frontier or paths_truncated:
            break

    hop_paths: list[HopPath] = []
    for idx, edges in enumerate(completed_paths):
        if not edges:
            continue
        nodes: list[HopNode] = [HopNode(
            account_id=victim_account, account_node_id=victim_node_id, layer=0
        )]
        seen_nodes: set[str] = {victim_account}
        for edge in edges:
            if edge.receiver_account not in seen_nodes:
                nodes.append(HopNode(
                    account_id=edge.receiver_account,
                    account_node_id=edge.receiver_node_id,
                    layer=edge.hop_number,
                ))
                seen_nodes.add(edge.receiver_account)
        total_amt = sum(Decimal(e.amount) for e in edges if e.amount)
        hop_paths.append(HopPath(
            path_id=f"path_{idx}", nodes=nodes, edges=edges,
            depth=max(e.hop_number for e in edges),
            total_amount_traced=str(total_amt),
        ))

    duration = time.perf_counter() - t0
    max_depth = max((p.depth for p in hop_paths), default=0)
    logger.info("FourHopTrace | victim=%s paths=%d depth=%d duration=%.2fs",
                victim_account, len(hop_paths), max_depth, duration)

    return FourHopTrace(
        victim_account=victim_account,
        victim_node_id=victim_node_id,
        paths=hop_paths,
        all_reached_accounts=sorted(all_reached - {victim_account}),
        max_depth_reached=max_depth,
        total_paths_found=len(hop_paths),
        paths_truncated=paths_truncated,
        duration_seconds=round(duration, 3),
        time_window_hours=time_window_hours,
        detection_version="1.0",
    )
