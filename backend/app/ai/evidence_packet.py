"""
app/ai/evidence_packet.py
--------------------------
Module D — Structured Investigation Evidence Packet builder.

This module extracts ONLY validated backend data and packages it into a
structured, bounded evidence packet for use by the AI generation layer.

CRITICAL RULES:
  - All data comes from the DuckDB database (single source of truth).
  - Never add, invent, or infer values not present in the database.
  - The AI model ONLY receives this packet — never raw SQL or full DB.
  - All account numbers, IFSCs, transaction IDs, amounts, timestamps must
    exist in the database before they can appear in the packet.
  - If a value is unavailable, return None — never substitute a placeholder.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import duckdb

from app.detection.hop_tracer import trace_victim
from app.detection.risk_scorer import score_account
from app.ingestion.query import get_account_profile, get_account_transactions

logger = logging.getLogger(__name__)

# Maximum transactions to include in evidence packet per layer
MAX_EVIDENCE_TXN = 50
MAX_PATHS = 20
MAX_HOPS = 4


@dataclass
class EvidenceTransaction:
    transaction_id: str
    sender_account: str
    receiver_account: str
    amount: str               # decimal string — exact
    ts: str                   # ISO timestamp
    payment_mode: str
    sender_ifsc: Optional[str]
    receiver_ifsc: Optional[str]
    device_type: Optional[str]
    ip_address: Optional[str]
    narration: Optional[str]  # UNTRUSTED — preserved verbatim, never executed
    hop_layer: Optional[int]  # 0=victim, 1–4=downstream layer


@dataclass
class EvidenceAccount:
    account_id: str
    account_node_id: Optional[int]
    layer: int                # 0=victim, 1–4
    incoming_count: int
    outgoing_count: int
    incoming_amount: str      # decimal string
    outgoing_amount: str      # decimal string
    unique_senders: int
    unique_receivers: int
    pass_through_ratio: Optional[float]
    risk_index: Optional[float]
    risk_level: Optional[str]
    first_seen: Optional[str]
    last_seen: Optional[str]
    detection_indicators: list[str]   # human-readable indicator labels
    supporting_tx_ids: list[str]      # bounded list of transaction IDs from DB


@dataclass
class EvidencePath:
    path_id: str
    depth: int
    account_sequence: list[str]   # ordered account IDs from victim → terminal
    transaction_ids: list[str]    # transaction IDs in order
    timestamps: list[str]         # corresponding timestamps
    amounts: list[str]            # corresponding amounts (decimal strings)
    total_amount_on_path: str     # sum of amounts — NOTE: not guaranteed = stolen funds


@dataclass
class InvestigationEvidencePacket:
    """
    Fully validated evidence packet for Module D AI generation.

    This is the ONLY input the AI model should receive.
    All values are sourced from the DuckDB database.
    """
    # Investigation metadata
    investigation_id: str
    generated_at: str
    victim_account: str
    observation_start: Optional[str]
    observation_end: Optional[str]
    max_hops_requested: int

    # Accounts involved
    accounts: list[EvidenceAccount]

    # Transactions (bounded)
    transactions: list[EvidenceTransaction]

    # Paths from victim
    paths: list[EvidencePath]

    # Calculated totals (deterministic — backend only)
    victim_total_outflow: str         # sum of victim's outgoing transactions on paths
    layer_totals: dict                # {layer_num: {"amount": str, "tx_count": int, "accounts": int}}
    total_transactions_in_evidence: int
    paths_truncated: bool

    # Whitelists for anti-hallucination validator
    allowed_account_ids: list[str]
    allowed_transaction_ids: list[str]
    allowed_ifsc_codes: list[str]
    allowed_amounts: list[str]        # decimal strings as they appear in evidence
    allowed_timestamps: list[str]

    # Limitations (always included)
    limitations: list[str]


def build_evidence_packet(
    conn: duckdb.DuckDBPyConnection,
    victim_account: str,
    *,
    max_hops: int = MAX_HOPS,
    max_paths: int = MAX_PATHS,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    investigation_id: Optional[str] = None,
) -> InvestigationEvidencePacket:
    """
    Build a validated, bounded investigation evidence packet.

    Steps:
    1. Validate victim account exists in DB.
    2. Run four-hop trace (Module B).
    3. For each reached account, extract risk (Module B).
    4. Collect bounded transaction evidence.
    5. Calculate deterministic totals.
    6. Build whitelists for anti-hallucination.
    7. Return structured packet — never includes data outside the DB.
    """
    victim_account = victim_account.upper().strip()
    inv_id = investigation_id or f"INV-{uuid.uuid4().hex[:8].upper()}"
    now_str = datetime.now(timezone.utc).isoformat()

    # ── 1. Validate victim exists ─────────────────────────────────────────── #
    victim_profile = get_account_profile(conn, victim_account)
    if victim_profile is None:
        raise ValueError(f"Victim account {victim_account} not found in database.")

    # ── 2. Four-hop trace ─────────────────────────────────────────────────── #
    logger.info("[%s] Running four-hop trace for victim %s", inv_id, victim_account)
    trace_result = trace_victim(
        conn, victim_account,
        max_hops=min(max_hops, MAX_HOPS),
        max_paths=min(max_paths, MAX_PATHS),
        time_window_hours=None,
    )

    # ── 3. Collect all reached accounts ──────────────────────────────────────#
    all_account_ids: set[str] = {victim_account}
    all_account_ids.update(trace_result.all_reached_accounts)

    # ── 4. Build account evidence with risk scores ────────────────────────── #
    evidence_accounts: list[EvidenceAccount] = []
    account_layers: dict[str, int] = {victim_account: 0}

    # Determine layer for each account from paths
    for path in trace_result.paths:
        for edge in path.edges:
            if edge.receiver_account not in account_layers:
                account_layers[edge.receiver_account] = edge.hop_number
            elif edge.hop_number < account_layers[edge.receiver_account]:
                account_layers[edge.receiver_account] = edge.hop_number

    for acct_id in sorted(all_account_ids):
        profile = get_account_profile(conn, acct_id)
        if profile is None:
            continue

        layer = account_layers.get(acct_id, 0)
        inc_amt = Decimal(str(profile.get("incoming_amount", "0") or "0"))
        out_amt = Decimal(str(profile.get("outgoing_amount", "0") or "0"))
        pt_ratio = None
        if inc_amt > 0:
            pt_ratio = round(float(out_amt / inc_amt), 4)

        # Get risk score (bounded — no expensive full scan)
        risk_idx = None
        risk_lvl = None
        indicators: list[str] = []
        try:
            risk = score_account(conn, acct_id,
                                 run_pass_through=False,  # skip expensive join for speed
                                 run_cycles=False)
            risk_idx = risk.risk_index
            risk_lvl = risk.risk_level
            indicators = [c.component for c in risk.components if c.triggered]
        except Exception as exc:
            logger.warning("Risk score failed for %s: %s", acct_id, exc)

        # Get supporting transaction IDs for this account (bounded)
        txn_data = get_account_transactions(conn, acct_id, limit=20)
        supporting_tx_ids = [t["transaction_id"] for t in txn_data["transactions"]]

        evidence_accounts.append(EvidenceAccount(
            account_id=acct_id,
            account_node_id=profile.get("account_node_id"),
            layer=layer,
            incoming_count=int(profile.get("incoming_count") or 0),
            outgoing_count=int(profile.get("outgoing_count") or 0),
            incoming_amount=str(inc_amt),
            outgoing_amount=str(out_amt),
            unique_senders=int(profile.get("unique_senders") or 0),
            unique_receivers=int(profile.get("unique_receivers") or 0),
            pass_through_ratio=pt_ratio,
            risk_index=risk_idx,
            risk_level=risk_lvl,
            first_seen=profile.get("first_seen"),
            last_seen=profile.get("last_seen"),
            detection_indicators=indicators,
            supporting_tx_ids=supporting_tx_ids,
        ))

    # ── 5. Collect transaction evidence (bounded) ──────────────────────────── #
    # Collect all transaction IDs from paths — strictly from DB trace
    path_tx_ids: set[str] = set()
    for path in trace_result.paths:
        for edge in path.edges:
            path_tx_ids.add(edge.transaction_id)

    evidence_transactions: list[EvidenceTransaction] = []
    edge_layer_map: dict[str, int] = {}
    for path in trace_result.paths:
        for edge in path.edges:
            edge_layer_map[edge.transaction_id] = edge.hop_number

    # Fetch actual transaction rows from DB for confirmed IDs (paranoid validation)
    if path_tx_ids:
        bounded_ids = list(path_tx_ids)[:MAX_EVIDENCE_TXN]
        ph = ", ".join("?" * len(bounded_ids))
        rows = conn.execute(
            f"""
            SELECT transaction_id, sender_account, receiver_account,
                   CAST(amount AS VARCHAR) AS amount,
                   ts::VARCHAR AS ts,
                   payment_mode, sender_ifsc, receiver_ifsc,
                   device_type, ip_address, narration
            FROM transactions
            WHERE transaction_id IN ({ph})
            ORDER BY ts ASC
            """,
            bounded_ids,
        ).fetchall()

        for row in rows:
            (tid, sender, recv, amt, ts, pm, s_ifsc, r_ifsc, dev, ip, narr) = row
            evidence_transactions.append(EvidenceTransaction(
                transaction_id=tid,
                sender_account=sender,
                receiver_account=recv,
                amount=str(Decimal(amt).quantize(Decimal("0.01"))),
                ts=ts,
                payment_mode=pm,
                sender_ifsc=s_ifsc,
                receiver_ifsc=r_ifsc,
                device_type=dev,
                ip_address=ip,
                narration=narr,   # preserved verbatim — AI treats as UNTRUSTED
                hop_layer=edge_layer_map.get(tid),
            ))

    # ── 6. Build evidence paths ────────────────────────────────────────────── #
    evidence_paths: list[EvidencePath] = []
    for hp in trace_result.paths[:MAX_PATHS]:
        if not hp.edges:
            continue
        acct_seq = [victim_account] + [e.receiver_account for e in hp.edges]
        tx_ids  = [e.transaction_id for e in hp.edges]
        ts_list = [e.ts for e in hp.edges]
        amt_list= [e.amount for e in hp.edges]
        total   = sum(Decimal(a) for a in amt_list)
        evidence_paths.append(EvidencePath(
            path_id=hp.path_id,
            depth=hp.depth,
            account_sequence=acct_seq,
            transaction_ids=tx_ids,
            timestamps=ts_list,
            amounts=amt_list,
            total_amount_on_path=str(total),
        ))

    # ── 7. Deterministic totals ────────────────────────────────────────────── #
    # Victim total outflow = sum of victim's OUTGOING transactions seen in evidence
    victim_outflow_row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) FROM transactions
        WHERE sender_account = ?
        """,
        [victim_account],
    ).fetchone()
    victim_total_outflow = str(Decimal(str(victim_outflow_row[0])).quantize(Decimal("0.01")))

    # Layer-wise totals
    layer_totals: dict = {}
    for acct in evidence_accounts:
        layer = acct.layer
        if layer not in layer_totals:
            layer_totals[layer] = {
                "amount_received": Decimal("0"),
                "amount_sent": Decimal("0"),
                "tx_count_in": 0,
                "tx_count_out": 0,
                "accounts": [],
            }
        layer_totals[layer]["amount_received"] += Decimal(acct.incoming_amount)
        layer_totals[layer]["amount_sent"]     += Decimal(acct.outgoing_amount)
        layer_totals[layer]["tx_count_in"]     += acct.incoming_count
        layer_totals[layer]["tx_count_out"]    += acct.outgoing_count
        layer_totals[layer]["accounts"].append(acct.account_id)

    # Serialise decimals to strings
    layer_totals_serial = {
        str(k): {
            "amount_received": str(v["amount_received"]),
            "amount_sent":     str(v["amount_sent"]),
            "tx_count_in":     v["tx_count_in"],
            "tx_count_out":    v["tx_count_out"],
            "account_count":   len(v["accounts"]),
            "accounts":        v["accounts"][:10],  # bounded
        }
        for k, v in layer_totals.items()
    }

    # ── 8. Build whitelists ────────────────────────────────────────────────── #
    allowed_accounts = sorted({a.account_id for a in evidence_accounts})
    allowed_txns     = sorted({t.transaction_id for t in evidence_transactions})
    allowed_ifscs    = sorted({
        code
        for t in evidence_transactions
        for code in [t.sender_ifsc, t.receiver_ifsc]
        if code
    })
    allowed_amounts  = sorted({t.amount for t in evidence_transactions})
    allowed_amounts.append(victim_total_outflow)
    # Also add layer totals
    for v in layer_totals_serial.values():
        allowed_amounts.append(v["amount_received"])
        allowed_amounts.append(v["amount_sent"])
    allowed_amounts = sorted(set(allowed_amounts))
    allowed_timestamps = sorted({t.ts for t in evidence_transactions})

    # Observation window from DB data
    obs_start = None
    obs_end   = None
    if evidence_transactions:
        ts_vals = sorted(t.ts for t in evidence_transactions)
        obs_start = ts_vals[0]
        obs_end   = ts_vals[-1]

    limitations = [
        "Evidence packet contains transaction connectivity data, not guaranteed unique rupee attribution.",
        "Pass-through ratios are aggregate behavioral signals, not exact fund tracking.",
        "Risk scores are investigative prioritization signals, not criminal findings.",
        "AI-generated content must be reviewed by authorized personnel before official use.",
        "This packet was generated on: " + now_str,
    ]

    logger.info(
        "[%s] Evidence packet built: %d accounts, %d transactions, %d paths, %d layers",
        inv_id, len(evidence_accounts), len(evidence_transactions),
        len(evidence_paths), len(layer_totals),
    )

    return InvestigationEvidencePacket(
        investigation_id=inv_id,
        generated_at=now_str,
        victim_account=victim_account,
        observation_start=obs_start,
        observation_end=obs_end,
        max_hops_requested=max_hops,
        accounts=evidence_accounts,
        transactions=evidence_transactions,
        paths=evidence_paths,
        victim_total_outflow=victim_total_outflow,
        layer_totals=layer_totals_serial,
        total_transactions_in_evidence=len(evidence_transactions),
        paths_truncated=trace_result.paths_truncated,
        allowed_account_ids=allowed_accounts,
        allowed_transaction_ids=allowed_txns,
        allowed_ifsc_codes=allowed_ifscs,
        allowed_amounts=allowed_amounts,
        allowed_timestamps=allowed_timestamps,
        limitations=limitations,
    )


def packet_to_dict(packet: InvestigationEvidencePacket) -> dict:
    """Serialise evidence packet to a plain dict (JSON-serialisable)."""
    import json
    from decimal import Decimal

    def _default(obj):
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    # asdict handles nested dataclasses; json round-trip ensures serialisability
    raw = asdict(packet)

    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_clean(i) for i in obj]
        elif isinstance(obj, Decimal):
            return str(obj)
        return obj

    return _clean(raw)
