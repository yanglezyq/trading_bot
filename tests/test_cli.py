"""CLI smoke tests for safe preview behavior."""

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from trading.main import app


runner = CliRunner()


def _write_temp_config(tmp_path: Path) -> Path:
    vault = tmp_path / "vault" / "体系化交易"
    (vault / "【持仓管理】").mkdir(parents=True, exist_ok=True)
    (vault / "【报告】").mkdir(parents=True, exist_ok=True)
    (vault / "合约交易体系-完整指南.md").write_text("# system guide", encoding="utf-8")

    config = {
        "vault": {
            "path": str(tmp_path / "vault"),
            "trading_dir": "体系化交易",
            "system_guide": "合约交易体系-完整指南.md",
            "position_tracking_dir": "【持仓管理】",
            "reports_dir": "【报告】",
            "decision_spec": "决策建议生成规范.md",
            "research_spec": "币种调研报告规范(v2.0-通用版).md",
        },
        "risk": {
            "max_account_drawdown": -0.20,
            "alert_drawdown": -0.10,
            "suspend_drawdown": -0.30,
            "max_leverage": 35,
        },
        "trading": {
            "dry_run": True,
            "default_stop_loss_pct": 0.05,
            "default_take_profit_pct": 0.15,
            "max_position_size_pct": 0.05,
            "event_driven_max_pct": 0.05,
        },
        "monitor": {"interval_seconds": 60, "price_stream_reconnect": True, "stream_symbols": ["BTCUSDT"]},
        "logging": {"level": "INFO", "sqlite_db": str(tmp_path / "test.db"), "console_format": "rich"},
        "binance": {"futures_testnet": True, "spot_testnet": True},
        "claude": {"model": "claude-sonnet-4-6", "cache_enabled": True, "cache_ttl_seconds": 300, "max_tokens": 2000},
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return config_path


def test_sync_dry_run_uses_mock_positions_without_api(tmp_path):
    config_path = _write_temp_config(tmp_path)
    result = runner.invoke(app, ["sync", "--dry-run", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "Loading mock positions for preview" in result.output
    assert "CHZUSDT" in result.output
    assert "Dry-run mode: no changes made to Vault" in result.output


def test_status_shows_auto_execute_mode(tmp_path):
    config_path = _write_temp_config(tmp_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["trading"]["auto_execute_enabled"] = True
    data["trading"]["auto_execute_require_cli_flag"] = False
    data["trading"]["auto_execute_live_enabled"] = False
    data["binance"]["futures_testnet"] = False
    data["binance"]["spot_testnet"] = False
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    result = runner.invoke(app, ["status", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "Auto Execute" in result.output
    assert "Config enabled, but live disabled" in result.output


def test_risk_stats_command_outputs_summary(tmp_path):
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "test.db"

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                model TEXT NOT NULL,
                research_decision_json TEXT,
                execution_plan_json TEXT,
                risk_decision_json TEXT,
                execution_result_json TEXT,
                account_snapshot_json TEXT,
                market_snapshot_json TEXT,
                onchain_snapshot_json TEXT,
                position_snapshot_json TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                report_path TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        risk_json = json.dumps(
            {
                "approved": False,
                "adjusted_action": "hold",
                "adjusted_size_pct": 0.0,
                "adjusted_leverage": 3,
                "violated_rules": ["size_pct exceeds max"],
                "warnings": ["Funding crowded"],
                "rationale": "blocked",
            },
            ensure_ascii=False,
        )
        conn.execute(
            """
            INSERT INTO trade_runs (
                symbol, model, risk_decision_json, execution_result_json, dry_run, success
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("BTCUSDT", "claude-test", risk_json, "{}", 1, 0),
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                notes TEXT DEFAULT '',
                linked_run_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trade_records (
                symbol, direction, entry_price, exit_price, quantity, leverage,
                realized_pnl, pnl_pct, open_time, close_time, notes, linked_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "BTCUSDT",
                "LONG",
                100000.0,
                103000.0,
                0.1,
                5.0,
                300.0,
                15.0,
                "2026-01-01T00:00:00",
                "2026-01-02T00:00:00",
                "linked",
                1,
            ),
        )
        conn.commit()

    result = runner.invoke(app, ["risk-stats", "--symbol", "BTCUSDT", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "Risk Rule Stats" in result.output
    assert "Funding crowded" in result.output
    assert "size_pct exceeds max" in result.output
    assert "Warning Rule Outcome Context" in result.output
    assert "linked_total_realized_pnl" in result.output


def test_record_command_can_link_pipeline_run(tmp_path):
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "test.db"

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                model TEXT NOT NULL,
                research_decision_json TEXT,
                execution_plan_json TEXT,
                risk_decision_json TEXT,
                execution_result_json TEXT,
                account_snapshot_json TEXT,
                market_snapshot_json TEXT,
                adaptive_context_json TEXT,
                onchain_snapshot_json TEXT,
                position_snapshot_json TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                report_path TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trade_runs (symbol, model, execution_result_json, dry_run, success)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("BTCUSDT", "claude-test", "{}", 1, 0),
        )
        conn.commit()

    result = runner.invoke(
        app,
        [
            "record",
            "BTCUSDT",
            "--side",
            "long",
            "--entry",
            "100000",
            "--exit",
            "101500",
            "--qty",
            "0.1",
            "--leverage",
            "5",
            "--open",
            "2026-01-02 00:00",
            "--close",
            "2026-01-03 00:00",
            "--run-id",
            "1",
            "--config",
            str(config_path),
        ],
    )
    assert result.exit_code == 0
    assert "已关联 pipeline run #1" in result.output


def test_replay_stats_command_outputs_group_tables(tmp_path):
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "test.db"

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                model TEXT NOT NULL,
                research_decision_json TEXT,
                execution_plan_json TEXT,
                risk_decision_json TEXT,
                execution_result_json TEXT,
                account_snapshot_json TEXT,
                market_snapshot_json TEXT,
                adaptive_context_json TEXT,
                onchain_snapshot_json TEXT,
                position_snapshot_json TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                report_path TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                notes TEXT DEFAULT '',
                linked_run_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trade_runs (
                id, symbol, model, research_decision_json, execution_plan_json,
                risk_decision_json, execution_result_json, market_snapshot_json,
                adaptive_context_json, dry_run, success
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "BTCUSDT",
                "claude-test",
                json.dumps({"stance": "long", "confidence": 0.8}),
                json.dumps({"action": "open_long"}),
                json.dumps({"approved": True, "warnings": ["Funding crowded"], "violated_rules": []}),
                json.dumps({"final_action": "open_long"}),
                json.dumps(
                    {
                        "btc_market_regime": "risk_on_trend",
                        "execution_template": "core_trend_follow",
                        "narrative_tag": "store_of_value",
                        "asset_tier": "core",
                    }
                ),
                json.dumps({"profile_mode": "normal"}),
                0,
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO trade_records (
                symbol, direction, entry_price, exit_price, quantity, leverage,
                realized_pnl, pnl_pct, open_time, close_time, notes, linked_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "BTCUSDT",
                "LONG",
                100000.0,
                102000.0,
                0.1,
                5.0,
                200.0,
                10.0,
                "2026-01-01T00:00:00",
                "2026-01-02T00:00:00",
                "linked",
                1,
            ),
        )
        conn.commit()

    result = runner.invoke(app, ["replay-stats", "--symbol", "BTCUSDT", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "Replay Stats" in result.output
    assert "Replay By BTC Regime" in result.output
    assert "core_trend_follow" in result.output


def test_edge_stats_command_outputs_expectancy_tables(tmp_path):
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "test.db"

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                model TEXT NOT NULL,
                research_decision_json TEXT,
                execution_plan_json TEXT,
                risk_decision_json TEXT,
                execution_result_json TEXT,
                account_snapshot_json TEXT,
                market_snapshot_json TEXT,
                adaptive_context_json TEXT,
                onchain_snapshot_json TEXT,
                position_snapshot_json TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                report_path TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                notes TEXT DEFAULT '',
                linked_run_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for run_id, pnl, pnl_pct in ((1, 200.0, 10.0), (2, -100.0, -5.0)):
            conn.execute(
                """
                INSERT INTO trade_runs (
                    id, symbol, model, research_decision_json, execution_plan_json,
                    risk_decision_json, execution_result_json, market_snapshot_json,
                    adaptive_context_json, dry_run, success
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "BTCUSDT",
                    "claude-test",
                    json.dumps({"stance": "long", "confidence": 0.8}),
                    json.dumps({"action": "open_long"}),
                    json.dumps(
                        {
                            "approved": True,
                            "warnings": [],
                            "violated_rules": [],
                            "setup_quality_score": 88.0,
                            "setup_quality_grade": "A",
                            "gating_profile": "conviction_override",
                        }
                    ),
                    json.dumps({"final_action": "open_long"}),
                    json.dumps(
                        {
                            "btc_market_regime": "risk_on_trend",
                            "execution_template": "core_trend_follow",
                            "narrative_tag": "store_of_value",
                            "asset_tier": "core",
                        }
                    ),
                    json.dumps({"profile_mode": "normal"}),
                    0,
                    0,
                ),
            )
            conn.execute(
                """
                INSERT INTO trade_records (
                    symbol, direction, entry_price, exit_price, quantity, leverage,
                    realized_pnl, pnl_pct, open_time, close_time, notes, linked_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "BTCUSDT",
                    "LONG",
                    100000.0,
                    102000.0 if pnl > 0 else 99000.0,
                    0.1,
                    5.0,
                    pnl,
                    pnl_pct,
                    "2026-01-01T00:00:00",
                    "2026-01-02T00:00:00",
                    "linked",
                    run_id,
                ),
            )
        conn.commit()

    result = runner.invoke(app, ["edge-stats", "--symbol", "BTCUSDT", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "Edge Stats" in result.output
    assert "Top Positive Edges" in result.output
    assert "core_trend_follow" in result.output


def test_batch_trade_output_includes_edge_and_quality(tmp_path):
    config_path = _write_temp_config(tmp_path)
    from unittest.mock import patch
    from types import SimpleNamespace

    with patch("trading.main.TradePipeline") as MockPipeline:
        mock_pipeline = MockPipeline.return_value
        from trading.ai.schemas import ResearchDecision, ExecutionPlan, RiskDecision, ExecutionResult

        def _mk_bundle(symbol: str, conf: float, quality: float, edge: str):
            return SimpleNamespace(
                symbol=symbol,
                research=ResearchDecision(
                    symbol=symbol,
                    stance="long",
                    confidence=conf,
                    thesis="x",
                    market_structure="x",
                    evidence=["x"],
                    catalysts=["x"],
                    risks=["x"],
                    invalidation="x",
                    time_horizon="swing",
                    preferred_market="futures",
                ),
                plan=ExecutionPlan(
                    action="open_long",
                    size_pct=3.0,
                    leverage=5,
                    entry_idea="x",
                    stop_loss_pct=5.0,
                    take_profit_pct=10.0,
                    rationale="x",
                ),
                risk=RiskDecision(
                    approved=True,
                    adjusted_action="open_long",
                    adjusted_size_pct=3.0,
                    adjusted_leverage=5,
                    violated_rules=[],
                    warnings=[],
                    rationale="ok",
                    setup_quality_score=quality,
                    setup_quality_grade="A",
                    gating_profile="conviction_override",
                    edge_policy_label=edge,
                    edge_policy_reasons=[],
                    edge_policy_expectancy_pnl_pct=1.5 if edge == "promoted" else 0.2,
                    opportunity_score=92.0 if edge == "promoted" else 61.0,
                    opportunity_bucket="elite" if edge == "promoted" else "watchlist",
                ),
            )

        def _mk_result(symbol: str, status: str = "manual_review_required"):
            return ExecutionResult(
                    executed=False,
                    status=status,
                    symbol=symbol,
                    final_action="open_long",
                    final_size_pct=3.0,
                    final_leverage=5,
                    message="manual",
                )

        mock_pipeline.analyze.side_effect = [
            _mk_bundle("BTCUSDT", 0.82, 86.0, "promoted"),
            _mk_bundle("ETHUSDT", 0.65, 70.0, "neutral"),
        ]
        mock_pipeline.finalize_analysis.side_effect = [
            _mk_result("BTCUSDT"),
            _mk_result("ETHUSDT"),
        ]
        result = runner.invoke(app, ["batch-trade", "BTCUSDT", "ETHUSDT", "--dry-run", "--top-n", "1", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "Signals Found" in result.output
    assert "Top 1 Opportunities" in result.output
    assert "Score" in result.output
    assert "Quality" in result.output
    assert "Edge" in result.output
    assert "较低优先级信号未进入 Top-N 机会池" in result.output


def test_batch_trade_auto_execute_only_reruns_top_n(tmp_path):
    config_path = _write_temp_config(tmp_path)
    from unittest.mock import patch
    from types import SimpleNamespace

    with patch("trading.main.TradePipeline") as MockPipeline:
        mock_pipeline = MockPipeline.return_value
        from trading.ai.schemas import ResearchDecision, ExecutionPlan, RiskDecision, ExecutionResult

        def _mk_bundle(symbol: str, conf: float, edge: str):
            return SimpleNamespace(
                symbol=symbol,
                research=ResearchDecision(
                    symbol=symbol,
                    stance="long",
                    confidence=conf,
                    thesis="x",
                    market_structure="x",
                    evidence=["x"],
                    catalysts=["x"],
                    risks=["x"],
                    invalidation="x",
                    time_horizon="swing",
                    preferred_market="futures",
                ),
                plan=ExecutionPlan(
                    action="open_long",
                    size_pct=3.0,
                    leverage=5,
                    entry_idea="x",
                    stop_loss_pct=5.0,
                    take_profit_pct=10.0,
                    rationale="x",
                ),
                risk=RiskDecision(
                    approved=True,
                    adjusted_action="open_long",
                    adjusted_size_pct=3.0,
                    adjusted_leverage=5,
                    violated_rules=[],
                    warnings=[],
                    rationale="ok",
                    setup_quality_score=85.0 if edge == "promoted" else 70.0,
                    setup_quality_grade="A",
                    gating_profile="conviction_override",
                    edge_policy_label=edge,
                    edge_policy_reasons=[],
                    edge_policy_expectancy_pnl_pct=1.5 if edge == "promoted" else 0.2,
                ),
            )

        def _mk_result(symbol: str, status: str = "manual_review_required"):
            return ExecutionResult(
                    executed=(status == "executed"),
                    status=status,
                    symbol=symbol,
                    final_action="open_long",
                    final_size_pct=3.0,
                    final_leverage=5,
                    message=status,
                )

        mock_pipeline.analyze.side_effect = [
            _mk_bundle("BTCUSDT", 0.90, "promoted"),
            _mk_bundle("ETHUSDT", 0.60, "neutral"),
        ]
        mock_pipeline.finalize_analysis.side_effect = [
            _mk_result("BTCUSDT", status="executed"),
            _mk_result("ETHUSDT", status="manual_review_required"),
        ]

        result = runner.invoke(
            app,
            ["batch-trade", "BTCUSDT", "ETHUSDT", "--auto-execute", "--top-n", "1", "--config", str(config_path)],
        )

    assert result.exit_code == 0
    assert "Auto-executing top 1 candidate" in result.output
    assert mock_pipeline.analyze.call_count == 2
    assert mock_pipeline.finalize_analysis.call_count == 2


def test_batch_trade_auto_execute_respects_max_candidate_cap(tmp_path):
    config_path = _write_temp_config(tmp_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["monitor"]["batch_top_n_signals"] = 3
    data["monitor"]["batch_auto_execute_max_candidates"] = 1
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    from unittest.mock import patch
    from types import SimpleNamespace

    with patch("trading.main.TradePipeline") as MockPipeline:
        mock_pipeline = MockPipeline.return_value
        from trading.ai.schemas import ResearchDecision, ExecutionPlan, RiskDecision, ExecutionResult

        def _mk_bundle(symbol: str, conf: float, edge: str):
            return SimpleNamespace(
                symbol=symbol,
                research=ResearchDecision(
                    symbol=symbol,
                    stance="long",
                    confidence=conf,
                    thesis="x",
                    market_structure="x",
                    evidence=["x"],
                    catalysts=["x"],
                    risks=["x"],
                    invalidation="x",
                    time_horizon="swing",
                    preferred_market="futures",
                ),
                plan=ExecutionPlan(
                    action="open_long",
                    size_pct=3.0,
                    leverage=5,
                    entry_idea="x",
                    stop_loss_pct=5.0,
                    take_profit_pct=10.0,
                    rationale="x",
                ),
                risk=RiskDecision(
                    approved=True,
                    adjusted_action="open_long",
                    adjusted_size_pct=3.0,
                    adjusted_leverage=5,
                    violated_rules=[],
                    warnings=[],
                    rationale="ok",
                    setup_quality_score=85.0,
                    setup_quality_grade="A",
                    gating_profile="conviction_override",
                    edge_policy_label=edge,
                    edge_policy_reasons=[],
                    edge_policy_expectancy_pnl_pct=1.5,
                    opportunity_score=90.0 if symbol == "BTCUSDT" else 80.0,
                    opportunity_bucket="elite",
                ),
            )

        def _mk_result(symbol: str, status: str = "manual_review_required"):
            return ExecutionResult(
                executed=(status == "executed"),
                status=status,
                symbol=symbol,
                final_action="open_long",
                final_size_pct=3.0,
                final_leverage=5,
                message=status,
            )

        mock_pipeline.analyze.side_effect = [
            _mk_bundle("BTCUSDT", 0.90, "promoted"),
            _mk_bundle("ETHUSDT", 0.80, "promoted"),
            _mk_bundle("SOLUSDT", 0.70, "neutral"),
        ]
        mock_pipeline.finalize_analysis.side_effect = [
            _mk_result("BTCUSDT", "executed"),
            _mk_result("ETHUSDT"),
            _mk_result("SOLUSDT"),
        ]

        result = runner.invoke(
            app,
            ["batch-trade", "BTCUSDT", "ETHUSDT", "SOLUSDT", "--auto-execute", "--config", str(config_path)],
        )

    assert result.exit_code == 0
    assert "auto-exec 名额上限未进入自动执行" in result.output
    calls = mock_pipeline.finalize_analysis.call_args_list
    auto_flags = [call.kwargs["auto_execute"] for call in calls]
    assert auto_flags == [True, False, False]


def test_batch_trade_auto_execute_respects_batch_capital_budget(tmp_path):
    config_path = _write_temp_config(tmp_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["monitor"]["batch_top_n_signals"] = 2
    data["monitor"]["batch_auto_execute_max_candidates"] = 2
    data["trading"]["auto_execute_max_batch_capital_pct"] = 3.0
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    from unittest.mock import patch
    from types import SimpleNamespace

    with patch("trading.main.TradePipeline") as MockPipeline:
        mock_pipeline = MockPipeline.return_value
        from trading.ai.schemas import ResearchDecision, ExecutionPlan, RiskDecision, ExecutionResult

        def _mk_bundle(symbol: str, conf: float, edge: str, size: float):
            return SimpleNamespace(
                symbol=symbol,
                research=ResearchDecision(
                    symbol=symbol,
                    stance="long",
                    confidence=conf,
                    thesis="x",
                    market_structure="x",
                    evidence=["x"],
                    catalysts=["x"],
                    risks=["x"],
                    invalidation="x",
                    time_horizon="swing",
                    preferred_market="futures",
                ),
                plan=ExecutionPlan(
                    action="open_long",
                    size_pct=size,
                    leverage=5,
                    entry_idea="x",
                    stop_loss_pct=5.0,
                    take_profit_pct=10.0,
                    rationale="x",
                ),
                risk=RiskDecision(
                    approved=True,
                    adjusted_action="open_long",
                    adjusted_size_pct=size,
                    adjusted_leverage=5,
                    violated_rules=[],
                    warnings=[],
                    rationale="ok",
                    setup_quality_score=85.0,
                    setup_quality_grade="A",
                    gating_profile="conviction_override",
                    edge_policy_label=edge,
                    edge_policy_reasons=[],
                    edge_policy_expectancy_pnl_pct=1.5,
                    opportunity_score=90.0 if symbol == "BTCUSDT" else 85.0,
                    opportunity_bucket="elite",
                ),
            )

        def _mk_result(symbol: str, status: str):
            return ExecutionResult(
                executed=(status == "executed"),
                status=status,
                symbol=symbol,
                final_action="open_long",
                final_size_pct=3.0,
                final_leverage=5,
                message=status,
            )

        mock_pipeline.analyze.side_effect = [
            _mk_bundle("BTCUSDT", 0.90, "promoted", 2.0),
            _mk_bundle("ETHUSDT", 0.88, "promoted", 2.0),
        ]
        mock_pipeline.finalize_analysis.side_effect = [
            _mk_result("BTCUSDT", "executed"),
            _mk_result("ETHUSDT", "manual_review_required"),
        ]

        result = runner.invoke(
            app,
            ["batch-trade", "BTCUSDT", "ETHUSDT", "--auto-execute", "--config", str(config_path)],
        )

    assert result.exit_code == 0
    assert "超出本轮 auto-exec 资金预算上限" in result.output
    calls = mock_pipeline.finalize_analysis.call_args_list
    auto_flags = [call.kwargs["auto_execute"] for call in calls]
    assert auto_flags == [True, False]


def test_backtest_command_outputs_summary(tmp_path):
    config_path = _write_temp_config(tmp_path)
    from unittest.mock import patch
    from trading.pipeline.backtest import BacktestOutcome

    with patch("trading.main.BacktestRunner") as MockBacktest:
        mock_runner = MockBacktest.return_value
        mock_runner.replay_runs.return_value = [
            BacktestOutcome(
                run_id=1,
                symbol="BTCUSDT",
                action="open_long",
                entry_price=100.0,
                exit_price=110.0,
                exit_reason="take_profit",
                pnl_pct=10.0,
                holding_hours=3,
                entry_trend_bias="bullish",
                entry_volatility_regime="normal",
                entry_momentum_regime="up",
                entry_distance_to_ema21_pct=1.2,
                btc_market_regime="risk_on_trend",
                execution_template="core_trend_follow",
                narrative_tag="store_of_value",
            )
        ]
        mock_runner.summarize.return_value = {
            "trades": 1,
            "win_rate": 100.0,
            "avg_win_pct": 10.0,
            "avg_loss_pct": 0.0,
            "profit_factor": None,
            "expectancy_pct": 10.0,
            "total_pnl_pct": 10.0,
        }
        mock_runner.replay_windows.return_value = [
            {
                "window_index": 1,
                "start_run_id": 1,
                "end_run_id": 1,
                "trades": 1,
                "win_rate": 100.0,
                "expectancy_pct": 10.0,
                "profit_factor": None,
                "total_pnl_pct": 10.0,
                "max_drawdown_pct": 0.0,
            }
        ]
        mock_runner.walk_forward.return_value = [
            {
                "window_index": 1,
                "train_start_run_id": 1,
                "train_end_run_id": 2,
                "test_start_run_id": 3,
                "test_end_run_id": 3,
                "train_trades": 2,
                "train_expectancy_pct": 2.0,
                "train_profit_factor": 1.5,
                "test_trades": 1,
                "test_expectancy_pct": 1.0,
                "test_profit_factor": 1.2,
                "test_total_pnl_pct": 1.0,
            }
        ]
        mock_runner.summarize_walk_forward.return_value = {
            "windows": 1,
            "positive_test_windows": 1,
            "positive_test_ratio": 100.0,
            "avg_test_expectancy_pct": 1.0,
            "avg_test_total_pnl_pct": 1.0,
        }

        result = runner.invoke(app, ["backtest", "--symbol", "BTCUSDT", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Backtest Summary" in result.output
    assert "Backtest Outcomes" in result.output
    assert "Rolling Windows" in result.output
    assert "Walk-Forward Summary" in result.output
    assert "Walk-Forward Windows" in result.output


def test_tune_risk_command_outputs_and_writes_patch(tmp_path):
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "test.db"
    patch_path = tmp_path / "tuned-risk.yaml"

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                model TEXT NOT NULL,
                research_decision_json TEXT,
                execution_plan_json TEXT,
                risk_decision_json TEXT,
                execution_result_json TEXT,
                account_snapshot_json TEXT,
                market_snapshot_json TEXT,
                adaptive_context_json TEXT,
                onchain_snapshot_json TEXT,
                position_snapshot_json TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                report_path TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                notes TEXT DEFAULT '',
                linked_run_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trade_runs (
                id, symbol, model, research_decision_json, execution_plan_json,
                risk_decision_json, execution_result_json, market_snapshot_json,
                adaptive_context_json, dry_run, success
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "DOGEUSDT",
                "claude-test",
                json.dumps({"stance": "long", "confidence": 0.7}),
                json.dumps({"action": "open_long"}),
                json.dumps({"approved": True, "warnings": ["Funding crowded"], "violated_rules": []}),
                json.dumps({"final_action": "open_long"}),
                json.dumps(
                    {
                        "btc_market_regime": "panic_flush",
                        "execution_template": "high_beta_confirmation_only",
                        "narrative_tag": "meme",
                        "asset_tier": "high_beta_alt",
                    }
                ),
                json.dumps({"profile_mode": "normal"}),
                0,
                0,
            ),
        )
        for idx in range(3):
            conn.execute(
                """
                INSERT INTO trade_records (
                    symbol, direction, entry_price, exit_price, quantity, leverage,
                    realized_pnl, pnl_pct, open_time, close_time, notes, linked_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "DOGEUSDT",
                    "LONG",
                    0.1,
                    0.09,
                    1000.0,
                    3.0,
                    -100.0,
                    -5.0,
                    f"2026-01-0{idx+1}T00:00:00",
                    f"2026-01-0{idx+2}T00:00:00",
                    "loss",
                    1,
                ),
            )
        conn.commit()

    result = runner.invoke(
        app,
        ["tune-risk", "--symbol", "DOGEUSDT", "--config", str(config_path), "--output", str(patch_path)],
    )
    assert result.exit_code == 0
    assert "Risk Tuning Suggestions" in result.output
    assert "调优建议已导出" in result.output
    assert patch_path.exists()
    patch_text = patch_path.read_text(encoding="utf-8")
    assert "meme_max_leverage" in patch_text


def test_tune_risk_apply_and_rollback_config(tmp_path):
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "test.db"
    backup_dir = tmp_path / "backups"

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                model TEXT NOT NULL,
                research_decision_json TEXT,
                execution_plan_json TEXT,
                risk_decision_json TEXT,
                execution_result_json TEXT,
                account_snapshot_json TEXT,
                market_snapshot_json TEXT,
                adaptive_context_json TEXT,
                onchain_snapshot_json TEXT,
                position_snapshot_json TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                report_path TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                notes TEXT DEFAULT '',
                linked_run_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trade_runs (
                id, symbol, model, research_decision_json, execution_plan_json,
                risk_decision_json, execution_result_json, market_snapshot_json,
                adaptive_context_json, dry_run, success
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "DOGEUSDT",
                "claude-test",
                json.dumps({"stance": "long", "confidence": 0.7}),
                json.dumps({"action": "open_long"}),
                json.dumps({"approved": True, "warnings": ["Funding crowded"], "violated_rules": []}),
                json.dumps({"final_action": "open_long"}),
                json.dumps(
                    {
                        "btc_market_regime": "panic_flush",
                        "execution_template": "high_beta_confirmation_only",
                        "narrative_tag": "meme",
                        "asset_tier": "high_beta_alt",
                    }
                ),
                json.dumps({"profile_mode": "normal"}),
                0,
                0,
            ),
        )
        for idx in range(3):
            conn.execute(
                """
                INSERT INTO trade_records (
                    symbol, direction, entry_price, exit_price, quantity, leverage,
                    realized_pnl, pnl_pct, open_time, close_time, notes, linked_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "DOGEUSDT",
                    "LONG",
                    0.1,
                    0.09,
                    1000.0,
                    3.0,
                    -100.0,
                    -5.0,
                    f"2026-01-0{idx+1}T00:00:00",
                    f"2026-01-0{idx+2}T00:00:00",
                    "loss",
                    1,
                ),
            )
        conn.commit()

    original_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    apply_result = runner.invoke(
        app,
        [
            "tune-risk",
            "--symbol",
            "DOGEUSDT",
            "--config",
            str(config_path),
            "--apply",
            "--yes",
            "--backup-dir",
            str(backup_dir),
        ],
    )
    assert apply_result.exit_code == 0
    applied_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert applied_config["risk"]["meme_max_leverage"] == 1
    assert backup_dir.exists()
    backups = sorted(backup_dir.glob("config-*.yaml"))
    assert backups

    rollback_result = runner.invoke(
        app,
        [
            "rollback-config",
            "--config",
            str(config_path),
            "--backup-dir",
            str(backup_dir),
            "--yes",
        ],
    )
    assert rollback_result.exit_code == 0
    restored_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert restored_config == original_config


def test_tune_edge_command_outputs_and_writes_patch(tmp_path):
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "test.db"
    patch_path = tmp_path / "edge-policy.yaml"

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                model TEXT NOT NULL,
                research_decision_json TEXT,
                execution_plan_json TEXT,
                risk_decision_json TEXT,
                execution_result_json TEXT,
                account_snapshot_json TEXT,
                market_snapshot_json TEXT,
                adaptive_context_json TEXT,
                onchain_snapshot_json TEXT,
                position_snapshot_json TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                report_path TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                notes TEXT DEFAULT '',
                linked_run_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for run_id, pnl, pnl_pct in ((1, 200.0, 10.0), (2, 180.0, 9.0), (3, -100.0, -5.0)):
            conn.execute(
                """
                INSERT INTO trade_runs (
                    id, symbol, model, research_decision_json, execution_plan_json,
                    risk_decision_json, execution_result_json, market_snapshot_json,
                    adaptive_context_json, dry_run, success
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "BTCUSDT",
                    "claude-test",
                    json.dumps({"stance": "long", "confidence": 0.8}),
                    json.dumps({"action": "open_long"}),
                    json.dumps(
                        {
                            "approved": True,
                            "warnings": [],
                            "violated_rules": [],
                            "setup_quality_score": 88.0,
                            "setup_quality_grade": "A",
                            "gating_profile": "conviction_override",
                            "edge_policy_label": "promoted",
                        }
                    ),
                    json.dumps({"final_action": "open_long"}),
                    json.dumps(
                        {
                            "btc_market_regime": "risk_on_trend" if run_id < 3 else "panic_flush",
                            "execution_template": "core_trend_follow",
                            "narrative_tag": "store_of_value",
                            "asset_tier": "core",
                        }
                    ),
                    json.dumps({"profile_mode": "normal"}),
                    0,
                    0,
                ),
            )
            conn.execute(
                """
                INSERT INTO trade_records (
                    symbol, direction, entry_price, exit_price, quantity, leverage,
                    realized_pnl, pnl_pct, open_time, close_time, notes, linked_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "BTCUSDT",
                    "LONG",
                    100000.0,
                    102000.0 if pnl > 0 else 99000.0,
                    0.1,
                    5.0,
                    pnl,
                    pnl_pct,
                    "2026-01-01T00:00:00",
                    "2026-01-02T00:00:00",
                    "linked",
                    run_id,
                ),
            )
        conn.commit()

    from unittest.mock import patch

    with patch("trading.main.BacktestRunner") as MockBacktest:
        mock_runner = MockBacktest.return_value
        mock_runner.walk_forward.return_value = [
            {
                "window_index": 1,
                "train_start_run_id": 1,
                "train_end_run_id": 2,
                "test_start_run_id": 3,
                "test_end_run_id": 3,
                "train_trades": 2,
                "train_expectancy_pct": 2.0,
                "train_profit_factor": 1.5,
                "test_trades": 1,
                "test_expectancy_pct": 1.0,
                "test_profit_factor": 1.2,
                "test_total_pnl_pct": 1.0,
            },
            {
                "window_index": 2,
                "train_start_run_id": 2,
                "train_end_run_id": 3,
                "test_start_run_id": 4,
                "test_end_run_id": 4,
                "train_trades": 2,
                "train_expectancy_pct": 1.5,
                "train_profit_factor": 1.3,
                "test_trades": 1,
                "test_expectancy_pct": 0.8,
                "test_profit_factor": 1.1,
                "test_total_pnl_pct": 0.8,
            },
        ]
        mock_runner.summarize_walk_forward.return_value = {
            "windows": 2,
            "positive_test_windows": 2,
            "non_positive_test_windows": 0,
            "positive_test_ratio": 100.0,
            "avg_test_expectancy_pct": 0.9,
            "avg_test_total_pnl_pct": 0.9,
        }
        result = runner.invoke(
            app,
            ["tune-edge", "--symbol", "BTCUSDT", "--config", str(config_path), "--output", str(patch_path)],
        )
    assert result.exit_code == 0
    assert "Edge Policy Suggestions" in result.output
    assert patch_path.exists()
    patch_text = patch_path.read_text(encoding="utf-8")
    assert "edge_policy_allowlist" in patch_text


def test_tune_edge_respects_bad_walk_forward_and_can_return_no_allowlist(tmp_path):
    config_path = _write_temp_config(tmp_path)
    db_path = tmp_path / "test.db"
    patch_path = tmp_path / "edge-policy.yaml"

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'anthropic',
                model TEXT NOT NULL,
                research_decision_json TEXT,
                execution_plan_json TEXT,
                risk_decision_json TEXT,
                execution_result_json TEXT,
                account_snapshot_json TEXT,
                market_snapshot_json TEXT,
                adaptive_context_json TEXT,
                onchain_snapshot_json TEXT,
                position_snapshot_json TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                report_path TEXT,
                success INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                notes TEXT DEFAULT '',
                linked_run_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for run_id, pnl, pnl_pct in ((1, 200.0, 10.0), (2, 180.0, 9.0), (3, -100.0, -5.0)):
            conn.execute(
                """
                INSERT INTO trade_runs (
                    id, symbol, model, research_decision_json, execution_plan_json,
                    risk_decision_json, execution_result_json, market_snapshot_json,
                    adaptive_context_json, dry_run, success
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "BTCUSDT",
                    "claude-test",
                    json.dumps({"stance": "long", "confidence": 0.8}),
                    json.dumps({"action": "open_long"}),
                    json.dumps(
                        {
                            "approved": True,
                            "warnings": [],
                            "violated_rules": [],
                            "setup_quality_score": 88.0,
                            "setup_quality_grade": "A",
                            "gating_profile": "conviction_override",
                            "edge_policy_label": "promoted",
                        }
                    ),
                    json.dumps({"final_action": "open_long"}),
                    json.dumps(
                        {
                            "btc_market_regime": "risk_on_trend" if run_id < 3 else "panic_flush",
                            "execution_template": "core_trend_follow",
                            "narrative_tag": "store_of_value",
                            "asset_tier": "core",
                        }
                    ),
                    json.dumps({"profile_mode": "normal"}),
                    0,
                    0,
                ),
            )
            conn.execute(
                """
                INSERT INTO trade_records (
                    symbol, direction, entry_price, exit_price, quantity, leverage,
                    realized_pnl, pnl_pct, open_time, close_time, notes, linked_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "BTCUSDT",
                    "LONG",
                    100000.0,
                    102000.0 if pnl > 0 else 99000.0,
                    0.1,
                    5.0,
                    pnl,
                    pnl_pct,
                    "2026-01-01T00:00:00",
                    "2026-01-02T00:00:00",
                    "linked",
                    run_id,
                ),
            )
        conn.commit()

    from unittest.mock import patch
    with patch("trading.main.BacktestRunner") as MockBacktest:
        mock_runner = MockBacktest.return_value
        mock_runner.walk_forward.return_value = [
            {
                "window_index": 1,
                "train_start_run_id": 1,
                "train_end_run_id": 2,
                "test_start_run_id": 3,
                "test_end_run_id": 3,
                "train_trades": 2,
                "train_expectancy_pct": 2.0,
                "train_profit_factor": 1.5,
                "test_trades": 1,
                "test_expectancy_pct": -1.0,
                "test_profit_factor": 0.8,
                "test_total_pnl_pct": -1.0,
            }
        ]
        mock_runner.summarize_walk_forward.return_value = {
            "windows": 1,
            "positive_test_windows": 0,
            "non_positive_test_windows": 1,
            "positive_test_ratio": 0.0,
            "avg_test_expectancy_pct": -1.0,
            "avg_test_total_pnl_pct": -1.0,
        }
        result = runner.invoke(
            app,
            ["tune-edge", "--symbol", "BTCUSDT", "--config", str(config_path), "--output", str(patch_path)],
        )

    assert result.exit_code == 0
    assert "walk-forward 平均测试期 expectancy" in result.output
    assert not patch_path.exists()
