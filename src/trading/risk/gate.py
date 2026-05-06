"""Deterministic risk gate — applies rule-based checks to an ExecutionPlan."""

from ..ai.schemas import ExecutionPlan, RiskDecision
from ..core.config import AppConfig
from ..exchange.market_data import infer_narrative_tag

_CLOSE_ACTIONS = frozenset({"close_long", "close_short", "sell_spot", "hold"})
_OPEN_ACTIONS = frozenset({"open_long", "open_short", "buy_spot"})


class RiskGate:
    """Apply deterministic risk rules to an ExecutionPlan and return a RiskDecision."""

    def __init__(self, config: AppConfig):
        self.config = config

    def evaluate(
        self,
        plan: ExecutionPlan,
        account_snapshot: dict,
        positions: list[dict],
        market_snapshot: dict | None = None,
        portfolio_snapshot: dict | None = None,
        portfolio_budget: dict | None = None,
    ) -> RiskDecision:
        """Return a RiskDecision after applying all configured risk rules."""
        violated_rules: list[str] = []
        warnings: list[str] = []

        adjusted_action = plan.action
        adjusted_size_pct = plan.size_pct
        adjusted_leverage = plan.leverage
        stop_loss_pct = plan.stop_loss_pct
        take_profit_pct = plan.take_profit_pct

        # Rule 1: max_position_size_pct
        max_size = self.config.trading.max_position_size_pct * 100
        if plan.action not in _CLOSE_ACTIONS and plan.size_pct > max_size:
            violated_rules.append(
                f"size_pct {plan.size_pct:.1f}% exceeds max_position_size_pct {max_size:.1f}%"
            )
            adjusted_size_pct = max_size

        if portfolio_budget and plan.action in _OPEN_ACTIONS:
            recommended_cap = float(portfolio_budget.get("recommended_max_size_pct", adjusted_size_pct) or adjusted_size_pct)
            hard_cap = float(portfolio_budget.get("hard_cap_size_pct", recommended_cap) or recommended_cap)
            if plan.size_pct > hard_cap:
                violated_rules.append(
                    f"size_pct {plan.size_pct:.1f}% exceeds portfolio hard cap {hard_cap:.1f}%"
                )
                adjusted_size_pct = min(adjusted_size_pct, hard_cap)
            elif adjusted_size_pct > recommended_cap:
                warnings.append(
                    f"Portfolio budget recommends max {recommended_cap:.1f}% for this setup."
                )
                adjusted_size_pct = min(adjusted_size_pct, recommended_cap)

        # Rule 2: event_driven_max_pct warning
        event_max = self.config.trading.event_driven_max_pct * 100
        if plan.action not in _CLOSE_ACTIONS and 0 < plan.size_pct > event_max:
            warnings.append(
                f"size_pct {plan.size_pct:.1f}% exceeds event_driven_max_pct {event_max:.1f}% "
                "— elevated event-driven risk"
            )

        # Rule 3: max_leverage
        max_lev = int(self.config.risk.max_leverage)
        if plan.leverage > max_lev:
            violated_rules.append(
                f"leverage {plan.leverage}x exceeds max_leverage {max_lev}x"
            )
            adjusted_leverage = max_lev

        # Rule 4: account drawdown gates
        drawdown = float(account_snapshot.get("drawdown_pct", 0.0))
        suspend_pct = self.config.risk.suspend_drawdown * 100  # e.g. -30.0
        alert_pct = self.config.risk.alert_drawdown * 100      # e.g. -10.0
        max_dd_pct = self.config.risk.max_account_drawdown * 100  # e.g. -20.0

        if drawdown <= suspend_pct:
            violated_rules.append(
                f"Drawdown {drawdown:.1f}% <= suspension threshold {suspend_pct:.1f}% "
                "— all new positions blocked"
            )
            adjusted_action = "hold"
            adjusted_size_pct = 0.0
        elif drawdown <= max_dd_pct:
            violated_rules.append(
                f"Drawdown {drawdown:.1f}% <= max_account_drawdown {max_dd_pct:.1f}% "
                "— auto-close mode"
            )
            adjusted_action = "hold"
            adjusted_size_pct = 0.0
        elif drawdown <= alert_pct:
            warnings.append(
                f"Drawdown {drawdown:.1f}% exceeds alert threshold {alert_pct:.1f}%"
            )

        if portfolio_snapshot and plan.action in _OPEN_ACTIONS:
            gross_exposure = float(portfolio_snapshot.get("gross_exposure_pct", 0.0) or 0.0)
            if gross_exposure >= self.config.risk.portfolio_hard_gross_exposure_pct:
                violated_rules.append(
                    f"Gross exposure {gross_exposure:.1f}% already exceeds hard portfolio cap."
                )
                adjusted_action = "hold"
                adjusted_size_pct = 0.0

        # Rule 5: missing stop loss → add default
        if stop_loss_pct <= 0 and plan.action not in ("hold", "close_long", "close_short"):
            default_sl = self.config.trading.default_stop_loss_pct * 100
            warnings.append(f"No stop_loss_pct set — defaulting to {default_sl:.1f}%")
            stop_loss_pct = default_sl

        # Rule 6: missing take profit → add default
        if take_profit_pct <= 0 and plan.action not in ("hold", "close_long", "close_short"):
            default_tp = self.config.trading.default_take_profit_pct * 100
            warnings.append(f"No take_profit_pct set — defaulting to {default_tp:.1f}%")
            take_profit_pct = default_tp

        # Rules 7 & 8: conflict and duplicate position checks
        if positions and plan.action in _OPEN_ACTIONS:
            for pos in positions:
                pos_dir = str(pos.get("direction", pos.get("side", ""))).upper()
                pos_sym = pos.get("symbol", "").upper()

                if plan.action == "open_long" and pos_dir in ("SHORT", "SELL"):
                    warnings.append(
                        f"Opening LONG while existing SHORT for {pos_sym} — consider closing first"
                    )
                elif plan.action == "open_short" and pos_dir in ("LONG", "BUY"):
                    warnings.append(
                        f"Opening SHORT while existing LONG for {pos_sym} — consider closing first"
                    )
                elif plan.action == "open_long" and pos_dir in ("LONG", "BUY"):
                    warnings.append(
                        f"Existing LONG for {pos_sym} — adding to position (pyramiding); "
                        "ensure this is intentional"
                    )
                elif plan.action == "open_short" and pos_dir in ("SHORT", "SELL"):
                    warnings.append(
                        f"Existing SHORT for {pos_sym} — adding to position (pyramiding); "
                        "ensure this is intentional"
                    )

        same_narrative_count = 0
        if market_snapshot and plan.action in _OPEN_ACTIONS:
            narrative_tag = str(market_snapshot.get("narrative_tag", "general_alt"))
            same_narrative_count = sum(
                1 for p in positions
                if infer_narrative_tag(str(p.get("symbol", ""))) == narrative_tag
            )
            if same_narrative_count >= 2 and narrative_tag not in {"store_of_value", "smart_contract_l1"}:
                warnings.append(
                    f"Portfolio already has {same_narrative_count} position(s) in narrative '{narrative_tag}' — concentration risk is rising."
                )
                adjusted_size_pct = min(adjusted_size_pct, 1.5)

        # Rule 8b: price-geometry sanity checks for crypto execution plans
        if plan.action in _OPEN_ACTIONS:
            if (
                plan.entry_zone_low is not None
                and plan.entry_zone_high is not None
                and plan.entry_zone_low > plan.entry_zone_high
            ):
                warnings.append("entry_zone_low is above entry_zone_high — review the manual execution zone.")
            if plan.invalidation_price is not None and plan.trigger_price is not None:
                if plan.action == "open_long" and plan.invalidation_price >= plan.trigger_price:
                    warnings.append("Long setup invalidation_price should stay below trigger_price.")
                elif plan.action == "open_short" and plan.invalidation_price <= plan.trigger_price:
                    warnings.append("Short setup invalidation_price should stay above trigger_price.")

        # Rules 9-12: crypto price / volatility / funding aware checks
        if market_snapshot and plan.action in _OPEN_ACTIONS:
            vol_24 = float(market_snapshot.get("realized_vol_24h_pct", 0.0) or 0.0)
            move_24 = abs(float(market_snapshot.get("price_change_24h_pct", 0.0) or 0.0))
            funding_rate = float(market_snapshot.get("funding_rate", 0.0) or 0.0)
            basis_bps = float(market_snapshot.get("basis_bps", 0.0) or 0.0)
            trend_bias = str(market_snapshot.get("hourly_trend_bias", "range"))
            rel_strength_7d = float(market_snapshot.get("relative_strength_7d_pct", 0.0) or 0.0)
            btc_regime = str(market_snapshot.get("btc_market_regime", "mixed"))
            asset_tier = str(market_snapshot.get("asset_tier", "liquid_alt"))
            narrative_tag = str(market_snapshot.get("narrative_tag", "general_alt"))
            crowding_regime = str(market_snapshot.get("crowding_regime", "balanced"))
            oi_to_volume_ratio = float(market_snapshot.get("oi_to_volume_ratio", 0.0) or 0.0)
            execution_template = str(market_snapshot.get("execution_template", "generic_manual_review"))

            high_vol = float(self.config.risk.high_volatility_24h_pct)
            max_lev_high_vol = int(self.config.risk.max_leverage_high_vol)
            max_size_high_vol = self.config.trading.max_position_size_pct * 100
            if self.config.risk.max_position_size_high_vol_pct > 0:
                max_size_high_vol = min(
                    max_size_high_vol,
                    self.config.risk.max_position_size_high_vol_pct * 100,
                )

            if vol_24 >= high_vol and adjusted_leverage > max_lev_high_vol:
                warnings.append(
                    f"24h realized volatility {vol_24:.1f}% is high — leverage reduced to {max_lev_high_vol}x"
                )
                adjusted_leverage = max_lev_high_vol

            if vol_24 >= high_vol and adjusted_size_pct > max_size_high_vol:
                warnings.append(
                    f"24h realized volatility {vol_24:.1f}% is high — size reduced to {max_size_high_vol:.1f}%"
                )
                adjusted_size_pct = max_size_high_vol

            if move_24 >= float(self.config.risk.extreme_move_24h_pct):
                warnings.append(
                    f"24h move {move_24:.1f}% is extreme — avoid chasing and demand better entry confirmation"
                )

            crowded_funding = float(self.config.risk.max_abs_funding_rate)
            if plan.action == "open_long" and funding_rate >= crowded_funding:
                warnings.append(
                    f"Funding rate {funding_rate:.4f} suggests crowded longs — reduce conviction / leverage"
                )
            elif plan.action == "open_short" and funding_rate <= -crowded_funding:
                warnings.append(
                    f"Funding rate {funding_rate:.4f} suggests crowded shorts — reduce conviction / leverage"
                )

            if abs(basis_bps) >= 80:
                warnings.append(
                    f"Basis {basis_bps:.1f} bps is stretched — derivatives positioning may be crowded"
                )

            if asset_tier == "high_beta_alt":
                adjusted_leverage = min(adjusted_leverage, self.config.risk.high_beta_alt_max_leverage)
                adjusted_size_pct = min(
                    adjusted_size_pct,
                    self.config.risk.high_beta_alt_max_position_size_pct * 100,
                )
                warnings.append(
                    "Symbol classified as high_beta_alt — applying tighter leverage and size caps."
                )

            if narrative_tag == "meme":
                adjusted_leverage = min(adjusted_leverage, self.config.risk.meme_max_leverage)
                adjusted_size_pct = min(
                    adjusted_size_pct,
                    self.config.risk.meme_max_position_size_pct * 100,
                )
                warnings.append(
                    "Meme narrative detected — enforce ultra-light size and leverage, and expect slippage/volatility spikes."
                )
                if btc_regime in {"panic_flush", "risk_off_trend", "risk_off"}:
                    warnings.append(
                        "Meme narrative under BTC risk-off regime — default to no-trade unless setup is exceptionally strong."
                    )
                    adjusted_action = "hold"
                    adjusted_size_pct = 0.0

            if plan.action == "open_long" and trend_bias == "bearish":
                warnings.append(
                    "Market structure is bearish on the 1h trend stack — long setups require stronger confirmation."
                )
            elif plan.action == "open_short" and trend_bias == "bullish":
                warnings.append(
                    "Market structure is bullish on the 1h trend stack — short setups require stronger confirmation."
                )

            if plan.action in {"open_long", "open_short"} and market_snapshot.get("benchmark_symbol") == "BTCUSDT":
                if btc_regime in {"panic_flush", "risk_off_trend", "risk_off"}:
                    warnings.append(
                        "BTC regime is risk-off — altcoin directional trades should run lighter and faster."
                    )
                    adjusted_leverage = min(adjusted_leverage, self.config.risk.altcoin_max_leverage_when_btc_weak)
                    adjusted_size_pct = min(
                        adjusted_size_pct,
                        self.config.risk.altcoin_max_position_size_when_btc_weak_pct * 100,
                    )
                elif btc_regime == "short_squeeze" and plan.action == "open_short":
                    warnings.append(
                        "BTC regime looks like a squeeze — fresh alt shorts need extra confirmation."
                    )
                elif btc_regime == "rebound" and plan.action == "open_short":
                    warnings.append(
                        "BTC is in rebound mode — avoid pressing new alt shorts into a reflex bounce."
                    )
                if plan.action == "open_long" and rel_strength_7d <= self.config.risk.alt_relative_strength_warning_pct:
                    warnings.append(
                        f"Symbol underperformed BTC by {abs(rel_strength_7d):.1f}% over 7d — weak relative strength for a long."
                    )
                elif plan.action == "open_short" and rel_strength_7d >= abs(self.config.risk.alt_relative_strength_warning_pct):
                    warnings.append(
                        f"Symbol outperformed BTC by {rel_strength_7d:.1f}% over 7d — short may be fighting relative strength."
                    )

            if crowding_regime == "crowded_long" and plan.action == "open_long":
                warnings.append(
                    f"Positioning is crowded long (oi/vol {oi_to_volume_ratio:.2f}) — reduce size, avoid market chasing."
                )
                adjusted_leverage = min(adjusted_leverage, max(2, self.config.risk.max_leverage_high_vol // 2))
                adjusted_size_pct = min(adjusted_size_pct, self.config.trading.max_position_size_pct * 100 * 0.5)
            elif crowding_regime == "crowded_short" and plan.action == "open_short":
                warnings.append(
                    f"Positioning is crowded short (oi/vol {oi_to_volume_ratio:.2f}) — reduce size, avoid late breakdown entries."
                )
                adjusted_leverage = min(adjusted_leverage, max(2, self.config.risk.max_leverage_high_vol // 2))
                adjusted_size_pct = min(adjusted_size_pct, self.config.trading.max_position_size_pct * 100 * 0.5)

            spot_price = float(market_snapshot.get("spot_price", 0.0) or 0.0)
            if spot_price > 0 and plan.entry_zone_low is not None and plan.entry_zone_high is not None:
                zone_mid = (plan.entry_zone_low + plan.entry_zone_high) / 2
                drift_pct = abs(spot_price - zone_mid) / spot_price * 100
                if drift_pct >= 3.0:
                    warnings.append(
                        f"Current spot price is {drift_pct:.1f}% away from the proposed entry zone midpoint — execution may need refresh."
                    )

            # Template-specific overlays
            if execution_template == "core_reclaim_wait":
                if plan.trigger_price is None:
                    warnings.append("core_reclaim_wait requires a trigger_price for reclaim confirmation.")
                if plan.thesis_window_hours and plan.thesis_window_hours > 48:
                    warnings.append("core_reclaim_wait thesis window is too long; reclaim setups should resolve quickly.")

            elif execution_template == "alt_follow_with_confirmation":
                if plan.trigger_price is None:
                    warnings.append("alt_follow_with_confirmation should define trigger_price before entry.")
                if rel_strength_7d < 0:
                    warnings.append("alt_follow_with_confirmation but relative strength vs BTC is negative — confirmation quality is weak.")

            elif execution_template == "alt_defensive_only":
                adjusted_leverage = min(adjusted_leverage, 3)
                adjusted_size_pct = min(adjusted_size_pct, 1.5)
                if plan.entry_style == "market_now":
                    warnings.append("alt_defensive_only should avoid immediate market chasing; prefer passive or staged entries.")

            elif execution_template == "mid_alt_staged_entry":
                if plan.entry_zone_low is None or plan.entry_zone_high is None:
                    warnings.append("mid_alt_staged_entry should define a concrete entry zone for staged execution.")
                adjusted_size_pct = min(adjusted_size_pct, 2.0)

            elif execution_template == "high_beta_confirmation_only":
                if plan.trigger_price is None:
                    warnings.append("high_beta_confirmation_only requires trigger_price before any entry.")
                if plan.thesis_window_hours and plan.thesis_window_hours > self.config.risk.narrative_thesis_window_cap_hours:
                    warnings.append("high_beta_confirmation_only should keep thesis_window_hours short.")
                adjusted_size_pct = min(adjusted_size_pct, self.config.risk.high_beta_alt_max_position_size_pct * 100)
                adjusted_leverage = min(adjusted_leverage, self.config.risk.high_beta_alt_max_leverage)

            if narrative_tag in {"ai_agent", "ai_compute"} and btc_regime in {"risk_on_trend", "short_squeeze"}:
                warnings.append(
                    "AI-related narrative aligns with a positive BTC tape — momentum can persist, but watch crowding closely."
                )

        approved = len(violated_rules) == 0

        # Build rationale
        parts: list[str] = []
        if approved:
            parts.append("All risk checks passed.")
        else:
            parts.append(f"REJECTED: {'; '.join(violated_rules)}")
        if warnings:
            parts.append(f"Warnings: {'; '.join(warnings)}")
        if stop_loss_pct != plan.stop_loss_pct:
            parts.append(f"stop_loss_pct set to {stop_loss_pct:.1f}%.")
        if take_profit_pct != plan.take_profit_pct:
            parts.append(f"take_profit_pct set to {take_profit_pct:.1f}%.")
        if adjusted_size_pct != plan.size_pct and approved:
            parts.append(f"size_pct adjusted {plan.size_pct:.1f}% → {adjusted_size_pct:.1f}%.")
        if adjusted_leverage != plan.leverage and approved:
            parts.append(f"leverage adjusted {plan.leverage}x → {adjusted_leverage}x.")

        return RiskDecision(
            approved=approved,
            adjusted_action=adjusted_action,
            adjusted_size_pct=adjusted_size_pct,
            adjusted_leverage=adjusted_leverage,
            violated_rules=violated_rules,
            warnings=warnings,
            rationale=" | ".join(parts),
        )
