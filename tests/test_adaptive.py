"""Tests for deterministic prompt adaptation based on realized outcomes."""

from trading.ai.adaptive import build_adaptive_prompt_context
from trading.ai.prompts import build_research_prompt


def test_build_adaptive_prompt_context_enters_defensive_mode():
    ctx = build_adaptive_prompt_context(
        symbol="BTCUSDT",
        symbol_summary={"total_trades": 7, "win_rate": 42.9},
        global_summary={"total_trades": 12, "win_rate": 45.0},
        recent_symbol_trades=[
            {"direction": "LONG", "realized_pnl": -50.0, "pnl_pct": -4.0},
            {"direction": "LONG", "realized_pnl": -30.0, "pnl_pct": -2.0},
            {"direction": "LONG", "realized_pnl": -10.0, "pnl_pct": -1.0},
            {"direction": "SHORT", "realized_pnl": 40.0, "pnl_pct": 3.0},
            {"direction": "SHORT", "realized_pnl": 40.0, "pnl_pct": 3.0},
            {"direction": "SHORT", "realized_pnl": 35.0, "pnl_pct": 2.5},
            {"direction": "SHORT", "realized_pnl": 20.0, "pnl_pct": 1.5},
        ],
        reflections=[
            {"lessons": ["Avoid chasing crowded funding", "Wait for confirmation"]},
        ],
        risk_stats={
            "top_warnings": [("Funding crowded", 3)],
            "top_violations": [("BTC regime risk-off", 2)],
        },
        min_trades=5,
        loss_streak_threshold=3,
    )

    assert ctx["enabled"] is True
    assert ctx["profile_mode"] == "defensive"
    assert ctx["preferred_bias"] == "short"
    assert ctx["recent_loss_streak"] == 3
    assert any("Funding crowded" in line for line in ctx["guidance_lines"])


def test_research_prompt_includes_adaptive_section():
    _, user = build_research_prompt(
        symbol="BTCUSDT",
        account_summary={"futures_balance_usdt": 10000.0, "drawdown_pct": 0.0},
        positions=[],
        market_snapshot={"spot_price": 100000, "futures_mark_price": 100100},
        vault_context="",
        trade_history=[],
        reflections=[],
        risk_params={"max_leverage": 10, "max_position_size_pct": 0.05, "default_stop_loss_pct": 0.05},
        adaptive_context={
            "enabled": True,
            "profile_mode": "defensive",
            "preferred_bias": "short",
            "guidance_lines": ["Recent loss streak = 3. Demand stronger confirmation."],
        },
    )

    assert "Adaptive Guidance From Realized Outcomes" in user
    assert "Demand stronger confirmation" in user
