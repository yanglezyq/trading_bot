"""Tests for the TradePipeline end-to-end flow (all external calls mocked)."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading.ai.schemas import ExecutionPlan, ExecutionResult, ResearchDecision, RiskDecision
from trading.core.journal import TradeJournal
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
        trading=TradingConfig(max_position_size_pct=0.05, combined_ai_calls=False),
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
        trading=TradingConfig(max_position_size_pct=0.05, combined_ai_calls=False),
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


def _mock_research_high_conviction() -> ResearchDecision:
    return ResearchDecision(
        symbol="BTCUSDT",
        stance="long",
        confidence=0.93,
        thesis="Strong trend and conviction",
        market_structure="uptrend with manageable volatility",
        evidence=["24h move positive", "relative strength strong"],
        catalysts=["Momentum continuation"],
        risks=["Macro reversal"],
        invalidation="Break below trend support",
        time_horizon="swing",
        preferred_market="futures",
        supporting_model_count=2,
        consensus_strength=0.9,
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


def _mock_research_short() -> ResearchDecision:
    return ResearchDecision(
        symbol="BTCUSDT",
        stance="short",
        confidence=0.7,
        thesis="Weak structure",
        market_structure="downtrend",
        evidence=["24h move negative"],
        catalysts=["Risk-off"],
        risks=["Short squeeze"],
        invalidation="Break above 50k",
        time_horizon="swing",
        preferred_market="futures",
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


class TestAutoExecute:
    def test_auto_execute_flag_calls_real_execution_path(self, config_with_api):
        config_with_api.trading.auto_execute_min_confidence = 0.8
        config_with_api.trading.auto_execute_min_consensus_strength = 0.75
        config_with_api.trading.auto_execute_min_quality_score = 60.0
        config_with_api.trading.auto_execute_allowed_symbols = ["BTCUSDT"]
        config_with_api.trading.auto_execute_live_enabled = True
        config_with_api.trading.auto_execute_allowed_edge_labels = []
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        executed_result = ExecutionResult(
            executed=True,
            status="executed",
            symbol="BTCUSDT",
            final_action="open_long",
            final_size_pct=3.0,
            final_leverage=6,
            message="executed",
            order_ids=["1"],
            exchange="binance",
        )

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={
                "symbol": "BTCUSDT",
                "entry_validity": "inside_entry_zone",
                "stop_loss_price": 48000.0,
                "take_profit_price": 53000.0,
            }),
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
            patch.object(pipeline, "_do_execute", return_value=executed_result) as mock_exec,
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            MockOrder.return_value.get_open_orders.return_value = []
            research, plan, risk, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert research.confidence >= 0.9
        assert risk.approved is True
        assert result.status == "executed"
        mock_exec.assert_called_once()

    def test_auto_execute_blocked_falls_back_to_manual_review(self, config_with_api):
        config_with_api.trading.auto_execute_live_enabled = True
        config_with_api.trading.auto_execute_allowed_symbols = ["ETHUSDT"]
        config_with_api.trading.auto_execute_allowed_edge_labels = []
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={
                "symbol": "BTCUSDT",
                "entry_validity": "inside_entry_zone",
                "stop_loss_price": 48000.0,
                "take_profit_price": 53000.0,
            }),
            patch.object(pipeline, "_do_execute") as mock_exec,
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert result.status == "auto_execute_blocked"
        assert result.executed is False
        assert "白名单" in (result.execution_reason or "")
        mock_exec.assert_not_called()

    def test_auto_execute_close_action_bypasses_symbol_whitelist(self, config_with_api):
        config_with_api.trading.auto_execute_live_enabled = True
        config_with_api.trading.auto_execute_allowed_symbols = ["ETHUSDT"]
        config_with_api.trading.auto_execute_allowed_edge_labels = []
        pipeline = TradePipeline(config=config_with_api, dry_run=False)
        executed_result = ExecutionResult(
            executed=True,
            status="executed",
            symbol="BTCUSDT",
            final_action="close_long",
            final_size_pct=100.0,
            final_leverage=5,
            message="closed",
            order_ids=["1"],
            exchange="binance",
        )

        close_plan = _mock_plan()
        close_plan.action = "close_long"
        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=close_plan),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={"symbol": "BTCUSDT"}),
            patch.object(pipeline, "_do_execute", return_value=executed_result) as mock_exec,
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert result.status == "executed"
        mock_exec.assert_called_once()

    def test_auto_execute_live_disabled_blocks_real_order(self, config_with_api):
        config_with_api.trading.auto_execute_allowed_symbols = ["BTCUSDT"]
        config_with_api.trading.auto_execute_live_enabled = False
        config_with_api.trading.auto_execute_allowed_edge_labels = []
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={
                "symbol": "BTCUSDT",
                "entry_validity": "inside_entry_zone",
                "stop_loss_price": 48000.0,
                "take_profit_price": 53000.0,
            }),
            patch.object(pipeline, "_do_execute") as mock_exec,
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert result.status == "auto_execute_blocked"
        assert "真实环境自动执行未开启" in (result.execution_reason or "")
        mock_exec.assert_not_called()

    def test_auto_execute_blocked_when_open_orders_exist(self, config_with_api):
        config_with_api.trading.auto_execute_allowed_symbols = ["BTCUSDT"]
        config_with_api.trading.auto_execute_live_enabled = True
        config_with_api.trading.auto_execute_allowed_edge_labels = []
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={
                "symbol": "BTCUSDT",
                "entry_validity": "inside_entry_zone",
                "stop_loss_price": 48000.0,
                "take_profit_price": 53000.0,
            }),
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
            patch.object(pipeline, "_do_execute") as mock_exec,
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            MockOrder.return_value.get_open_orders.return_value = [MagicMock()]
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert result.status == "auto_execute_blocked"
        assert "未完成订单" in (result.execution_reason or "")
        mock_exec.assert_not_called()

    def test_auto_execute_daily_notional_limit_blocks(self, config_with_api):
        config_with_api.trading.auto_execute_allowed_symbols = ["BTCUSDT"]
        config_with_api.trading.auto_execute_live_enabled = True
        config_with_api.trading.auto_execute_max_daily_notional_usdt = 10_000.0
        config_with_api.trading.auto_execute_allowed_edge_labels = []
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={
                "symbol": "BTCUSDT",
                "entry_validity": "inside_entry_zone",
                "stop_loss_price": 48000.0,
                "take_profit_price": 53000.0,
                "notional_usdt": 6000.0,
            }),
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
            patch.object(pipeline.db, "sum_recent_executed_notional_usdt", return_value=5000.0),
            patch.object(pipeline, "_do_execute") as mock_exec,
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            MockOrder.return_value.get_open_orders.return_value = []
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert result.status == "auto_execute_blocked"
        assert "名义金额" in (result.execution_reason or "")
        mock_exec.assert_not_called()

    def test_auto_execute_blocked_when_edge_label_not_allowed(self, config_with_api):
        config_with_api.trading.auto_execute_allowed_symbols = ["BTCUSDT"]
        config_with_api.trading.auto_execute_live_enabled = True
        config_with_api.trading.auto_execute_allowed_edge_labels = ["allowlist_promoted"]
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={
                "symbol": "BTCUSDT",
                "entry_validity": "inside_entry_zone",
                "stop_loss_price": 48000.0,
                "take_profit_price": 53000.0,
            }),
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
            patch.object(pipeline.db, "get_edge_stats", return_value={
                "by_regime": [],
                "by_template": [],
                "by_narrative": [],
                "by_quality_grade": [],
                "by_profile_mode": [],
                "by_edge_policy": [],
                "top_positive_edges": [],
            }),
            patch.object(pipeline, "_do_execute") as mock_exec,
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            MockOrder.return_value.get_open_orders.return_value = []
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert result.status == "auto_execute_blocked"
        assert "edge policy label" in (result.execution_reason or "")
        mock_exec.assert_not_called()

    def test_auto_execute_blocked_when_opportunity_score_too_low(self, config_with_api):
        config_with_api.trading.auto_execute_allowed_symbols = ["BTCUSDT"]
        config_with_api.trading.auto_execute_live_enabled = True
        config_with_api.trading.auto_execute_allowed_edge_labels = []
        config_with_api.trading.opportunity_score_min_auto_execute = 90.0
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={
                "symbol": "BTCUSDT",
                "entry_validity": "inside_entry_zone",
                "stop_loss_price": 48000.0,
                "take_profit_price": 53000.0,
            }),
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
            patch.object(pipeline, "_do_execute") as mock_exec,
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            MockOrder.return_value.get_open_orders.return_value = []
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert result.status == "auto_execute_blocked"
        assert "opportunity score" in (result.execution_reason or "")
        mock_exec.assert_not_called()

    def test_auto_execute_blocked_for_negative_edge_without_explicit_allowlist(self, config_with_api):
        config_with_api.trading.auto_execute_allowed_symbols = ["BTCUSDT"]
        config_with_api.trading.auto_execute_live_enabled = True
        config_with_api.trading.auto_execute_allowed_edge_labels = []
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={
                "symbol": "BTCUSDT",
                "entry_validity": "inside_entry_zone",
                "stop_loss_price": 48000.0,
                "take_profit_price": 53000.0,
            }),
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
            patch.object(pipeline, "_do_execute") as mock_exec,
            patch.object(pipeline.db, "get_edge_stats", return_value={
                "by_regime": [
                    {
                        "group": "risk_on_trend",
                        "samples": 4,
                        "expectancy_pnl_pct": -0.8,
                        "profit_factor": 0.7,
                    }
                ],
                "by_template": [],
                "by_narrative": [],
                "by_quality_grade": [],
                "by_profile_mode": [],
                "by_edge_policy": [],
                "top_positive_edges": [],
            }),
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            MockOrder.return_value.get_open_orders.return_value = []
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False, auto_execute=True)

        assert result.status in {"auto_execute_blocked", "blocked_by_risk"}
        assert (
            "负 edge" in (result.execution_reason or "")
            or "Edge policy blocked current slice" in result.message
            or "edge policy blocked current slice" in (result.execution_reason or "")
        )
        mock_exec.assert_not_called()


class TestEdgePolicyOverlay:
    def test_promoted_edge_slice_boosts_size(self, config_no_api):
        pipeline = TradePipeline(config=config_no_api, dry_run=True)
        base_risk = RiskDecision(
            approved=True,
            adjusted_action="open_long",
            adjusted_size_pct=3.0,
            adjusted_leverage=6,
            violated_rules=[],
            warnings=[],
            rationale="ok",
            setup_quality_score=88.0,
            setup_quality_grade="A",
            gating_profile="conviction_override",
        )
        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline.risk_gate, "evaluate", return_value=base_risk),
            patch.object(
                pipeline.db,
                "get_edge_stats",
                return_value={
                    "by_regime": [
                        {
                            "group": "risk_on_trend",
                            "samples": 4,
                            "expectancy_pnl": 80.0,
                            "expectancy_pnl_pct": 1.8,
                            "profit_factor": 1.5,
                        }
                    ],
                    "by_template": [],
                    "by_narrative": [],
                    "by_quality_grade": [],
                    "by_profile_mode": [],
                    "by_edge_policy": [],
                    "top_positive_edges": [],
                },
            ),
        ):
            _, _, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert risk.edge_policy_label == "promoted"
        assert risk.adjusted_size_pct is not None
        assert risk.adjusted_size_pct > 3.0
        assert risk.opportunity_score is not None
        assert result.status == "dry_run"

    def test_blocked_edge_slice_turns_setup_into_hold(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)
        base_risk = RiskDecision(
            approved=True,
            adjusted_action="open_long",
            adjusted_size_pct=3.0,
            adjusted_leverage=6,
            violated_rules=[],
            warnings=[],
            rationale="ok",
            setup_quality_score=88.0,
            setup_quality_grade="A",
            gating_profile="conviction_override",
        )
        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_account_summary",
                return_value={
                    "futures_balance_usdt": 10000.0,
                    "spot_balance_usdt": 0.0,
                    "total_balance_usdt": 10000.0,
                    "drawdown_pct": 0.0,
                },
            ),
            patch.object(pipeline, "_get_positions", return_value=[]),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline.risk_gate, "evaluate", return_value=base_risk),
            patch.object(
                pipeline.db,
                "get_edge_stats",
                return_value={
                    "by_regime": [
                        {
                            "group": "risk_on_trend",
                            "samples": 4,
                            "expectancy_pnl": -40.0,
                            "expectancy_pnl_pct": -0.8,
                            "profit_factor": 0.7,
                        }
                    ],
                    "by_template": [],
                    "by_narrative": [],
                    "by_quality_grade": [],
                    "by_profile_mode": [],
                    "by_edge_policy": [],
                    "top_positive_edges": [],
                },
            ),
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            _, _, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert risk.edge_policy_label == "blocked"
        assert risk.approved is False
        assert result.status == "blocked_by_risk"

    def test_allowlist_promotes_even_without_history_match(self, config_no_api):
        config_no_api.trading.edge_policy_allowlist = ["template:core_trend_follow"]
        pipeline = TradePipeline(config=config_no_api, dry_run=True)
        base_risk = RiskDecision(
            approved=True,
            adjusted_action="open_long",
            adjusted_size_pct=2.0,
            adjusted_leverage=5,
            violated_rules=[],
            warnings=[],
            rationale="ok",
            setup_quality_score=75.0,
            setup_quality_grade="B",
            gating_profile="stable_mode",
        )
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
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline.risk_gate, "evaluate", return_value=base_risk),
            patch.object(pipeline.db, "get_edge_stats", return_value={}),
        ):
            _, _, risk, _ = pipeline.run("BTCUSDT", write_vault=False)

        assert risk.edge_policy_label == "allowlist_promoted"
        assert risk.adjusted_size_pct is not None
        assert risk.adjusted_size_pct > 2.0

    def test_denylist_blocks_even_without_history_match(self, config_with_api):
        config_with_api.trading.edge_policy_denylist = ["narrative:store_of_value"]
        pipeline = TradePipeline(config=config_with_api, dry_run=False)
        base_risk = RiskDecision(
            approved=True,
            adjusted_action="open_long",
            adjusted_size_pct=2.0,
            adjusted_leverage=5,
            violated_rules=[],
            warnings=[],
            rationale="ok",
            setup_quality_score=75.0,
            setup_quality_grade="B",
            gating_profile="stable_mode",
        )
        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(pipeline, "_get_account_summary", return_value={"futures_balance_usdt": 10000.0, "spot_balance_usdt": 0.0, "total_balance_usdt": 10000.0, "drawdown_pct": 0.0}),
            patch.object(pipeline, "_get_positions", return_value=[]),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
            patch.object(pipeline.risk_gate, "evaluate", return_value=base_risk),
            patch.object(pipeline.db, "get_edge_stats", return_value={}),
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            _, _, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert risk.edge_policy_label == "denylist_blocked"
        assert risk.approved is False
        assert result.status == "blocked_by_risk"


class TestOpportunityScore:
    def test_opportunity_score_is_attached_to_risk(self, config_no_api):
        pipeline = TradePipeline(config=config_no_api, dry_run=True)
        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research_high_conviction()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(
                pipeline,
                "_get_market_snapshot",
                return_value={
                    "symbol": "BTCUSDT",
                    "spot_price": 50000.0,
                    "futures_mark_price": 50000.0,
                    "asset_tier": "core",
                    "narrative_tag": "store_of_value",
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "crowding_regime": "balanced",
                    "volatility_regime": "normal",
                },
            ),
        ):
            _, _, risk, _ = pipeline.run("BTCUSDT", write_vault=False)

        assert risk.opportunity_score is not None
        assert risk.opportunity_score > 0
        assert risk.opportunity_bucket in {"elite", "strong", "watchlist", "avoid"}

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

    def test_ensemble_research_path_takes_precedence(self, config_with_api):
        config_with_api.claude.ensemble_enabled = True
        config_with_api.claude.ensemble_models = ["claude-opus-4-5", "claude-sonnet-4-6"]
        config_with_api.trading.combined_ai_calls = True

        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        with (
            patch.object(pipeline.advisor, "generate_research_ensemble", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
            patch.object(pipeline, "_get_market_snapshot", return_value={"symbol": "BTCUSDT", "execution_template": "core_trend_follow"}),
            patch.object(pipeline, "_get_account_summary", return_value={"futures_balance_usdt": 10000.0, "spot_balance_usdt": 0.0, "total_balance_usdt": 10000.0, "drawdown_pct": 0.0}),
            patch.object(pipeline, "_get_positions", return_value=[]),
            patch.object(pipeline, "_get_onchain_snapshot", return_value={}),
            patch.object(pipeline, "_get_event_snapshot", return_value={}),
            patch.object(pipeline.portfolio_manager, "build_snapshot", return_value={"gross_exposure_pct": 0.0}),
            patch.object(pipeline.portfolio_manager, "recommend_budget", return_value={"recommended_max_size_pct": 3.0, "portfolio_role": "anchor"}),
            patch.object(pipeline.risk_gate, "evaluate", return_value=RiskDecision(True, "open_long", 3.0, 10, [], [], "ok")),
            patch.object(pipeline, "_build_manual_order_ticket", return_value={"symbol": "BTCUSDT"}),
            patch.object(pipeline, "_write_vault_report", return_value=None),
        ):
            research, plan, risk, result = pipeline.run("BTCUSDT", write_vault=False)

        assert research.stance == "long"
        assert plan.action == "open_long"
        assert result.status == "manual_review_required"


class TestRiskStats:
    def test_risk_rule_stats_aggregates_warnings_and_violations(self, tmp_db):
        db = TradeRunDB(tmp_db)
        r1 = RiskDecision(
            approved=True,
            adjusted_action="open_long",
            adjusted_size_pct=3.0,
            adjusted_leverage=5,
            violated_rules=[],
            warnings=["Funding crowded", "BTC regime risk-off"],
            rationale="warn",
        )
        r2 = RiskDecision(
            approved=False,
            adjusted_action="hold",
            adjusted_size_pct=0.0,
            adjusted_leverage=3,
            violated_rules=["size_pct exceeds max", "BTC regime risk-off"],
            warnings=["Funding crowded"],
            rationale="block",
        )
        for idx, risk_decision in enumerate((r1, r2), start=1):
            db.save_run(
                symbol="BTCUSDT",
                model="claude-test",
                research_decision=_mock_research() if idx == 1 else _mock_research_short(),
                execution_plan=_mock_plan(),
                risk_decision=risk_decision,
                execution_result=ExecutionResult(
                    executed=False,
                    status="manual_review_required",
                    symbol="BTCUSDT",
                    final_action=risk_decision.adjusted_action or "hold",
                    final_size_pct=risk_decision.adjusted_size_pct or 0.0,
                    message="m",
                ),
                account_snapshot={},
                position_snapshot=[],
                dry_run=True,
            )

        stats = db.get_risk_rule_stats(symbol="BTCUSDT", limit=10)
        assert stats["runs_scanned"] == 2
        assert stats["approved_runs"] == 1
        assert stats["blocked_runs"] == 1
        assert stats["warning_runs"] == 2
        assert stats["top_warnings"][0][0] == "Funding crowded"
        assert any("size_pct exceeds max" == rule for rule, _ in stats["top_violations"])

    def test_risk_rule_stats_include_linked_trade_outcomes(self, tmp_db):
        db = TradeRunDB(tmp_db)
        run_id = db.save_run(
            symbol="BTCUSDT",
            model="claude-test",
            research_decision=_mock_research(),
            execution_plan=_mock_plan(),
            risk_decision=RiskDecision(
                approved=True,
                adjusted_action="open_long",
                adjusted_size_pct=3.0,
                adjusted_leverage=5,
                violated_rules=[],
                warnings=["Funding crowded"],
                rationale="warn",
            ),
            execution_result=ExecutionResult(
                executed=False,
                status="manual_review_required",
                symbol="BTCUSDT",
                final_action="open_long",
                final_size_pct=3.0,
                message="manual",
            ),
            account_snapshot={},
            position_snapshot=[],
            dry_run=False,
        )
        journal = TradeJournal(tmp_db)
        journal.record_trade(
            symbol="BTCUSDT",
            direction="LONG",
            entry_price=100.0,
            exit_price=110.0,
            quantity=10.0,
            leverage=5.0,
            open_time=datetime.now() - timedelta(hours=5),
            close_time=datetime.now(),
            linked_run_id=run_id,
        )

        stats = db.get_risk_rule_stats(symbol="BTCUSDT", limit=10)
        assert stats["linked_runs"] == 1
        assert stats["linked_trades"] == 1
        assert stats["linked_win_rate"] == 100.0
        first = stats["warning_effectiveness"][0]
        assert first["rule"] == "Funding crowded"
        assert first["linked_trades"] == 1
        assert first["avg_realized_pnl"] > 0

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

        def capture_research(
            symbol,
            account_summary,
            positions,
            market_snapshot,
            vault_context,
            trade_history,
            reflections,
            onchain_snapshot=None,
            portfolio_snapshot=None,
            portfolio_budget=None,
            event_snapshot=None,
            adaptive_context=None,
        ):
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

    def test_pipeline_passes_adaptive_context_to_advisor(self, config_no_api):
        pipeline = TradePipeline(config=config_no_api, dry_run=True)

        captured_context = {}

        def capture_research(
            symbol,
            account_summary,
            positions,
            market_snapshot,
            vault_context,
            trade_history,
            reflections,
            onchain_snapshot=None,
            portfolio_snapshot=None,
            portfolio_budget=None,
            event_snapshot=None,
            adaptive_context=None,
        ):
            captured_context.update(adaptive_context or {})
            return _mock_research()

        with (
            patch.object(
                pipeline,
                "_get_adaptive_context",
                return_value={
                    "enabled": True,
                    "profile_mode": "defensive",
                    "guidance_lines": ["Recent loss streak = 3."],
                },
            ),
            patch.object(pipeline.advisor, "generate_research", side_effect=capture_research),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            pipeline.run("BTCUSDT", write_vault=False)

        assert captured_context["enabled"] is True
        assert captured_context["profile_mode"] == "defensive"


class TestReplayStats:
    def test_replay_stats_group_linked_samples(self, tmp_db):
        db = TradeRunDB(tmp_db)
        run_id = db.save_run(
            symbol="BTCUSDT",
            model="claude-test",
            research_decision=_mock_research(),
            execution_plan=_mock_plan(),
            risk_decision=RiskDecision(
                approved=True,
                adjusted_action="open_long",
                adjusted_size_pct=3.0,
                adjusted_leverage=10,
                violated_rules=[],
                warnings=["Funding crowded"],
                rationale="ok",
                setup_quality_score=88.0,
                setup_quality_grade="A",
                gating_profile="stable_mode",
            ),
            execution_result=ExecutionResult(
                executed=False,
                status="manual_review_required",
                symbol="BTCUSDT",
                final_action="open_long",
                final_size_pct=3.0,
                message="manual",
            ),
            account_snapshot={},
            market_snapshot={
                "btc_market_regime": "risk_on_trend",
                "execution_template": "core_trend_follow",
                "narrative_tag": "store_of_value",
                "asset_tier": "core",
            },
            adaptive_context={
                "enabled": True,
                "profile_mode": "normal",
            },
            position_snapshot=[],
            dry_run=False,
        )

        journal = TradeJournal(tmp_db)
        journal.record_trade(
            symbol="BTCUSDT",
            direction="LONG",
            entry_price=100.0,
            exit_price=120.0,
            quantity=10.0,
            leverage=5.0,
            open_time=datetime.now() - timedelta(days=1),
            close_time=datetime.now(),
            linked_run_id=run_id,
        )

        stats = db.get_replay_stats(symbol="BTCUSDT", limit=10)
        assert stats["samples_scanned"] == 1
        assert stats["win_rate"] == 100.0
        assert stats["by_regime"][0]["group"] == "risk_on_trend"
        assert stats["by_template"][0]["group"] == "core_trend_follow"
        assert stats["by_narrative"][0]["group"] == "store_of_value"
        assert stats["by_profile_mode"][0]["group"] == "normal"
        assert stats["by_quality_grade"][0]["group"] == "A"


class TestEdgeStats:
    def test_edge_stats_calculates_expectancy_and_profit_factor(self, tmp_db):
        db = TradeRunDB(tmp_db)
        for run_id, pnl, pnl_pct in ((1, 200.0, 10.0), (2, -100.0, -5.0)):
            db.save_run(
                symbol="BTCUSDT",
                model="claude-test",
                research_decision=_mock_research(),
                execution_plan=_mock_plan(),
                risk_decision=RiskDecision(
                    approved=True,
                    adjusted_action="open_long",
                    adjusted_size_pct=3.0,
                    adjusted_leverage=10,
                    violated_rules=[],
                    warnings=[],
                    rationale="ok",
                    setup_quality_score=88.0,
                    setup_quality_grade="A",
                    gating_profile="conviction_override",
                ),
                execution_result=ExecutionResult(
                    executed=False,
                    status="manual_review_required",
                    symbol="BTCUSDT",
                    final_action="open_long",
                    final_size_pct=3.0,
                    message="manual",
                ),
                account_snapshot={},
                market_snapshot={
                    "btc_market_regime": "risk_on_trend",
                    "execution_template": "core_trend_follow",
                    "narrative_tag": "store_of_value",
                    "asset_tier": "core",
                },
                adaptive_context={},
                position_snapshot=[],
                dry_run=False,
            )
            journal = TradeJournal(tmp_db)
            journal.record_trade(
                symbol="BTCUSDT",
                direction="LONG",
                entry_price=100.0,
                exit_price=120.0 if pnl > 0 else 95.0,
                quantity=10.0,
                leverage=5.0,
                open_time=datetime.now() - timedelta(days=1),
                close_time=datetime.now(),
                realized_pnl=pnl,
                linked_run_id=run_id,
            )

        stats = db.get_edge_stats(symbol="BTCUSDT", limit=10, min_samples=2)
        overall = stats["overall"]
        assert overall["samples"] == 2
        assert overall["win_rate"] == 50.0
        assert overall["avg_win_pnl"] == 200.0
        assert overall["avg_loss_pnl"] == 100.0
        assert overall["payoff_ratio"] == 2.0
        assert overall["profit_factor"] == 2.0
        assert overall["expectancy_pnl"] == 50.0


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


# ──────────────────────────── P1: trailing stop & staged TP in ticket ────────────────────────────

class TestTrailingStopAndStagedTp:
    def test_ticket_contains_trailing_stop_suggestion(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

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
            mock_binance_inst.futures_client.mark_price.return_value = {"markPrice": "50000"}
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

        # Ticket should have trailing_stop and staged_tp
        ticket = result.manual_order_details
        assert ticket is not None
        assert "trailing_stop_suggestion" in ticket
        ts = ticket["trailing_stop_suggestion"]
        assert "activation_price" in ts
        assert ts["activation_pct"] == config_with_api.trading.trailing_stop_activation_pct
        assert ts["trailing_distance_pct"] == config_with_api.trading.trailing_stop_distance_pct

        assert "staged_tp_plan" in ticket
        assert len(ticket["staged_tp_plan"]) == 2  # default 2 levels
        assert ticket["staged_tp_plan"][0]["close_pct"] == 30
