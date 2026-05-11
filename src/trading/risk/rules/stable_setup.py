"""Stable-mode setup quality gating for conservative, low-frequency trading."""

from __future__ import annotations

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS
from .stable_common import is_conviction_setup


def _grade(score: float) -> str:
    """Convert numeric quality score to a coarse letter grade."""
    if score >= 85:
        return "A"
    if score >= 75:
        return "B"
    if score >= 65:
        return "C"
    return "D"


class StableSetupRule(BaseRule):
    """Prefer only high-quality, easier-to-execute setups when stable mode is enabled."""

    name = "stable_setup_gate"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        if not ctx.config.trading.stable_mode_enabled:
            return False
        if ctx.plan.action not in OPEN_ACTIONS:
            return False
        if ctx.market_snapshot is None or ctx.research_decision is None:
            return False
        required = ("asset_tier", "btc_market_regime", "execution_template", "crowding_regime")
        return all(key in ctx.market_snapshot for key in required)

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        ms = ctx.market_snapshot or {}
        rd = ctx.research_decision or {}
        tc = ctx.config.trading
        conviction = bool(getattr(tc, "conviction_override_enabled", True) and is_conviction_setup(ctx))

        score = 100.0
        reasons: list[str] = []

        confidence = float(rd.get("confidence", 0.0) or 0.0)
        consensus = float(rd.get("consensus_strength", 1.0) or 1.0)
        model_count = int(rd.get("supporting_model_count", 1) or 1)
        asset_tier = str(ms.get("asset_tier", "unknown"))
        narrative = str(ms.get("narrative_tag", "general_alt"))
        btc_regime = str(ms.get("btc_market_regime", "unknown"))
        execution_template = str(ms.get("execution_template", "generic_manual_review"))
        crowding = str(ms.get("crowding_regime", "balanced"))
        volatility = str(ms.get("volatility_regime", "normal"))
        rel_strength_7d = float(ms.get("relative_strength_7d_pct", 0.0) or 0.0)
        portfolio_role = str((ctx.portfolio_budget or {}).get("portfolio_role", ""))

        if confidence < tc.stable_min_confidence:
            score -= 25
            reasons.append(f"confidence {confidence:.0%} < stable threshold {tc.stable_min_confidence:.0%}")

        if model_count > 1 and consensus < tc.stable_min_consensus_strength:
            score -= 15
            reasons.append(
                f"consensus {consensus:.0%} < stable threshold {tc.stable_min_consensus_strength:.0%}"
            )

        if asset_tier not in set(tc.stable_allowed_tiers):
            score -= 10 if conviction else 35
            reasons.append(f"asset tier {asset_tier} is outside stable allowlist")

        if narrative in set(tc.stable_blocked_narratives):
            score -= 10 if conviction else 35
            reasons.append(f"narrative {narrative} is blocked in stable mode")

        if btc_regime not in set(tc.stable_allowed_btc_regimes):
            score -= 5 if conviction else 20
            reasons.append(f"BTC regime {btc_regime} is not stable-friendly")

        if crowding in {"crowded_long", "crowded_short", "heavy_positioning"}:
            score -= 10
            reasons.append(f"crowding regime is {crowding}")

        if volatility in {"high_volatility", "high", "extreme"}:
            score -= 10
            reasons.append(f"volatility regime is {volatility}")

        if execution_template in {"high_beta_confirmation_only", "mid_alt_staged_entry", "alt_defensive_only"}:
            score -= 5 if conviction else 15
            reasons.append(f"execution template {execution_template} is not ideal for stable mode")

        if portfolio_role in {"speculative_probe", "tactical_alt"}:
            score -= 2 if conviction else 10
            reasons.append(f"portfolio role {portfolio_role} is speculative")

        if ctx.plan.action == "open_long" and rel_strength_7d < 0:
            score -= 10
            reasons.append("relative strength vs BTC is negative for a long")
        elif ctx.plan.action == "open_short" and rel_strength_7d > 0:
            score -= 10
            reasons.append("relative strength vs BTC is positive for a short")

        score = max(0.0, round(score, 1))
        grade = _grade(score)
        result.quality_score = score
        result.quality_grade = grade
        result.gating_profile = "conviction_override" if conviction else "stable_mode"

        # Stable mode caps by default, but high-conviction setups can use a looser ceiling.
        if conviction:
            result.adjusted_size_pct = min(result.adjusted_size_pct, tc.conviction_override_size_pct * 100)
            result.adjusted_leverage = min(result.adjusted_leverage, tc.conviction_override_max_leverage)
        else:
            result.adjusted_size_pct = min(result.adjusted_size_pct, tc.stable_max_position_size_pct * 100)
            result.adjusted_leverage = min(result.adjusted_leverage, tc.stable_max_leverage)

        if reasons:
            result.warnings.append("Stable mode quality review: " + "; ".join(reasons[:4]))

        min_quality = tc.conviction_override_min_quality_score if conviction else tc.stable_min_quality_score
        if score < min_quality:
            result.violated_rules.append(
                f"stable setup quality {score:.1f} < required {min_quality:.1f}"
            )
            result.adjusted_action = "hold"
            result.adjusted_size_pct = 0.0
