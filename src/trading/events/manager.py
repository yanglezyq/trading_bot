"""Event manager: orchestrates macro calendar and token unlock data sources."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.config import EventsConfig
from .macro_calendar import MacroCalendarClient, MacroEvent
from .token_unlocks import TokenUnlocksClient, UnlockEvent

logger = logging.getLogger(__name__)


@dataclass
class EventSnapshot:
    """Aggregated upcoming events relevant to a trading decision."""

    macro_events: list[MacroEvent] = field(default_factory=list)
    unlock_events: list[UnlockEvent] = field(default_factory=list)
    has_high_impact_soon: bool = False  # High-impact macro event within configured hours
    has_major_unlock_soon: bool = False  # >1% supply unlock within 7 days
    fetched_at: str = ""

    def to_dict(self) -> dict:
        return {
            "macro_events": [e.to_dict() for e in self.macro_events],
            "unlock_events": [e.to_dict() for e in self.unlock_events],
            "has_high_impact_soon": self.has_high_impact_soon,
            "has_major_unlock_soon": self.has_major_unlock_soon,
            "fetched_at": self.fetched_at,
        }


class EventManager:
    """Orchestrates event data sources for trading decisions.

    Assembles macro calendar events and token unlock schedules into
    an EventSnapshot that feeds into AI prompts and risk rules.
    """

    def __init__(self, config: EventsConfig):
        self._config = config
        self._macro = MacroCalendarClient(cache_ttl_seconds=1800.0)
        self._unlocks = TokenUnlocksClient(cache_ttl_seconds=3600.0)

    def get_upcoming_events(self, symbol: str) -> EventSnapshot:
        """Get all relevant upcoming events for the given symbol.

        Never raises — returns empty snapshot on any failure.
        """
        macro_events: list[MacroEvent] = []
        unlock_events: list[UnlockEvent] = []

        if self._config.macro_calendar_enabled:
            try:
                macro_events = self._macro.get_upcoming_high_impact(
                    hours_ahead=float(self._config.high_impact_hours_before * 2)
                )
            except Exception as exc:
                logger.warning(f"Macro calendar error: {exc}")

        if self._config.token_unlocks_enabled:
            try:
                unlock_events = self._unlocks.get_upcoming_unlocks(symbol, days_ahead=7.0)
            except Exception as exc:
                logger.warning(f"Token unlock error: {exc}")

        # Determine flags
        hours_threshold = float(self._config.high_impact_hours_before)
        has_high_impact_soon = any(
            ev.impact == "High" and 0 <= ev.hours_until <= hours_threshold
            for ev in macro_events
        )
        has_major_unlock_soon = any(
            ev.pct_of_supply >= 1.0
            for ev in unlock_events
        )

        return EventSnapshot(
            macro_events=macro_events,
            unlock_events=unlock_events,
            has_high_impact_soon=has_high_impact_soon,
            has_major_unlock_soon=has_major_unlock_soon,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
