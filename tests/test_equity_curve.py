"""Tests for equity curve protection."""

import pytest

from trading.core.config import (
    AppConfig, BinanceConfig, ClaudeConfig, LoggingConfig,
    MonitorConfig, RiskConfig, TradingConfig, VaultConfig,
)
from trading.pipeline.persistence import TradeRunDB
from trading.risk.equity_curve import EquityCurveTracker
from trading.risk.gate import RiskGate
from trading.ai.schemas import ExecutionPlan


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def config():
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(
            equity_protection_enabled=True,
            equity_ema_period=5,
            equity_pause_factor=0.95,
            equity_reduce_factor=0.5,
        ),
        binance=BinanceConfig(),
        claude=ClaudeConfig(),
        trading=TradingConfig(max_position_size_pct=0.05),
        monitor=MonitorConfig(),
        logging=LoggingConfig(),
    )


class TestEquityCurveTracker:
    def test_insufficient_history_returns_normal(self, config, tmp_db):
        db = TradeRunDB(tmp_db)
        tracker = EquityCurveTracker(config, db)
        # Only 3 data points, period=5 -> not enough
        for b in [10000, 10100, 10200]:
            tracker.record_balance(b)
        status = tracker.get_protection_status(10300)
        assert status["mode"] == "normal"
        assert status["ema"] is None

    def test_normal_mode_when_above_ema(self, config, tmp_db):
        db = TradeRunDB(tmp_db)
        tracker = EquityCurveTracker(config, db)
        # Create 5+ history points at 10000
        for i in range(7):
            tracker.record_balance(10000.0)
        status = tracker.get_protection_status(10500.0)
        assert status["mode"] == "normal"
        assert status["factor"] == 1.0

    def test_reduced_mode_when_below_ema(self, config, tmp_db):
        db = TradeRunDB(tmp_db)
        tracker = EquityCurveTracker(config, db)
        # History around 10000
        for i in range(7):
            tracker.record_balance(10000.0)
        # Current balance below EMA but above pause threshold (EMA*0.95)
        status = tracker.get_protection_status(9800.0)
        assert status["mode"] == "reduced"
        assert status["factor"] == 0.5

    def test_paused_mode_when_far_below_ema(self, config, tmp_db):
        db = TradeRunDB(tmp_db)
        tracker = EquityCurveTracker(config, db)
        # History around 10000
        for i in range(7):
            tracker.record_balance(10000.0)
        # Current balance far below (< EMA * 0.95 = 9500)
        status = tracker.get_protection_status(9000.0)
        assert status["mode"] == "paused"
        assert status["factor"] == 0.0

    def test_disabled_always_returns_normal(self, tmp_db):
        disabled_config = AppConfig(
            vault=VaultConfig(path="/tmp"),
            risk=RiskConfig(equity_protection_enabled=False),
            binance=BinanceConfig(),
            claude=ClaudeConfig(),
            trading=TradingConfig(),
            monitor=MonitorConfig(),
            logging=LoggingConfig(),
        )
        db = TradeRunDB(tmp_db)
        tracker = EquityCurveTracker(disabled_config, db)
        for i in range(10):
            tracker.record_balance(10000.0)
        status = tracker.get_protection_status(5000.0)
        assert status["mode"] == "normal"


class TestEquityCurveRule:
    def test_reduced_mode_reduces_size(self, config):
        gate = RiskGate(config)
        plan = ExecutionPlan(
            action="open_long", size_pct=4.0, leverage=10,
            entry_idea="", stop_loss_pct=5.0, take_profit_pct=15.0, rationale="",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={
                "drawdown_pct": 0.0,
                "equity_curve_status": {"mode": "reduced", "ema": 10000.0, "factor": 0.5},
            },
            positions=[],
        )
        # size should be halved by equity curve rule
        assert decision.adjusted_size_pct <= 2.0
        assert any("Equity curve" in w for w in decision.warnings)

    def test_paused_mode_blocks_trading(self, config):
        gate = RiskGate(config)
        plan = ExecutionPlan(
            action="open_long", size_pct=3.0, leverage=5,
            entry_idea="", stop_loss_pct=5.0, take_profit_pct=15.0, rationale="",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={
                "drawdown_pct": 0.0,
                "equity_curve_status": {"mode": "paused", "ema": 10000.0, "factor": 0.0},
            },
            positions=[],
        )
        assert decision.approved is False
        assert decision.adjusted_action == "hold"
        assert decision.adjusted_size_pct == 0.0

    def test_normal_mode_no_impact(self, config):
        gate = RiskGate(config)
        plan = ExecutionPlan(
            action="open_long", size_pct=3.0, leverage=5,
            entry_idea="", stop_loss_pct=5.0, take_profit_pct=15.0, rationale="",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={
                "drawdown_pct": 0.0,
                "equity_curve_status": {"mode": "normal", "ema": 10000.0, "factor": 1.0},
            },
            positions=[],
        )
        assert not any("Equity curve" in w for w in decision.warnings)
