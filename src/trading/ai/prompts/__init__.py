"""Prompt builders for ResearchDecision, ExecutionPlan, and Reflection generation."""

import json
from typing import Any


def build_research_prompt(
    symbol: str,
    account_summary: dict,
    positions: list[dict],
    market_snapshot: dict,
    vault_context: str,
    trade_history: list[dict],
    reflections: list[dict],
    risk_params: dict,
) -> tuple[str, str]:
    """Return (system, user) prompts for ResearchDecision generation."""

    max_lev = int(risk_params.get("max_leverage", 35))
    max_size = risk_params.get("max_position_size_pct", 0.05) * 100
    default_sl = risk_params.get("default_stop_loss_pct", 0.05) * 100
    vault_section = vault_context[:8000] if vault_context else "（无 Vault 交易知识可用）"

    system = f"""You are an expert cryptocurrency trading analyst. Generate a structured research decision.

RESPONSE FORMAT: Respond with ONLY a valid JSON object, no markdown fences, no other text.

Schema:
{{
  "symbol": "string — trading pair, e.g. BTCUSDT",
  "stance": "long | neutral | short",
  "confidence": 0.0-1.0,
  "thesis": "string — core investment thesis (2-4 sentences)",
  "market_structure": "string — summarize crypto trend / volatility / positioning regime",
  "evidence": ["list of 2-5 concrete, verifiable observations from the supplied market snapshot"],
  "catalysts": ["list of 2-5 positive catalysts"],
  "risks": ["list of 2-5 risk factors"],
  "invalidation": "string — what would invalidate this thesis",
  "time_horizon": "string — expected trade horizon such as intraday / swing / multi-day",
  "preferred_market": "spot | futures | none"
}}

Risk constraints:
- Max leverage: {max_lev}x
- Max position size: {max_size:.0f}% of account
- Default stop loss: {default_sl:.0f}%

Trading knowledge base:
{vault_section}"""

    balance = account_summary.get("futures_balance_usdt", 0)
    drawdown = account_summary.get("drawdown_pct", 0)
    acct_section = f"- Futures balance: ${balance:,.2f} USDT\n- Drawdown: {drawdown:.2f}%"
    market_section = "\n".join([
        f"- Spot price: {market_snapshot.get('spot_price', 'N/A')}",
        f"- Futures mark price: {market_snapshot.get('futures_mark_price', 'N/A')}",
        f"- 24h price change: {market_snapshot.get('price_change_24h_pct', 'N/A')}%",
        f"- 7d price change: {market_snapshot.get('price_change_7d_pct', 'N/A')}%",
        f"- 24h quote volume: {market_snapshot.get('quote_volume_24h_usdt', 'N/A')} USDT",
        f"- Funding rate: {market_snapshot.get('funding_rate', 'N/A')}",
        f"- Open interest: {market_snapshot.get('open_interest', 'N/A')}",
        f"- OI / 24h volume ratio: {market_snapshot.get('oi_to_volume_ratio', 'N/A')}",
        f"- Basis: {market_snapshot.get('basis_bps', 'N/A')} bps",
        f"- Realized vol (24h): {market_snapshot.get('realized_vol_24h_pct', 'N/A')}%",
        f"- Realized vol (7d): {market_snapshot.get('realized_vol_7d_pct', 'N/A')}%",
        f"- EMA21 / EMA55 / EMA144 (1h): {market_snapshot.get('ema_21_1h', 'N/A')} / {market_snapshot.get('ema_55_1h', 'N/A')} / {market_snapshot.get('ema_144_1h', 'N/A')}",
        f"- Distance to EMA21: {market_snapshot.get('distance_to_ema21_pct', 'N/A')}%",
        f"- Distance to EMA55: {market_snapshot.get('distance_to_ema55_pct', 'N/A')}%",
        f"- Distance to 7d high: {market_snapshot.get('distance_to_7d_high_pct', 'N/A')}%",
        f"- Distance to 7d low: {market_snapshot.get('distance_to_7d_low_pct', 'N/A')}%",
        f"- Hourly trend bias: {market_snapshot.get('hourly_trend_bias', 'N/A')}",
        f"- Asset tier: {market_snapshot.get('asset_tier', 'N/A')}",
        f"- Narrative tag: {market_snapshot.get('narrative_tag', 'N/A')}",
        f"- Liquidity regime: {market_snapshot.get('liquidity_regime', 'N/A')}",
        f"- Crowding regime: {market_snapshot.get('crowding_regime', 'N/A')}",
        f"- Momentum regime: {market_snapshot.get('momentum_regime', 'N/A')}",
        f"- Volatility regime: {market_snapshot.get('volatility_regime', 'N/A')}",
        f"- BTC regime: {market_snapshot.get('btc_market_regime', 'N/A')}",
        f"- Relative strength vs BTC (24h): {market_snapshot.get('relative_strength_24h_pct', 'N/A')}%",
        f"- Relative strength vs BTC (7d): {market_snapshot.get('relative_strength_7d_pct', 'N/A')}%",
        f"- Execution template: {market_snapshot.get('execution_template', 'N/A')}",
    ])

    pos_section = ""
    if positions:
        pos_section = "\n\n## Current Positions\n"
        for p in positions:
            direction = p.get("direction", p.get("side", ""))
            price = p.get("price", p.get("entry_price", "N/A"))
            upnl = p.get("unrealized_pnl", "N/A")
            pos_section += f"  - {p.get('symbol', symbol)} {direction} | entry {price} | uPnL {upnl}\n"

    hist_section = ""
    if trade_history:
        hist_section = "\n\n## Recent Trade History\n"
        for t in trade_history[:5]:
            hist_section += (
                f"  - {t.get('symbol')} {t.get('direction')} | "
                f"P&L: {t.get('realized_pnl', 0):+.2f} ({t.get('pnl_pct', 0):+.1f}%) | "
                f"notes: {t.get('notes', '—')}\n"
            )

    ref_section = ""
    if reflections:
        ref_section = "\n\n## Past Reflections & Lessons\n"
        for r in reflections[:3]:
            lessons = r.get("lessons", [])
            if isinstance(lessons, list) and lessons:
                ref_section += f"\n### {r.get('symbol', 'Unknown')} ({r.get('created_at', '')})\n"
                for lesson in lessons[:3]:
                    ref_section += f"  - {lesson}\n"
            elif r.get("reflection_text"):
                ref_section += f"\n{r.get('reflection_text', '')[:400]}\n"

    user = (
        f"Analyze {symbol} and generate a ResearchDecision JSON.\n\n"
        f"## Account\n{acct_section}"
        f"\n\n## Market Snapshot\n{market_section}"
        f"{pos_section}{hist_section}{ref_section}\n\n"
        f"Your thesis must be anchored in the market snapshot. The evidence list must quote concrete observations from the supplied crypto price / funding / volatility data.\n\n"
        f"Respond with ONLY the JSON object for {symbol}."
    )

    return system, user


