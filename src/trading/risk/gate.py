"""Deterministic risk gate — applies rule-based checks to an ExecutionPlan."""

from __future__ import annotations

from ..ai.schemas import ExecutionPlan, RiskDecision
from ..core.config import AppConfig
from ..exchange.market_data import infer_narrative_tag  # noqa: F401 — re-exported for downstream
from .rules import DEFAULT_RULES, BaseRule, RuleContext, RuleResult

_CLOSE_ACTIONS = frozenset({"close_long", "close_short", "sell_spot", "hold"})
_OPEN_ACTIONS = frozenset({"open_long", "open_short", "buy_spot"})


class RiskGate:
    """Apply deterministic risk rules to an ExecutionPlan and return a RiskDecision."""

    def __init__(self, config: AppConfig, rules: list[BaseRule] | None = None):
        self.config = config
        self.rules = rules if rules is not None else DEFAULT_RULES

    def evaluate(
        self,
        plan: ExecutionPlan,
        account_snapshot: dict,
        positions: list[dict],
        market_snapshot: dict | None = None,
        portfolio_snapshot: dict | None = None,
        portfolio_budget: dict | None = None,
        research_decision: dict | None = None,
        recent_trade_history: list[dict] | None = None,
        event_snapshot: dict | None = None,
    ) -> RiskDecision:
        """Return a RiskDecision after applying all configured risk rules."""
        ctx = RuleContext(
            plan=plan,
            account_snapshot=account_snapshot,
            positions=positions,
            market_snapshot=market_snapshot,
            portfolio_snapshot=portfolio_snapshot,
            portfolio_budget=portfolio_budget,
            config=self.config,
            research_decision=research_decision,
            recent_trade_history=recent_trade_history,
            event_snapshot=event_snapshot,
        )
        result = RuleResult(
            adjusted_size_pct=plan.size_pct,
            adjusted_leverage=plan.leverage,
            adjusted_action=plan.action,
            stop_loss_pct=plan.stop_loss_pct,
            take_profit_pct=plan.take_profit_pct,
        )

        for rule in self.rules:
            if rule.applies(ctx, result):
                rule.evaluate(ctx, result)

        return self._build_decision(plan, result)

    @staticmethod
    def _build_decision(plan: ExecutionPlan, result: RuleResult) -> RiskDecision:
        approved = len(result.violated_rules) == 0

        parts: list[str] = []
        if approved:
            parts.append("All risk checks passed.")
        else:
            parts.append(f"REJECTED: {'; '.join(result.violated_rules)}")
        if result.warnings:
            parts.append(f"Warnings: {'; '.join(result.warnings)}")
        if result.stop_loss_pct != plan.stop_loss_pct:
            parts.append(f"stop_loss_pct set to {result.stop_loss_pct:.1f}%.")
        if result.take_profit_pct != plan.take_profit_pct:
            parts.append(f"take_profit_pct set to {result.take_profit_pct:.1f}%.")
        if result.adjusted_size_pct != plan.size_pct and approved:
            parts.append(f"size_pct adjusted {plan.size_pct:.1f}% → {result.adjusted_size_pct:.1f}%.")
        if result.adjusted_leverage != plan.leverage and approved:
            parts.append(f"leverage adjusted {plan.leverage}x → {result.adjusted_leverage}x.")

        return RiskDecision(
            approved=approved,
            adjusted_action=result.adjusted_action,
            adjusted_size_pct=result.adjusted_size_pct,
            adjusted_leverage=result.adjusted_leverage,
            violated_rules=result.violated_rules,
            warnings=result.warnings,
            rationale=" | ".join(parts),
            setup_quality_score=result.quality_score,
            setup_quality_grade=result.quality_grade,
            gating_profile=result.gating_profile,
        )
