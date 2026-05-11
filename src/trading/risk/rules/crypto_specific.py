"""Crypto-specific rules: asset tier, meme, BTC regime, relative strength, AI narrative."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS


class AssetTierRule(BaseRule):
    name = "asset_tier_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        asset_tier = str(ctx.market_snapshot.get("asset_tier", "liquid_alt"))
        if asset_tier == "high_beta_alt":
            result.adjusted_leverage = min(result.adjusted_leverage, ctx.config.risk.high_beta_alt_max_leverage)
            result.adjusted_size_pct = min(
                result.adjusted_size_pct,
                ctx.config.risk.high_beta_alt_max_position_size_pct * 100,
            )
            result.warnings.append(
                "Symbol classified as high_beta_alt — applying tighter leverage and size caps."
            )


class MemeRule(BaseRule):
    name = "meme_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.market_snapshot is not None and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        narrative_tag = str(ms.get("narrative_tag", "general_alt"))
        btc_regime = str(ms.get("btc_market_regime", "mixed"))

        if narrative_tag == "meme":
            result.adjusted_leverage = min(result.adjusted_leverage, ctx.config.risk.meme_max_leverage)
            result.adjusted_size_pct = min(
                result.adjusted_size_pct,
                ctx.config.risk.meme_max_position_size_pct * 100,
            )
            result.warnings.append(
                "Meme narrative detected — enforce ultra-light size and leverage, and expect slippage/volatility spikes."
            )
            if btc_regime in {"panic_flush", "risk_off_trend", "risk_off"}:
                result.warnings.append(
                    "Meme narrative under BTC risk-off regime — default to no-trade unless setup is exceptionally strong."
                )
                result.adjusted_action = "hold"
                result.adjusted_size_pct = 0.0


class BtcRegimeRule(BaseRule):
    name = "btc_regime_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if ctx.market_snapshot is None or ctx.plan.action not in OPEN_ACTIONS:
            return False
        return ctx.market_snapshot.get("benchmark_symbol") == "BTCUSDT"

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot
        btc_regime = str(ms.get("btc_market_regime", "mixed"))
        rel_strength_7d = float(ms.get("relative_strength_7d_pct", 0.0) or 0.0)
        plan = ctx.plan

        if btc_regime in {"panic_flush", "risk_off_trend", "risk_off"}:
            result.warnings.append(
                "BTC regime is risk-off — altcoin directional trades should run lighter and faster."
            )
            result.adjusted_leverage = min(result.adjusted_leverage, ctx.config.risk.altcoin_max_leverage_when_btc_weak)
            result.adjusted_size_pct = min(
                result.adjusted_size_pct,
                ctx.config.risk.altcoin_max_position_size_when_btc_weak_pct * 100,
            )
        elif btc_regime == "short_squeeze" and plan.action == "open_short":
            result.warnings.append(
                "BTC regime looks like a squeeze — fresh alt shorts need extra confirmation."
            )
        elif btc_regime == "rebound" and plan.action == "open_short":
            result.warnings.append(
                "BTC is in rebound mode — avoid pressing new alt shorts into a reflex bounce."
            )

        if plan.action == "open_long" and rel_strength_7d <= ctx.config.risk.alt_relative_strength_warning_pct:
            result.warnings.append(
                f"Symbol underperformed BTC by {abs(rel_strength_7d):.1f}% over 7d — weak relative strength for a long."
            )
        elif plan.action == "open_short" and rel_strength_7d >= abs(ctx.config.risk.alt_relative_strength_warning_pct):
            result.warnings.append(
                f"Symbol outperformed BTC by {rel_strength_7d:.1f}% over 7d — short may be fighting relative strength."
            )


class AiNarrativeRule(BaseRule):
    name = "ai_narrative_check"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if ctx.market_snapshot is None or ctx.plan.action not in OPEN_ACTIONS:
            return False
        narrative_tag = str(ctx.market_snapshot.get("narrative_tag", "general_alt"))
        btc_regime = str(ctx.market_snapshot.get("btc_market_regime", "mixed"))
        return narrative_tag in {"ai_agent", "ai_compute"} and btc_regime in {"risk_on_trend", "short_squeeze"}

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        result.warnings.append(
            "AI-related narrative aligns with a positive BTC tape — momentum can persist, but watch crowding closely."
        )
