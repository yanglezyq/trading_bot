"""Tests for public crypto market snapshot and crypto-specific risk rules."""

from unittest.mock import MagicMock

import pytest

from trading.core.config import (
    AppConfig, BinanceConfig, ClaudeConfig, LoggingConfig,
    MonitorConfig, RiskConfig, TradingConfig, VaultConfig,
)
from trading.exchange.market_data import MarketDataManager
from trading.risk.gate import RiskGate
from trading.ai.schemas import ExecutionPlan


@pytest.fixture
def market_config(tmp_path):
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(
            high_volatility_24h_pct=8.0,
            extreme_move_24h_pct=12.0,
            max_leverage_high_vol=10,
            max_position_size_high_vol_pct=0.02,
            max_abs_funding_rate=0.0010,
        ),
        binance=BinanceConfig(api_key="", api_secret="", futures_testnet=True),
        claude=ClaudeConfig(),
        trading=TradingConfig(max_position_size_pct=0.05),
        monitor=MonitorConfig(),
        logging=LoggingConfig(sqlite_db=str(tmp_path / "test.db")),
    )


class TestMarketDataManager:
    def test_mock_snapshot(self, market_config):
        mgr = MarketDataManager(market_config.binance, dry_run=True)
        snap = mgr.get_market_snapshot("BTCUSDT")
        assert snap.symbol == "BTCUSDT"
        assert snap.spot_price > 0
        assert snap.volatility_regime in {"low", "normal", "high", "extreme"}
        assert snap.hourly_trend_bias in {"bullish", "bearish", "range"}

    def test_live_snapshot_computes_metrics_from_clients(self, market_config):
        mgr = MarketDataManager(market_config.binance, dry_run=False)
        mgr.spot_client = MagicMock()
        mgr.futures_client = MagicMock()

        mgr.spot_client.ticker_price.return_value = {"price": "100"}
        mgr.futures_client.mark_price.return_value = {
            "markPrice": "100.5",
            "lastFundingRate": "0.0008",
        }
        mgr.futures_client.ticker_24hr.return_value = {
            "priceChangePercent": "5.0",
            "highPrice": "110",
            "lowPrice": "95",
            "quoteVolume": "250000000",
        }
        mgr.futures_client.open_interest.return_value = {"openInterest": "1234567"}
        mgr.futures_client.klines.return_value = [
            [0, "100", "101", "99", str(100 + i), "0", 0, "0", 0, "0", "0", "0"]
            for i in range(168)
        ]

        snap = mgr.get_market_snapshot("BTCUSDT")
        assert snap.spot_price == 100.0
        assert snap.futures_mark_price == 100.5
        assert snap.funding_rate == 0.0008
        assert snap.open_interest == 1234567.0
        assert snap.quote_volume_24h_usdt == 250000000.0
        assert snap.momentum_regime in {"up", "strong_up"}
        assert snap.ema_21_1h > 0
        assert snap.hourly_trend_bias in {"bullish", "bearish", "range"}

    def test_alt_snapshot_includes_btc_relative_context(self, market_config):
        mgr = MarketDataManager(market_config.binance, dry_run=True)
        snap = mgr.get_market_snapshot("ETHUSDT")
        assert snap.benchmark_symbol == "BTCUSDT"
        assert snap.relative_strength_24h_pct is not None
        assert snap.btc_market_regime in {"risk_on_trend", "risk_off_trend", "panic_flush", "rebound", "short_squeeze", "range"}
        assert snap.asset_tier in {"core", "major_alt", "liquid_alt", "mid_alt", "high_beta_alt"}
        assert snap.crowding_regime in {"balanced", "heavy_positioning", "crowded_long", "crowded_short"}
        assert snap.execution_template is not None

    def test_live_snapshot_classifies_high_beta_and_crowding(self, market_config):
        mgr = MarketDataManager(market_config.binance, dry_run=False)
        mgr.spot_client = MagicMock()
        mgr.futures_client = MagicMock()

        mgr.spot_client.ticker_price.return_value = {"price": "1.0"}
        mgr.futures_client.mark_price.return_value = {
            "markPrice": "1.01",
            "lastFundingRate": "0.0015",
        }
        mgr.futures_client.ticker_24hr.return_value = {
            "priceChangePercent": "9.0",
            "highPrice": "1.20",
            "lowPrice": "0.80",
            "quoteVolume": "20000000",
        }
        mgr.futures_client.open_interest.return_value = {"openInterest": "20000000"}
        mgr.futures_client.klines.return_value = [
            [0, "1.0", "1.0", "1.0", "1.0", "0", 0, "0", 0, "0", "0", "0"]
            for _ in range(167)
        ] + [[0, "1.0", "1.2", "0.9", "1.1", "0", 0, "0", 0, "0", "0", "0"]]

        snap = mgr.get_market_snapshot("SOMEALTUSDT")
        assert snap.asset_tier == "high_beta_alt"
        assert snap.crowding_regime in {"crowded_long", "heavy_positioning"}

    def test_btc_regime_state_machine_detects_panic_flush(self, market_config):
        mgr = MarketDataManager(market_config.binance, dry_run=False)
        mgr.spot_client = MagicMock()
        mgr.futures_client = MagicMock()

        mgr.spot_client.ticker_price.return_value = {"price": "100"}
        mgr.futures_client.mark_price.return_value = {
            "markPrice": "95",
            "lastFundingRate": "-0.0008",
        }
        mgr.futures_client.ticker_24hr.return_value = {
            "priceChangePercent": "-9.0",
            "highPrice": "110",
            "lowPrice": "90",
            "quoteVolume": "1200000000",
        }
        mgr.futures_client.open_interest.return_value = {"openInterest": "10000000"}
        downward = []
        price = 120.0
        for _ in range(168):
            price -= 0.15
            downward.append([0, "0", "0", "0", str(price), "0", 0, "0", 0, "0", "0", "0"])
        mgr.futures_client.klines.return_value = downward

        snap = mgr.get_market_snapshot("BTCUSDT")
        assert snap.btc_market_regime in {"panic_flush", "risk_off_trend"}
        assert snap.execution_template is not None


