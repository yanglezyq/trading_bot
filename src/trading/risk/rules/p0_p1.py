"""P0/P1/P2 rules: funding velocity, order book depth, multi-timeframe conflict, dynamic leverage."""

from __future__ import annotations

from math import floor

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS


_TIER_FACTOR = {
    "core": 1.0,
    "major_alt": 0.7,
    "liquid_alt": 0.5,
    "mid_alt": 0.35,
    "high_beta_alt": 0.2,
}

_REGIME_FACTOR = {
    "risk_on_trend": 1.0,
    "short_squeeze": 0.9,
    "range": 0.8,
    "rebound": 0.6,
    "risk_off_trend": 0.4,
    "risk_off": 0.4,
    "panic_flush": 0.2,
}


class FundingVelocityRule(BaseRule):
    name = "funding_velocity"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if ctx.market_snapshot is None or ctx.plan.action not in OPEN_ACTIONS:
            return False
        return ctx.market_snapshot.get("funding_velocity") is not None

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        funding_velocity = ctx.market_snapshot.get("funding_velocity")
        fv_threshold = float(ctx.config.risk.funding_velocity_block_threshold)
        if funding_velocity > fv_threshold and ctx.plan.action == "open_long":
            result.warnings.append(
                f"Funding velocity {funding_velocity:.5f} is spiking — crowded longs building rapidly, avoid new longs."
            )
            result.adjusted_leverage = min(result.adjusted_leverage, 3)
            result.adjusted_size_pct = min(result.adjusted_size_pct, 1.0)
        elif funding_velocity < -fv_threshold and ctx.plan.action == "open_short":
            result.warnings.append(
                f"Funding velocity {funding_velocity:.5f} is dropping rapidly — crowded shorts building, avoid new shorts."
            )
            result.adjusted_leverage = min(result.adjusted_leverage, 3)
            result.adjusted_size_pct = min(result.adjusted_size_pct, 1.0)


class DepthRule(BaseRule):
    name = "order_book_depth"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if ctx.market_snapshot is None or ctx.plan.action not in OPEN_ACTIONS:
            return False
        return ctx.config.risk.depth_check_enabled

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        ask_depth = ms.get("ask_depth_at_100bps")
        bid_depth = ms.get("bid_depth_at_100bps")

        relevant_depth = None
        if ctx.plan.action == "open_long" and ask_depth is not None:
            relevant_depth = ask_depth
        elif ctx.plan.action == "open_short" and bid_depth is not None:
            relevant_depth = bid_depth

        if relevant_depth is not None and relevant_depth > 0:
            balance = float(ctx.account_snapshot.get("futures_balance_usdt", 0.0) or 0.0)
            estimated_notional = balance * (result.adjusted_size_pct / 100.0) * result.adjusted_leverage
            fill_ratio = estimated_notional / relevant_depth
            if fill_ratio >= ctx.config.risk.depth_warning_fill_pct:
                result.warnings.append(
                    f"Estimated notional ${estimated_notional:,.0f} fills {fill_ratio*100:.0f}% of available book depth "
                    f"(${relevant_depth:,.0f} within 100bps) — consider splitting order or reducing size."
                )
                if fill_ratio >= 1.0:
                    result.adjusted_size_pct = min(result.adjusted_size_pct, result.adjusted_size_pct * 0.5)
                    result.warnings.append("Order exceeds available depth — size halved automatically.")


class MultiTfRule(BaseRule):
    name = "multi_tf_conflict"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if ctx.market_snapshot is None or ctx.plan.action not in OPEN_ACTIONS:
            return False
        return ctx.market_snapshot.get("multi_tf_alignment") == "conflicted"

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        result.warnings.append(
            "1h and 4h trend biases conflict — reduced conviction; size capped by 20%."
        )
        result.adjusted_size_pct = min(result.adjusted_size_pct, result.adjusted_size_pct * 0.8)


class DynamicLeverageRule(BaseRule):
    """P2: Continuous dynamic leverage formula based on vol, tier, and regime."""
    name = "dynamic_leverage"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if not ctx.config.risk.dynamic_leverage_enabled:
            return False
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        vol_24 = float(ms.get("realized_vol_24h_pct", 0.0) or 0.0)
        asset_tier = str(ms.get("asset_tier", "liquid_alt"))
        btc_regime = str(ms.get("btc_market_regime", "range"))

        base_leverage = float(ctx.config.risk.max_leverage)
        vol_ceiling = float(ctx.config.risk.vol_ceiling_pct)

        vol_factor = max(0.1, 1.0 - (vol_24 / vol_ceiling))
        tier_factor = _TIER_FACTOR.get(asset_tier, 0.5)
        regime_factor = _REGIME_FACTOR.get(btc_regime, 0.8)

        suggested = int(floor(base_leverage * vol_factor * tier_factor * regime_factor))
        suggested = max(1, min(suggested, int(base_leverage)))

        if suggested < result.adjusted_leverage:
            result.warnings.append(
                f"Dynamic leverage formula suggests {suggested}x "
                f"(vol={vol_24:.1f}%, tier={asset_tier}, regime={btc_regime})."
            )
            result.adjusted_leverage = suggested
