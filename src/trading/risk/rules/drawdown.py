"""Drawdown gate rules: account drawdown thresholds + portfolio exposure + equity curve."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS


class DrawdownGateRule(BaseRule):
    name = "drawdown_gate"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return True

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        drawdown = float(ctx.account_snapshot.get("drawdown_pct", 0.0))
        suspend_pct = ctx.config.risk.suspend_drawdown * 100
        alert_pct = ctx.config.risk.alert_drawdown * 100
        max_dd_pct = ctx.config.risk.max_account_drawdown * 100

        if drawdown <= suspend_pct:
            result.violated_rules.append(
                f"Drawdown {drawdown:.1f}% <= suspension threshold {suspend_pct:.1f}% "
                "— all new positions blocked"
            )
            result.adjusted_action = "hold"
            result.adjusted_size_pct = 0.0
        elif drawdown <= max_dd_pct:
            result.violated_rules.append(
                f"Drawdown {drawdown:.1f}% <= max_account_drawdown {max_dd_pct:.1f}% "
                "— auto-close mode"
            )
            result.adjusted_action = "hold"
            result.adjusted_size_pct = 0.0
        elif drawdown <= alert_pct:
            result.warnings.append(
                f"Drawdown {drawdown:.1f}% exceeds alert threshold {alert_pct:.1f}%"
            )

        # Portfolio gross exposure cap
        if ctx.portfolio_snapshot and ctx.plan.action in OPEN_ACTIONS:
            gross_exposure = float(ctx.portfolio_snapshot.get("gross_exposure_pct", 0.0) or 0.0)
            if gross_exposure >= ctx.config.risk.portfolio_hard_gross_exposure_pct:
                result.violated_rules.append(
                    f"Gross exposure {gross_exposure:.1f}% already exceeds hard portfolio cap."
                )
                result.adjusted_action = "hold"
                result.adjusted_size_pct = 0.0


class EquityCurveRule(BaseRule):
    """P2: Reduce size or pause when equity curve is below its EMA.

    This rule reads equity_curve_status from account_snapshot (injected by pipeline).
    """
    name = "equity_curve_protection"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if not ctx.config.risk.equity_protection_enabled:
            return False
        return ctx.plan.action in OPEN_ACTIONS and "equity_curve_status" in ctx.account_snapshot

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        status = ctx.account_snapshot["equity_curve_status"]
        mode = status.get("mode", "normal")
        factor = float(status.get("factor", 1.0))
        ema = status.get("ema")

        if mode == "paused":
            result.violated_rules.append(
                f"Equity curve below pause threshold (balance < EMA*{ctx.config.risk.equity_pause_factor:.2f}, "
                f"EMA={ema:.0f}) — all new positions blocked."
            )
            result.adjusted_action = "hold"
            result.adjusted_size_pct = 0.0
        elif mode == "reduced":
            result.warnings.append(
                f"Equity curve below EMA({ctx.config.risk.equity_ema_period}) = {ema:.0f} — "
                f"size reduced by {(1 - factor)*100:.0f}%."
            )
            result.adjusted_size_pct = result.adjusted_size_pct * factor
