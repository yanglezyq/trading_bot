"""Tests for onchain data layer: DeFiLlamaClient and OnchainDataManager."""

from unittest.mock import MagicMock, patch

import pytest

from src.trading.onchain.defillama import DeFiLlamaClient
from src.trading.onchain.manager import OnchainDataManager, OnchainSnapshot


# ---------------------------------------------------------------------------
# DeFiLlamaClient
# ---------------------------------------------------------------------------

class TestDeFiLlamaClient:
    def _client(self) -> DeFiLlamaClient:
        return DeFiLlamaClient(timeout=1.0)

    def test_get_protocol_returns_dict(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "name": "Uniswap",
            "category": "Dexes",
            "tvl": 3_500_000_000.0,
            "chains": ["Ethereum", "Arbitrum"],
            "change_1d": 1.5,
            "change_7d": -3.2,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            result = client.get_protocol("uniswap")

        assert result is not None
        assert result["name"] == "Uniswap"
        assert result["category"] == "Dexes"

    def test_get_protocol_returns_none_on_error(self):
        client = self._client()
        with patch("httpx.get", side_effect=Exception("timeout")):
            result = client.get_protocol("uniswap")
        assert result is None

    def test_get_protocol_tvl_returns_float(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = 3_500_000_000.0
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            tvl = client.get_protocol_tvl("uniswap")

        assert tvl == pytest.approx(3_500_000_000.0)

    def test_get_protocol_tvl_returns_none_on_non_numeric(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "not found"}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            tvl = client.get_protocol_tvl("unknown-slug")

        assert tvl is None

    def test_get_chain_tvl_matches_by_name(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"name": "Ethereum", "tvl": 50_000_000_000.0},
            {"name": "Solana", "tvl": 5_000_000_000.0},
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            tvl = client.get_chain_tvl("Ethereum")

        assert tvl == pytest.approx(50_000_000_000.0)

    def test_get_chain_tvl_case_insensitive(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"name": "Ethereum", "tvl": 50e9}]
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_chain_tvl("ethereum") == pytest.approx(50e9)

    def test_get_chain_tvl_returns_none_for_unknown(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"name": "Ethereum", "tvl": 50e9}]
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_chain_tvl("FantomChainXYZ") is None

    def test_get_stablecoin_total_sums_supply(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "peggedAssets": [
                {"circulating": {"peggedUSD": 80_000_000_000.0}},
                {"circulating": {"peggedUSD": 30_000_000_000.0}},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            result = client.get_stablecoin_total()

        assert result is not None
        assert result["total_usd"] == pytest.approx(110_000_000_000.0)
        assert result["count"] == 2

    def test_get_stablecoin_total_returns_none_on_error(self):
        client = self._client()
        with patch("httpx.get", side_effect=Exception("network")):
            result = client.get_stablecoin_total()
        assert result is None


# ---------------------------------------------------------------------------
# OnchainDataManager
# ---------------------------------------------------------------------------

class TestOnchainDataManager:

    def _protocol_data(self) -> dict:
        return {
            "name": "Aave",
            "category": "Lending",
            "tvl": 10_000_000_000.0,
            "chains": ["Ethereum", "Polygon", "Arbitrum"],
            "change_1d": 2.0,
            "change_7d": -1.5,
        }

    def test_snapshot_with_known_defi_protocol(self):
        mgr = OnchainDataManager()
        with patch.object(mgr._client, "get_protocol", return_value=self._protocol_data()), \
             patch.object(mgr._client, "get_stablecoin_total", return_value={"total_usd": 150e9, "count": 10}):
            snap = mgr.get_snapshot("AAVEUSDT")

        assert snap.symbol == "AAVEUSDT"
        assert snap.protocol_name == "Aave"
        assert snap.protocol_category == "Lending"
        assert snap.protocol_tvl_usd == pytest.approx(10e9)
        assert snap.tvl_change_24h_pct == pytest.approx(2.0)
        assert snap.tvl_change_7d_pct == pytest.approx(-1.5)
        assert "Ethereum" in snap.protocol_chains
        assert snap.stablecoin_total_usd == pytest.approx(150e9)
        assert snap.has_onchain_data is True

    def test_snapshot_with_l1_chain(self):
        mgr = OnchainDataManager()
        with patch.object(mgr._client, "get_chain_tvl", return_value=50e9), \
             patch.object(mgr._client, "get_stablecoin_total", return_value={"total_usd": 150e9, "count": 10}):
            snap = mgr.get_snapshot("ETHUSDT")

        assert snap.chain_name == "Ethereum"
        assert snap.chain_tvl_usd == pytest.approx(50e9)
        assert snap.protocol_tvl_usd is None  # ETH has no protocol slug
        assert snap.has_onchain_data is True

    def test_snapshot_stablecoin_always_fetched(self):
        mgr = OnchainDataManager()
        with patch.object(mgr._client, "get_stablecoin_total", return_value={"total_usd": 120e9, "count": 8}), \
             patch.object(mgr._client, "get_chain_tvl", return_value=None):
            snap = mgr.get_snapshot("BTCUSDT")

        assert snap.stablecoin_total_usd == pytest.approx(120e9)
        assert snap.has_onchain_data is True

    def test_snapshot_graceful_on_all_failures(self):
        mgr = OnchainDataManager()
        with patch.object(mgr._client, "get_protocol", return_value=None), \
             patch.object(mgr._client, "get_chain_tvl", return_value=None), \
             patch.object(mgr._client, "get_stablecoin_total", return_value=None):
            snap = mgr.get_snapshot("AAVEUSDT")

        assert snap.has_onchain_data is False
        assert snap.protocol_tvl_usd is None

    def test_snapshot_never_raises(self):
        mgr = OnchainDataManager()
        with patch.object(mgr._client, "get_protocol", side_effect=Exception("boom")), \
             patch.object(mgr._client, "get_chain_tvl", side_effect=Exception("boom")), \
             patch.object(mgr._client, "get_stablecoin_total", side_effect=Exception("boom")):
            snap = mgr.get_snapshot("UNIUSDT")

        assert isinstance(snap, OnchainSnapshot)

    def test_to_dict_is_serialisable(self):
        import json
        mgr = OnchainDataManager()
        with patch.object(mgr._client, "get_protocol", return_value=self._protocol_data()), \
             patch.object(mgr._client, "get_stablecoin_total", return_value={"total_usd": 150e9, "count": 10}):
            snap = mgr.get_snapshot("AAVEUSDT")

        d = snap.to_dict()
        assert json.dumps(d)  # must not raise
        assert d["symbol"] == "AAVEUSDT"
        assert d["has_onchain_data"] is True

    def test_data_sources_populated(self):
        mgr = OnchainDataManager()
        with patch.object(mgr._client, "get_protocol", return_value=self._protocol_data()), \
             patch.object(mgr._client, "get_stablecoin_total", return_value={"total_usd": 150e9, "count": 10}):
            snap = mgr.get_snapshot("AAVEUSDT")

        assert "defillama_protocol" in snap.data_sources
        assert "defillama_stable" in snap.data_sources
