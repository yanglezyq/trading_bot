"""Macro economic calendar client using ForexFactory free JSON feed.

Fetches upcoming high-impact economic events (CPI, FOMC, NFP, etc.) that
may affect crypto markets through risk-off sentiment or USD volatility.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_REQUEST_TIMEOUT = 10

# Countries whose events significantly impact crypto markets
_CRYPTO_RELEVANT_COUNTRIES = {"USD", "EUR", "CNY", "JPY"}


@dataclass
class MacroEvent:
    """A single macro economic event."""

    title: str
    country: str
    date: datetime
    impact: str  # "High", "Medium", "Low", "Holiday"
    forecast: str
    previous: str

    @property
    def hours_until(self) -> float:
        """Hours until this event occurs (negative if past)."""
        now = datetime.now(timezone.utc)
        delta = self.date - now
        return delta.total_seconds() / 3600

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "country": self.country,
            "date": self.date.isoformat(),
            "impact": self.impact,
            "forecast": self.forecast,
            "previous": self.previous,
            "hours_until": round(self.hours_until, 1),
        }


class MacroCalendarClient:
    """Fetches upcoming macro economic events from ForexFactory.

    Features:
    - 30-minute TTL cache (calendar data is weekly, rarely changes)
    - Filters to crypto-relevant countries (USD, EUR, CNY, JPY)
    - Returns only High/Medium impact events within next 48h
    - Silent degradation on network failure
    """

    def __init__(self, cache_ttl_seconds: float = 1800.0):
        self._cache: tuple[float, list[MacroEvent]] | None = None
        self._cache_ttl = cache_ttl_seconds
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "trading-bot/1.0",
        })

    def get_upcoming_high_impact(self, hours_ahead: float = 48.0) -> list[MacroEvent]:
        """Get High/Medium impact events within the next `hours_ahead` hours.

        Returns empty list on network failure (silent degradation).
        """
        events = self._fetch_all()
        now = datetime.now(timezone.utc)

        upcoming = []
        for ev in events:
            if ev.impact not in ("High", "Medium"):
                continue
            if ev.country not in _CRYPTO_RELEVANT_COUNTRIES:
                continue
            hours = ev.hours_until
            if 0 <= hours <= hours_ahead:
                upcoming.append(ev)

        # Sort by time (soonest first)
        upcoming.sort(key=lambda e: e.date)
        return upcoming

    def _fetch_all(self) -> list[MacroEvent]:
        """Fetch and cache all events for the current week."""
        if self._cache is not None:
            ts, cached_events = self._cache
            if (time.time() - ts) < self._cache_ttl:
                return cached_events

        try:
            resp = self._session.get(_CALENDAR_URL, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            raw_events = resp.json()

            events = []
            for item in raw_events:
                try:
                    dt = datetime.fromisoformat(item["date"])
                    # Ensure timezone-aware
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)

                    events.append(MacroEvent(
                        title=item.get("title", ""),
                        country=item.get("country", ""),
                        date=dt,
                        impact=item.get("impact", "Low"),
                        forecast=item.get("forecast", ""),
                        previous=item.get("previous", ""),
                    ))
                except (KeyError, ValueError):
                    continue

            self._cache = (time.time(), events)
            return events

        except Exception as exc:
            logger.warning(f"Macro calendar fetch failed: {exc}")
            # Return stale cache if available
            if self._cache is not None:
                return self._cache[1]
            return []
