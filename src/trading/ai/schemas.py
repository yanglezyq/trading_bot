"""Structured data models for the AI trading pipeline."""

from dataclasses import asdict, dataclass, field
from typing import Optional

_VALID_STANCES = frozenset({"long", "neutral", "short"})
_VALID_MARKETS = frozenset({"spot", "futures", "none"})
_VALID_ACTIONS = frozenset(
    {"hold", "buy_spot", "sell_spot", "open_long", "close_long", "open_short", "close_short"}
)
_VALID_STATUSES = frozenset(
    {
        "executed",
        "dry_run",
        "manual_review_required",
        "auto_execute_blocked",
        "blocked_by_risk",
        "not_executed_missing_api",
        "execution_failed",
        "hold",
        "blocked_duplicate",   # fix 4: dedup protection
    }
)
_VALID_SL_TP_STATUSES = frozenset({"placed", "failed", "skipped"})


@dataclass
class ResearchDecision:
    """AI-generated market research conclusion."""

    symbol: str
    stance: str  # "long" | "neutral" | "short"
    confidence: float  # 0.0 – 1.0
    thesis: str
    market_structure: str
    evidence: list[str]
    catalysts: list[str]
    risks: list[str]
    invalidation: str
    time_horizon: str
    preferred_market: str  # "spot" | "futures" | "none"
    supporting_model_count: int = 1
    consensus_strength: float = 1.0
    disagreement_note: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "ResearchDecision":
        stance = str(d.get("stance", "neutral")).lower()
        if stance not in _VALID_STANCES:
            raise ValueError(f"Invalid stance: {stance!r}, must be one of {sorted(_VALID_STANCES)}")
        confidence = float(d.get("confidence", 0.5))
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got: {confidence}")
        preferred_market = str(d.get("preferred_market", "futures")).lower()
        if preferred_market not in _VALID_MARKETS:
            preferred_market = "futures"
        symbol = str(d.get("symbol", "")).upper() or "UNKNOWN"
        return cls(
            symbol=symbol,
            stance=stance,
            confidence=confidence,
            thesis=str(d.get("thesis", "")),
            market_structure=str(d.get("market_structure", "")),
            evidence=list(d.get("evidence", [])),
            catalysts=list(d.get("catalysts", [])),
            risks=list(d.get("risks", [])),
            invalidation=str(d.get("invalidation", "")),
            time_horizon=str(d.get("time_horizon", "")),
            preferred_market=preferred_market,
            supporting_model_count=int(d.get("supporting_model_count", 1) or 1),
            consensus_strength=float(d.get("consensus_strength", 1.0) or 1.0),
            disagreement_note=str(d.get("disagreement_note", "")),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionPlan:
    """AI-generated trade execution plan."""

    action: str  # one of _VALID_ACTIONS
    size_pct: float  # 0–100, percentage of account margin to use
    leverage: int  # 1–125
    entry_idea: str
    stop_loss_pct: float  # percentage, e.g. 5.0 means 5%
    take_profit_pct: float  # percentage, e.g. 15.0 means 15%
    rationale: str
    entry_style: str = ""
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    trigger_price: Optional[float] = None
    invalidation_price: Optional[float] = None
    thesis_window_hours: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionPlan":
        action = str(d.get("action", "hold")).lower()
        if action not in _VALID_ACTIONS:
            raise ValueError(f"Invalid action: {action!r}, must be one of {sorted(_VALID_ACTIONS)}")
        size_pct = float(d.get("size_pct", 0.0))
        if not (0.0 <= size_pct <= 100.0):
            raise ValueError(f"size_pct must be in [0, 100], got: {size_pct}")
        leverage = int(d.get("leverage", 1))
        if leverage < 1:
            leverage = 1
        stop_loss_pct = float(d.get("stop_loss_pct", 0.0))
        take_profit_pct = float(d.get("take_profit_pct", 0.0))
        return cls(
            action=action,
            size_pct=size_pct,
            leverage=leverage,
            entry_idea=str(d.get("entry_idea", "")),
            entry_style=str(d.get("entry_style", "")),
            entry_zone_low=float(d["entry_zone_low"]) if d.get("entry_zone_low") is not None else None,
            entry_zone_high=float(d["entry_zone_high"]) if d.get("entry_zone_high") is not None else None,
            trigger_price=float(d["trigger_price"]) if d.get("trigger_price") is not None else None,
            invalidation_price=float(d["invalidation_price"]) if d.get("invalidation_price") is not None else None,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            thesis_window_hours=int(d["thesis_window_hours"]) if d.get("thesis_window_hours") is not None else None,
            rationale=str(d.get("rationale", "")),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskDecision:
    """Deterministic risk gate evaluation output."""

    approved: bool
    adjusted_action: Optional[str]
    adjusted_size_pct: Optional[float]
    adjusted_leverage: Optional[int]
    violated_rules: list[str]
    warnings: list[str]
    rationale: str
    setup_quality_score: Optional[float] = None
    setup_quality_grade: str = ""
    gating_profile: str = ""
    edge_policy_label: str = ""
    edge_policy_reasons: list[str] = field(default_factory=list)
    edge_policy_expectancy_pnl_pct: Optional[float] = None
    opportunity_score: Optional[float] = None
    opportunity_bucket: str = ""
    opportunity_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionResult:
    """Final order execution outcome."""

    executed: bool
    status: str  # one of _VALID_STATUSES
    symbol: str
    final_action: str
    final_size_pct: float
    message: str
    order_ids: list[str] = field(default_factory=list)
    exchange: str = ""
    final_leverage: Optional[int] = None
    raw_response_json: Optional[dict] = None
    manual_order_details: Optional[dict] = None
    execution_reason: Optional[str] = None
    # fix 5: explicit SL/TP attachment tracking
    sl_status: str = "skipped"     # "placed" | "failed" | "skipped"
    tp_status: str = "skipped"     # "placed" | "failed" | "skipped"
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
