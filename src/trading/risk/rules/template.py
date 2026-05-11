"""Template-specific execution overlay rules."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS


class TemplateExecutionRule(BaseRule):
    name = "template_execution"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        execution_template = str(ms.get("execution_template", "generic_manual_review"))
        rel_strength_7d = float(ms.get("relative_strength_7d_pct", 0.0) or 0.0)
        plan = ctx.plan

        if execution_template == "core_reclaim_wait":
            if plan.trigger_price is None:
                result.warnings.append("core_reclaim_wait requires a trigger_price for reclaim confirmation.")
            if plan.thesis_window_hours and plan.thesis_window_hours > 48:
                result.warnings.append("core_reclaim_wait thesis window is too long; reclaim setups should resolve quickly.")

        elif execution_template == "alt_follow_with_confirmation":
            if plan.trigger_price is None:
                result.warnings.append("alt_follow_with_confirmation should define trigger_price before entry.")
            if rel_strength_7d < 0:
                result.warnings.append("alt_follow_with_confirmation but relative strength vs BTC is negative — confirmation quality is weak.")

        elif execution_template == "alt_defensive_only":
            result.adjusted_leverage = min(result.adjusted_leverage, 3)
            result.adjusted_size_pct = min(result.adjusted_size_pct, 1.5)
            if plan.entry_style == "market_now":
                result.warnings.append("alt_defensive_only should avoid immediate market chasing; prefer passive or staged entries.")

        elif execution_template == "mid_alt_staged_entry":
            if plan.entry_zone_low is None or plan.entry_zone_high is None:
                result.warnings.append("mid_alt_staged_entry should define a concrete entry zone for staged execution.")
            result.adjusted_size_pct = min(result.adjusted_size_pct, 2.0)

        elif execution_template == "high_beta_confirmation_only":
            if plan.trigger_price is None:
                result.warnings.append("high_beta_confirmation_only requires trigger_price before any entry.")
            if plan.thesis_window_hours and plan.thesis_window_hours > ctx.config.risk.narrative_thesis_window_cap_hours:
                result.warnings.append("high_beta_confirmation_only should keep thesis_window_hours short.")
            result.adjusted_size_pct = min(result.adjusted_size_pct, ctx.config.risk.high_beta_alt_max_position_size_pct * 100)
            result.adjusted_leverage = min(result.adjusted_leverage, ctx.config.risk.high_beta_alt_max_leverage)
