"""Event-driven risk rules: reduce exposure near high-impact events."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS


class EventRiskRule(BaseRule):
    """Reduce position size and cap leverage when high-impact events are imminent.

    Applies when:
    - has_high_impact_soon: Macro event (FOMC, CPI, etc.) within configured hours
    - has_major_unlock_soon: Token unlock >1% supply within 7 days

    Effects:
    - Size reduced by config.events.high_impact_size_reduction (default 50%)
    - Leverage capped at config.events.high_impact_max_leverage (default 10x)
    """

    name = "event_risk"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if result.adjusted_action not in OPEN_ACTIONS:
            return False
        if not ctx.event_snapshot:
            return False
        return (
            ctx.event_snapshot.get("has_high_impact_soon", False)
            or ctx.event_snapshot.get("has_major_unlock_soon", False)
        )

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        events_cfg = ctx.config.events
        reasons: list[str] = []

        if ctx.event_snapshot.get("has_high_impact_soon"):
            reasons.append(f"high-impact macro event within {events_cfg.high_impact_hours_before}h")
        if ctx.event_snapshot.get("has_major_unlock_soon"):
            reasons.append("major token unlock (>1% supply) within 7d")

        # Reduce size
        reduction = events_cfg.high_impact_size_reduction
        new_size = result.adjusted_size_pct * (1.0 - reduction)
        if new_size < result.adjusted_size_pct:
            result.adjusted_size_pct = round(new_size, 2)

        # Cap leverage
        max_lev = events_cfg.high_impact_max_leverage
        if result.adjusted_leverage > max_lev:
            result.adjusted_leverage = max_lev

        reason_str = "; ".join(reasons)
        result.warnings.append(
            f"[event_risk] Upcoming event(s): {reason_str}. "
            f"Size reduced by {reduction*100:.0f}%, leverage capped at {max_lev}x."
        )
