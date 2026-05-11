"""Position limit rules: max size, max leverage, event-driven warning."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, CLOSE_ACTIONS, OPEN_ACTIONS


class MaxSizeRule(BaseRule):
    name = "max_position_size"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.plan.action not in CLOSE_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        max_size = ctx.config.trading.max_position_size_pct * 100
        if ctx.plan.size_pct > max_size:
            result.violated_rules.append(
                f"size_pct {ctx.plan.size_pct:.1f}% exceeds max_position_size_pct {max_size:.1f}%"
            )
            result.adjusted_size_pct = max_size

        # Portfolio budget overlay
        budget = ctx.portfolio_budget
        if budget and ctx.plan.action in OPEN_ACTIONS:
            recommended_cap = float(budget.get("recommended_max_size_pct", result.adjusted_size_pct) or result.adjusted_size_pct)
            hard_cap = float(budget.get("hard_cap_size_pct", recommended_cap) or recommended_cap)
            if ctx.plan.size_pct > hard_cap:
                result.violated_rules.append(
                    f"size_pct {ctx.plan.size_pct:.1f}% exceeds portfolio hard cap {hard_cap:.1f}%"
                )
                result.adjusted_size_pct = min(result.adjusted_size_pct, hard_cap)
            elif result.adjusted_size_pct > recommended_cap:
                result.warnings.append(
                    f"Portfolio budget recommends max {recommended_cap:.1f}% for this setup."
                )
                result.adjusted_size_pct = min(result.adjusted_size_pct, recommended_cap)


class EventDrivenWarningRule(BaseRule):
    name = "event_driven_warning"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.plan.action not in CLOSE_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        event_max = ctx.config.trading.event_driven_max_pct * 100
        if 0 < ctx.plan.size_pct > event_max:
            result.warnings.append(
                f"size_pct {ctx.plan.size_pct:.1f}% exceeds event_driven_max_pct {event_max:.1f}% "
                "— elevated event-driven risk"
            )


class MaxLeverageRule(BaseRule):
    name = "max_leverage"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return True

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        max_lev = int(ctx.config.risk.max_leverage)
        if ctx.plan.leverage > max_lev:
            result.violated_rules.append(
                f"leverage {ctx.plan.leverage}x exceeds max_leverage {max_lev}x"
            )
            result.adjusted_leverage = max_lev
