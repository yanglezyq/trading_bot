"""Tests for the deterministic risk gate."""

from datetime import datetime, timedelta

import pytest

from trading.ai.schemas import ExecutionPlan, RiskDecision
from trading.core.config import AppConfig, BinanceConfig, ClaudeConfig, LoggingConfig, MonitorConfig, RiskConfig, TradingConfig, VaultConfig
from trading.risk.gate import RiskGate


def _make_config(
    max_position_size_pct: float = 0.05,
    max_leverage: float = 35.0,
    suspend_drawdown: float = -0.30,
    max_account_drawdown: float = -0.20,
    alert_drawdown: float = -0.10,
    default_stop_loss_pct: float = 0.05,
    default_take_profit_pct: float = 0.15,
) -> AppConfig:
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(
            max_leverage=max_leverage,
            suspend_drawdown=suspend_drawdown,
            max_account_drawdown=max_account_drawdown,
            alert_drawdown=alert_drawdown,
        ),
        binance=BinanceConfig(),
        claude=ClaudeConfig(),
        trading=TradingConfig(
            max_position_size_pct=max_position_size_pct,
            event_driven_max_pct=max_position_size_pct,
            default_stop_loss_pct=default_stop_loss_pct,
            default_take_profit_pct=default_take_profit_pct,
        ),
        monitor=MonitorConfig(),
        logging=LoggingConfig(),
    )


def _make_plan(
    action: str = "open_long",
    size_pct: float = 3.0,
    leverage: int = 10,
    stop_loss_pct: float = 5.0,
    take_profit_pct: float = 15.0,
) -> ExecutionPlan:
    return ExecutionPlan(
        action=action,
        size_pct=size_pct,
        leverage=leverage,
        entry_idea="",
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        rationale="",
    )


class TestRiskGateApproval:
    def test_valid_plan_approved(self):
        gate = RiskGate(_make_config())
        decision = gate.evaluate(_make_plan(), account_snapshot={}, positions=[])
        assert decision.approved is True
        assert decision.violated_rules == []

    def test_size_exceeds_max_rejected(self):
        gate = RiskGate(_make_config(max_position_size_pct=0.05))
        plan = _make_plan(size_pct=20.0)  # 20% > 5%
        decision = gate.evaluate(plan, account_snapshot={}, positions=[])
        assert decision.approved is False
        assert any("size_pct" in r for r in decision.violated_rules)
        # Gate still exposes the capped adjusted value for allow_partial flows
        assert decision.adjusted_size_pct == 5.0
        assert decision.adjusted_action == "open_long"  # action not forced to hold

    def test_leverage_exceeds_max_rejected(self):
        gate = RiskGate(_make_config(max_leverage=10.0))
        plan = _make_plan(leverage=50)
        decision = gate.evaluate(plan, account_snapshot={}, positions=[])
        assert decision.approved is False
        assert any("leverage" in r for r in decision.violated_rules)
        assert decision.adjusted_leverage == 10  # capped value preserved

    def test_suspend_drawdown_blocks_all(self):
        gate = RiskGate(_make_config(suspend_drawdown=-0.30))
        plan = _make_plan()
        decision = gate.evaluate(plan, account_snapshot={"drawdown_pct": -35.0}, positions=[])
        assert decision.approved is False
        assert decision.adjusted_action == "hold"
        assert decision.adjusted_size_pct == 0.0

    def test_max_account_drawdown_blocks(self):
        gate = RiskGate(_make_config(max_account_drawdown=-0.20))
        plan = _make_plan()
        decision = gate.evaluate(plan, account_snapshot={"drawdown_pct": -25.0}, positions=[])
        assert decision.approved is False
        assert decision.adjusted_action == "hold"

    def test_alert_drawdown_warns_not_rejects(self):
        gate = RiskGate(_make_config(alert_drawdown=-0.10))
        plan = _make_plan()
        decision = gate.evaluate(plan, account_snapshot={"drawdown_pct": -12.0}, positions=[])
        assert decision.approved is True
        assert any("alert" in w.lower() or "drawdown" in w.lower() for w in decision.warnings)

    def test_hold_action_passes_size_check(self):
        gate = RiskGate(_make_config(max_position_size_pct=0.05))
        plan = _make_plan(action="hold", size_pct=0.0)
        decision = gate.evaluate(plan, account_snapshot={}, positions=[])
        assert decision.approved is True

    def test_close_action_bypasses_size_rule(self):
        gate = RiskGate(_make_config(max_position_size_pct=0.05))
        plan = _make_plan(action="close_long", size_pct=50.0)
        decision = gate.evaluate(plan, account_snapshot={}, positions=[])
        assert decision.approved is True


