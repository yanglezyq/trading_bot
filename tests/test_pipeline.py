"""Tests for the TradePipeline end-to-end flow (all external calls mocked)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading.ai.schemas import ExecutionPlan, ExecutionResult, ResearchDecision, RiskDecision
from trading.core.config import (
    AppConfig,
    BinanceConfig,
    ClaudeConfig,
    LoggingConfig,
    MonitorConfig,
    RiskConfig,
    TradingConfig,
    VaultConfig,
)
from trading.pipeline.persistence import TradeRunDB
from trading.pipeline.runner import TradePipeline, is_trading_api_configured


# ──────────────────────────── fixtures ────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def config_with_api(tmp_db):
    return AppConfig(
        vault=VaultConfig(path="/tmp/nonexistent_vault"),
        risk=RiskConfig(),
        binance=BinanceConfig(api_key="testkey", api_secret="testsecret"),
        claude=ClaudeConfig(api_key="sk-ant-test"),
        trading=TradingConfig(max_position_size_pct=0.05),
        monitor=MonitorConfig(),
        logging=LoggingConfig(sqlite_db=tmp_db),
    )


@pytest.fixture
def config_no_api(tmp_db):
    return AppConfig(
        vault=VaultConfig(path="/tmp/nonexistent_vault"),
        risk=RiskConfig(),
        binance=BinanceConfig(api_key="", api_secret=""),
        claude=ClaudeConfig(api_key="sk-ant-test"),
        trading=TradingConfig(max_position_size_pct=0.05),
        monitor=MonitorConfig(),
        logging=LoggingConfig(sqlite_db=tmp_db),
    )


def _mock_research() -> ResearchDecision:
    return ResearchDecision(
        symbol="BTCUSDT",
        stance="long",
        confidence=0.8,
        thesis="Strong momentum",
        market_structure="uptrend with normal volatility",
        evidence=["24h move positive", "funding balanced"],
        catalysts=["Halving"],
        risks=["Regulation"],
        invalidation="Break below 40k",
        time_horizon="swing",
        preferred_market="futures",
    )


def _mock_plan() -> ExecutionPlan:
    return ExecutionPlan(
        action="open_long",
        size_pct=3.0,
        leverage=10,
        entry_idea="Market order",
        stop_loss_pct=5.0,
        take_profit_pct=15.0,
        rationale="High confidence",
    )


# ──────────────────────────── is_trading_api_configured ────────────────────────────

class TestApiConfiguredCheck:
    def test_both_keys_set(self, config_with_api):
        assert is_trading_api_configured(config_with_api) is True

    def test_no_keys(self, config_no_api):
        assert is_trading_api_configured(config_no_api) is False

    def test_partial_keys(self, tmp_db):
        cfg = AppConfig(
            vault=VaultConfig(path="/tmp"),
            risk=RiskConfig(),
            binance=BinanceConfig(api_key="key", api_secret=""),
            claude=ClaudeConfig(),
            trading=TradingConfig(),
            monitor=MonitorConfig(),
            logging=LoggingConfig(sqlite_db=tmp_db),
        )
        assert is_trading_api_configured(cfg) is False


# ──────────────────────────── not_executed_missing_api ────────────────────────────

class TestPipelineMissingApi:
    def test_not_executed_missing_api_status(self, config_no_api):
        pipeline = TradePipeline(config=config_no_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            research, plan, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert result.status == "not_executed_missing_api"
        assert result.executed is False
        assert "未配置交易api" in result.message

    def test_run_is_persisted_even_without_api(self, config_no_api, tmp_db):
        pipeline = TradePipeline(config=config_no_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            pipeline.run("BTCUSDT", write_vault=False)

        db = TradeRunDB(tmp_db)
        runs = db.get_runs(symbol="BTCUSDT")
        assert len(runs) == 1
        result_json = json.loads(runs[0]["execution_result_json"])
        assert result_json["status"] == "not_executed_missing_api"


# ──────────────────────────── dry_run mode ────────────────────────────

class TestPipelineDryRun:
    def test_dry_run_status(self, config_no_api):
        pipeline = TradePipeline(config=config_no_api, dry_run=True)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            research, plan, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert result.status == "dry_run"
        assert result.executed is False
        assert "dry_run" in result.message or "手动下单草案" in result.message

    def test_all_four_outputs_returned(self, config_no_api):
        pipeline = TradePipeline(config=config_no_api, dry_run=True)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            research, plan, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert isinstance(research, ResearchDecision)
        assert isinstance(plan, ExecutionPlan)
        assert isinstance(risk, RiskDecision)
        assert isinstance(result, ExecutionResult)


# ──────────────────────────── blocked_by_risk ────────────────────────────

class TestPipelineRiskBlocked:
    def test_blocked_by_risk_when_size_too_large(self, config_no_api):
        big_plan = ExecutionPlan(
            action="open_long",
            size_pct=99.0,  # way over 5% max
            leverage=10,
            entry_idea="",
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            rationale="",
        )
        pipeline = TradePipeline(config=config_no_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=big_plan),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            # No API → not_executed_missing_api, but risk is still computed
            research, plan, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert risk.approved is False
        assert any("size_pct" in r for r in risk.violated_rules)


# ──────────────────────────── with API + mock OrderManager ────────────────────────────

class TestPipelineWithApi:
    def test_manual_review_when_api_configured_and_risk_passes(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        mock_mark_price = 50000.0

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "price_change_24h_pct": 3.0,
                    "price_change_7d_pct": 12.0,
                    "quote_volume_24h_usdt": 1_500_000_000.0,
                    "funding_rate": 0.0005,
                    "open_interest": 1_000_000.0,
                    "oi_to_volume_ratio": 0.4,
                    "basis_bps": 20.0,
                    "ema_21_1h": 49500.0,
                    "ema_55_1h": 48500.0,
                    "ema_144_1h": 47000.0,
                    "distance_to_ema21_pct": 1.0,
                    "distance_to_ema55_pct": 3.0,
                    "distance_to_7d_high_pct": -1.0,
                    "distance_to_7d_low_pct": 8.0,
                    "hourly_trend_bias": "bullish",
                    "asset_tier": "core",
                    "liquidity_regime": "deep",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                    "momentum_regime": "strong_up",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                },
            ),
            patch("trading.pipeline.runner.BinanceClient") as MockBinance,
            patch("trading.pipeline.runner.AccountManager") as MockAccount,
            patch("trading.pipeline.runner.PositionManager") as MockPosition,
        ):
            mock_binance_inst = MagicMock()
            mock_binance_inst.futures_client.mark_price.return_value = {"markPrice": str(mock_mark_price)}
            mock_binance_inst.futures_client.get_symbol_lot_size.return_value = 0.001
            mock_binance_inst.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
            MockBinance.return_value = mock_binance_inst

            mock_account_inst = MagicMock()
            mock_account_inst.get_account_summary.return_value = {
                "futures_balance_usdt": 10000.0,
                "spot_balance_usdt": 0.0,
                "total_balance_usdt": 10000.0,
                "unrealized_pnl": 0.0,
                "pnl_pct": 0.0,
                "drawdown_pct": 0.0,
            }
            MockAccount.return_value = mock_account_inst

            MockPosition.return_value.get_futures_positions.return_value = []

            research, plan, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert result.executed is False
        assert result.status == "manual_review_required"
        assert result.manual_order_details is not None
        assert result.manual_order_details["symbol"] == "BTCUSDT"
        assert result.manual_order_details["side"] == "BUY"
        assert result.manual_order_details["stop_loss_price"] is not None
        assert result.manual_order_details["execution_template"] == "core_trend_follow"
        assert result.manual_order_details["preferred_order_type"] == "market_or_passive_limit"
        assert "40% starter" in result.manual_order_details["staging_plan"]
        assert "先下 40% 观察仓" in result.manual_order_details["operator_steps"]
        assert result.manual_order_details["review_after_hours"] == "12h"
        assert pipeline.last_portfolio_budget["portfolio_role"] == "anchor"
        assert "自动下单已禁用" in (result.execution_reason or "")

    def test_high_beta_template_produces_limit_only_ticket(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)
        ticket = pipeline._template_execution_rules(
            {
                "execution_template": "high_beta_confirmation_only",
                "btc_market_regime": "range",
                "asset_tier": "high_beta_alt",
                "narrative_tag": "general_alt",
                "notional_usdt": 20_000.0,
            }
        )
        assert ticket["preferred_order_type"] == "laddered_limit_only"
        assert "确认后参与" in ticket["template_risk_note"]

    def test_meme_template_has_tighter_manual_rules(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)
        ticket = pipeline._template_execution_rules(
            {
                "execution_template": "alt_follow_with_confirmation",
                "btc_market_regime": "risk_on_trend",
                "asset_tier": "major_alt",
                "narrative_tag": "meme",
                "notional_usdt": 5_000.0,
            }
        )
        assert ticket["max_slippage_bps"] <= 5
        assert "Meme 币叙事" in ticket["template_risk_note"]

    def test_meme_template_note_is_more_conservative(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)
        ticket = pipeline._template_execution_rules(
            {
                "execution_template": "alt_follow_with_confirmation",
                "btc_market_regime": "risk_on_trend",
                "asset_tier": "major_alt",
                "narrative_tag": "meme",
                "notional_usdt": 5_000.0,
            }
        )
        assert ticket["max_slippage_bps"] <= 5
        assert "Meme 币叙事" in ticket["template_risk_note"]

    def test_blocked_by_risk_with_api_configured(self, config_with_api):
        oversized_plan = ExecutionPlan(
            action="open_long",
            size_pct=90.0,
            leverage=10,
            entry_idea="",
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
            rationale="",
        )
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=oversized_plan),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch("trading.pipeline.runner.BinanceClient") as MockBinance,
            patch("trading.pipeline.runner.AccountManager") as MockAccount,
            patch("trading.pipeline.runner.PositionManager") as MockPosition,
        ):
            MockBinance.return_value = MagicMock()
            mock_acct = MagicMock()
            mock_acct.get_account_summary.return_value = {
                "futures_balance_usdt": 10000.0,
                "spot_balance_usdt": 0.0,
                "total_balance_usdt": 10000.0,
                "unrealized_pnl": 0.0,
                "pnl_pct": 0.0,
                "drawdown_pct": 0.0,
            }
            MockAccount.return_value = mock_acct
            MockPosition.return_value.get_futures_positions.return_value = []

            research, plan, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert result.status == "blocked_by_risk"
        assert result.executed is False


# ──────────────────────────── reflections injected ────────────────────────────

class TestReflectionInjection:
    def test_reflection_saved_and_loaded(self, config_no_api, tmp_db):
        from trading.pipeline.persistence import TradeRunDB

        db = TradeRunDB(tmp_db)
        ref_id = db.save_reflection(
            symbol="BTCUSDT",
            model="claude-test",
            trades_analyzed=3,
            reflection_data={
                "direction_accuracy": "Correct",
                "thesis_evaluation": "Held",
                "lessons": ["lesson 1", "lesson 2"],
                "reflection_text": "Overall good trade.",
            },
            source_trade_ids=[1, 2, 3],
        )

        refs = db.get_reflections(symbol="BTCUSDT")
        assert len(refs) == 1
        assert refs[0]["id"] == ref_id
        assert isinstance(refs[0]["lessons"], list)
        assert "lesson 1" in refs[0]["lessons"]

    def test_pipeline_loads_reflections(self, config_no_api, tmp_db):
        db = TradeRunDB(tmp_db)
        db.save_reflection(
            symbol="BTCUSDT",
            model="test",
            trades_analyzed=1,
            reflection_data={
                "direction_accuracy": "Good",
                "thesis_evaluation": "OK",
                "lessons": ["Always use stop loss"],
                "reflection_text": "Test reflection",
            },
            source_trade_ids=[1],
        )

        # Now run pipeline and check that reflections were passed to advisor
        pipeline = TradePipeline(config=config_no_api, dry_run=True)

        captured_reflections = []

        def capture_research(symbol, account_summary, positions, market_snapshot, vault_context, trade_history, reflections):
            captured_reflections.extend(reflections)
            return _mock_research()

        with (
            patch.object(pipeline.advisor, "generate_research", side_effect=capture_research),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            pipeline.run("BTCUSDT", write_vault=False)

        assert len(captured_reflections) == 1
        assert "Always use stop loss" in captured_reflections[0]["lessons"]


# ──────────────────────────── AI JSON parse ────────────────────────────

class TestAiJsonParse:
    def test_extract_json_from_clean_text(self):
        from trading.ai.client import ClaudeClient
        from trading.core.config import ClaudeConfig

        client = ClaudeClient(ClaudeConfig(api_key=""))
        result = client._extract_json('{"stance": "long", "confidence": 0.8}')
        assert result["stance"] == "long"

    def test_extract_json_from_markdown_fence(self):
        from trading.ai.client import ClaudeClient
        from trading.core.config import ClaudeConfig

        client = ClaudeClient(ClaudeConfig(api_key=""))
        text = '```json\n{"action": "open_long"}\n```'
        result = client._extract_json(text)
        assert result["action"] == "open_long"

    def test_extract_json_embedded_in_text(self):
        from trading.ai.client import ClaudeClient
        from trading.core.config import ClaudeConfig

        client = ClaudeClient(ClaudeConfig(api_key=""))
        text = 'Here is the result: {"key": "value"} (end)'
        result = client._extract_json(text)
        assert result["key"] == "value"

    def test_extract_json_raises_on_no_json(self):
        from trading.ai.client import ClaudeClient
        from trading.core.config import ClaudeConfig

        client = ClaudeClient(ClaudeConfig(api_key=""))
        with pytest.raises(ValueError, match="No valid JSON"):
            client._extract_json("This is plain text with no JSON.")
