"""Base classes for the pluggable risk-rule engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ...ai.schemas import ExecutionPlan
from ...core.config import AppConfig

CLOSE_ACTIONS = frozenset({"close_long", "close_short", "sell_spot", "hold"})
OPEN_ACTIONS = frozenset({"open_long", "open_short", "buy_spot"})


@dataclass
class RuleContext:
    """Immutable context passed to every rule."""

    plan: ExecutionPlan
    account_snapshot: dict
    positions: list[dict]
    market_snapshot: dict | None
    portfolio_snapshot: dict | None
    portfolio_budget: dict | None
    config: AppConfig
    research_decision: dict | None = None
    recent_trade_history: Optional[list[dict]] = None
    event_snapshot: dict | None = None


@dataclass
class RuleResult:
    """Mutable accumulator modified by each rule in sequence."""

    adjusted_size_pct: float
    adjusted_leverage: float
    adjusted_action: str
    stop_loss_pct: float
    take_profit_pct: float
    violated_rules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quality_score: float | None = None
    quality_grade: str = ""
    gating_profile: str = ""


class BaseRule(ABC):
    """A single, self-contained risk rule."""

    name: str = "unnamed_rule"

    @abstractmethod
    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        """Return True if this rule should run given current context."""
        ...

    @abstractmethod
    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        """Mutate *result* in place (add warnings, adjust values, etc.)."""
        ...
