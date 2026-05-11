"""Tests for historical kline replay backtest runner."""

from datetime import datetime

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
from trading.pipeline.backtest import BacktestRunner
from trading.pipeline.persistence import TradeRunDB


def _make_config(tmp_path) -> AppConfig:
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(),
        binance=BinanceConfig(futures_testnet=True, spot_testnet=True),
        claude=ClaudeConfig(),
        trading=TradingConfig(),
        monitor=MonitorConfig(),
        logging=LoggingConfig(sqlite_db=str(tmp_path / "test.db")),
    )


def _seed_run(db: TradeRunDB, symbol: str = "BTCUSDT") -> int:
    return db.save_run(
        symbol=symbol,
        model="claude-test",
        research_decision=ResearchDecision(
            symbol=symbol,
            stance="long",
            confidence=0.8,
            thesis="x",
            market_structure="x",
            evidence=["x"],
            catalysts=["x"],
            risks=["x"],
            invalidation="x",
            time_horizon="swing",
            preferred_market="futures",
        ),
        execution_plan=ExecutionPlan(
            action="open_long",
            size_pct=3.0,
            leverage=5,
            entry_idea="x",
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
            rationale="x",
            thesis_window_hours=4,
        ),
        risk_decision=RiskDecision(
            approved=True,
            adjusted_action="open_long",
            adjusted_size_pct=3.0,
            adjusted_leverage=5,
            violated_rules=[],
            warnings=[],
            rationale="ok",
        ),
        execution_result=ExecutionResult(
            executed=False,
            status="manual_review_required",
            symbol=symbol,
            final_action="open_long",
            final_size_pct=3.0,
            final_leverage=5,
            message="manual",
            manual_order_details={
                "market": "futures",
                "mark_price": 100.0,
                "stop_loss_price": 95.0,
                "take_profit_price": 110.0,
            },
        ),
        account_snapshot={},
        market_snapshot={"btc_market_regime": "risk_on_trend", "execution_template": "core_trend_follow", "narrative_tag": "store_of_value"},
        position_snapshot=[],
        dry_run=False,
    )


