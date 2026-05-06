"""CLI smoke tests for safe preview behavior."""

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