class TestCryptoRiskRules:
    def test_high_volatility_caps_size_and_leverage(self, market_config):
        gate = RiskGate(market_config)
        plan = ExecutionPlan(
            action="open_long",
            size_pct=5.0,
            leverage=20,
            entry_idea="breakout",
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            rationale="momentum",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={"drawdown_pct": 0.0},
            positions=[],
            market_snapshot={
                "realized_vol_24h_pct": 12.0,
                "price_change_24h_pct": 14.0,
                "funding_rate": 0.0012,
                "basis_bps": 120.0,
            },
        )
        assert decision.approved is True
        assert decision.adjusted_leverage == 10
        assert decision.adjusted_size_pct == 2.0
        assert any("volatility" in w.lower() for w in decision.warnings)
        assert any("Funding rate" in w for w in decision.warnings)

    def test_price_geometry_warning_for_bad_long_levels(self, market_config):
        gate = RiskGate(market_config)
        plan = ExecutionPlan(
            action="open_long",
            size_pct=3.0,
            leverage=5,
            entry_idea="buy breakout",
            entry_style="breakout_confirmation",
            entry_zone_low=104.0,
            entry_zone_high=103.0,
            trigger_price=100.0,
            invalidation_price=101.0,
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            thesis_window_hours=12,
            rationale="momentum",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={"drawdown_pct": 0.0},
            positions=[],
            market_snapshot={
                "spot_price": 98.0,
                "realized_vol_24h_pct": 3.0,
                "price_change_24h_pct": 2.0,
                "funding_rate": 0.0,
                "basis_bps": 10.0,
            },
        )
        assert any("entry_zone_low" in w for w in decision.warnings)
        assert any("invalidation_price" in w for w in decision.warnings)

    def test_altcoin_long_reduced_when_btc_regime_risk_off(self, market_config):
        gate = RiskGate(market_config)
        plan = ExecutionPlan(
            action="open_long",
            size_pct=5.0,
            leverage=12,
            entry_idea="buy dip",
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            rationale="alt setup",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={"drawdown_pct": 0.0},
            positions=[],
            market_snapshot={
                "benchmark_symbol": "BTCUSDT",
                "btc_market_regime": "risk_off",
                "relative_strength_7d_pct": -8.0,
                "hourly_trend_bias": "bullish",
                "realized_vol_24h_pct": 5.0,
                "price_change_24h_pct": -1.0,
                "funding_rate": 0.0,
                "basis_bps": 10.0,
            },
        )
        assert decision.approved is True
        assert decision.adjusted_leverage == market_config.risk.altcoin_max_leverage_when_btc_weak
        assert decision.adjusted_size_pct == market_config.risk.altcoin_max_position_size_when_btc_weak_pct * 100
        assert any("BTC regime is risk-off" in w for w in decision.warnings)
        assert any("underperformed BTC" in w for w in decision.warnings)

    def test_high_beta_alt_gets_tighter_caps(self, market_config):
        gate = RiskGate(market_config)
        plan = ExecutionPlan(
            action="open_long",
            size_pct=4.0,
            leverage=8,
            entry_idea="alt breakout",
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            rationale="high beta setup",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={"drawdown_pct": 0.0},
            positions=[],
            market_snapshot={
                "asset_tier": "high_beta_alt",
                "hourly_trend_bias": "bullish",
                "realized_vol_24h_pct": 5.0,
                "price_change_24h_pct": 3.0,
                "funding_rate": 0.0,
                "basis_bps": 0.0,
            },
        )
        assert decision.adjusted_leverage == market_config.risk.high_beta_alt_max_leverage
        assert decision.adjusted_size_pct == market_config.risk.high_beta_alt_max_position_size_pct * 100
        assert any("high_beta_alt" in w for w in decision.warnings)

    def test_crowded_long_reduces_crypto_risk(self, market_config):
        gate = RiskGate(market_config)
        plan = ExecutionPlan(
            action="open_long",
            size_pct=5.0,
            leverage=10,
            entry_idea="late breakout",
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            rationale="crowded long setup",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={"drawdown_pct": 0.0},
            positions=[],
            market_snapshot={
                "asset_tier": "major_alt",
                "crowding_regime": "crowded_long",
                "oi_to_volume_ratio": 1.2,
                "hourly_trend_bias": "bullish",
                "realized_vol_24h_pct": 6.0,
                "price_change_24h_pct": 8.0,
                "funding_rate": 0.0015,
                "basis_bps": 120.0,
            },
        )
        assert decision.adjusted_size_pct <= 2.5
        assert decision.adjusted_leverage <= 5
        assert any("crowded long" in w.lower() for w in decision.warnings)

    def test_btc_rebound_warns_against_new_alt_shorts(self, market_config):
        gate = RiskGate(market_config)
        plan = ExecutionPlan(
            action="open_short",
            size_pct=3.0,
            leverage=5,
            entry_idea="fade weak alt",
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
            rationale="short alt",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={"drawdown_pct": 0.0},
            positions=[],
            market_snapshot={
                "benchmark_symbol": "BTCUSDT",
                "btc_market_regime": "rebound",
                "relative_strength_7d_pct": 2.0,
                "hourly_trend_bias": "bearish",
                "realized_vol_24h_pct": 5.0,
                "price_change_24h_pct": -1.0,
                "funding_rate": 0.0,
                "basis_bps": 0.0,
            },
        )
        assert any("rebound mode" in w for w in decision.warnings)

    def test_bearish_trend_warns_against_long(self, market_config):
        gate = RiskGate(market_config)
        plan = ExecutionPlan(
            action="open_long",
            size_pct=2.0,
            leverage=3,
            entry_idea="mean reversion",
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
            rationale="countertrend",
        )
        decision = gate.evaluate(
            plan,
            account_snapshot={"drawdown_pct": 0.0},
            positions=[],
            market_snapshot={
                "hourly_trend_bias": "bearish",
                "realized_vol_24h_pct": 4.0,
                "price_change_24h_pct": -2.0,
                "funding_rate": 0.0,
                "basis_bps": 0.0,
            },
        )
        assert any("bearish" in w.lower() for w in decision.warnings)
