"""Market condition rules: volatility, funding rate, basis, trend, crowding, entry drift."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS


class VolatilityRule(BaseRule):
    name = "volatility_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        vol_24 = float(ms.get("realized_vol_24h_pct", 0.0) or 0.0)
        move_24 = abs(float(ms.get("price_change_24h_pct", 0.0) or 0.0))

        high_vol = float(ctx.config.risk.high_volatility_24h_pct)
        max_lev_high_vol = int(ctx.config.risk.max_leverage_high_vol)
        max_size_high_vol = ctx.config.trading.max_position_size_pct * 100
        if ctx.config.risk.max_position_size_high_vol_pct > 0:
            max_size_high_vol = min(
                max_size_high_vol,
                ctx.config.risk.max_position_size_high_vol_pct * 100,
            )

        if vol_24 >= high_vol and result.adjusted_leverage > max_lev_high_vol:
            result.warnings.append(
                f"24h realized volatility {vol_24:.1f}% is high — leverage reduced to {max_lev_high_vol}x"
            )
            result.adjusted_leverage = max_lev_high_vol

        if vol_24 >= high_vol and result.adjusted_size_pct > max_size_high_vol:
            result.warnings.append(
                f"24h realized volatility {vol_24:.1f}% is high — size reduced to {max_size_high_vol:.1f}%"
            )
            result.adjusted_size_pct = max_size_high_vol

        if move_24 >= float(ctx.config.risk.extreme_move_24h_pct):
            result.warnings.append(
                f"24h move {move_24:.1f}% is extreme — avoid chasing and demand better entry confirmation"
            )


class FundingRateRule(BaseRule):
    name = "funding_rate_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        funding_rate = float(ms.get("funding_rate", 0.0) or 0.0)
        crowded_funding = float(ctx.config.risk.max_abs_funding_rate)

        if ctx.plan.action == "open_long" and funding_rate >= crowded_funding:
            result.warnings.append(
                f"Funding rate {funding_rate:.4f} suggests crowded longs — reduce conviction / leverage"
            )
        elif ctx.plan.action == "open_short" and funding_rate <= -crowded_funding:
            result.warnings.append(
                f"Funding rate {funding_rate:.4f} suggests crowded shorts — reduce conviction / leverage"
            )


class BasisRule(BaseRule):
    name = "basis_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        basis_bps = float(ctx.market_snapshot.get("basis_bps", 0.0) or 0.0)
        if abs(basis_bps) >= 80:
            result.warnings.append(
                f"Basis {basis_bps:.1f} bps is stretched — derivatives positioning may be crowded"
            )


class TrendBiasRule(BaseRule):
    name = "trend_bias_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        trend_bias = str(ctx.market_snapshot.get("hourly_trend_bias", "range"))
        if ctx.plan.action == "open_long" and trend_bias == "bearish":
            result.warnings.append(
                "Market structure is bearish on the 1h trend stack — long setups require stronger confirmation."
            )
        elif ctx.plan.action == "open_short" and trend_bias == "bullish":
            result.warnings.append(
                "Market structure is bullish on the 1h trend stack — short setups require stronger confirmation."
            )


class CrowdingRule(BaseRule):
    name = "crowding_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        crowding_regime = str(ms.get("crowding_regime", "balanced"))
        oi_to_volume_ratio = float(ms.get("oi_to_volume_ratio", 0.0) or 0.0)

        if crowding_regime == "crowded_long" and ctx.plan.action == "open_long":
            result.warnings.append(
                f"Positioning is crowded long (oi/vol {oi_to_volume_ratio:.2f}) — reduce size, avoid market chasing."
            )
            result.adjusted_leverage = min(result.adjusted_leverage, max(2, ctx.config.risk.max_leverage_high_vol // 2))
            result.adjusted_size_pct = min(result.adjusted_size_pct, ctx.config.trading.max_position_size_pct * 100 * 0.5)
        elif crowding_regime == "crowded_short" and ctx.plan.action == "open_short":
            result.warnings.append(
                f"Positioning is crowded short (oi/vol {oi_to_volume_ratio:.2f}) — reduce size, avoid late breakdown entries."
            )
            result.adjusted_leverage = min(result.adjusted_leverage, max(2, ctx.config.risk.max_leverage_high_vol // 2))
            result.adjusted_size_pct = min(result.adjusted_size_pct, ctx.config.trading.max_position_size_pct * 100 * 0.5)


class EntryDriftRule(BaseRule):
    name = "entry_drift_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        spot_price = float(ms.get("spot_price", 0.0) or 0.0)
        plan = ctx.plan
        if spot_price > 0 and plan.entry_zone_low is not None and plan.entry_zone_high is not None:
            zone_mid = (plan.entry_zone_low + plan.entry_zone_high) / 2
            drift_pct = abs(spot_price - zone_mid) / spot_price * 100
            if drift_pct >= 3.0:
                result.warnings.append(
                    f"Current spot price is {drift_pct:.1f}% away from the proposed entry zone midpoint — execution may need refresh."
                )
