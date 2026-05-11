"""Prompt builders for ResearchDecision, ExecutionPlan, and Reflection generation."""

import json
from typing import Any

# Approximate chars-per-token ratio for Claude (conservative)
_CHARS_PER_TOKEN = 4
_VAULT_TOKEN_BUDGET = 2000  # ~2000 tokens = ~8000 chars


def _smart_truncate_vault(vault_context: str, symbol: str, token_budget: int = _VAULT_TOKEN_BUDGET) -> str:
    """Intelligently truncate vault context, prioritizing symbol-relevant sections."""
    if not vault_context:
        return "\uff08\u65e0 Vault \u4ea4\u6613\u77e5\u8bc6\u53ef\u7528\uff09"

    char_budget = token_budget * _CHARS_PER_TOKEN
    if len(vault_context) <= char_budget:
        return vault_context

    # Split into sections by ## headers
    sections = vault_context.split("\n## ")
    header = sections[0]  # Content before first ## or the first section itself
    named_sections = [(f"## {s}" if i > 0 else s) for i, s in enumerate(sections)]

    # Classify: symbol-specific vs general
    sym_lower = symbol.lower().replace("usdt", "").replace("usdc", "")
    symbol_sections: list[str] = []
    general_sections: list[str] = []

    for sec in named_sections:
        if sym_lower in sec.lower() or symbol.upper() in sec:
            symbol_sections.append(sec)
        else:
            general_sections.append(sec)

    # Build result: symbol sections first (full), then general (truncated)
    result_parts: list[str] = []
    remaining = char_budget

    for sec in symbol_sections:
        if remaining <= 0:
            break
        result_parts.append(sec[:remaining])
        remaining -= len(sec)

    for sec in general_sections:
        if remaining <= 200:  # Leave at least 200 chars margin
            break
        result_parts.append(sec[:remaining])
        remaining -= len(sec)

    result = "\n".join(result_parts)
    if len(result) < len(vault_context):
        result += "\n\n[...vault context truncated for token budget...]"
    return result


def build_research_prompt(
    symbol: str,
    account_summary: dict,
    positions: list[dict],
    market_snapshot: dict,
    vault_context: str,
    trade_history: list[dict],
    reflections: list[dict],
    risk_params: dict,
    onchain_snapshot: dict | None = None,
    portfolio_snapshot: dict | None = None,
    portfolio_budget: dict | None = None,
    event_snapshot: dict | None = None,
    adaptive_context: dict | None = None,
) -> tuple[str, str]:
    """Return (system, user) prompts for ResearchDecision generation."""

    max_lev = int(risk_params.get("max_leverage", 35))
    max_size = risk_params.get("max_position_size_pct", 0.05) * 100
    default_sl = risk_params.get("default_stop_loss_pct", 0.05) * 100
    vault_section = _smart_truncate_vault(vault_context, symbol)

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

    onchain_section = _build_onchain_section(onchain_snapshot)
    portfolio_section = _build_portfolio_section(portfolio_snapshot, portfolio_budget)
    events_section = _build_events_section(event_snapshot)
    adaptive_section = _build_adaptive_section(adaptive_context)

    user = (
        f"Analyze {symbol} and generate a ResearchDecision JSON.\n\n"
        f"## Account\n{acct_section}"
        f"\n\n## Market Snapshot\n{market_section}"
        f"{onchain_section}"
        f"{portfolio_section}"
        f"{events_section}"
        f"{adaptive_section}"
        f"{pos_section}{hist_section}{ref_section}\n\n"
        f"Your thesis must be anchored in the market snapshot. The evidence list must quote concrete observations from the supplied crypto price / funding / volatility data.\n"
        f"Use adaptive guidance only as a secondary overlay when current evidence is ambiguous; never invent facts to fit past outcomes.\n\n"
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
    adaptive_context: dict | None = None,
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
    adaptive_section = _build_adaptive_section(adaptive_context)

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
        f"{adaptive_section}"
        f"{pos_section}\n\n"
        f"Your execution plan must respect the current crypto volatility / funding backdrop. If the move is stretched or funding is crowded, prefer smaller size, lower leverage, or HOLD.\n"
        f"If adaptive guidance is defensive, reduce aggressiveness via smaller size, lower leverage, tighter confirmation, or HOLD.\n"
        f"Provide concrete entry geometry for crypto execution: ideal entry zone, trigger price, invalidation price, and how many hours the setup stays valid.\n\n"
        f"Respond with ONLY the JSON object."
    )

    return system, user


