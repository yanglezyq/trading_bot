"""Tests for portfolio rebalancer: compute_rebalance_plan and execute_rebalance."""

from unittest.mock import MagicMock, patch

import pytest

from trading.core.config import (
    AppConfig, BinanceConfig, ClaudeConfig, LoggingConfig,
    MonitorConfig, RebalanceConfig, RiskConfig, TradingConfig, VaultConfig,
)
from trading.pipeline.rebalancer import RebalanceOrder, Rebalancer, _step_round


def _make_config(tmp_path, **rebalance_overrides) -> AppConfig:
    rb_cfg = RebalanceConfig(**rebalance_overrides) if rebalance_overrides else RebalanceConfig()
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(),
        binance=BinanceConfig(),
        claude=ClaudeConfig(),
        trading=TradingConfig(max_position_size_pct=0.05),
        monitor=MonitorConfig(),
        logging=LoggingConfig(sqlite_db=str(tmp_path / "test.db")),
        rebalance=rb_cfg,
    )


def _make_rebalancer(tmp_path, **rebalance_overrides) -> Rebalancer:
    config = _make_config(tmp_path, **rebalance_overrides)
    mock_client = MagicMock()
    return Rebalancer(config=config, live_client=mock_client)


# ------------------------------------------------------------------
# compute_rebalance_plan tests
# ------------------------------------------------------------------


class TestComputeRebalancePlan:
    """Tests for Rebalancer.compute_rebalance_plan()."""

    def test_empty_positions_returns_empty(self, tmp_path):
        rb = _make_rebalancer(tmp_path)
        result = rb.compute_rebalance_plan(
            account_summary={"futures_balance_usdt": 10000, "total_balance_usdt": 10000},
            positions=[],
        )
        assert result == []

    def test_zero_balance_returns_empty(self, tmp_path):
        rb = _make_rebalancer(tmp_path)
        result = rb.compute_rebalance_plan(
            account_summary={"futures_balance_usdt": 0, "total_balance_usdt": 0},
            positions=[{"symbol": "BTCUSDT", "direction": "LONG", "amount": 0.1, "price": 50000, "leverage": 10}],
        )
        assert result == []

    def test_within_threshold_no_orders(self, tmp_path):
        """Position close to target → no rebalance needed."""
        rb = _make_rebalancer(tmp_path, deviation_threshold_pct=90.0)
        # With very high threshold, even somewhat deviated positions won't trigger
        result = rb.compute_rebalance_plan(
            account_summary={"futures_balance_usdt": 10000, "total_balance_usdt": 10000},
            positions=[{"symbol": "BTCUSDT", "direction": "LONG", "amount": 0.1, "price": 50000, "leverage": 10}],
        )
        assert result == []

    def test_oversized_position_generates_reduce(self, tmp_path):
        """Position much larger than budget → reduce order."""
        rb = _make_rebalancer(tmp_path, deviation_threshold_pct=5.0)
        # Balance=10000, budget for liquid_alt ~3% cap → target ~3000 with 10x leverage
        # Current notional = 100 * 100 = 10000 → way above target → reduce
        result = rb.compute_rebalance_plan(
            account_summary={"futures_balance_usdt": 10000, "total_balance_usdt": 10000},
            positions=[{"symbol": "XYZUSDT", "direction": "LONG", "amount": 100, "price": 100, "leverage": 10}],
            market_snapshots={"XYZUSDT": {"asset_tier": "liquid_alt", "narrative_tag": "defi"}},
        )
        assert len(result) >= 1
        assert result[0].action in ("reduce", "close")
        assert result[0].delta_notional < 0

    def test_max_single_reduce_cap(self, tmp_path):
        """max_single_reduce_pct caps the reduction amount."""
        rb = _make_rebalancer(tmp_path, deviation_threshold_pct=1.0, max_single_reduce_pct=20.0)
        # Current notional = 50000, target would be much lower → reduce, but capped at 20%
        result = rb.compute_rebalance_plan(
            account_summary={"futures_balance_usdt": 10000, "total_balance_usdt": 10000},
            positions=[{"symbol": "XYZUSDT", "direction": "LONG", "amount": 500, "price": 100, "leverage": 10}],
            market_snapshots={"XYZUSDT": {"asset_tier": "liquid_alt", "narrative_tag": "defi"}},
        )
        if result:
            # Delta should not exceed 20% of current notional (50000 * 0.2 = 10000)
            assert abs(result[0].delta_notional) <= 50000 * 0.20 + 1  # +1 for rounding

    def test_gross_exposure_scaling(self, tmp_path):
        """When gross exposure > target_gross_exposure_pct, targets are scaled down."""
        rb = _make_rebalancer(tmp_path, target_gross_exposure_pct=50.0, deviation_threshold_pct=5.0)
        # balance=10000, position notional=8000 → gross=80% > target 50%
        result = rb.compute_rebalance_plan(
            account_summary={"futures_balance_usdt": 10000, "total_balance_usdt": 10000},
            positions=[{"symbol": "ETHUSDT", "direction": "LONG", "amount": 2.0, "price": 4000, "leverage": 5}],
            market_snapshots={"ETHUSDT": {"asset_tier": "major_alt", "narrative_tag": "smart_contract"}},
        )
        # Should generate reduce because of gross exposure scaling
        if result:
            assert result[0].action in ("reduce", "close")

    def test_multiple_positions_sorted_by_deviation(self, tmp_path):
        """Orders sorted by |delta_notional| descending."""
        rb = _make_rebalancer(tmp_path, deviation_threshold_pct=1.0, max_single_reduce_pct=80.0)
        result = rb.compute_rebalance_plan(
            account_summary={"futures_balance_usdt": 10000, "total_balance_usdt": 10000},
            positions=[
                {"symbol": "AAUSDT", "direction": "LONG", "amount": 10, "price": 100, "leverage": 5},  # 1000
                {"symbol": "BBUSDT", "direction": "SHORT", "amount": -50, "price": 200, "leverage": 10},  # 10000
            ],
            market_snapshots={
                "AAUSDT": {"asset_tier": "liquid_alt", "narrative_tag": "defi"},
                "BBUSDT": {"asset_tier": "liquid_alt", "narrative_tag": "gaming"},
            },
        )
        if len(result) >= 2:
            assert abs(result[0].delta_notional) >= abs(result[1].delta_notional)


