"""Tests for AI pipeline structured schemas."""

import pytest

from trading.ai.schemas import ExecutionPlan, ExecutionResult, ResearchDecision, RiskDecision


class TestResearchDecision:
    def test_from_dict_valid(self):
        d = {
            "symbol": "btcusdt",
            "stance": "long",
            "confidence": 0.8,
            "thesis": "Strong momentum",
            "market_structure": "uptrend with normal volatility",
            "evidence": ["24h move positive", "funding healthy"],
            "catalysts": ["ETF approval", "Halving"],
            "risks": ["Regulatory risk"],
            "invalidation": "Break below 40k",
            "time_horizon": "swing",
            "preferred_market": "futures",
        }
        rd = ResearchDecision.from_dict(d)
        assert rd.symbol == "BTCUSDT"
        assert rd.stance == "long"
        assert rd.confidence == 0.8
        assert rd.preferred_market == "futures"
        assert "ETF approval" in rd.catalysts
        assert "24h move positive" in rd.evidence

    def test_symbol_uppercased(self):
        d = {
            "symbol": "ethusdt",
            "stance": "neutral",
            "confidence": 0.5,
            "thesis": "Flat",
            "market_structure": "range",
            "evidence": [],
            "catalysts": [],
            "risks": [],
            "invalidation": "—",
            "time_horizon": "intraday",
            "preferred_market": "spot",
        }
        assert ResearchDecision.from_dict(d).symbol == "ETHUSDT"

    def test_invalid_stance_raises(self):
        with pytest.raises(ValueError, match="Invalid stance"):
            ResearchDecision.from_dict(
                {"symbol": "BTC", "stance": "bullish", "confidence": 0.5,
                 "thesis": "", "market_structure": "", "evidence": [], "catalysts": [], "risks": [], "invalidation": "",
                 "time_horizon": "",
                 "preferred_market": "futures"}
            )

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            ResearchDecision.from_dict(
                {"symbol": "BTC", "stance": "long", "confidence": 1.5,
                 "thesis": "", "market_structure": "", "evidence": [], "catalysts": [], "risks": [], "invalidation": "",
                 "time_horizon": "",
                 "preferred_market": "futures"}
            )

    def test_invalid_preferred_market_defaults(self):
        d = {
            "symbol": "BTC",
            "stance": "short",
            "confidence": 0.6,
            "thesis": "",
            "market_structure": "",
            "evidence": [],
            "catalysts": [],
            "risks": [],
            "invalidation": "",
            "time_horizon": "",
            "preferred_market": "derivatives",
        }
        rd = ResearchDecision.from_dict(d)
        assert rd.preferred_market == "futures"

    def test_to_dict_round_trip(self):
        d = {
            "symbol": "BTCUSDT",
            "stance": "long",
            "confidence": 0.75,
            "thesis": "T",
            "market_structure": "uptrend",
            "evidence": ["e1"],
            "catalysts": ["C1"],
            "risks": ["R1"],
            "invalidation": "I",
            "time_horizon": "multi-day",
            "preferred_market": "futures",
        }
        rd = ResearchDecision.from_dict(d)
        assert rd.to_dict()["stance"] == "long"
        assert rd.to_dict()["confidence"] == 0.75
        assert rd.to_dict()["market_structure"] == "uptrend"


class TestExecutionPlan:
    def test_from_dict_valid(self):
        d = {
            "action": "open_long",
            "size_pct": 3.0,
            "leverage": 10,
            "entry_idea": "Break above resistance",
            "entry_style": "breakout_confirmation",
            "entry_zone_low": 99500,
            "entry_zone_high": 100500,
            "trigger_price": 100800,
            "invalidation_price": 97900,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 15.0,
            "thesis_window_hours": 24,
            "rationale": "Strong thesis",
        }
        ep = ExecutionPlan.from_dict(d)
        assert ep.action == "open_long"
        assert ep.size_pct == 3.0
        assert ep.leverage == 10
        assert ep.entry_style == "breakout_confirmation"
        assert ep.entry_zone_low == 99500
        assert ep.trigger_price == 100800
        assert ep.thesis_window_hours == 24

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError, match="Invalid action"):
            ExecutionPlan.from_dict(
                {"action": "buy_futures", "size_pct": 3.0, "leverage": 5,
                 "entry_idea": "", "stop_loss_pct": 5.0, "take_profit_pct": 15.0,
                 "rationale": ""}
            )

    def test_size_pct_out_of_range_raises(self):
        with pytest.raises(ValueError, match="size_pct"):
            ExecutionPlan.from_dict(
                {"action": "open_long", "size_pct": 150.0, "leverage": 5,
                 "entry_idea": "", "stop_loss_pct": 5.0, "take_profit_pct": 15.0,
                 "rationale": ""}
            )

    def test_leverage_minimum_clamped(self):
        d = {
            "action": "hold",
            "size_pct": 0.0,
            "leverage": 0,
            "entry_idea": "",
            "stop_loss_pct": 0.0,
            "take_profit_pct": 0.0,
            "rationale": "",
        }
        ep = ExecutionPlan.from_dict(d)
        assert ep.leverage == 1

    def test_all_valid_actions(self):
        valid = ["hold", "buy_spot", "sell_spot", "open_long", "close_long", "open_short", "close_short"]
        for action in valid:
            ep = ExecutionPlan.from_dict(
                {"action": action, "size_pct": 0.0, "leverage": 1,
                 "entry_idea": "", "stop_loss_pct": 0.0, "take_profit_pct": 0.0, "rationale": ""}
            )
            assert ep.action == action


class TestExecutionResult:
    def test_to_dict(self):
        r = ExecutionResult(
            executed=False,
            status="not_executed_missing_api",
            symbol="BTCUSDT",
            final_action="open_long",
            final_size_pct=3.0,
            message="未配置交易api",
            manual_order_details={"symbol": "BTCUSDT"},
            execution_reason="reason",
        )
        d = r.to_dict()
        assert d["status"] == "not_executed_missing_api"
        assert d["executed"] is False
        assert d["order_ids"] == []
        assert d["manual_order_details"]["symbol"] == "BTCUSDT"
        assert d["execution_reason"] == "reason"
