"""Position conflict, liquidation distance, and profit lock rules."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS
from ...exchange.market_data import infer_narrative_tag


class PositionConflictRule(BaseRule):
    name = "position_conflict"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return bool(ctx.positions) and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        for pos in ctx.positions:
            pos_dir = str(pos.get("direction", pos.get("side", ""))).upper()
            pos_sym = pos.get("symbol", "").upper()

            if ctx.plan.action == "open_long" and pos_dir in ("SHORT", "SELL"):
                result.warnings.append(
                    f"Opening LONG while existing SHORT for {pos_sym} — consider closing first"
                )
            elif ctx.plan.action == "open_short" and pos_dir in ("LONG", "BUY"):
                result.warnings.append(
                    f"Opening SHORT while existing LONG for {pos_sym} — consider closing first"
                )
            elif ctx.plan.action == "open_long" and pos_dir in ("LONG", "BUY"):
                result.warnings.append(
                    f"Existing LONG for {pos_sym} — adding to position (pyramiding); "
                    "ensure this is intentional"
                )
            elif ctx.plan.action == "open_short" and pos_dir in ("SHORT", "SELL"):
                result.warnings.append(
                    f"Existing SHORT for {pos_sym} — adding to position (pyramiding); "
                    "ensure this is intentional"
                )


class LiquidationDistanceRule(BaseRule):
    name = "liquidation_distance"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return bool(ctx.positions) and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        min_liq_dist = float(ctx.config.risk.min_liquidation_distance_pct)
        for pos in ctx.positions:
            liq_price = pos.get("liquidation_price")
            entry_price = pos.get("price", 0.0)
            if liq_price and entry_price and float(liq_price) > 0 and float(entry_price) > 0:
                liq_dist = abs(float(entry_price) - float(liq_price)) / float(entry_price) * 100
                if liq_dist < min_liq_dist:
                    pos_sym = pos.get("symbol", "")
                    result.warnings.append(
                        f"{pos_sym} liquidation distance is only {liq_dist:.1f}% "
                        f"(threshold: {min_liq_dist:.0f}%) — reduce leverage or close before adding risk."
                    )
                    result.adjusted_size_pct = min(result.adjusted_size_pct, 1.0)


class ProfitLockRule(BaseRule):
    name = "profit_lock"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return bool(ctx.positions) and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        lock_threshold = ctx.config.risk.profit_lock_threshold * 100
        lock_ratio = ctx.config.risk.profit_lock_ratio * 100
        for pos in ctx.positions:
            pnl_pct = float(pos.get("unrealized_pnl_pct", 0.0) or 0.0)
            if pnl_pct >= lock_threshold:
                pos_sym = pos.get("symbol", "")
                result.warnings.append(
                    f"{pos_sym} 浮盈 {pnl_pct:.1f}% 已达锁定阈值 ({lock_threshold:.0f}%) — "
                    f"建议先平仓 {lock_ratio:.0f}% 锁定利润再加仓"
                )


class NarrativeConcentrationRule(BaseRule):
    name = "narrative_concentration"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        narrative_tag = str(ctx.market_snapshot.get("narrative_tag", "general_alt"))
        same_narrative_count = sum(
            1 for p in ctx.positions
            if infer_narrative_tag(str(p.get("symbol", ""))) == narrative_tag
        )
        if same_narrative_count >= 2 and narrative_tag not in {"store_of_value", "smart_contract_l1"}:
            result.warnings.append(
                f"Portfolio already has {same_narrative_count} position(s) in narrative '{narrative_tag}' — concentration risk is rising."
            )
            result.adjusted_size_pct = min(result.adjusted_size_pct, 1.5)