def build_execution_prompt(
    symbol: str,
    research: Any,  # ResearchDecision
    account_summary: dict,
    positions: list[dict],
    market_snapshot: dict,
    risk_params: dict,
) -> tuple[str, str]:
    """Return (system, user) prompts for ExecutionPlan generation."""

    max_lev = int(risk_params.get("max_leverage", 35))
    max_size = risk_params.get("max_position_size_pct", 0.05) * 100
    default_sl = risk_params.get("default_stop_loss_pct", 0.05) * 100
    default_tp = risk_params.get("default_take_profit_pct", 0.15) * 100

    system = f"""You are an expert cryptocurrency trade execution planner. Given a research decision, generate a precise execution plan.

RESPONSE FORMAT: Respond with ONLY a valid JSON object, no markdown fences, no other text.

Schema:
{{
  "action": "hold | buy_spot | sell_spot | open_long | close_long | open_short | close_short",
  "size_pct": 0.0-100.0,
  "leverage": 1-{max_lev},
  "entry_idea": "string — entry timing or conditions grounded in current crypto price structure",
  "entry_style": "market_now | buy_dip | sell_rip | breakout_confirmation | breakdown_confirmation | exit_now | hold",
  "entry_zone_low": "number or null — lower bound of ideal entry zone",
  "entry_zone_high": "number or null — upper bound of ideal entry zone",
  "trigger_price": "number or null — price level that confirms the setup",
  "invalidation_price": "number or null — price level that invalidates the setup",
  "stop_loss_pct": 0.0+,
  "take_profit_pct": 0.0+,
  "thesis_window_hours": "integer or null — how long this setup remains valid before reassessment",
  "rationale": "string — why this specific plan"
}}

Rules:
- size_pct MUST NOT exceed {max_size:.0f}% (risk gate enforces this)
- leverage MUST NOT exceed {max_lev}x
- If stance is "neutral" or confidence < 0.4, set action to "hold" and size_pct to 0
- stop_loss_pct default: {default_sl:.0f}%
- take_profit_pct default: {default_tp:.0f}%"""

    balance = account_summary.get("futures_balance_usdt", 0)
    drawdown = account_summary.get("drawdown_pct", 0)
    market_section = "\n".join([
        f"- Spot price: {market_snapshot.get('spot_price', 'N/A')}",
        f"- Futures mark price: {market_snapshot.get('futures_mark_price', 'N/A')}",
        f"- 24h move: {market_snapshot.get('price_change_24h_pct', 'N/A')}%",
        f"- 7d move: {market_snapshot.get('price_change_7d_pct', 'N/A')}%",
        f"- Funding rate: {market_snapshot.get('funding_rate', 'N/A')}",
        f"- Open interest: {market_snapshot.get('open_interest', 'N/A')}",
        f"- OI / 24h volume ratio: {market_snapshot.get('oi_to_volume_ratio', 'N/A')}",
        f"- Basis: {market_snapshot.get('basis_bps', 'N/A')} bps",
        f"- EMA21 / EMA55 / EMA144 (1h): {market_snapshot.get('ema_21_1h', 'N/A')} / {market_snapshot.get('ema_55_1h', 'N/A')} / {market_snapshot.get('ema_144_1h', 'N/A')}",
        f"- Hourly trend bias: {market_snapshot.get('hourly_trend_bias', 'N/A')}",
        f"- Asset tier: {market_snapshot.get('asset_tier', 'N/A')}",
        f"- Narrative tag: {market_snapshot.get('narrative_tag', 'N/A')}",
        f"- Liquidity regime: {market_snapshot.get('liquidity_regime', 'N/A')}",
        f"- Crowding regime: {market_snapshot.get('crowding_regime', 'N/A')}",
        f"- Volatility regime: {market_snapshot.get('volatility_regime', 'N/A')}",
        f"- Momentum regime: {market_snapshot.get('momentum_regime', 'N/A')}",
        f"- BTC regime: {market_snapshot.get('btc_market_regime', 'N/A')}",
        f"- Relative strength vs BTC (7d): {market_snapshot.get('relative_strength_7d_pct', 'N/A')}%",
        f"- Execution template: {market_snapshot.get('execution_template', 'N/A')}",
    ])

    pos_section = ""
    if positions:
        pos_section = "\n\n## Current Positions\n"
        for p in positions:
            pos_section += (
                f"  - {p.get('symbol', symbol)} {p.get('direction', '')} | "
                f"uPnL: {p.get('unrealized_pnl', 'N/A')}\n"
            )

    catalysts_str = ", ".join(research.catalysts[:3]) if research.catalysts else "none"
    risks_str = ", ".join(research.risks[:3]) if research.risks else "none"

    user = (
        f"Generate an ExecutionPlan JSON for {symbol}.\n\n"
        f"## Research Decision\n"
        f"- Stance: {research.stance}\n"
        f"- Confidence: {research.confidence:.1%}\n"
        f"- Thesis: {research.thesis}\n"
        f"- Market structure: {research.market_structure}\n"
        f"- Catalysts: {catalysts_str}\n"
        f"- Risks: {risks_str}\n"
        f"- Invalidation: {research.invalidation}\n"
        f"- Time horizon: {research.time_horizon}\n"
        f"- Preferred market: {research.preferred_market}\n\n"
        f"## Account\n"
        f"- Balance: ${balance:,.2f} USDT\n"
        f"- Drawdown: {drawdown:.2f}%"
        f"\n\n## Market Snapshot\n{market_section}"
        f"{pos_section}\n\n"
        f"Your execution plan must respect the current crypto volatility / funding backdrop. If the move is stretched or funding is crowded, prefer smaller size, lower leverage, or HOLD.\n"
        f"Provide concrete entry geometry for crypto execution: ideal entry zone, trigger price, invalidation price, and how many hours the setup stays valid.\n\n"
        f"Respond with ONLY the JSON object."
    )

    return system, user


def build_reflection_prompt(
    symbol: str,
    trades: list[dict],
) -> tuple[str, str]:
    """Return (system, user) prompts for trade reflection generation."""

    system = """You are an experienced trading coach. Analyze past trades and extract actionable lessons.

RESPONSE FORMAT: Respond with ONLY a valid JSON object, no markdown fences, no other text.

Schema:
{
  "direction_accuracy": "string — was the directional call correct overall?",
  "thesis_evaluation": "string — which parts of the thesis held or failed",
  "lessons": ["lesson 1", "lesson 2", "lesson 3"],
  "reflection_text": "string — comprehensive 2-4 paragraph reflection"
}"""

    trades_text = json.dumps(trades, indent=2, default=str)[:4000]

    user = (
        f"Analyze these closed {symbol} trades and generate a Reflection JSON.\n\n"
        f"```json\n{trades_text}\n```\n\n"
        f"Respond with ONLY the JSON object."
    )

    return system, user