def _build_onchain_section(onchain: dict | None) -> str:
    """Format on-chain snapshot into a prompt section. Returns '' when no data."""
    if not onchain or not onchain.get("has_onchain_data"):
        return ""

    lines: list[str] = ["\n\n## On-Chain Data"]

    # Protocol TVL
    if onchain.get("protocol_name") and onchain.get("protocol_tvl_usd") is not None:
        tvl = onchain["protocol_tvl_usd"]
        lines.append(f"- Protocol: {onchain['protocol_name']} ({onchain.get('protocol_category', 'DeFi')})")
        lines.append(f"- Protocol TVL: ${tvl / 1e6:.1f}M")
        if onchain.get("tvl_change_24h_pct") is not None:
            lines.append(f"- TVL 24h change: {onchain['tvl_change_24h_pct']:+.1f}%")
        if onchain.get("tvl_change_7d_pct") is not None:
            lines.append(f"- TVL 7d change: {onchain['tvl_change_7d_pct']:+.1f}%")
        if onchain.get("protocol_chains"):
            lines.append(f"- Active chains: {', '.join(onchain['protocol_chains'][:4])}")

    # Chain TVL
    if onchain.get("chain_name") and onchain.get("chain_tvl_usd") is not None:
        chain_tvl = onchain["chain_tvl_usd"]
        lines.append(f"- {onchain['chain_name']} chain TVL: ${chain_tvl / 1e9:.2f}B")

    # Stablecoin macro
    if onchain.get("stablecoin_total_usd") is not None:
        st = onchain["stablecoin_total_usd"]
        lines.append(f"- Total stablecoin supply: ${st / 1e9:.1f}B")

    return "\n".join(lines) if len(lines) > 1 else ""


