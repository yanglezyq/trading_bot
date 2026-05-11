"""OnchainDataManager — fetches and assembles on-chain context for a trading symbol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .defillama import DeFiLlamaClient

# ---------------------------------------------------------------------------
# Symbol → DeFiLlama protocol slug mapping
# Add more entries as needed; None means no matching DeFi protocol.
# ---------------------------------------------------------------------------
_SYMBOL_TO_PROTOCOL: dict[str, Optional[str]] = {
    # DEXes
    "UNIUSDT": "uniswap",
    "SUSHIUSDT": "sushi",
    "CRVUSDT": "curve-dex",
    "BALUSDT": "balancer-v2",
    "1INCHUSDT": "1inch-network",
    "DYDXUSDT": "dydx",
    "GMXUSDT": "gmx",
    "PERPUSDT": "perpetual-protocol",
    # Lending
    "AAVEUSDT": "aave-v3",
    "COMPUSDT": "compound-finance",
    "MKRUSDT": "makerdao",
    "RADELLUSDT": "radiant-v2",
    # Yield / aggregators
    "YFIUSDT": "yearn-finance",
    "CVXUSDT": "convex-finance",
    "FRAXUSDT": "frax",
    # Staking / liquid staking
    "LDOUSDT": "lido",
    "RPLLUSDT": "rocket-pool",
    "STGUSDT": "stargate-finance",
    "PENDLE": "pendle",
    # Bridges / cross-chain
    "SYNUSDT": "synapse-bridge",
    "MULTIUSDT": "multichain",
    # Options / structured
    "RIBBONUSDT": "ribbon-finance",
    # Oracles — TVL usually low / not applicable
    "LINKUSDT": None,
    "BANDUSDT": None,
    # Layer-1 / Layer-2 — use chain TVL instead
    "BTCUSDT": None,
    "ETHUSDT": None,
    "BNBUSDT": None,
    "SOLUSDT": None,
    "AVAXUSDT": None,
    "MATICUSDT": None,
    "ARBUSDT": None,
    "OPUSDT": None,
    "APTUSDT": None,
    "SUIUSDT": None,
    "NEARUSDT": None,
    "ATOMUSDT": None,
    "DOTUSDT": None,
    "ADAUSDT": None,
    "XRPUSDT": None,
    "LTCUSDT": None,
    "BCHUSDT": None,
    "TRXUSDT": None,
}

# Symbol → DeFiLlama chain name (for L1/L2 TVL lookup)
_SYMBOL_TO_CHAIN: dict[str, str] = {
    "ETHUSDT":   "Ethereum",
    "BNBUSDT":   "BSC",
    "SOLUSDT":   "Solana",
    "AVAXUSDT":  "Avalanche",
    "MATICUSDT": "Polygon",
    "ARBUSDT":   "Arbitrum",
    "OPUSDT":    "Optimism",
    "APTUSDT":   "Aptos",
    "SUIUSDT":   "Sui",
    "NEARUSDT":  "Near",
    "ATOMUSDT":  "CosmosHub",
    "TONUSDT":   "TON",
    "TRXUSDT":   "Tron",
}


@dataclass
class OnchainSnapshot:
    symbol: str

    # DeFi protocol TVL (for defi/dex/lending tokens)
    protocol_name: Optional[str] = None
    protocol_slug: Optional[str] = None
    protocol_tvl_usd: Optional[float] = None
    tvl_change_24h_pct: Optional[float] = None
    tvl_change_7d_pct: Optional[float] = None
    protocol_category: Optional[str] = None
    protocol_chains: list[str] = field(default_factory=list)

    # Chain TVL (for L1 / L2 tokens)
    chain_name: Optional[str] = None
    chain_tvl_usd: Optional[float] = None

    # Macro stablecoin supply
    stablecoin_total_usd: Optional[float] = None

    # Metadata
    data_sources: list[str] = field(default_factory=list)
    fetched_at: str = ""
    has_onchain_data: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "protocol_name": self.protocol_name,
            "protocol_slug": self.protocol_slug,
            "protocol_tvl_usd": self.protocol_tvl_usd,
            "tvl_change_24h_pct": self.tvl_change_24h_pct,
            "tvl_change_7d_pct": self.tvl_change_7d_pct,
            "protocol_category": self.protocol_category,
            "protocol_chains": self.protocol_chains,
            "chain_name": self.chain_name,
            "chain_tvl_usd": self.chain_tvl_usd,
            "stablecoin_total_usd": self.stablecoin_total_usd,
            "data_sources": self.data_sources,
            "fetched_at": self.fetched_at,
            "has_onchain_data": self.has_onchain_data,
        }


class OnchainDataManager:
    """Assembles on-chain context for a given trading symbol using DeFiLlama."""

    def __init__(self, timeout: float = 6.0) -> None:
        self._client = DeFiLlamaClient(timeout=timeout)

    def get_snapshot(self, symbol: str) -> OnchainSnapshot:
        """Fetch all available on-chain data for symbol. Never raises — returns partial data on errors."""
        sym = symbol.upper()
        snap = OnchainSnapshot(
            symbol=sym,
            fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # ── 1. Protocol TVL ────────────────────────────────────────────
        try:
            protocol_slug = self._resolve_protocol_slug(sym)
            if protocol_slug:
                self._fetch_protocol_tvl(snap, protocol_slug)
        except Exception:
            pass

        # ── 2. Chain TVL ───────────────────────────────────────────────
        try:
            chain_name = _SYMBOL_TO_CHAIN.get(sym)
            if chain_name:
                tvl = self._client.get_chain_tvl(chain_name)
                if tvl is not None:
                    snap.chain_name = chain_name
                    snap.chain_tvl_usd = tvl
                    snap.data_sources.append("defillama_chain")
                    snap.has_onchain_data = True
        except Exception:
            pass

        # ── 3. Stablecoin macro supply ─────────────────────────────────
        try:
            stable = self._client.get_stablecoin_total()
            if stable and stable.get("total_usd"):
                snap.stablecoin_total_usd = stable["total_usd"]
                if "defillama_stable" not in snap.data_sources:
                    snap.data_sources.append("defillama_stable")
                snap.has_onchain_data = True
        except Exception:
            pass

        return snap

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_protocol_slug(self, sym: str) -> Optional[str]:
        """Return DeFiLlama slug for symbol, or attempt auto-discovery."""
        # Explicit map hit (including None = known no-protocol symbol)
        if sym in _SYMBOL_TO_PROTOCOL:
            return _SYMBOL_TO_PROTOCOL[sym]

        # Auto-discover by searching symbol name
        base = sym.replace("USDT", "").replace("USDC", "").replace("BUSD", "")
        matches = self._client.search_protocols_by_symbol(base)
        if matches:
            # Prefer the match with highest TVL
            best = max(matches, key=lambda p: float(p.get("tvl", 0) or 0))
            return str(best.get("slug", "")) or None
        return None

    def _fetch_protocol_tvl(self, snap: OnchainSnapshot, slug: str) -> None:
        data = self._client.get_protocol(slug)
        if not data:
            return

        snap.protocol_slug = slug
        snap.protocol_name = data.get("name")
        snap.protocol_category = data.get("category")
        snap.protocol_chains = list(data.get("chains", []))[:6]

        # Current TVL
        tvl_raw = data.get("tvl")
        if isinstance(tvl_raw, (int, float)):
            snap.protocol_tvl_usd = float(tvl_raw)
        elif isinstance(tvl_raw, list) and tvl_raw:
            # TVL history list: last entry is current
            last = tvl_raw[-1]
            snap.protocol_tvl_usd = float(last.get("totalLiquidityUSD", 0) or 0) or None

        # 24h / 7d change from the change1d / change7d keys
        change_1d = data.get("change_1d")
        change_7d = data.get("change_7d")
        if change_1d is not None:
            try:
                snap.tvl_change_24h_pct = float(change_1d)
            except (ValueError, TypeError):
                pass
        if change_7d is not None:
            try:
                snap.tvl_change_7d_pct = float(change_7d)
            except (ValueError, TypeError):
                pass

        if snap.protocol_tvl_usd is not None:
            snap.data_sources.append("defillama_protocol")
            snap.has_onchain_data = True
