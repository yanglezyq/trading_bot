"""Default stop-loss and take-profit rules."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult


class DefaultStopLossRule(BaseRule):
    name = "default_stop_loss"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return (
            result.stop_loss_pct <= 0
            and ctx.plan.action not in ("hold", "close_long", "close_short")
        )

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        default_sl = ctx.config.trading.default_stop_loss_pct * 100
        result.warnings.append(f"No stop_loss_pct set — defaulting to {default_sl:.1f}%")
        result.stop_loss_pct = default_sl


class DefaultTakeProfitRule(BaseRule):
    name = "default_take_profit"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return (
            result.take_profit_pct <= 0
            and ctx.plan.action not in ("hold", "close_long", "close_short")
        )

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        default_tp = ctx.config.trading.default_take_profit_pct * 100
        result.warnings.append(f"No take_profit_pct set — defaulting to {default_tp:.1f}%")
        result.take_profit_pct = default_tp
