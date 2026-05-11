"""Deterministic prompt-adaptation helpers based on realized trade outcomes."""

from __future__ import annotations

from collections import Counter
from typing import Any


def _safe_win_rate(wins: int, total: int) -> float | None:
    """Return percentage win rate or None when total is zero."""
    if total <= 0:
        return None
    return wins / total * 100.0


def _recent_loss_streak(recent_trades: list[dict[str, Any]]) -> int:
    """Count consecutive recent losing trades from newest to oldest."""
    streak = 0
    for trade in recent_trades:
        pnl = float(trade.get("realized_pnl", 0.0) or 0.0)
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def build_adaptive_prompt_context(
    *,
    symbol: str,
    symbol_summary: dict[str, Any],
    global_summary: dict[str, Any],
    recent_symbol_trades: list[dict[str, Any]],
    reflections: list[dict[str, Any]],
    risk_stats: dict[str, Any],
    min_trades: int = 5,
    loss_streak_threshold: int = 3,
) -> dict[str, Any]:
    """Build a compact adaptive prompt context from recent realized outcomes."""
    symbol = symbol.upper()
    symbol_total = int(symbol_summary.get("total_trades", 0) or 0)
    global_total = int(global_summary.get("total_trades", 0) or 0)
    long_trades = [
        t for t in recent_symbol_trades
        if str(t.get("direction", "")).upper() == "LONG"
    ]
    short_trades = [
        t for t in recent_symbol_trades
        if str(t.get("direction", "")).upper() == "SHORT"
    ]
    long_wins = sum(1 for t in long_trades if float(t.get("realized_pnl", 0.0) or 0.0) > 0)
    short_wins = sum(1 for t in short_trades if float(t.get("realized_pnl", 0.0) or 0.0) > 0)
    long_win_rate = _safe_win_rate(long_wins, len(long_trades))
    short_win_rate = _safe_win_rate(short_wins, len(short_trades))
    symbol_win_rate = (
        float(symbol_summary.get("win_rate", 0.0)) if symbol_total > 0 else None
    )
    global_win_rate = (
        float(global_summary.get("win_rate", 0.0)) if global_total > 0 else None
    )
    recent_loss_streak = _recent_loss_streak(recent_symbol_trades)
    lessons = [
        str(lesson).strip()
        for ref in reflections[:3]
        for lesson in (ref.get("lessons") or [])
        if str(lesson).strip()
    ]
    unique_lessons = list(dict.fromkeys(lessons))[:5]

    risk_patterns = []
    for rule, count in (risk_stats.get("top_warnings") or [])[:3]:
        risk_patterns.append(f"warning:{rule} x{count}")
    for rule, count in (risk_stats.get("top_violations") or [])[:2]:
        risk_patterns.append(f"block:{rule} x{count}")

    profile_mode = "normal"
    lines: list[str] = []
    if symbol_total >= min_trades and symbol_win_rate is not None:
        lines.append(
            f"{symbol} recent closed-trade sample: {symbol_total} trade(s), "
            f"win rate {symbol_win_rate:.1f}%."
        )
    elif global_total >= min_trades and global_win_rate is not None:
        lines.append(
            f"Global recent sample: {global_total} trade(s), "
            f"win rate {global_win_rate:.1f}% (symbol-specific sample still small)."
        )

    if recent_loss_streak >= loss_streak_threshold:
        profile_mode = "defensive"
        lines.append(
            f"Recent {symbol} loss streak = {recent_loss_streak}. "
            "Demand stronger confirmation, lower confidence, and smaller size."
        )
    elif symbol_win_rate is not None and symbol_total >= min_trades and symbol_win_rate < 45:
        profile_mode = "conservative"
        lines.append(
            f"{symbol} realized win rate is weak ({symbol_win_rate:.1f}%). "
            "Prefer selective setups and avoid forcing marginal trades."
        )

    preferred_bias = ""
    bias_reason = ""
    if long_win_rate is not None and short_win_rate is not None:
        if len(long_trades) >= 3 and len(short_trades) >= 3:
            if long_win_rate >= short_win_rate + 20:
                preferred_bias = "long"
                bias_reason = (
                    f"Recent long trades outperformed shorts ({long_win_rate:.1f}% vs {short_win_rate:.1f}% win rate)."
                )
            elif short_win_rate >= long_win_rate + 20:
                preferred_bias = "short"
                bias_reason = (
                    f"Recent short trades outperformed longs ({short_win_rate:.1f}% vs {long_win_rate:.1f}% win rate)."
                )
    if bias_reason:
        lines.append(bias_reason)

    if risk_patterns:
        lines.append(
            "Recurring risk patterns to explicitly address: " + "; ".join(risk_patterns[:4]) + "."
        )
    if unique_lessons:
        lines.append("Carry forward these lessons: " + "; ".join(unique_lessons[:4]) + ".")

    return {
        "enabled": bool(lines),
        "symbol": symbol,
        "profile_mode": profile_mode,
        "preferred_bias": preferred_bias,
        "bias_reason": bias_reason,
        "symbol_trade_count": symbol_total,
        "global_trade_count": global_total,
        "symbol_win_rate": symbol_win_rate,
        "global_win_rate": global_win_rate,
        "long_win_rate": long_win_rate,
        "short_win_rate": short_win_rate,
        "recent_loss_streak": recent_loss_streak,
        "risk_patterns": risk_patterns[:5],
        "lessons": unique_lessons,
        "guidance_lines": lines,
    }