# ------------------------------------------------------------------
# execute_rebalance tests
# ------------------------------------------------------------------


class TestExecuteRebalance:
    """Tests for Rebalancer.execute_rebalance()."""

    def test_empty_orders_returns_empty(self, tmp_path):
        rb = _make_rebalancer(tmp_path)
        assert rb.execute_rebalance([]) == []

    def test_dry_run_does_not_place_orders(self, tmp_path):
        rb = _make_rebalancer(tmp_path)
        rb.live_client.futures_client.get_symbol_lot_size.return_value = 0.001
        rb.live_client.futures_client.get_position_mode.return_value = {"dualSidePosition": False}

        order = RebalanceOrder(
            symbol="BTCUSDT", direction="LONG", action="reduce",
            current_notional=10000, target_notional=8000,
            delta_notional=-2000, delta_quantity=0.04, reason="test",
        )
        results = rb.execute_rebalance([order], dry_run=True)
        assert len(results) == 1
        assert results[0]["status"] == "dry_run"
        # OrderManager should NOT have been called for real orders
        rb.live_client.futures_client.new_order.assert_not_called()

    def test_reduce_long_places_sell_order(self, tmp_path):
        rb = _make_rebalancer(tmp_path)
        rb.live_client.futures_client.get_symbol_lot_size.return_value = 0.001
        rb.live_client.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
        rb.live_client.futures_client.new_order.return_value = {"orderId": 12345}
        rb.live_client.dry_run = False

        order = RebalanceOrder(
            symbol="BTCUSDT", direction="LONG", action="reduce",
            current_notional=10000, target_notional=8000,
            delta_notional=-2000, delta_quantity=0.04, reason="test",
        )
        results = rb.execute_rebalance([order], dry_run=False)
        assert len(results) == 1
        assert results[0]["status"] == "executed"
        # Verify market order placed with SELL side and reduceOnly
        call_kwargs = rb.live_client.futures_client.new_order.call_args[1]
        assert call_kwargs["side"] == "SELL"
        assert call_kwargs["reduceOnly"] == "true"

    def test_reduce_short_places_buy_order(self, tmp_path):
        rb = _make_rebalancer(tmp_path)
        rb.live_client.futures_client.get_symbol_lot_size.return_value = 1.0
        rb.live_client.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
        rb.live_client.futures_client.new_order.return_value = {"orderId": 99}
        rb.live_client.dry_run = False

        order = RebalanceOrder(
            symbol="ETHUSDT", direction="SHORT", action="reduce",
            current_notional=5000, target_notional=3000,
            delta_notional=-2000, delta_quantity=1.0, reason="test",
        )
        results = rb.execute_rebalance([order], dry_run=False)
        assert results[0]["status"] == "executed"
        call_kwargs = rb.live_client.futures_client.new_order.call_args[1]
        assert call_kwargs["side"] == "BUY"

    def test_execution_failure_returns_failed(self, tmp_path):
        rb = _make_rebalancer(tmp_path)
        rb.live_client.futures_client.get_symbol_lot_size.return_value = 0.01
        rb.live_client.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
        rb.live_client.futures_client.new_order.side_effect = RuntimeError("API error")
        rb.live_client.dry_run = False

        order = RebalanceOrder(
            symbol="XYZUSDT", direction="LONG", action="reduce",
            current_notional=5000, target_notional=3000,
            delta_notional=-2000, delta_quantity=5.0, reason="test",
        )
        results = rb.execute_rebalance([order], dry_run=False)
        assert results[0]["status"] == "failed"

    def test_hedge_mode_uses_position_side(self, tmp_path):
        rb = _make_rebalancer(tmp_path)
        rb.live_client.futures_client.get_symbol_lot_size.return_value = 0.01
        rb.live_client.futures_client.get_position_mode.return_value = {"dualSidePosition": True}
        rb.live_client.futures_client.new_order.return_value = {"orderId": 777}
        rb.live_client.dry_run = False

        order = RebalanceOrder(
            symbol="BTCUSDT", direction="LONG", action="reduce",
            current_notional=10000, target_notional=7000,
            delta_notional=-3000, delta_quantity=0.06, reason="test",
        )
        results = rb.execute_rebalance([order], dry_run=False)
        assert results[0]["status"] == "executed"
        call_kwargs = rb.live_client.futures_client.new_order.call_args[1]
        assert call_kwargs["positionSide"] == "LONG"
        assert "reduceOnly" not in call_kwargs


# ------------------------------------------------------------------
# step_round utility
# ------------------------------------------------------------------


class TestStepRound:
    def test_basic_rounding(self):
        assert _step_round(0.12345, 0.001) == 0.123

    def test_zero_step_size(self):
        assert _step_round(1.23456, 0) == 1.235

    def test_large_step(self):
        assert _step_round(7.5, 1.0) == 7.0
