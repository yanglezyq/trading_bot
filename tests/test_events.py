"""Tests for the events module: macro calendar, token unlocks, manager, and risk rule."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from trading.core.config import AppConfig, BinanceConfig, ClaudeConfig, EventsConfig, LoggingConfig, MonitorConfig, RiskConfig, TradingConfig, VaultConfig
from trading.events import EventManager, EventSnapshot, MacroCalendarClient, MacroEvent, TokenUnlocksClient, UnlockEvent
from trading.events.macro_calendar import MacroCalendarClient as _MC
from trading.events.token_unlocks import TokenUnlocksClient as _TU
from trading.risk.rules.events import EventRiskRule
from trading.risk.rules.base import RuleContext, RuleResult
from trading.ai.schemas import ExecutionPlan


# ──────────────────── Fixtures ────────────────────

@pytest.fixture
def events_config():
    return EventsConfig(
        enabled=True,
        macro_calendar_enabled=True,
        token_unlocks_enabled=True,
        high_impact_hours_before=24,
        high_impact_size_reduction=0.5,
        high_impact_max_leverage=10,
    )


@pytest.fixture
def app_config(events_config):
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(),
        binance=BinanceConfig(),
        claude=ClaudeConfig(),
        trading=TradingConfig(),
        monitor=MonitorConfig(),
        logging=LoggingConfig(),
        events=events_config,
    )


def _make_plan(action="open_long", size_pct=5.0, leverage=20):
    return ExecutionPlan(
        action=action,
        size_pct=size_pct,
        leverage=leverage,
        entry_idea="test",
        stop_loss_pct=5.0,
        take_profit_pct=15.0,
        rationale="test",
    )


# ──────────────────── MacroCalendarClient ────────────────────

class TestMacroCalendar:
    def test_parse_events(self):
        """MacroCalendarClient can parse ForexFactory JSON format."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=6)
        sample_data = [
            {
                "title": "Non-Farm Payrolls",
                "country": "USD",
                "date": future.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "impact": "High",
                "forecast": "200K",
                "previous": "180K",
            },
            {
                "title": "Trade Balance",
                "country": "CNY",
                "date": future.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "impact": "Medium",
                "forecast": "",
                "previous": "-50B",
            },
            {
                "title": "Housing Starts",
                "country": "USD",
                "date": future.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "impact": "Low",
                "forecast": "1.5M",
                "previous": "1.4M",
            },
        ]

        client = _MC(cache_ttl_seconds=0.0)
        with patch.object(client._session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = sample_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            events = client.get_upcoming_high_impact(hours_ahead=48.0)

        # Should filter out Low impact and non-major currencies
        assert len(events) >= 1
        assert all(ev.impact in ("High", "Medium") for ev in events)
        assert all(isinstance(ev, MacroEvent) for ev in events)

    def test_hours_until(self):
        """MacroEvent.hours_until returns positive for future events."""
        future_dt = datetime.now(timezone.utc) + timedelta(hours=12)
        ev = MacroEvent(
            title="CPI",
            country="USD",
            date=future_dt,
            impact="High",
            forecast="3.2%",
            previous="3.1%",
        )
        assert 11.5 < ev.hours_until < 12.5

    def test_graceful_failure(self):
        """Client returns empty list on network error."""
        client = _MC(cache_ttl_seconds=0.0)
        with patch.object(client._session, "get", side_effect=Exception("timeout")):
            events = client.get_upcoming_high_impact(hours_ahead=48.0)
        assert events == []


# ──────────────────── TokenUnlocksClient ────────────────────

class TestTokenUnlocks:
    def test_symbol_to_protocol_mapping(self):
        """Known symbols are mapped to protocol slugs."""
        client = _TU(cache_ttl_seconds=0.0)
        # Check some known mappings
        assert client._get_protocol_slug("ARBUSDT") is not None
        assert client._get_protocol_slug("SOLUSDT") is not None

    def test_unknown_symbol_returns_empty(self):
        """Unknown symbols return empty list without error."""
        client = _TU(cache_ttl_seconds=0.0)
        events = client.get_upcoming_unlocks("UNKNOWNUSDT", days_ahead=7.0)
        assert events == []

    def test_parse_unlock_events(self):
        """Can parse DeFiLlama emission API response."""
        now = datetime.now(timezone.utc)
        future_ts = int((now + timedelta(days=3)).timestamp())
        sample_data = {
            "events": [
                {
                    "timestamp": future_ts,
                    "noOfTokens": 1000000,
                    "price": 5.0,
                    "percentage": 0.02,  # 2% of supply
                    "description": "Team vesting",
                },
            ],
        }

        client = _TU(cache_ttl_seconds=0.0)
        with patch.object(client._session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = sample_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            events = client.get_upcoming_unlocks("ARBUSDT", days_ahead=7.0)

        assert len(events) >= 1
        assert events[0].pct_of_supply >= 1.0
        assert events[0].amount_usd > 0


# ──────────────────── EventManager ────────────────────

class TestEventManager:
    def test_get_snapshot_returns_correct_flags(self, events_config):
        """EventManager sets flags correctly based on data."""
        mgr = EventManager(events_config)

        future_dt = datetime.now(timezone.utc) + timedelta(hours=6)
        mock_macro = [MacroEvent("FOMC", "USD", future_dt, "High", "5.5%", "5.25%")]
        mock_unlock = [UnlockEvent("arbitrum", future_dt, 5_000_000, 2.0, "cliff", "Team vest")]

        with patch.object(mgr._macro, "get_upcoming_high_impact", return_value=mock_macro):
            with patch.object(mgr._unlocks, "get_upcoming_unlocks", return_value=mock_unlock):
                snap = mgr.get_upcoming_events("ARBUSDT")

        assert snap.has_high_impact_soon is True
        assert snap.has_major_unlock_soon is True
        assert len(snap.macro_events) == 1
        assert len(snap.unlock_events) == 1

    def test_never_raises(self, events_config):
        """EventManager never raises, returns empty on error."""
        mgr = EventManager(events_config)

        with patch.object(mgr._macro, "get_upcoming_high_impact", side_effect=RuntimeError("boom")):
            with patch.object(mgr._unlocks, "get_upcoming_unlocks", side_effect=RuntimeError("crash")):
                snap = mgr.get_upcoming_events("BTCUSDT")

        assert snap.has_high_impact_soon is False
        assert snap.has_major_unlock_soon is False

    def test_disabled_sources(self):
        """Disabled sources are not fetched."""
        cfg = EventsConfig(
            enabled=True,
            macro_calendar_enabled=False,
            token_unlocks_enabled=False,
        )
        mgr = EventManager(cfg)
        snap = mgr.get_upcoming_events("BTCUSDT")
        assert snap.macro_events == []
        assert snap.unlock_events == []

    def test_to_dict(self, events_config):
        """EventSnapshot.to_dict() produces serializable output."""
        snap = EventSnapshot(
            macro_events=[],
            unlock_events=[],
            has_high_impact_soon=True,
            has_major_unlock_soon=False,
            fetched_at="2026-01-01T00:00:00+00:00",
        )
        d = snap.to_dict()
        assert d["has_high_impact_soon"] is True
        assert d["has_major_unlock_soon"] is False
        # Ensure JSON serializable
        json.dumps(d)


# ──────────────────── EventRiskRule ────────────────────

class TestEventRiskRule:
    def test_applies_only_for_open_actions(self, app_config):
        """Rule does not apply for close/hold actions."""
        rule = EventRiskRule()
        plan = _make_plan(action="close_long")
        ctx = RuleContext(
            plan=plan,
            account_snapshot={},
            positions=[],
            market_snapshot=None,
            portfolio_snapshot=None,
            portfolio_budget=None,
            config=app_config,
            event_snapshot={"has_high_impact_soon": True, "has_major_unlock_soon": False},
        )
        result = RuleResult(
            adjusted_size_pct=plan.size_pct,
            adjusted_leverage=plan.leverage,
            adjusted_action=plan.action,
            stop_loss_pct=plan.stop_loss_pct,
            take_profit_pct=plan.take_profit_pct,
        )
        assert rule.applies(ctx, result) is False

    def test_applies_when_high_impact(self, app_config):
        """Rule applies for open actions with high impact event."""
        rule = EventRiskRule()
        plan = _make_plan(action="open_long")
        ctx = RuleContext(
            plan=plan,
            account_snapshot={},
            positions=[],
            market_snapshot=None,
            portfolio_snapshot=None,
            portfolio_budget=None,
            config=app_config,
            event_snapshot={"has_high_impact_soon": True, "has_major_unlock_soon": False},
        )
        result = RuleResult(
            adjusted_size_pct=plan.size_pct,
            adjusted_leverage=plan.leverage,
            adjusted_action=plan.action,
            stop_loss_pct=plan.stop_loss_pct,
            take_profit_pct=plan.take_profit_pct,
        )
        assert rule.applies(ctx, result) is True

    def test_reduces_size_and_caps_leverage(self, app_config):
        """Rule reduces size by 50% and caps leverage at 10x."""
        rule = EventRiskRule()
        plan = _make_plan(action="open_long", size_pct=5.0, leverage=20)
        ctx = RuleContext(
            plan=plan,
            account_snapshot={},
            positions=[],
            market_snapshot=None,
            portfolio_snapshot=None,
            portfolio_budget=None,
            config=app_config,
            event_snapshot={"has_high_impact_soon": True, "has_major_unlock_soon": True},
        )
        result = RuleResult(
            adjusted_size_pct=plan.size_pct,
            adjusted_leverage=plan.leverage,
            adjusted_action=plan.action,
            stop_loss_pct=plan.stop_loss_pct,
            take_profit_pct=plan.take_profit_pct,
        )
        rule.evaluate(ctx, result)

        assert result.adjusted_size_pct == 2.5  # 5.0 * 0.5
        assert result.adjusted_leverage == 10   # capped from 20
        assert len(result.warnings) == 1
        assert "event_risk" in result.warnings[0]

    def test_no_reduction_when_no_events(self, app_config):
        """Rule does not apply when no events flagged."""
        rule = EventRiskRule()
        plan = _make_plan(action="open_short", size_pct=5.0, leverage=20)
        ctx = RuleContext(
            plan=plan,
            account_snapshot={},
            positions=[],
            market_snapshot=None,
            portfolio_snapshot=None,
            portfolio_budget=None,
            config=app_config,
            event_snapshot={"has_high_impact_soon": False, "has_major_unlock_soon": False},
        )
        result = RuleResult(
            adjusted_size_pct=plan.size_pct,
            adjusted_leverage=plan.leverage,
            adjusted_action=plan.action,
            stop_loss_pct=plan.stop_loss_pct,
            take_profit_pct=plan.take_profit_pct,
        )
        assert rule.applies(ctx, result) is False
        # Size and leverage unchanged
        assert result.adjusted_size_pct == 5.0
        assert result.adjusted_leverage == 20

    def test_no_event_snapshot_safe(self, app_config):
        """Rule handles None event_snapshot gracefully."""
        rule = EventRiskRule()
        plan = _make_plan(action="open_long")
        ctx = RuleContext(
            plan=plan,
            account_snapshot={},
            positions=[],
            market_snapshot=None,
            portfolio_snapshot=None,
            portfolio_budget=None,
            config=app_config,
            event_snapshot=None,
        )
        result = RuleResult(
            adjusted_size_pct=plan.size_pct,
            adjusted_leverage=plan.leverage,
            adjusted_action=plan.action,
            stop_loss_pct=plan.stop_loss_pct,
            take_profit_pct=plan.take_profit_pct,
        )
        assert rule.applies(ctx, result) is False
