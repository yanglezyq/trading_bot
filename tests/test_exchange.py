"""
Tests for exchange module.
"""

import pytest

from trading.core import AppConfig, BinanceConfig
from trading.exchange import AccountManager, BinanceClient, PositionManager


@pytest.fixture
def mock_binance_config():
    """Create mock Binance config for testing."""
    return BinanceConfig(
        api_key="test_key",
        api_secret="test_secret",
        futures_testnet=True,
    )


@pytest.fixture
def binance_client_dry_run(mock_binance_config):
    """Create Binance client in dry_run mode."""
    return BinanceClient(mock_binance_config, dry_run=True)


class TestBinanceClient:
    """Test BinanceClient."""

    def test_client_initialization(self, binance_client_dry_run):
        """Test client can be initialized."""
        assert binance_client_dry_run is not None
        assert binance_client_dry_run.dry_run is True

    def test_client_requires_credentials(self):
        """Test client requires API credentials."""
        config = BinanceConfig(api_key="", api_secret="")
        with pytest.raises(ValueError):
            BinanceClient(config)

    def test_dry_run_mode(self, binance_client_dry_run):
        """Test dry_run mode returns mock data."""
        status = binance_client_dry_run.get_api_status()
        assert status["dry_run"] is True


class TestAccountManager:
    """Test AccountManager."""

    def test_get_futures_balance_dry_run(self, binance_client_dry_run):
        """Test getting futures balance in dry_run mode."""
        mgr = AccountManager(binance_client_dry_run)
        balance = mgr.get_futures_balance()
        assert balance == 10000.0

    def test_get_spot_balance_dry_run(self, binance_client_dry_run):
        """Test getting spot balance in dry_run mode."""
        mgr = AccountManager(binance_client_dry_run)
        balance = mgr.get_spot_balance("USDT")
        assert balance == 5000.0

    def test_get_account_summary_dry_run(self, binance_client_dry_run):
        """Test getting account summary in dry_run mode."""
        mgr = AccountManager(binance_client_dry_run)
        summary = mgr.get_account_summary()
        assert "futures_balance_usdt" in summary
        assert "spot_balance_usdt" in summary
        assert "total_balance_usdt" in summary
        assert summary["dry_run"] is True


class TestPositionManager:
    """Test PositionManager."""

    def test_get_futures_positions_dry_run(self, binance_client_dry_run):
        """Test getting futures positions in dry_run mode."""
        mgr = PositionManager(binance_client_dry_run)
        positions = mgr.get_futures_positions()
        assert len(positions) == 2
        assert positions[0].symbol == "CHZUSDT"
        assert positions[0].is_long is True

    def test_get_spot_holdings_dry_run(self, binance_client_dry_run):
        """Test getting spot holdings in dry_run mode."""
        mgr = PositionManager(binance_client_dry_run)
        holdings = mgr.get_spot_holdings()
        assert len(holdings) >= 1
        assert any(h.asset == "USDT" for h in holdings)

    def test_get_all_positions_dry_run(self, binance_client_dry_run):
        """Test getting all positions in dry_run mode."""
        mgr = PositionManager(binance_client_dry_run)
        all_pos = mgr.get_all_positions()
        assert "futures" in all_pos
        assert "spot" in all_pos
        assert len(all_pos["futures"]) > 0

    def test_positions_summary(self, binance_client_dry_run):
        """Test positions summary calculation."""
        mgr = PositionManager(binance_client_dry_run)
        summary = mgr.get_positions_summary()
        assert "futures_count" in summary
        assert "total_notional_usdt" in summary
        assert "total_unrealized_pnl" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
