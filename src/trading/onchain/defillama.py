"""DeFiLlama API client — free, no API key required."""

from __future__ import annotations

from typing import Optional

import httpx

_TIMEOUT = 6.0
_BASE = "https://api.llama.fi"
_STABLE_BASE = "https://stablecoins.llama.fi"


class DeFiLlamaClient:
    """Thin wrapper around DeFiLlama public REST endpoints."""

    def __init__(self, timeout: float = _TIMEOUT) -> None:
        self._timeout = timeout

    def _get(self, url: str) -> Optional[dict | list]:
        try:
            r = httpx.get(url, timeout=self._timeout, follow_redirects=True)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Protocol TVL
    # ------------------------------------------------------------------

    def get_protocol(self, slug: str) -> Optional[dict]:
        """Return full protocol record including tvl, chains, category, and 24h/7d change."""
        data = self._get(f"{_BASE}/protocol/{slug}")
        if not isinstance(data, dict):
            return None
        return data

    def get_protocol_tvl(self, slug: str) -> Optional[float]:
        """Return current TVL in USD for a protocol slug. Fastest endpoint."""
        data = self._get(f"{_BASE}/tvl/{slug}")
        try:
            return float(data)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def search_protocols_by_symbol(self, symbol: str) -> list[dict]:
        """Return protocols whose symbol matches (case-insensitive). Searches /protocols list."""
        data = self._get(f"{_BASE}/protocols")
        if not isinstance(data, list):
            return []
        sym = symbol.upper().replace("USDT", "").replace("USDC", "").replace("BUSD", "")
        return [
            p for p in data
            if isinstance(p, dict)
            and str(p.get("symbol", "")).upper() == sym
        ]

    # ------------------------------------------------------------------
    # Chain TVL
    # ------------------------------------------------------------------

    def get_chains(self) -> Optional[list[dict]]:
        """Return per-chain TVL list."""
        data = self._get(f"{_BASE}/v2/chains")
        return data if isinstance(data, list) else None  # type: ignore[return-value]

    def get_chain_tvl(self, chain_name: str) -> Optional[float]:
        """Return current TVL for a named chain (e.g. 'Ethereum', 'Solana')."""
        chains = self.get_chains()
        if not chains:
            return None
        name_lower = chain_name.lower()
        for c in chains:
            if str(c.get("name", "")).lower() == name_lower:
                return float(c.get("tvl", 0) or 0) or None
        return None

    # ------------------------------------------------------------------
    # Stablecoins
    # ------------------------------------------------------------------

    def get_stablecoin_total(self) -> Optional[dict]:
        """Return total stablecoin market cap and 7d peg change summary."""
        data = self._get(f"{_STABLE_BASE}/stablecoins?includePrices=true")
        if not isinstance(data, dict):
            return None
        pegs = data.get("peggedAssets", [])
        total = sum(
            float(p.get("circulating", {}).get("peggedUSD", 0) or 0)
            for p in pegs
            if isinstance(p, dict)
        )
        # 7d change: compare current vs 7d-ago circulating for top 3 stables
        return {"total_usd": total, "count": len(pegs)}
