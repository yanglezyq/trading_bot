"""Price geometry sanity check rule."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS


class PriceGeometryRule(BaseRule):
    name = "price_geometry"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        plan = ctx.plan
        if (
            plan.entry_zone_low is not None
            and plan.entry_zone_high is not None
            and plan.entry_zone_low > plan.entry_zone_high
        ):
            result.warnings.append("entry_zone_low is above entry_zone_high — review the manual execution zone.")
        if plan.invalidation_price is not None and plan.trigger_price is not None:
            if plan.action == "open_long" and plan.invalidation_price >= plan.trigger_price:
                result.warnings.append("Long setup invalidation_price should stay below trigger_price.")
            elif plan.action == "open_short" and plan.invalidation_price <= plan.trigger_price:
                result.warnings.append("Short setup invalidation_price should stay above trigger_price.")
