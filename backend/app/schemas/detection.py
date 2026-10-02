"""
app/schemas/detection.py
------------------------
Pydantic schemas for Module B — Mule Ring Detection.

Design rules
------------
- Every monetary field is str (decimal string) — no float in financial data.
- All transaction_ids, account_ids are strings, never integers.
- All timestamps are ISO-8601 strings.
- Risk scores are float in [0.0, 100.0] — not probabilities, not classifications.
- ML probability (if present) is float in [0.0, 1.0], kept separate from risk_index.
- Evidence fields reference actual DB values only — never fabricated.
- All detection results carry a detection_version string for reproducibility.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# ── Supporting evidence types ──────────────────────────────────────────────── #

class SupportingTransaction(BaseModel):
    """A transaction that directly supports a detection finding."""
    transaction_id: str
    sender_account: str
    receiver_account: str
    amount: str                # decimal string
    ts: str                    # ISO timestamp
    payment_mode: str
    narration_raw: Optional[str] = None
    narration_markers: Optional[str] = None  # JSON string
    device_type: Optional[str] = None
    source_ip: Optional[str] = None


class AccountFeatures(BaseModel):
    """
    Account-level feature vector computed from DuckDB.

    All amount fields are decimal strings.
    Velocity and ratio fields are floats.
    All fields are nullable — a feature may be undefined if there is
    insufficient data (e.g. no outgoing transactions).
    """
    account_id: str
    account_node_id: Optional[int] = None

    # Basic counts (from accounts table)
    incoming_count: int = 0
    outgoing_count: int = 0
    total_count: int = 0
    unique_senders: int = 0
    unique_receivers: int = 0

    # Amount aggregates (decimal strings)
    incoming_amount: str = "0"
    outgoing_amount: str = "0"
    net_amount: str = "0"           # incoming - outgoing

    # Amount statistics (float — computed from full transaction set)
    avg_incoming_amount: Optional[float] = None
    avg_outgoing_amount: Optional[float] = None
    median_incoming_amount: Optional[float] = None
    median_outgoing_amount: Optional[float] = None
    amount_variance_outgoing: Optional[float] = None

    # Ratios
    in_out_count_ratio: Optional[float] = None    # incoming / outgoing
    pass_through_ratio: Optional[float] = None    # outgoing_amount / incoming_amount

    # Temporal
    activity_duration_hours: Optional[float] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None

    # Velocity (transactions per hour, normalised to observation window)
    tx_velocity_per_hour: Optional[float] = None

    # Rapid dispersal features
    pct_dispersed_within_3min: Optional[float] = None   # % of incoming value dispersed ≤3min later
    pct_dispersed_within_15min: Optional[float] = None  # % of incoming value dispersed ≤15min later

    # Graph degree
    in_degree: int = 0    # unique_senders
    out_degree: int = 0   # unique_receivers

    # Counterparty concentration (Herfindahl-like)
    sender_concentration: Optional[float] = None    # 0=many equal senders, 1=single sender
    receiver_concentration: Optional[float] = None

    # Device / payment anomaly
    dominant_device_type: Optional[str] = None
    device_type_count: int = 0           # distinct device types
    linux_script_tx_count: int = 0
    web_emulator_tx_count: int = 0
    unusual_device_tx_count: int = 0     # Linux_Script + Web_Emulator

    dominant_payment_mode: Optional[str] = None
    payment_mode_count: int = 0

    # IP diversity
    unique_ip_count: int = 0
    public_ip_count: int = 0
    private_ip_count: int = 0
    invalid_ip_count: int = 0

    # Narration markers
    narration_marker_categories: list[str] = Field(default_factory=list)
    has_crypto_marker: bool = False
    has_p2p_marker: bool = False
    has_wallet_marker: bool = False
    has_automation_marker: bool = False
    has_earning_marker: bool = False

    # Cycle participation
    in_cycle: bool = False
    cycle_count: int = 0

    # Downstream position (set by hop tracer)
    max_downstream_depth: Optional[int] = None

    # Metadata
    feature_version: str = "1.0"
    computed_at: Optional[str] = None


# ── Detection findings ─────────────────────────────────────────────────────── #

class PassThroughFinding(BaseModel):
    """Result of high-velocity pass-through detection for one account."""
    account_id: str
    detected: bool
    pass_through_ratio: Optional[float] = None      # outgoing / incoming (within window)
    pct_dispersed_within_window: Optional[float] = None
    window_minutes: int = 15
    incoming_amount_in_window: str = "0"            # decimal string
    outgoing_amount_in_window: str = "0"
    outgoing_tx_count: int = 0
    unique_receivers_in_window: int = 0
    supporting_incoming_tx: list[str] = Field(default_factory=list)   # transaction_ids
    supporting_outgoing_tx: list[str] = Field(default_factory=list)
    explanation: str = ""
    limitation: str = ""    # explicit note if exact attribution is impossible


class FanInFinding(BaseModel):
    """Fan-in (collector) detection result."""
    account_id: str
    detected: bool
    unique_senders: int = 0
    in_degree: int = 0
    incoming_tx_count: int = 0
    incoming_amount: str = "0"
    sender_concentration: Optional[float] = None    # 0=many equal, 1=single
    peak_hourly_incoming: Optional[float] = None    # max transactions in any 1-hour window
    supporting_tx: list[str] = Field(default_factory=list)
    explanation: str = ""


class FanOutFinding(BaseModel):
    """Fan-out (distributor) detection result."""
    account_id: str
    detected: bool
    unique_receivers: int = 0
    out_degree: int = 0
    outgoing_tx_count: int = 0
    outgoing_amount: str = "0"
    receiver_concentration: Optional[float] = None
    splitting_detected: bool = False     # many small equal-ish outgoing amounts
    supporting_tx: list[str] = Field(default_factory=list)
    explanation: str = ""


class TerminalIndicatorFinding(BaseModel):
    """Terminal / cash-out indicator signals for one account."""
    account_id: str
    detected: bool
    linux_script_tx_count: int = 0
    web_emulator_tx_count: int = 0
    crypto_narration_count: int = 0
    p2p_narration_count: int = 0
    wallet_narration_count: int = 0
    unusual_device_ratio: Optional[float] = None  # unusual_device_tx / total_tx
    downstream_position_score: float = 0.0  # 0=upstream 1=very downstream
    supporting_tx: list[str] = Field(default_factory=list)
    explanation: str = ""
    limitation: str = (
        "Terminal indicators are behavioral signals only. "
        "Device type and narration patterns do not prove criminality."
    )


class CycleFinding(BaseModel):
    """Cycle participation detection."""
    account_id: str
    detected: bool
    cycle_count: int = 0
    cycle_paths: list[list[str]] = Field(default_factory=list)  # each path = list of account_ids
    explanation: str = ""


# ── Risk score ─────────────────────────────────────────────────────────────── #

class RiskComponent(BaseModel):
    """One component of the Mule Risk Index."""
    component: str
    score: float        # contribution to total (0–component_max)
    weight: float       # configured weight (e.g. 25.0 for pass-through)
    triggered: bool
    reason: str
    evidence_tx_ids: list[str] = Field(default_factory=list)


class MuleRiskResult(BaseModel):
    """
    Mule Risk Index (0–100) for one account.

    The risk_index is a deterministic engineering score, NOT an ML probability.
    It is reproducible given the same data and configuration.
    """
    account_id: str
    risk_index: float = Field(ge=0.0, le=100.0)
    risk_level: str     # LOW | MEDIUM | HIGH
    components: list[RiskComponent] = Field(default_factory=list)
    pass_through: Optional[PassThroughFinding] = None
    fan_in: Optional[FanInFinding] = None
    fan_out: Optional[FanOutFinding] = None
    terminal: Optional[TerminalIndicatorFinding] = None
    cycles: Optional[CycleFinding] = None
    ml_probability: Optional[float] = None     # None if model unavailable
    ml_model_version: Optional[str] = None
    detection_version: str = "1.0"
    explanation: str = ""
    limitations: list[str] = Field(default_factory=list)


# ── Four-hop trace ─────────────────────────────────────────────────────────── #

class HopEdge(BaseModel):
    """A single transaction edge in the four-hop trace."""
    transaction_id: str
    sender_account: str
    receiver_account: str
    sender_node_id: Optional[int] = None
    receiver_node_id: Optional[int] = None
    amount: str             # decimal string
    ts: str                 # ISO timestamp
    payment_mode: str
    sender_ifsc: Optional[str] = None
    receiver_ifsc: Optional[str] = None
    narration_raw: Optional[str] = None
    narration_markers: Optional[str] = None     # JSON string
    device_type: Optional[str] = None
    source_ip: Optional[str] = None
    hop_number: int         # 1–4


class HopNode(BaseModel):
    """An account node in the four-hop trace."""
    account_id: str
    account_node_id: Optional[int] = None
    layer: int              # 0=victim, 1–4
    risk_index: Optional[float] = None
    risk_level: Optional[str] = None
    incoming_count: Optional[int] = None
    outgoing_count: Optional[int] = None


class HopPath(BaseModel):
    """One path from victim through up to 4 hops."""
    path_id: str            # e.g. "path_0"
    nodes: list[HopNode]
    edges: list[HopEdge]
    depth: int              # number of hops
    total_amount_traced: str = "0"   # decimal string — sum of edge amounts on this path


class FourHopTrace(BaseModel):
    """
    Full four-hop downstream victim trace result.

    Note: paths prove transaction connectivity, not exact physical rupee flow.
    Funds may be mixed at intermediate accounts.
    """
    victim_account: str
    victim_node_id: Optional[int] = None
    paths: list[HopPath] = Field(default_factory=list)
    all_reached_accounts: list[str] = Field(default_factory=list)
    max_depth_reached: int = 0
    total_paths_found: int = 0
    paths_truncated: bool = False       # True if result_limit was hit
    duration_seconds: Optional[float] = None
    time_window_hours: Optional[float] = None
    detection_version: str = "1.0"
    limitation: str = (
        "Paths show transaction connectivity. "
        "Exact rupee tracing is not possible when funds are mixed at intermediate accounts."
    )


# ── API request/response schemas ──────────────────────────────────────────── #

class AccountRiskRequest(BaseModel):
    account_id: str

    @field_validator("account_id")
    @classmethod
    def _validate(cls, v: str) -> str:
        import re
        if not v or not re.match(r"^[A-Za-z0-9]{1,32}$", v):
            raise ValueError("account_id must be 1–32 alphanumeric characters")
        return v.upper()


class VictimTraceRequest(BaseModel):
    victim_account: str
    max_hops: int = Field(default=4, ge=1, le=4)
    time_window_hours: Optional[float] = Field(default=None, ge=0.1, le=720.0)
    max_paths: int = Field(default=50, ge=1, le=200)

    @field_validator("victim_account")
    @classmethod
    def _validate(cls, v: str) -> str:
        import re
        if not v or not re.match(r"^[A-Za-z0-9]{1,32}$", v):
            raise ValueError("victim_account must be 1–32 alphanumeric characters")
        return v.upper()


class SuspiciousAccountsRequest(BaseModel):
    min_risk_index: float = Field(default=50.0, ge=0.0, le=100.0)
    limit: int = Field(default=100, ge=1, le=1000)
    include_features: bool = False


class ModelEvaluationResponse(BaseModel):
    model_available: bool
    model_version: Optional[str] = None
    training_date: Optional[str] = None
    n_train: Optional[int] = None
    n_test: Optional[int] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
    pr_auc: Optional[float] = None
    feature_importances: Optional[dict[str, float]] = None
    blocker: Optional[str] = None   # set when model could not be trained
    message: str = ""
