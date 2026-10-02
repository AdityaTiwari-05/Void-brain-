"""
app/detection/risk_scorer.py
-----------------------------
Mule Risk Index (0–100) combinator — Module B Stage F.

Combines all detection signals into a single explainable score.

Default weights (sum = 100):
  pass_through       25
  fan_in             15
  fan_out            15
  temporal_layering  15
  terminal           10
  device_anomaly      5
  ip_anomaly          5
  narration_markers   5
  cycle               5

Rules:
  - Each component contributes [0, weight] based on signal strength.
  - Components are independent — no evidence can contribute twice.
  - The final score is clamped to [0, 100].
  - ML probability (if available) is reported separately; it does NOT
    modify the rule-based risk_index.

Risk levels:
  LOW    [0, 30)
  MEDIUM [30, 60)
  HIGH   [60, 100]
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import duckdb

from app.schemas.detection import (
    MuleRiskResult, RiskComponent, AccountFeatures,
    PassThroughFinding, FanInFinding, FanOutFinding,
    TerminalIndicatorFinding, CycleFinding,
)
from app.detection.features import extract_account_features
from app.detection.pass_through import detect_pass_through
from app.detection.fan_in import detect_fan_in
from app.detection.fan_out import detect_fan_out
from app.detection.terminal import detect_terminal_indicators
from app.detection.cycles import detect_cycles

logger = logging.getLogger(__name__)

DETECTION_VERSION = "1.0"

# ── Default weights ────────────────────────────────────────────────────────── #

DEFAULT_WEIGHTS: dict[str, float] = {
    "pass_through":      25.0,
    "fan_in":            15.0,
    "fan_out":           15.0,
    "temporal_layering": 15.0,
    "terminal":          10.0,
    "device_anomaly":     5.0,
    "ip_anomaly":         5.0,
    "narration_markers":  5.0,
    "cycle":              5.0,
}

RISK_LOW_MAX    = 30.0
RISK_MEDIUM_MAX = 60.0


def _risk_level(score: float) -> str:
    if score < RISK_LOW_MAX:
        return "LOW"
    elif score < RISK_MEDIUM_MAX:
        return "MEDIUM"
    return "HIGH"


# ── Scoring sub-functions ──────────────────────────────────────────────────── #

def _score_pass_through(
    finding: PassThroughFinding, weight: float
) -> RiskComponent:
    if not finding.detected:
        return RiskComponent(
            component="pass_through", score=0.0, weight=weight,
            triggered=False, reason="Pass-through ratio below threshold.",
        )
    # Scale by ratio: 0.90→0.5*weight, 1.00→full weight
    ratio = finding.pass_through_ratio or 0.0
    scale = min(1.0, max(0.0, (ratio - 0.90) / 0.10 * 0.5 + 0.5))
    score = weight * scale
    return RiskComponent(
        component="pass_through",
        score=round(score, 2),
        weight=weight,
        triggered=True,
        reason=(
            f"Pass-through ratio {ratio*100:.1f}% within {finding.window_minutes}min "
            f"to {finding.unique_receivers_in_window} receivers."
        ),
        evidence_tx_ids=finding.supporting_outgoing_tx[:10],
    )


def _score_fan_in(finding: FanInFinding, weight: float) -> RiskComponent:
    if not finding.detected:
        return RiskComponent(
            component="fan_in", score=0.0, weight=weight,
            triggered=False, reason="Fan-in below threshold.",
        )
    return RiskComponent(
        component="fan_in",
        score=weight,
        weight=weight,
        triggered=True,
        reason=f"Fan-in: {finding.unique_senders} unique senders "
               f"({finding.incoming_tx_count} total incoming txns).",
        evidence_tx_ids=finding.supporting_tx[:10],
    )


def _score_fan_out(finding: FanOutFinding, weight: float) -> RiskComponent:
    if not finding.detected:
        return RiskComponent(
            component="fan_out", score=0.0, weight=weight,
            triggered=False, reason="Fan-out below threshold.",
        )
    score = weight if not finding.splitting_detected else weight
    return RiskComponent(
        component="fan_out",
        score=round(score, 2),
        weight=weight,
        triggered=True,
        reason=(
            f"Fan-out: {finding.unique_receivers} unique receivers. "
            f"Splitting: {'YES' if finding.splitting_detected else 'NO'}."
        ),
        evidence_tx_ids=finding.supporting_tx[:10],
    )


def _score_temporal_layering(
    features: AccountFeatures, weight: float
) -> RiskComponent:
    """
    Temporal layering score: accounts with very high velocity AND high
    pass-through are more likely to be layering funds quickly.
    Uses features already computed — no extra DB query.
    """
    score = 0.0
    reasons = []

    # High velocity (above 2 tx/hour for a 15-day window)
    if features.tx_velocity_per_hour and features.tx_velocity_per_hour > 2.0:
        score += weight * 0.4
        reasons.append(f"velocity={features.tx_velocity_per_hour:.1f} tx/hr")

    # High dispersal within 15min
    if features.pct_dispersed_within_15min and features.pct_dispersed_within_15min > 0.5:
        score += weight * 0.6
        reasons.append(f"15min_dispersal={features.pct_dispersed_within_15min*100:.0f}%")

    score = min(score, weight)
    triggered = score > 0.0
    return RiskComponent(
        component="temporal_layering",
        score=round(score, 2),
        weight=weight,
        triggered=triggered,
        reason="; ".join(reasons) if reasons else "No temporal layering signals.",
    )


def _score_terminal(
    finding: TerminalIndicatorFinding, weight: float
) -> RiskComponent:
    if not finding.detected:
        return RiskComponent(
            component="terminal", score=0.0, weight=weight,
            triggered=False, reason="No terminal indicators detected.",
        )
    return RiskComponent(
        component="terminal",
        score=weight,
        weight=weight,
        triggered=True,
        reason=finding.explanation[:200],
        evidence_tx_ids=finding.supporting_tx[:10],
    )


def _score_device_anomaly(
    features: AccountFeatures, weight: float
) -> RiskComponent:
    if features.unusual_device_tx_count == 0:
        return RiskComponent(
            component="device_anomaly", score=0.0, weight=weight,
            triggered=False, reason="No unusual device types detected.",
        )
    total = features.total_count or 1
    ratio = features.unusual_device_tx_count / total
    score = weight * min(1.0, ratio * 10)   # even 10% = full score
    return RiskComponent(
        component="device_anomaly",
        score=round(score, 2),
        weight=weight,
        triggered=True,
        reason=(
            f"Linux_Script: {features.linux_script_tx_count}, "
            f"Web_Emulator: {features.web_emulator_tx_count} "
            f"({ratio*100:.1f}% of transactions)."
        ),
    )


def _score_ip_anomaly(features: AccountFeatures, weight: float) -> RiskComponent:
    """
    IP anomaly scoring.

    In this dataset ALL IPs are PUBLIC_IP — ip_anomaly_flags carries no
    discriminative signal.  We score purely on IP diversity (many distinct IPs
    across transactions) as a proxy for account sharing or bot activity.
    A single account using >50 distinct IPs is unusual.
    """
    if features.unique_ip_count < 50:
        return RiskComponent(
            component="ip_anomaly", score=0.0, weight=weight,
            triggered=False,
            reason=f"IP diversity: {features.unique_ip_count} unique IPs — within normal range.",
        )
    # Scale: 50 IPs → 0 score, 200+ IPs → full score
    scale = min(1.0, (features.unique_ip_count - 50) / 150)
    return RiskComponent(
        component="ip_anomaly",
        score=round(weight * scale, 2),
        weight=weight,
        triggered=True,
        reason=f"High IP diversity: {features.unique_ip_count} unique IPs.",
    )


def _score_narration_markers(
    features: AccountFeatures, weight: float
) -> RiskComponent:
    high_risk_cats = {"CRYPTO", "P2P", "AUTOMATION", "EARNING"}
    triggered_cats = high_risk_cats & set(features.narration_marker_categories)
    if not triggered_cats:
        return RiskComponent(
            component="narration_markers", score=0.0, weight=weight,
            triggered=False, reason="No high-risk narration markers.",
        )
    score = weight * min(1.0, len(triggered_cats) / 2)
    return RiskComponent(
        component="narration_markers",
        score=round(score, 2),
        weight=weight,
        triggered=True,
        reason=f"High-risk narration categories: {sorted(triggered_cats)}.",
    )


def _score_cycle(finding: CycleFinding, weight: float) -> RiskComponent:
    if not finding.detected:
        return RiskComponent(
            component="cycle", score=0.0, weight=weight,
            triggered=False, reason="No transaction cycles detected.",
        )
    return RiskComponent(
        component="cycle",
        score=weight,
        weight=weight,
        triggered=True,
        reason=f"{finding.cycle_count} transaction cycle(s) detected.",
    )


# ── Main scorer ────────────────────────────────────────────────────────────── #

def score_account(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    weights: Optional[dict[str, float]] = None,
    ml_probability: Optional[float] = None,
    ml_model_version: Optional[str] = None,
    run_pass_through: bool = True,
    run_cycles: bool = True,
) -> MuleRiskResult:
    """
    Compute the Mule Risk Index for *account_id*.

    Parameters
    ----------
    conn           : read-only DuckDB connection
    account_id     : account to score
    weights        : override default component weights (must sum <= 100)
    ml_probability : if a model has scored this account, pass its output here
    run_pass_through : set False to skip the expensive dispersal join
    run_cycles     : set False to skip cycle detection (SQL join)

    Returns MuleRiskResult with all component scores and evidence.
    """
    t0 = time.perf_counter()
    w  = weights or DEFAULT_WEIGHTS
    account_id = account_id.upper()

    # 1. Base features
    features = extract_account_features(conn, account_id)
    if features is None:
        return MuleRiskResult(
            account_id=account_id,
            risk_index=0.0,
            risk_level="LOW",
            detection_version=DETECTION_VERSION,
            explanation="Account not found in analytics store.",
        )

    # 2. Run detectors
    pt   = detect_pass_through(conn, account_id) if run_pass_through else PassThroughFinding(account_id=account_id, detected=False, explanation="Skipped.")
    fi   = detect_fan_in(conn, account_id)
    fo   = detect_fan_out(conn, account_id)
    term = detect_terminal_indicators(conn, account_id)
    cyc  = detect_cycles(conn, account_id) if run_cycles else CycleFinding(account_id=account_id, detected=False, explanation="Skipped.")

    # 3. Score each component
    components = [
        _score_pass_through(pt, w.get("pass_through", 25.0)),
        _score_fan_in(fi, w.get("fan_in", 15.0)),
        _score_fan_out(fo, w.get("fan_out", 15.0)),
        _score_temporal_layering(features, w.get("temporal_layering", 15.0)),
        _score_terminal(term, w.get("terminal", 10.0)),
        _score_device_anomaly(features, w.get("device_anomaly", 5.0)),
        _score_ip_anomaly(features, w.get("ip_anomaly", 5.0)),
        _score_narration_markers(features, w.get("narration_markers", 5.0)),
        _score_cycle(cyc, w.get("cycle", 5.0)),
    ]

    total_score = min(100.0, sum(c.score for c in components))
    level       = _risk_level(total_score)

    triggered = [c.component for c in components if c.triggered]
    explanation = (
        f"Risk index {total_score:.1f}/100 ({level}). "
        f"Triggered: {triggered if triggered else 'none'}. "
        f"Computed in {(time.perf_counter()-t0)*1000:.0f}ms."
    )

    limitations = [
        "Risk index is a deterministic engineering score, not an ML probability.",
        "Pass-through ratio is an aggregate behavioral indicator; exact rupee attribution is not claimed.",
        "Terminal indicators (device type, narration) are signals for investigation, not proof of criminality.",
    ]
    if ml_probability is None:
        limitations.append("ML model not available; score is rule-based only.")

    return MuleRiskResult(
        account_id=account_id,
        risk_index=round(total_score, 2),
        risk_level=level,
        components=components,
        pass_through=pt,
        fan_in=fi,
        fan_out=fo,
        terminal=term,
        cycles=cyc,
        ml_probability=ml_probability,
        ml_model_version=ml_model_version,
        detection_version=DETECTION_VERSION,
        explanation=explanation,
        limitations=limitations,
    )