def test_backtest_runner_hits_take_profit(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    db = TradeRunDB(config.logging.sqlite_db)
    _seed_run(db)
    runner = BacktestRunner(config)

    monkeypatch.setattr(
        runner,
        "_fetch_klines",
        lambda symbol, market, start, hours: [
            [0, "100", "101", "99", "100", "0", 0, "0", 0, "0", "0", "0"],
            [0, "100", "112", "99", "111", "0", 0, "0", 0, "0", "0", "0"],
        ],
    )
    monkeypatch.setattr(
        runner,
        "_fetch_lookback_klines",
        lambda symbol, market, end, bars=168: [
            [0, "100", "101", "99", str(100 + i * 0.1), "0", 0, "0", 0, "0", "0", "0"]
            for i in range(30)
        ],
    )

    outcomes = runner.replay_runs(symbol="BTCUSDT", limit=5, default_hours=4)
    assert len(outcomes) == 1
    assert outcomes[0].exit_reason == "take_profit"
    assert outcomes[0].pnl_pct > 0
    assert outcomes[0].entry_trend_bias in {"bullish", "bearish", "range"}
    assert outcomes[0].entry_volatility_regime in {"low", "normal", "high", "extreme"}


def test_backtest_summary_computes_profit_factor(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    db = TradeRunDB(config.logging.sqlite_db)
    _seed_run(db, "BTCUSDT")
    _seed_run(db, "ETHUSDT")
    runner = BacktestRunner(config)

    calls = {"n": 0}

    def _fake_fetch(symbol, market, start, hours):
        calls["n"] += 1
        if calls["n"] == 1:
            return [
                [0, "100", "101", "99", "100", "0", 0, "0", 0, "0", "0", "0"],
                [0, "100", "112", "99", "111", "0", 0, "0", 0, "0", "0", "0"],
            ]
        return [
            [0, "100", "101", "99", "100", "0", 0, "0", 0, "0", "0", "0"],
            [0, "100", "101", "94", "95", "0", 0, "0", 0, "0", "0", "0"],
        ]

    monkeypatch.setattr(runner, "_fetch_klines", _fake_fetch)
    monkeypatch.setattr(
        runner,
        "_fetch_lookback_klines",
        lambda symbol, market, end, bars=168: [
            [0, "100", "101", "99", str(100 + i * 0.1), "0", 0, "0", 0, "0", "0", "0"]
            for i in range(30)
        ],
    )
    outcomes = runner.replay_runs(limit=5, default_hours=4)
    summary = runner.summarize(outcomes)
    assert summary["trades"] == 2
    assert summary["win_rate"] == 50.0
    assert summary["profit_factor"] is not None
    assert summary["profit_factor"] > 1.0


def test_backtest_runner_replay_windows(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    db = TradeRunDB(config.logging.sqlite_db)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        _seed_run(db, symbol)
    runner = BacktestRunner(config)

    monkeypatch.setattr(
        runner,
        "_fetch_klines",
        lambda symbol, market, start, hours: [
            [0, "100", "101", "99", "100", "0", 0, "0", 0, "0", "0", "0"],
            [0, "100", "112", "99", "111", "0", 0, "0", 0, "0", "0", "0"],
        ],
    )
    monkeypatch.setattr(
        runner,
        "_fetch_lookback_klines",
        lambda symbol, market, end, bars=168: [
            [0, "100", "101", "99", str(100 + i * 0.1), "0", 0, "0", 0, "0", "0", "0"]
            for i in range(30)
        ],
    )

    windows = runner.replay_windows(limit=3, default_hours=4, window_size=2, step=1)
    assert windows
    assert windows[0]["trades"] >= 1
    assert "max_drawdown_pct" in windows[0]


def test_backtest_runner_walk_forward(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    db = TradeRunDB(config.logging.sqlite_db)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
        _seed_run(db, symbol)
    runner = BacktestRunner(config)

    calls = {"n": 0}

    def _fake_fetch(symbol, market, start, hours):
        calls["n"] += 1
        if calls["n"] % 2:
            return [
                [0, "100", "101", "99", "100", "0", 0, "0", 0, "0", "0", "0"],
                [0, "100", "112", "99", "111", "0", 0, "0", 0, "0", "0", "0"],
            ]
        return [
            [0, "100", "101", "99", "100", "0", 0, "0", 0, "0", "0", "0"],
            [0, "100", "101", "94", "95", "0", 0, "0", 0, "0", "0", "0"],
        ]

    monkeypatch.setattr(runner, "_fetch_klines", _fake_fetch)
    monkeypatch.setattr(
        runner,
        "_fetch_lookback_klines",
        lambda symbol, market, end, bars=168: [
            [0, "100", "101", "99", str(100 + i * 0.1), "0", 0, "0", 0, "0", "0", "0"]
            for i in range(30)
        ],
    )
    wf = runner.walk_forward(limit=4, default_hours=4, train_size=2, test_size=1, step=1)
    assert wf
    assert "train_expectancy_pct" in wf[0]
    assert "test_expectancy_pct" in wf[0]


def test_backtest_runner_summarize_walk_forward(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    db = TradeRunDB(config.logging.sqlite_db)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
        _seed_run(db, symbol)
    runner = BacktestRunner(config)

    calls = {"n": 0}

    def _fake_fetch(symbol, market, start, hours):
        calls["n"] += 1
        if calls["n"] % 2:
            return [
                [0, "100", "101", "99", "100", "0", 0, "0", 0, "0", "0", "0"],
                [0, "100", "112", "99", "111", "0", 0, "0", 0, "0", "0", "0"],
            ]
        return [
            [0, "100", "101", "99", "100", "0", 0, "0", 0, "0", "0", "0"],
            [0, "100", "101", "94", "95", "0", 0, "0", 0, "0", "0", "0"],
        ]

    monkeypatch.setattr(runner, "_fetch_klines", _fake_fetch)
    monkeypatch.setattr(
        runner,
        "_fetch_lookback_klines",
        lambda symbol, market, end, bars=168: [
            [0, "100", "101", "99", str(100 + i * 0.1), "0", 0, "0", 0, "0", "0", "0"]
            for i in range(30)
        ],
    )
    wf = runner.walk_forward(limit=4, default_hours=4, train_size=2, test_size=1, step=1)
    summary = runner.summarize_walk_forward(wf)
    assert summary["windows"] >= 1
    assert "positive_test_ratio" in summary