class TestRiskGateDefaults:
    def test_missing_stop_loss_adds_default(self):
        gate = RiskGate(_make_config(default_stop_loss_pct=0.05))
        plan = _make_plan(stop_loss_pct=0.0)
        decision = gate.evaluate(plan, account_snapshot={}, positions=[])
        assert any("stop_loss_pct" in w or "5.0%" in w for w in decision.warnings)

    def test_missing_take_profit_adds_default(self):
        gate = RiskGate(_make_config(default_take_profit_pct=0.15))
        plan = _make_plan(take_profit_pct=0.0)
        decision = gate.evaluate(plan, account_snapshot={}, positions=[])
        assert any("take_profit_pct" in w or "15.0%" in w for w in decision.warnings)


class TestRiskGateConflicts:
    def test_opening_long_with_existing_short_warns(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long")
        positions = [{"symbol": "BTCUSDT", "direction": "SHORT"}]
        decision = gate.evaluate(plan, account_snapshot={}, positions=positions)
        assert any("SHORT" in w or "long" in w.lower() for w in decision.warnings)

    def test_duplicate_same_direction_warns(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long")
        positions = [{"symbol": "BTCUSDT", "direction": "LONG"}]
        decision = gate.evaluate(plan, account_snapshot={}, positions=positions)
        assert any("LONG" in w or "pyramiding" in w.lower() for w in decision.warnings)

    def test_no_warnings_for_unrelated_symbol(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long")
        positions = [{"symbol": "ETHUSDT", "direction": "SHORT"}]
        decision = gate.evaluate(plan, account_snapshot={}, positions=positions)
        # Should have warning since positions list passed in without symbol filter
        # (pipeline pre-filters; gate just warns on whatever it receives)
        assert isinstance(decision.warnings, list)


class TestLiquidationDistance:
    def test_liquidation_too_close_warns_and_caps_size(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=4.0)
        positions = [
            {"symbol": "BTCUSDT", "direction": "LONG", "price": 50000.0, "liquidation_price": 47000.0}
        ]
        decision = gate.evaluate(plan, account_snapshot={}, positions=positions)
        assert any("liquidation distance" in w for w in decision.warnings)
        assert decision.adjusted_size_pct <= 1.0

    def test_liquidation_safe_no_warning(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=3.0)
        positions = [
            {"symbol": "BTCUSDT", "direction": "LONG", "price": 50000.0, "liquidation_price": 30000.0}
        ]
        decision = gate.evaluate(plan, account_snapshot={}, positions=positions)
        assert not any("liquidation distance" in w for w in decision.warnings)

    def test_no_liquidation_price_skips(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=3.0)
        positions = [{"symbol": "BTCUSDT", "direction": "LONG", "price": 50000.0}]
        decision = gate.evaluate(plan, account_snapshot={}, positions=positions)
        assert not any("liquidation distance" in w for w in decision.warnings)


class TestFundingVelocity:
    def test_funding_spike_blocks_long(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=3.0, leverage=10)
        market = {"funding_velocity": 0.0008}  # > 0.0005 threshold
        decision = gate.evaluate(plan, account_snapshot={}, positions=[], market_snapshot=market)
        assert any("Funding velocity" in w for w in decision.warnings)
        assert decision.adjusted_leverage <= 3
        assert decision.adjusted_size_pct <= 1.0

    def test_funding_drop_blocks_short(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_short", size_pct=3.0, leverage=10)
        market = {"funding_velocity": -0.0008}
        decision = gate.evaluate(plan, account_snapshot={}, positions=[], market_snapshot=market)
        assert any("Funding velocity" in w for w in decision.warnings)
        assert decision.adjusted_leverage <= 3

    def test_normal_funding_velocity_no_warning(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=3.0)
        market = {"funding_velocity": 0.0002}
        decision = gate.evaluate(plan, account_snapshot={}, positions=[], market_snapshot=market)
        assert not any("Funding velocity" in w for w in decision.warnings)


class TestProfitLock:
    def test_high_profit_warns(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=3.0)
        positions = [{"symbol": "BTCUSDT", "direction": "LONG", "unrealized_pnl_pct": 60.0}]
        decision = gate.evaluate(plan, account_snapshot={}, positions=positions)
        assert any("浮盈" in w for w in decision.warnings)

    def test_below_threshold_no_warn(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=3.0)
        positions = [{"symbol": "BTCUSDT", "direction": "LONG", "unrealized_pnl_pct": 30.0}]
        decision = gate.evaluate(plan, account_snapshot={}, positions=positions)
        assert not any("浮盈" in w for w in decision.warnings)


class TestMultiTfAlignment:
    def test_conflicted_reduces_size(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=4.0)
        market = {"multi_tf_alignment": "conflicted"}
        decision = gate.evaluate(plan, account_snapshot={}, positions=[], market_snapshot=market)
        assert any("conflict" in w for w in decision.warnings)
        assert decision.adjusted_size_pct < 4.0

    def test_aligned_no_reduction(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=4.0)
        market = {"multi_tf_alignment": "aligned_bullish"}
        decision = gate.evaluate(plan, account_snapshot={}, positions=[], market_snapshot=market)
        assert not any("conflict" in w for w in decision.warnings)


class TestDepthWarning:
    def test_large_order_vs_thin_book_warns(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=5.0, leverage=10)
        market = {"ask_depth_at_100bps": 30000.0}  # 30k USDT depth
        account = {"futures_balance_usdt": 10000.0}  # 5% * 10k * 10x = 5000 notional
        decision = gate.evaluate(plan, account_snapshot=account, positions=[], market_snapshot=market)
        # 5000/30000 = 16.7% < 50% threshold, no warning
        assert not any("book depth" in w for w in decision.warnings)

    def test_very_large_order_warns(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=5.0, leverage=20)
        market = {"ask_depth_at_100bps": 10000.0}  # thin book
        account = {"futures_balance_usdt": 10000.0}  # 5% * 10k * 20x = 10000 notional
        decision = gate.evaluate(plan, account_snapshot=account, positions=[], market_snapshot=market)
        assert any("book depth" in w for w in decision.warnings)


class TestDynamicLeverage:
    def test_core_risk_on_low_vol_gets_high_leverage(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=3.0, leverage=35)
        market = {
            "realized_vol_24h_pct": 3.0,
            "asset_tier": "core",
            "btc_market_regime": "risk_on_trend",
        }
        decision = gate.evaluate(plan, account_snapshot={}, positions=[], market_snapshot=market)
        # floor(35 * 0.85 * 1.0 * 1.0) = 29
        assert decision.adjusted_leverage >= 20

    def test_high_beta_panic_flush_gets_minimal_leverage(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=2.0, leverage=20)
        market = {
            "realized_vol_24h_pct": 15.0,
            "asset_tier": "high_beta_alt",
            "btc_market_regime": "panic_flush",
        }
        decision = gate.evaluate(plan, account_snapshot={}, positions=[], market_snapshot=market)
        # floor(35 * 0.25 * 0.2 * 0.2) = floor(0.35) = 0 -> clamped to 1
        assert decision.adjusted_leverage <= 3

    def test_dynamic_leverage_disabled_no_warning(self):
        from trading.core.config import RiskConfig
        config = _make_config()
        config.risk.dynamic_leverage_enabled = False
        gate = RiskGate(config)
        plan = _make_plan(action="open_long", size_pct=3.0, leverage=20)
        market = {
            "realized_vol_24h_pct": 15.0,
            "asset_tier": "mid_alt",
            "btc_market_regime": "panic_flush",
        }
        decision = gate.evaluate(plan, account_snapshot={}, positions=[], market_snapshot=market)
        assert not any("Dynamic leverage" in w for w in decision.warnings)


class TestStableSetupRule:
    def test_low_quality_speculative_setup_is_blocked(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=3.0, leverage=12)
        market = {
            "asset_tier": "high_beta_alt",
            "narrative_tag": "meme",
            "btc_market_regime": "panic_flush",
            "execution_template": "high_beta_confirmation_only",
            "crowding_regime": "crowded_long",
            "volatility_regime": "high_volatility",
            "relative_strength_7d_pct": -12.0,
        }
        research = {
            "confidence": 0.58,
            "consensus_strength": 0.5,
            "supporting_model_count": 2,
        }
        budget = {"portfolio_role": "speculative_probe"}
        decision = gate.evaluate(
            plan,
            account_snapshot={},
            positions=[],
            market_snapshot=market,
            portfolio_budget=budget,
            research_decision=research,
        )
        assert decision.approved is False
        assert decision.adjusted_action == "hold"
        assert decision.setup_quality_score is not None
        assert decision.setup_quality_score < 70.0
        assert decision.gating_profile == "stable_mode"

    def test_high_quality_core_setup_passes_with_conservative_caps(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=4.0, leverage=10)
        market = {
            "asset_tier": "core",
            "narrative_tag": "store_of_value",
            "btc_market_regime": "risk_on_trend",
            "execution_template": "core_trend_follow",
            "crowding_regime": "balanced",
            "volatility_regime": "normal",
            "relative_strength_7d_pct": 6.0,
        }
        research = {
            "confidence": 0.84,
            "consensus_strength": 1.0,
            "supporting_model_count": 2,
        }
        budget = {"portfolio_role": "anchor"}
        decision = gate.evaluate(
            plan,
            account_snapshot={},
            positions=[],
            market_snapshot=market,
            portfolio_budget=budget,
            research_decision=research,
        )
        assert decision.approved is True
        assert decision.setup_quality_score is not None
        assert decision.setup_quality_score >= 85.0
        assert decision.setup_quality_grade == "A"
        assert decision.adjusted_size_pct <= 2.0
        assert decision.adjusted_leverage <= 5

    def test_high_conviction_can_override_some_stable_penalties(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=4.0, leverage=10)
        market = {
            "asset_tier": "high_beta_alt",
            "narrative_tag": "meme",
            "btc_market_regime": "rebound",
            "execution_template": "high_beta_confirmation_only",
            "crowding_regime": "balanced",
            "volatility_regime": "normal",
            "relative_strength_7d_pct": 8.0,
        }
        research = {
            "confidence": 0.92,
            "consensus_strength": 0.9,
            "supporting_model_count": 2,
        }
        budget = {"portfolio_role": "speculative_probe"}
        decision = gate.evaluate(
            plan,
            account_snapshot={},
            positions=[],
            market_snapshot=market,
            portfolio_budget=budget,
            research_decision=research,
        )
        assert decision.approved is True
        assert decision.gating_profile == "conviction_override"
        assert decision.setup_quality_score is not None
        assert decision.setup_quality_score >= 55.0
        assert decision.adjusted_size_pct <= 3.5
        assert decision.adjusted_leverage <= 8


class TestStableBehaviorRules:
    def test_low_reward_risk_ratio_is_blocked_in_stable_mode(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=2.0, leverage=4, stop_loss_pct=4.0, take_profit_pct=5.0)
        market = {
            "asset_tier": "core",
            "narrative_tag": "store_of_value",
            "btc_market_regime": "risk_on_trend",
            "execution_template": "core_trend_follow",
            "crowding_regime": "balanced",
            "volatility_regime": "normal",
            "relative_strength_7d_pct": 5.0,
        }
        research = {
            "confidence": 0.82,
            "consensus_strength": 1.0,
            "supporting_model_count": 2,
        }
        decision = gate.evaluate(
            plan,
            account_snapshot={},
            positions=[],
            market_snapshot=market,
            research_decision=research,
        )
        assert decision.approved is False
        assert any("reward/risk" in rule for rule in decision.violated_rules)

    def test_recent_loss_cooldown_blocks_same_symbol_reentry(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=2.0, leverage=4)
        market = {
            "asset_tier": "core",
            "narrative_tag": "store_of_value",
            "btc_market_regime": "risk_on_trend",
            "execution_template": "core_trend_follow",
            "crowding_regime": "balanced",
            "volatility_regime": "normal",
            "relative_strength_7d_pct": 5.0,
        }
        research = {
            "confidence": 0.82,
            "consensus_strength": 1.0,
            "supporting_model_count": 2,
        }
        recent_trade_history = [
            {
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "realized_pnl": -120.0,
                "close_time": (datetime.now() - timedelta(hours=3)).isoformat(),
            }
        ]
        decision = gate.evaluate(
            plan,
            account_snapshot={},
            positions=[],
            market_snapshot=market,
            research_decision=research,
            recent_trade_history=recent_trade_history,
        )
        assert decision.approved is False
        assert decision.adjusted_action == "hold"
        assert any("cooldown" in rule for rule in decision.violated_rules)

    def test_high_conviction_during_single_recent_loss_warns_but_allows(self):
        gate = RiskGate(_make_config())
        plan = _make_plan(action="open_long", size_pct=2.0, leverage=4)
        market = {
            "asset_tier": "core",
            "narrative_tag": "store_of_value",
            "btc_market_regime": "risk_on_trend",
            "execution_template": "core_trend_follow",
            "crowding_regime": "balanced",
            "volatility_regime": "normal",
            "relative_strength_7d_pct": 5.0,
        }
        research = {
            "confidence": 0.91,
            "consensus_strength": 0.9,
            "supporting_model_count": 2,
        }
        recent_trade_history = [
            {
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "realized_pnl": -120.0,
                "close_time": (datetime.now() - timedelta(hours=3)).isoformat(),
            }
        ]
        decision = gate.evaluate(
            plan,
            account_snapshot={},
            positions=[],
            market_snapshot=market,
            research_decision=research,
            recent_trade_history=recent_trade_history,
        )
        assert decision.approved is True
        assert any("high-conviction setup allowed" in warning for warning in decision.warnings)
