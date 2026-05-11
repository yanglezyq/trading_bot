"""Tests for conservative risk tuning suggestions from linked outcomes."""

from trading.core.config import (
    AppConfig,
    BinanceConfig,
    ClaudeConfig,
    EventsConfig,
    LoggingConfig,
    MonitorConfig,
    RiskConfig,
    TradingConfig,
    VaultConfig,
)
from trading.risk.tuning import RiskTuningAdvisor


def _make_config() -> AppConfig:
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(),
        binance=BinanceConfig(),
        claude=ClaudeConfig(),
        trading=TradingConfig(),
        monitor=MonitorConfig(),
        logging=LoggingConfig(),
        events=EventsConfig(),
    )


def test_tuning_advisor_suggests_conservative_changes():
    advisor = RiskTuningAdvisor(_make_config())
    replay_stats = {
        "by_narrative": [
            {"group": "meme", "samples": 4, "win_rate": 25.0, "avg_pnl_pct": -6.0},
        ],
        "by_template": [
            {"group": "high_beta_confirmation_only", "samples": 4, "win_rate": 25.0, "avg_pnl_pct": -5.0},
        ],
        "by_regime": [
            {"group": "panic_flush", "samples": 3, "win_rate": 33.0, "avg_pnl_pct": -4.0},
            {"group": "risk_off_trend", "samples": 3, "win_rate": 30.0, "avg_pnl_pct": -3.0},
        ],
        "by_profile_mode": [
            {"group": "normal", "samples": 6, "win_rate": 35.0, "avg_pnl_pct": -2.5},
            {"group": "defensive", "samples": 4, "win_rate": 50.0, "avg_pnl_pct": 1.0},
        ],
    }
    risk_stats = {
        "warning_effectiveness": [
            {
                "rule": "Funding crowded",
                "linked_trades": 4,
                "avg_pnl_pct": -3.0,
            },
            {
                "rule": "[event_risk] Upcoming event(s)",
                "linked_trades": 4,
                "avg_pnl_pct": -2.0,
            },
        ]
    }

    suggestions = advisor.suggest(replay_stats=replay_stats, risk_stats=risk_stats)
    keys = {s.config_path for s in suggestions}
    assert "risk.meme_max_leverage" in keys
    assert "risk.high_beta_alt_max_leverage" in keys
    assert "risk.altcoin_max_leverage_when_btc_weak" in keys
    assert "risk.max_abs_funding_rate" in keys
    assert "events.high_impact_max_leverage" in keys
    assert "claude.adaptive_prompt_loss_streak_threshold" in keys


def test_tuning_advisor_exports_nested_patch():
    advisor = RiskTuningAdvisor(_make_config())
    suggestions = advisor.suggest(
        replay_stats={
            "by_narrative": [{"group": "meme", "samples": 3, "win_rate": 0.0, "avg_pnl_pct": -5.0}],
            "by_template": [],
            "by_regime": [],
            "by_profile_mode": [],
        },
        risk_stats={"warning_effectiveness": []},
    )
    patch = advisor.export_patch(suggestions)
    assert "risk" in patch
    assert "meme_max_leverage" in patch["risk"]


def test_tuning_advisor_uses_walk_forward_to_trim_global_size():
    advisor = RiskTuningAdvisor(_make_config())
    suggestions = advisor.suggest(
        replay_stats={
            "by_narrative": [],
            "by_template": [],
            "by_regime": [],
            "by_profile_mode": [],
            "by_opportunity_bucket": [],
        },
        risk_stats={"warning_effectiveness": []},
        walk_forward_summary={"windows": 3, "avg_test_expectancy_pct": -1.2},
    )
    keys = {s.config_path for s in suggestions}
    assert "trading.max_position_size_pct" in keys


def test_tuning_advisor_raises_auto_execute_score_threshold_from_bucket_gap():
    advisor = RiskTuningAdvisor(_make_config())
    suggestions = advisor.suggest(
        replay_stats={
            "by_narrative": [],
            "by_template": [],
            "by_regime": [],
            "by_profile_mode": [],
            "by_opportunity_bucket": [
                {"group": "avoid", "samples": 6, "avg_pnl_pct": -2.0},
                {"group": "elite", "samples": 6, "avg_pnl_pct": 1.5},
            ],
        },
        risk_stats={"warning_effectiveness": []},
        walk_forward_summary={},
    )
    keys = {s.config_path for s in suggestions}
    assert "trading.opportunity_score_min_auto_execute" in keys
