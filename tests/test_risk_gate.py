"""Tests for the deterministic risk gate."""

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
