"""Token unlock schedule client using DeFiLlama unlocks API.

Fetches upcoming token unlock/vesting events that may create sell pressure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.llama.fi"
_REQUEST_TIMEOUT = 10

# Mapping from Binance symbol base to DeFiLlama protocol slug
_SYMBOL_TO_PROTOCOL: dict[str, str] = {
    "ARB": "arbitrum",
    "OP": "optimism",
    "APT": "aptos",
    "SUI": "sui",
    "SEI": "sei-network",
    "TIA": "celestia",
    "STRK": "starknet",
    "MANTA": "manta-network",
    "ZK": "zksync",
    "DYDX": "dydx",
    "IMX": "immutable-x",
    "RENDER": "render-network",
    "FET": "fetch-ai",
    "WLD": "worldcoin",
    "ARKM": "arkham",
    "EIGEN": "eigenlayer",
    "ETHFI": "ether-fi",
    "PENDLE": "pendle",
    "INJ": "injective",
    "AXS": "axie-infinity",
    "SAND": "the-sandbox",
    "GALA": "gala-games",
    "ICP": "internet-computer",
    "FIL": "filecoin",
    "NEAR": "near",
    "AVAX": "avalanche",
    "DOT": "polkadot",
    "ATOM": "cosmos",
    "SOL": "solana",
    "PIXEL": "pixels",
    "ONDO": "ondo-finance",
}


@dataclass
class UnlockEvent:
    """A single token unlock/vesting event."""

    protocol: str
    date: datetime
    amount_usd: float
    pct_of_supply: float  # 0.0-100.0
    unlock_type: str  # "cliff", "linear", "unknown"
    description: str = ""

    @property
    def days_until(self) -> float:
        """Days until this unlock occurs."""
        now = datetime.now(timezone.utc)
        delta = self.date - now
        return delta.total_seconds() / 86400

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "date": self.date.isoformat(),
            "amount_usd": self.amount_usd,
            "pct_of_supply": self.pct_of_supply,
            "unlock_type": self.unlock_type,
            "description": self.description,
            "days_until": round(self.days_until, 1),
        }


class TokenUnlocksClient:
    """Fetches upcoming token unlock events from DeFiLlama.

    Features:
    - 1-hour TTL cache per protocol
    - Symbol-to-protocol mapping for Binance pairs
    - Filters to unlocks within next 7 days
    - Silent degradation on failure
    """

    def __init__(self, cache_ttl_seconds: float = 3600.0):
        self._cache: dict[str, tuple[float, list[UnlockEvent]]] = {}
        self._cache_ttl = cache_ttl_seconds
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "trading-bot/1.0",
        })

    def _get_protocol_slug(self, symbol: str) -> Optional[str]:
        """Map Binance symbol to DeFiLlama protocol slug."""
        base = symbol.upper().replace("USDT", "").replace("USDC", "").replace("BUSD", "")
        return _SYMBOL_TO_PROTOCOL.get(base)

    def get_upcoming_unlocks(self, symbol: str, days_ahead: float = 7.0) -> list[UnlockEvent]:
        """Get unlock events within the next `days_ahead` days for a symbol.

        Returns empty list if symbol not mapped or API fails.
        """
        slug = self._get_protocol_slug(symbol)
        if not slug:
            return []

        events = self._fetch_protocol_unlocks(slug)
        upcoming = [ev for ev in events if 0 <= ev.days_until <= days_ahead]
        upcoming.sort(key=lambda e: e.date)
        return upcoming

    def _fetch_protocol_unlocks(self, slug: str) -> list[UnlockEvent]:
        """Fetch unlock events for a specific protocol."""
        cache_key = slug
        if cache_key in self._cache:
            ts, cached = self._cache[cache_key]
            if (time.time() - ts) < self._cache_ttl:
                return cached

        try:
            # DeFiLlama unlocks API pattern
            url = f"{_BASE_URL}/emission/{slug}"
            resp = self._session.get(url, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            events = self._parse_unlock_events(slug, data)
            self._cache[cache_key] = (time.time(), events)
            return events

        except Exception as exc:
            logger.warning(f"Token unlock fetch failed for {slug}: {exc}")
            # Return stale cache if available
            if cache_key in self._cache:
                return self._cache[cache_key][1]
            return []

    def _parse_unlock_events(self, slug: str, data: dict) -> list[UnlockEvent]:
        """Parse DeFiLlama emission/unlock response into UnlockEvent list."""
        events: list[UnlockEvent] = []

        # DeFiLlama returns events in different formats; handle common patterns
        # Pattern 1: "events" array with timestamp + amount
        raw_events = data.get("events", [])
        if not raw_events:
            # Pattern 2: "tokenAllocation" with "unlockSchedule"
            allocations = data.get("tokenAllocation", [])
            for alloc in allocations:
                schedule = alloc.get("unlockSchedule", [])
                for entry in schedule:
                    raw_events.append(entry)

        now = datetime.now(timezone.utc)
        for item in raw_events:
            try:
                # Parse timestamp
                ts = item.get("timestamp") or item.get("date")
                if ts is None:
                    continue
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(ts))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)

                # Only future events
                if dt <= now:
                    continue

                amount_usd = float(item.get("noOfTokens", 0) * item.get("price", 0)) if "noOfTokens" in item else float(item.get("amount_usd", 0))
                pct = float(item.get("percentage", 0)) * 100 if item.get("percentage", 0) <= 1 else float(item.get("percentage", 0))
                unlock_type = item.get("type", item.get("category", "unknown")).lower()
                if unlock_type not in ("cliff", "linear"):
                    unlock_type = "cliff" if pct > 1.0 else "linear"

                description = item.get("description", item.get("name", ""))

                events.append(UnlockEvent(
                    protocol=slug,
                    date=dt,
                    amount_usd=amount_usd,
                    pct_of_supply=pct,
                    unlock_type=unlock_type,
                    description=description,
                ))
            except (KeyError, ValueError, TypeError):
                continue

        return events