def _build_portfolio_section(portfolio_snapshot: dict | None, portfolio_budget: dict | None) -> str:
    """Format portfolio overlay into a prompt section for AI context."""
    if not portfolio_snapshot and not portfolio_budget:
        return ""

    lines: list[str] = ["\n\n## Portfolio Context"]

    if portfolio_snapshot:
        gross = portfolio_snapshot.get("gross_exposure_pct", 0)
        net = portfolio_snapshot.get("net_exposure_pct", 0)
        pos_count = portfolio_snapshot.get("position_count", 0)
        total_bal = portfolio_snapshot.get("total_balance_usdt", 0)
        lines.append(f"- Total balance: ${total_bal:,.0f} USDT")
        lines.append(f"- Gross exposure: {gross:.1f}%")
        lines.append(f"- Net exposure: {net:.1f}%")
        lines.append(f"- Open positions: {pos_count}")
        # Narrative breakdown if available
        narratives = portfolio_snapshot.get("narrative_breakdown", {})
        if narratives:
            top = sorted(narratives.items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append(f"- Narrative concentration: {', '.join(f'{k}({v})' for k, v in top)}")

    if portfolio_budget:
        role = portfolio_budget.get("portfolio_role", "N/A")
        rec_size = portfolio_budget.get("recommended_max_size_pct", "N/A")
        hard_cap = portfolio_budget.get("hard_cap_size_pct", "N/A")
        narrative = portfolio_budget.get("narrative_tag", "N/A")
        lines.append(f"- This symbol's portfolio role: {role}")
        lines.append(f"- Recommended max size: {rec_size}%")
        lines.append(f"- Tier hard cap: {hard_cap}%")
        lines.append(f"- Narrative: {narrative}")
        warnings = portfolio_budget.get("warnings", [])
        if warnings:
            lines.append(f"- Budget warnings: {'; '.join(warnings[:3])}")
        rationale = portfolio_budget.get("rationale", "")
        if rationale:
            lines.append(f"- Budget rationale: {rationale[:150]}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _build_events_section(event_snapshot: dict | None) -> str:
    """Format upcoming events into a prompt section for AI context."""
    if not event_snapshot:
        return ""

    macro_events = event_snapshot.get("macro_events", [])
    unlock_events = event_snapshot.get("unlock_events", [])
    has_high = event_snapshot.get("has_high_impact_soon", False)
    has_unlock = event_snapshot.get("has_major_unlock_soon", False)

    if not macro_events and not unlock_events:
        return ""

    lines: list[str] = ["\n\n## Upcoming Events"]

    if has_high:
        lines.append("- ⚠️ HIGH-IMPACT MACRO EVENT IMMINENT — consider reducing size or waiting")
    if has_unlock:
        lines.append("- ⚠️ MAJOR TOKEN UNLOCK (>1% supply) within 7 days")

    if macro_events:
        lines.append("\n### Macro Calendar (next 48h)")
        for ev in macro_events[:5]:
            hours = ev.get("hours_until", "?")
            lines.append(f"  - [{ev.get('impact', '?')}] {ev.get('title', 'Unknown')} ({ev.get('country', '?')}) — in {hours:.0f}h")

    if unlock_events:
        lines.append("\n### Token Unlocks (next 7d)")
        for ev in unlock_events[:3]:
            pct = ev.get("pct_of_supply", 0)
            amt = ev.get("amount_usd", 0)
            lines.append(f"  - {ev.get('protocol', '?')}: ${amt/1e6:.1f}M ({pct:.1f}% supply) — {ev.get('unlock_type', 'unknown')}")

    return "\n".join(lines)


def _build_adaptive_section(adaptive_context: dict | None) -> str:
    """Format historical outcome guidance into a compact prompt section."""
    if not adaptive_context or not adaptive_context.get("enabled"):
        return ""

    lines = ["\n\n## Adaptive Guidance From Realized Outcomes"]
    mode = adaptive_context.get("profile_mode")
    if mode:
        lines.append(f"- Current adaptation mode: {mode}")
    preferred_bias = adaptive_context.get("preferred_bias")
    if preferred_bias:
        lines.append(f"- Preferred directional bias when evidence is close: {preferred_bias}")
    for line in (adaptive_context.get("guidance_lines") or [])[:6]:
        lines.append(f"- {line}")
    return "\n".join(lines)


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


def build_combined_research_execution_prompt(
    symbol: str,
    account_summary: dict,
    positions: list[dict],
    market_snapshot: dict,
    vault_context: str,
    trade_history: list[dict],
    reflections: list[dict],
    risk_params: dict,
    onchain_snapshot: dict | None = None,
    portfolio_snapshot: dict | None = None,
    portfolio_budget: dict | None = None,
    event_snapshot: dict | None = None,
    adaptive_context: dict | None = None,
) -> tuple[str, str]:
    """Return (system, user) prompts that produce BOTH ResearchDecision + ExecutionPlan in one call.

    This saves one Claude API call (~30% token reduction) by combining research and execution
    into a single structured output.
    """
    max_lev = int(risk_params.get("max_leverage", 35))
    max_size = risk_params.get("max_position_size_pct", 0.05) * 100
    default_sl = risk_params.get("default_stop_loss_pct", 0.05) * 100
    default_tp = risk_params.get("default_take_profit_pct", 0.15) * 100
    vault_section = _smart_truncate_vault(vault_context, symbol)

    system = f"""You are an expert cryptocurrency trading analyst AND execution planner.
Generate BOTH a research decision AND an execution plan in a single JSON response.

RESPONSE FORMAT: Respond with ONLY a valid JSON object, no markdown fences, no other text.

Schema:
{{
  "research": {{
    "stance": "bullish | bearish | neutral",
    "confidence": 0.0-1.0,
    "thesis": "string \u2014 core thesis grounded in crypto price data",
    "market_structure": "string \u2014 current market structure reading",
    "evidence": ["evidence point 1", "evidence point 2", ...],
    "catalysts": ["catalyst 1", ...],
    "risks": ["risk 1", ...],
    "invalidation": "string \u2014 what invalidates the thesis",
    "time_horizon": "intraday | swing_2_5d | position_1_4w",
    "preferred_market": "spot | futures"
  }},
  "execution": {{
    "action": "hold | buy_spot | sell_spot | open_long | close_long | open_short | close_short",
    "size_pct": 0.0-100.0,
    "leverage": 1-{max_lev},
    "entry_idea": "string",
    "entry_style": "market_now | buy_dip | sell_rip | breakout_confirmation | breakdown_confirmation | exit_now | hold",
    "entry_zone_low": "number or null",
    "entry_zone_high": "number or null",
    "trigger_price": "number or null",
    "invalidation_price": "number or null",
    "stop_loss_pct": 0.0+,
    "take_profit_pct": 0.0+,
    "thesis_window_hours": "integer or null",
    "rationale": "string"
  }}
}}

Rules:
- size_pct MUST NOT exceed {max_size:.0f}% (risk gate enforces this)
- leverage MUST NOT exceed {max_lev}x
- If stance is "neutral" or confidence < 0.4, set action to "hold" and size_pct to 0
- stop_loss_pct default: {default_sl:.0f}%
- take_profit_pct default: {default_tp:.0f}%
- Evidence and thesis MUST reference concrete data from the supplied crypto market snapshot

## Trading Knowledge Base
{vault_section}"""

    # Build user prompt sections
    balance = account_summary.get("futures_balance_usdt", 0)
    drawdown = account_summary.get("drawdown_pct", 0)
    acct_section = f"- Balance: ${balance:,.2f} USDT\n- Drawdown: {drawdown:.2f}%"

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
                f"size: {p.get('size_pct', 'N/A')}% | uPnL: {p.get('unrealized_pnl', 'N/A')}\n"
            )

    hist_section = ""
    if trade_history:
        hist_section = "\n\n## Recent Trade History\n"
        for t in trade_history[:5]:
            hist_section += f"  - {t.get('symbol', symbol)} {t.get('side', '')} PnL: {t.get('pnl', 'N/A')}\n"

    ref_section = ""
    if reflections:
        ref_section = "\n\n## Past Reflections\n"
        for r in reflections[:3]:
            ref_section += f"\n{r.get('reflection_text', '')[:400]}\n"

    onchain_section = _build_onchain_section(onchain_snapshot)
    portfolio_section = _build_portfolio_section(portfolio_snapshot, portfolio_budget)
    events_section = _build_events_section(event_snapshot)
    adaptive_section = _build_adaptive_section(adaptive_context)

    user = (
        f"Analyze {symbol} and generate BOTH a ResearchDecision AND ExecutionPlan in one JSON.\n\n"
        f"## Account\n{acct_section}"
        f"\n\n## Market Snapshot\n{market_section}"
        f"{onchain_section}"
        f"{portfolio_section}"
        f"{events_section}"
        f"{adaptive_section}"
        f"{pos_section}{hist_section}{ref_section}\n\n"
        f"Your thesis MUST be anchored in the market snapshot data. Evidence must quote concrete crypto observations.\n"
        f"Execution must respect current volatility/funding backdrop.\n"
        f"Use adaptive guidance only as a tie-breaker or sizing bias; current market evidence remains primary.\n\n"
        f"Respond with ONLY the JSON object."
    )

    return system, user
