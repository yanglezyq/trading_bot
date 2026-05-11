"""CoinGecko free API integration for market cap, rank, and category data.

Uses the free /api/v3 endpoint (no API key required, rate-limited to ~10-30 req/min).
All calls are cached with TTL to avoid hitting rate limits.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Mapping from Binance symbol base to CoinGecko coin ID
_SYMBOL_TO_COINGECKO_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "SHIB": "shiba-inu",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "ATOM": "cosmos",
    "UNI": "uniswap",
    "ARB": "arbitrum",
    "OP": "optimism",
    "NEAR": "near",
    "APT": "aptos",
    "SUI": "sui",
    "TON": "the-open-network",
    "ICP": "internet-computer",
    "FIL": "filecoin",
    "RENDER": "render-token",
    "FET": "artificial-superintelligence-alliance",
    "TAO": "bittensor",
    "INJ": "injective-protocol",
    "SEI": "sei-network",
    "TIA": "celestia",
    "PEPE": "pepe",
    "BONK": "bonk",
    "WIF": "dogwifcoin",
    "FLOKI": "floki",
    "AAVE": "aave",
    "MKR": "maker",
    "CRV": "curve-dao-token",
    "LDO": "lido-dao",
    "EIGEN": "eigenlayer",
    "MATIC": "matic-network",
    "MANTA": "manta-network",
    "IMX": "immutable-x",
    "ONDO": "ondo-finance",
    "WLD": "worldcoin-wld",
    "ARKM": "arkham",
    "STX": "blockstack",
    "PENDLE": "pendle",
    "GMX": "gmx",
    "DYDX": "dydx-chain",
}

# Base URL for free CoinGecko API
_BASE_URL = "https://api.coingecko.com/api/v3"
_REQUEST_TIMEOUT = 10


class CoinGeckoData:
    """Lightweight CoinGecko client with in-memory caching.

    Provides market_cap, market_cap_rank, categories, and 24h volume from CoinGecko.
    """

    def __init__(self, cache_ttl_seconds: float = 600.0):
        """Initialize with cache TTL (default 10 min)."""
        self._cache: dict[str, tuple[float, dict]] = {}
        self._cache_ttl = cache_ttl_seconds
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "trading-bot/1.0",
        })

    def _get_coingecko_id(self, symbol: str) -> Optional[str]:
        """Map Binance symbol to CoinGecko coin ID."""
        base = symbol.upper().replace("USDT", "").replace("USDC", "").replace("BUSD", "")
        return _SYMBOL_TO_COINGECKO_ID.get(base)

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache:
            return False
        ts, _ = self._cache[key]
        return (time.time() - ts) < self._cache_ttl

    def get_market_data(self, symbol: str) -> Optional[dict]:
        """Fetch market data for a symbol from CoinGecko.

        Returns dict with keys:
          - market_cap_usd: float
          - market_cap_rank: int
          - coingecko_volume_24h_usd: float
          - categories: list[str]
          - market_cap_change_24h_pct: float
          - fully_diluted_valuation_usd: float | None

        Returns None if symbol not mapped or API fails.
        """
        coin_id = self._get_coingecko_id(symbol)
        if not coin_id:
            return None

        cache_key = f"market_{coin_id}"
        if self._is_cached(cache_key):
            return self._cache[cache_key][1]

        try:
            url = f"{_BASE_URL}/coins/{coin_id}"
            params = {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
            }
            resp = self._session.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            market_data = data.get("market_data", {})
            result = {
                "market_cap_usd": market_data.get("market_cap", {}).get("usd", 0),
                "market_cap_rank": data.get("market_cap_rank", 0),
                "coingecko_volume_24h_usd": market_data.get("total_volume", {}).get("usd", 0),
                "categories": data.get("categories", [])[:5],
                "market_cap_change_24h_pct": market_data.get("market_cap_change_percentage_24h", 0),
                "fully_diluted_valuation_usd": market_data.get("fully_diluted_valuation", {}).get("usd"),
            }

            self._cache[cache_key] = (time.time(), result)
            return result

        except Exception as exc:
            logger.warning(f"CoinGecko API failed for {coin_id}: {exc}")
            return None

    def enrich_market_snapshot(self, symbol: str, snapshot_dict: dict) -> dict:
        """Enrich an existing market snapshot dict with CoinGecko data.

        Adds market_cap_usd, market_cap_rank, categories fields if available.
        Non-destructive: only adds keys, never overwrites existing ones.
        """
        cg_data = self.get_market_data(symbol)
        if not cg_data:
            return snapshot_dict

        enriched = dict(snapshot_dict)
        for key, value in cg_data.items():
            if key not in enriched:
                enriched[key] = value

        return enriched
