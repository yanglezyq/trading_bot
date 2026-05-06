"""Tests for portfolio-level sizing and concentration logic."""

from trading.core.config import (
    AppConfig, BinanceConfig, ClaudeConfig, LoggingConfig,
    MonitorConfig, RiskConfig, TradingConfig, VaultConfig,
)
from trading.risk.portfolio import PortfolioManager


def _config(tmp_path):
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(),
        binance=BinanceConfig(),
        claude=ClaudeConfig(),
        trading=TradingConfig(max_position_size_pct=0.05),
        monitor=MonitorConfig(),
        logging=LoggingConfig(sqlite_db=str(tmp_path / "test.db")),
    )


def test_portfolio_snapshot_aggregates_exposure(tmp_path):
    mgr = PortfolioManager(_config(tmp_path))
    snapshot = mgr.build_snapshot(
        {"total_balance_usdt": 10_000.0, "futures_balance_usdt": 8_000.0},
        [
            {"symbol": "BTCUSDT", "direction": "LONG", "amount": 0.1, "price": 50_000.0},
            {"symbol": "ETHUSDT", "direction": "SHORT", "amount": -2.0, "price": 3_000.0},
        ],
    )
    assert round(snapshot.gross_notional_usdt, 2) == 11_000.0
    assert snapshot.position_count == 2
    assert snapshot.narrative_counts["store_of_value"] == 1


def test_portfolio_budget_caps_same_narrative(tmp_path):
    mgr = PortfolioManager(_config(tmp_path))
    budget = mgr.recommend_budget(
        symbol="MKRUSDT",
        market_snapshot={"asset_tier": "major_alt", "narrative_tag": "defi", "btc_market_regime": "range"},
        positions=[
            {"symbol": "AAVEUSDT", "direction": "LONG", "amount": 10, "price": 100},
            {"symbol": "UNIUSDT", "direction": "LONG", "amount": 20, "price": 10},
        ],
        account_summary={"total_balance_usdt": 10_000.0, "futures_balance_usdt": 8_000.0},
    )
    assert budget.narrative_position_count >= 2
    assert budget.recommended_max_size_pct <= 1.5
    assert budget.warnings
