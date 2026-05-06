"""End-to-end AI trading execution pipeline."""

import math
from datetime import datetime
from typing import Optional

from rich.console import Console

from ..ai.advisor import TradingAdvisor
from ..ai.client import ClaudeClient
from ..ai.schemas import ExecutionPlan, ExecutionResult, ResearchDecision, RiskDecision
from ..core.config import AppConfig
from ..core.journal import TradeJournal
from ..core.vault import VaultReader
from ..exchange.account import AccountManager
from ..exchange.client import BinanceClient
from ..exchange.market_data import MarketDataManager
from ..exchange.orders import OrderManager
from ..exchange.positions import PositionManager
from ..risk.gate import RiskGate
from ..risk.portfolio import PortfolioManager
from .persistence import TradeRunDB

console = Console()

_CLOSE_ACTIONS = frozenset({"close_long", "close_short", "sell_spot"})

# Map from pipeline action → Binance positionSide (hedge mode only)
_ACTION_TO_POSITION_SIDE: dict[str, str] = {
    "open_long":  "LONG",
    "close_long": "LONG",
    "open_short": "SHORT",
    "close_short": "SHORT",
}


def is_trading_api_configured(config: AppConfig) -> bool:
    """Return True if both Binance API key and secret are set."""
    return bool(config.binance.api_key and config.binance.api_secret)


# ------------------------------------------------------------------ #
# fix 2: quantity step-size rounding                                   #
# ------------------------------------------------------------------ #

def _step_round(quantity: float, step_size: float) -> float:
    """Floor-round quantity to the nearest valid step_size increment.

    Binance enforces LOT_SIZE.stepSize; submitting more decimal places than
    stepSize allows returns error -1111 (bad precision).  We floor (not round)
    so we never exceed available balance.
    """
    if step_size <= 0:
        return round(quantity, 3)
    floored = math.floor(quantity / step_size) * step_size
    # derive decimal places from step_size string representation
    step_str = f"{step_size:.10f}".rstrip("0")
    precision = len(step_str.split(".")[1]) if "." in step_str else 0
    return round(floored, precision)


class TradePipeline:
    """Complete AI trading execution pipeline (14 steps)."""

    def __init__(self, config: AppConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.db = TradeRunDB(config.logging.sqlite_db)
        self.claude = ClaudeClient(config.claude)
        self.advisor = TradingAdvisor(self.claude, config)
        self.risk_gate = RiskGate(config)
        self.portfolio_manager = PortfolioManager(config)
        self.last_market_snapshot: dict = {}
        self.last_portfolio_snapshot: dict = {}
        self.last_portfolio_budget: dict = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        symbol: str,
        market: str = "auto",
        write_vault: bool = True,
        allow_partial: bool = False,
    ) -> tuple[ResearchDecision, ExecutionPlan, RiskDecision, ExecutionResult]:
        """Run the full pipeline and return (research, plan, risk, result)."""
        symbol = symbol.upper()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        api_configured = is_trading_api_configured(self.config)

        account_summary = self._get_account_summary(api_configured)
        all_positions = self._get_positions(api_configured)
        symbol_positions = [p for p in all_positions if p.get("symbol", "").upper() == symbol]
        market_snapshot = self._get_market_snapshot(symbol)
        self.last_market_snapshot = market_snapshot
        portfolio_snapshot = self.portfolio_manager.build_snapshot(account_summary, all_positions).to_dict()
        portfolio_budget = self.portfolio_manager.recommend_budget(
            symbol=symbol,
            market_snapshot=market_snapshot,
            positions=all_positions,
            account_summary=account_summary,
        ).to_dict()
        self.last_portfolio_snapshot = portfolio_snapshot
        self.last_portfolio_budget = portfolio_budget

        vault_context = self._get_vault_context(symbol)
        trade_history = self._get_trade_history(symbol)
        reflections = self._get_reflections(symbol)

        if not self.claude.is_configured:
            raise RuntimeError(
                "Anthropic API key not configured (ANTHROPIC_API_KEY). "
                "Cannot generate research decision."
            )

        console.print(f"[cyan]Step 7/14  Generating ResearchDecision for {symbol}…[/cyan]")
        research = self.advisor.generate_research(
            symbol=symbol,
            account_summary=account_summary,
            positions=symbol_positions,
            market_snapshot=market_snapshot,
            vault_context=vault_context,
            trade_history=trade_history,
            reflections=reflections,
        )

        console.print(f"[cyan]Step 8/14  Generating ExecutionPlan for {symbol}…[/cyan]")
        plan = self.advisor.generate_execution_plan(
            symbol=symbol,
            research=research,
            account_summary=account_summary,
            positions=symbol_positions,
            market_snapshot=market_snapshot,
        )

        console.print("[cyan]Step 9/14  Running risk gate…[/cyan]")
        risk = self.risk_gate.evaluate(
            plan=plan,
            account_snapshot=account_summary,
            positions=all_positions,
            market_snapshot=market_snapshot,
            portfolio_snapshot=portfolio_snapshot,
            portfolio_budget=portfolio_budget,
        )

        console.print("[cyan]Step 10–11  Executing action…[/cyan]")
        result = self._execute(
            symbol=symbol,
            market=market,
            plan=plan,
            risk=risk,
            api_configured=api_configured,
            account_summary=account_summary,
            positions=symbol_positions,
            allow_partial=allow_partial,
        )

        report_path: Optional[str] = None
        if write_vault:
            report_path = self._write_vault_report(
                symbol=symbol,
                timestamp=timestamp,
                account_summary=account_summary,
                market_snapshot=market_snapshot,
                positions=symbol_positions,
                research=research,
                plan=plan,
                risk=risk,
                result=result,
                reflections=reflections,
                portfolio_snapshot=portfolio_snapshot,
                portfolio_budget=portfolio_budget,
            )

        console.print("[cyan]Step 12  Persisting run to SQLite…[/cyan]")
        self.db.save_run(
            symbol=symbol,
            model=self.config.claude.model,
            research_decision=research,
            execution_plan=plan,
            risk_decision=risk,
            execution_result=result,
            account_snapshot=account_summary,
            market_snapshot=market_snapshot,
            position_snapshot=symbol_positions,
            dry_run=self.dry_run,
            report_path=report_path,
        )

        return research, plan, risk, result

    # ------------------------------------------------------------------
    # Data-gathering helpers
    # ------------------------------------------------------------------

    def _get_account_summary(self, api_configured: bool) -> dict:
        if not api_configured or self.dry_run:
            return {
                "futures_balance_usdt": 10000.0,
                "spot_balance_usdt": 5000.0,
                "total_balance_usdt": 15000.0,
                "unrealized_pnl": 0.0,
                "pnl_pct": 0.0,
                "drawdown_pct": 0.0,
                "dry_run": True,
            }
        try:
            binance = BinanceClient(self.config.binance, dry_run=False)
            return AccountManager(binance).get_account_summary()
        except Exception as exc:
            console.print(f"[yellow]Warning: account fetch failed ({exc}), using mock data.[/yellow]")
            return {
                "futures_balance_usdt": 0.0,
                "spot_balance_usdt": 0.0,
                "total_balance_usdt": 0.0,
                "unrealized_pnl": 0.0,
                "pnl_pct": 0.0,
                "drawdown_pct": 0.0,
                "dry_run": True,
                "error": str(exc),
            }

    def _get_positions(self, api_configured: bool) -> list[dict]:
        if not api_configured or self.dry_run:
            return []
        try:
            binance = BinanceClient(self.config.binance, dry_run=False)
            futures = PositionManager(binance).get_futures_positions()
            return [
                {
                    "symbol": p.symbol,
                    "direction": "LONG" if p.is_long else "SHORT",
                    "amount": p.amount,
                    "price": p.price,
                    "leverage": p.leverage,
                    "unrealized_pnl": p.unrealized_pnl,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                }
                for p in futures
            ]
        except Exception as exc:
            console.print(f"[yellow]Warning: positions fetch failed ({exc}).[/yellow]")
            return []

    def _get_vault_context(self, symbol: str) -> str:
        try:
            vault = VaultReader(self.config.vault)
            return vault.build_knowledge_context([symbol])
        except Exception as exc:
            console.print(f"[yellow]Warning: Vault not available ({exc}).[/yellow]")
            return ""

    def _get_market_snapshot(self, symbol: str) -> dict:
        try:
            market_data = MarketDataManager(self.config, dry_run=self.dry_run)
            return market_data.get_market_snapshot(symbol).to_dict()
        except Exception as exc:
            console.print(f"[yellow]Warning: market snapshot unavailable ({exc}).[/yellow]")
            return {"symbol": symbol.upper(), "error": str(exc)}

    def _get_trade_history(self, symbol: str) -> list[dict]:
        try:
            journal = TradeJournal(self.config.logging.sqlite_db)
            trades = journal.get_trades(symbol=symbol, limit=10)
            return [
                {
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "realized_pnl": t.realized_pnl,
                    "pnl_pct": t.pnl_pct,
                    "open_time": t.open_time.isoformat(),
                    "close_time": t.close_time.isoformat(),
                    "notes": t.notes,
                }
                for t in trades
            ]
        except Exception:
            return []

    def _get_reflections(self, symbol: str) -> list[dict]:
        try:
            by_symbol = self.db.get_reflections(symbol=symbol, limit=3)
            cross = self.db.get_reflections(limit=2)
            seen = {r["id"] for r in by_symbol}
            combined = by_symbol + [r for r in cross if r["id"] not in seen]
            return combined[:5]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Execution logic
    # ------------------------------------------------------------------

    def _execute(
        self,
        symbol: str,
        market: str,
        plan: ExecutionPlan,
        risk: RiskDecision,
        api_configured: bool,
        account_summary: dict,
        positions: list[dict],
        allow_partial: bool,
    ) -> ExecutionResult:
        final_action = risk.adjusted_action or plan.action
        final_size_pct = (
            risk.adjusted_size_pct if risk.adjusted_size_pct is not None else plan.size_pct
        )
        final_leverage = (
            risk.adjusted_leverage if risk.adjusted_leverage is not None else plan.leverage
        )

        # Case 1: dry-run mode
        if self.dry_run:
            manual_ticket = self._build_manual_order_ticket(
                symbol=symbol,
                market=market,
                final_action=final_action,
                final_size_pct=final_size_pct,
                final_leverage=final_leverage,
                plan=plan,
                account_summary=account_summary,
                positions=positions,
                api_configured=api_configured,
            )
            return ExecutionResult(
                executed=False,
                status="dry_run",
                symbol=symbol,
                final_action=final_action,
                final_size_pct=final_size_pct,
                final_leverage=final_leverage,
                message=(
                    "已生成手动下单草案（基于 dry_run / mock 账户快照），"
                    "不会自动下单。请勿直接据此实盘执行。"
                    if manual_ticket
                    else "dry_run 仅完成分析预览；由于未配置交易 API，未生成可靠的手动下单明细。"
                ),
                order_ids=[],
                exchange="binance",
                manual_order_details=manual_ticket,
                execution_reason=(
                    f"自动下单已禁用；当前为 dry_run 预览。"
                    f" 计划理由：{plan.rationale} | 风控结论：{risk.rationale}"
                ),
            )

        # Case 2: trading API not configured
        if not api_configured:
            return ExecutionResult(
                executed=False,
                status="not_executed_missing_api",
                symbol=symbol,
                final_action=final_action,
                final_size_pct=final_size_pct,
                final_leverage=final_leverage,
                message="未配置交易api",
                order_ids=[],
                exchange="",
                execution_reason="未读取到 BINANCE_API_KEY / BINANCE_API_SECRET，无法生成基于真实账户的手动下单明细。",
            )

        # fix 4: duplicate execution protection
        recent = self.db.get_recent_executed_run(symbol, within_seconds=300)
        if recent:
            return ExecutionResult(
                executed=False,
                status="blocked_duplicate",
                symbol=symbol,
                final_action=final_action,
                final_size_pct=0.0,
                message=(
                    f"重复执行保护：{symbol} 在最近 5 分钟内已有成功订单 "
                    f"(run_id={recent['id']})，跳过本次执行"
                ),
                order_ids=[],
                exchange="binance",
            )

        # Case 3: risk gate rejected
        if not risk.approved:
            can_partial = (
                allow_partial
                and risk.adjusted_action not in (None, "hold")
                and (risk.adjusted_size_pct or 0.0) > 0
            )
            if not can_partial:
                return ExecutionResult(
                    executed=False,
                    status="blocked_by_risk",
                    symbol=symbol,
                    final_action=final_action,
                    final_size_pct=0.0,
                    final_leverage=None,
                    message=f"风险审批拒绝: {risk.rationale}",
                    order_ids=[],
                    exchange="binance",
                )

        # Case 4: hold action
        if final_action == "hold":
            return ExecutionResult(
                executed=False,
                status="hold",
                symbol=symbol,
                final_action="hold",
                final_size_pct=0.0,
                message="AI 最终建议 HOLD，不需要下单。",
                order_ids=[],
                exchange="binance",
                execution_reason=f"计划理由：{plan.rationale} | 风控结论：{risk.rationale}",
            )

        # Case 5: manual-review mode (live order placement disabled by design)
        manual_ticket = self._build_manual_order_ticket(
            symbol=symbol,
            market=market,
            final_action=final_action,
            final_size_pct=final_size_pct,
            final_leverage=final_leverage,
            plan=plan,
            account_summary=account_summary,
            positions=positions,
            api_configured=api_configured,
        )
        return ExecutionResult(
            executed=False,
            status="manual_review_required",
            symbol=symbol,
            final_action=final_action,
            final_size_pct=final_size_pct,
            final_leverage=final_leverage,
            message="已生成手动下单明细，请人工确认后自行下单。",
            order_ids=[],
            exchange="binance",
            manual_order_details=manual_ticket,
            execution_reason=(
                "自动下单已禁用，以降低误下单、精度处理错误、仓位模式不一致和止损止盈挂单失败带来的风险。"
                f" 计划理由：{plan.rationale} | 风控结论：{risk.rationale}"
            ),
        )

    # ------------------------------------------------------------------
    # Manual order preparation / Real order execution
    # ------------------------------------------------------------------

    def _get_mark_price(self, binance: BinanceClient, symbol: str) -> float:
        """Return current mark price; 0.0 on failure."""
        try:
            data = binance.futures_client.mark_price(symbol=symbol)
            return float(data.get("markPrice", 0))
        except Exception:
            return 0.0

    def _get_position_mode(self, binance: BinanceClient) -> bool:
        """Return True if the account is in Hedge Mode (dual position side).

        fix 3: Hedge Mode requires positionSide instead of reduceOnly.
        Falls back to False (one-way mode assumed) on any error.
        """
        try:
            result = binance.futures_client.get_position_mode()
            return bool(result.get("dualSidePosition", False))
        except Exception:
            return False

    def _infer_market(self, market: str, final_action: str) -> str:
        """Resolve market type from CLI hint + action semantics."""
        if market in ("spot", "futures"):
            return market
        return "spot" if final_action in ("buy_spot", "sell_spot") else "futures"

    def _get_spot_price(self, binance: BinanceClient, symbol: str) -> float:
        """Return current spot ticker price; 0.0 on failure."""
        try:
            data = binance.spot_client.ticker_price(symbol=symbol)  # type: ignore[union-attr]
            if isinstance(data, list):
                return 0.0
            return float(data.get("price", 0))
        except Exception:
            return 0.0

    def _prepare_order_context(
        self,
        symbol: str,
        market: str,
        final_action: str,
        final_size_pct: float,
        final_leverage: int,
        plan: ExecutionPlan,
        account_summary: dict,
        positions: list[dict],
        *,
        require_live_data: bool = True,
    ) -> dict:
        """Compute the order parameters without placing any order."""
        side_map = {
            "open_long": "BUY",
            "close_long": "SELL",
            "open_short": "SELL",
            "close_short": "BUY",
            "buy_spot": "BUY",
            "sell_spot": "SELL",
        }
        side = side_map.get(final_action)
        if not side:
            raise ValueError(f"Unknown action: {final_action!r}")

        resolved_market = self._infer_market(market, final_action)
        reduce_only = final_action in _CLOSE_ACTIONS

        mark_price = 0.0
        step_size = 0.001
        position_side: Optional[str] = None
        effective_reduce_only = reduce_only

        if require_live_data:
            binance = BinanceClient(self.config.binance, dry_run=False)
            if resolved_market == "spot":
                mark_price = self._get_spot_price(binance, symbol)
            else:
                mark_price = self._get_mark_price(binance, symbol)
                step_size = binance.futures_client.get_symbol_lot_size(symbol)
                is_hedge = self._get_position_mode(binance)
                position_side = _ACTION_TO_POSITION_SIDE.get(final_action) if is_hedge else None
                effective_reduce_only = reduce_only and not is_hedge
        else:
            mark_price = 0.0

        if mark_price <= 0:
            raise ValueError(f"无法获取 {symbol} 当前价格，无法生成可靠的下单明细")

        if resolved_market == "spot":
            if final_action == "sell_spot":
                quantity = 0.0
            else:
                quote_balance = float(account_summary.get("spot_balance_usdt", 0))
                notional = quote_balance * (final_size_pct / 100.0)
                quantity = round(notional / mark_price, 6)
        else:
            if final_action in ("close_long", "close_short"):
                pos_dir = "LONG" if final_action == "close_long" else "SHORT"
                matching = [
                    p for p in positions
                    if str(p.get("direction", "")).upper() == pos_dir
                ]
                if matching:
                    raw_qty = abs(float(matching[0]["amount"]))
                    quantity = _step_round(raw_qty, step_size)
                else:
                    balance = float(account_summary.get("futures_balance_usdt", 0))
                    margin = balance * (final_size_pct / 100.0)
                    quantity = _step_round(margin * final_leverage / mark_price, step_size)
            else:
                balance = float(account_summary.get("futures_balance_usdt", 0))
                margin = balance * (final_size_pct / 100.0)
                notional = margin * final_leverage
                quantity = _step_round(notional / mark_price, step_size)

        if quantity <= 0:
            raise ValueError("计算出的下单数量 <= 0，无法生成可执行明细")

        stop_loss_price: Optional[float] = None
        take_profit_price: Optional[float] = None
        if not reduce_only:
            if plan.stop_loss_pct > 0:
                stop_loss_price = round(
                    mark_price * (1 - plan.stop_loss_pct / 100), 4
                ) if side == "BUY" else round(
                    mark_price * (1 + plan.stop_loss_pct / 100), 4
                )
            if plan.take_profit_pct > 0:
                take_profit_price = round(
                    mark_price * (1 + plan.take_profit_pct / 100), 4
                ) if side == "BUY" else round(
                    mark_price * (1 - plan.take_profit_pct / 100), 4
                )

        notional_usdt = quantity * mark_price
        entry_validity = "valid_now"
        if plan.entry_zone_low is not None and plan.entry_zone_high is not None:
            if plan.entry_zone_low <= mark_price <= plan.entry_zone_high:
                entry_validity = "inside_entry_zone"
            else:
                entry_validity = "outside_entry_zone"
        ticket = {
            "symbol": symbol,
            "market": resolved_market,
            "side": side,
            "order_type": "MARKET",
            "quantity": quantity,
            "mark_price": mark_price,
            "notional_usdt": round(notional_usdt, 4),
            "size_pct": final_size_pct,
            "leverage": final_leverage if resolved_market == "futures" else None,
            "reduce_only": effective_reduce_only if resolved_market == "futures" else False,
            "position_side": position_side,
            "step_size": step_size if resolved_market == "futures" else None,
            "entry_style": plan.entry_style,
            "entry_zone_low": plan.entry_zone_low,
            "entry_zone_high": plan.entry_zone_high,
            "trigger_price": plan.trigger_price,
            "invalidation_price": plan.invalidation_price,
            "thesis_window_hours": plan.thesis_window_hours,
            "entry_validity": entry_validity,
            "execution_template": self.last_market_snapshot.get("execution_template"),
            "btc_market_regime": self.last_market_snapshot.get("btc_market_regime"),
            "asset_tier": self.last_market_snapshot.get("asset_tier"),
            "narrative_tag": self.last_market_snapshot.get("narrative_tag"),
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "entry_idea": plan.entry_idea,
            "rationale": plan.rationale,
        }
        ticket.update(self._template_execution_rules(ticket))
        return ticket

    def _template_execution_rules(self, ticket: dict) -> dict:
        """Map execution_template to concrete manual execution instructions."""
        template = ticket.get("execution_template") or "generic_manual_review"
        btc_regime = ticket.get("btc_market_regime") or "range"
        asset_tier = ticket.get("asset_tier") or "unknown"
        narrative_tag = ticket.get("narrative_tag") or "general_alt"
        notional = float(ticket.get("notional_usdt", 0.0) or 0.0)

        rules = {
            "preferred_order_type": "market",
            "staging_plan": "single entry",
            "max_slippage_bps": 20,
            "confirmation_checklist": "",
            "cancel_if": "",
            "template_risk_note": "",
            "operator_steps": "",
            "post_fill_protocol": "",
            "review_after_hours": "",
        }

        if template == "core_trend_follow":
            rules.update(
                preferred_order_type="market_or_passive_limit",
                staging_plan="40% starter / 30% add on confirmation / 30% add on pullback hold",
                max_slippage_bps=15,
                confirmation_checklist=(
                    "价格保持在 EMA21/EMA55 之上，Funding 未显著过热，BTC 仍处于 risk_on_trend 或 short_squeeze。"
                ),
                cancel_if="1h 重新跌回 EMA21 下方且量价转弱。",
                template_risk_note="核心币趋势单，可接受更主动执行，但仍避免在 24h 极端拉升后追高。",
                operator_steps="1. 先确认 BTC 仍在趋势中 2. 先下 40% 观察仓 3. 只有趋势确认后再补仓",
                post_fill_protocol="若 1h 收盘跌回 EMA21 下方，暂停加仓并重新评估。",
                review_after_hours="12h",
            )
        elif template == "core_reclaim_wait":
            rules.update(
                preferred_order_type="limit_after_reclaim",
                staging_plan="30% 试单 / 30% reclaim 确认 / 40% 回踩不破再补",
                max_slippage_bps=12,
                confirmation_checklist="等待 BTC 从 panic_flush/rebound 中完成 reclaim，确认收回关键均线或触发价。",
                cancel_if="reclaim 失败或反弹量能快速衰减。",
                template_risk_note="核心币反转/收复型交易，不追第一根反弹棒，优先等确认。",
                operator_steps="1. 等 reclaim 信号 2. 触发后先小仓试单 3. 回踩不破再补",
                post_fill_protocol="若 reclaim 后 1-2 根 K 线无法站稳，直接取消剩余计划。",
                review_after_hours="6h",
            )
        elif template == "core_range_trade":
            rules.update(
                preferred_order_type="limit",
                staging_plan="50% near zone / 50% on retest",
                max_slippage_bps=10,
                confirmation_checklist="仅在给定 entry zone 内执行，若已脱离区间则放弃。",
                cancel_if="价格离开区间并形成新趋势结构。",
                template_risk_note="区间交易必须严格执行 entry zone 与 invalidation，不做区间中段追单。",
                operator_steps="1. 只挂区间边缘单 2. 中段不追 3. 触发后马上设置失效价",
                post_fill_protocol="若区间边缘失守且未快速收回，按 invalidation 退出。",
                review_after_hours="8h",
            )
        elif template == "alt_follow_with_confirmation":
            rules.update(
                preferred_order_type="limit_or_stop_limit",
                staging_plan="25% 试单 / 25% 确认突破 / 50% 站稳 trigger 后加仓",
                max_slippage_bps=10,
                confirmation_checklist=(
                    "BTC 维持 risk_on_trend 或 rebound；该币相对 BTC 不转弱；Funding 与 OI 没有出现拥挤长仓失控。"
                ),
                cancel_if="BTC regime 转 risk_off 或相对 BTC 强度快速走弱。",
                template_risk_note="主流山寨跟随单，核心在于跟随 BTC 节奏而不是独立猜底。",
                operator_steps="1. 先看 BTC 方向 2. 只在 trigger 附近动手 3. 突破失败不硬接",
                post_fill_protocol="若 BTC 先转弱，优先减仓而不是等本币单独走坏。",
                review_after_hours="8h",
            )
        elif template == "alt_defensive_only":
            rules.update(
                preferred_order_type="small_limit_only",
                staging_plan="最多 2 次试单，小仓位逐步介入",
                max_slippage_bps=8,
                confirmation_checklist="只允许轻仓防守型试单，必须有清晰失效价，且 BTC 不继续走弱。",
                cancel_if="BTC 延续 risk_off 或本币继续跑输 BTC。",
                template_risk_note="防守模式下的山寨币不应重仓，宁可错过也不硬接。",
                operator_steps="1. 只挂小额限价单 2. 不抢第一波反弹 3. 只给一次补仓机会",
                post_fill_protocol="若 BTC 再度转弱，优先先退出来，不做死扛。",
                review_after_hours="4h",
            )
        elif template == "alt_selective_range":
            rules.update(
                preferred_order_type="limit",
                staging_plan="33% / 33% / 34% 分三档",
                max_slippage_bps=8,
                confirmation_checklist="只在区间下沿或明确 trigger 附近做，区间中部不交易。",
                cancel_if="BTC 或本币脱离区间形成趋势突破/跌破。",
                template_risk_note="选择性山寨区间单，重点是耐心，不抢中间位置。",
                operator_steps="1. 三档限价 2. 区间中段绝不追 3. 只在边缘做风险收益比",
                post_fill_protocol="一旦脱离区间，取消未成交剩余订单。",
                review_after_hours="6h",
            )
        elif template == "mid_alt_staged_entry":
            rules.update(
                preferred_order_type="passive_limit",
                staging_plan="20% 试单 / 30% 回踩确认 / 50% 仅在量能确认后补",
                max_slippage_bps=6,
                confirmation_checklist="中等流动性山寨币必须分批；若实际成交偏离 zone，则放弃。",
                cancel_if="突破失败、成交量萎缩、或 BTC 转弱。",
                template_risk_note="中等流动性 alt 以价格位置优先，不能用市场单硬追。",
                operator_steps="1. 只挂被动限价 2. 分批成交 3. 量能不确认不补",
                post_fill_protocol="若成交量迅速回落，保留观察仓，取消加仓计划。",
                review_after_hours="6h",
            )
        elif template == "high_beta_confirmation_only":
            rules.update(
                preferred_order_type="limit_only",
                staging_plan="10% 观察仓 / 20% 确认仓 / 70% 只有放量确认后才考虑",
                max_slippage_bps=5,
                confirmation_checklist=(
                    "必须满足 trigger_price、BTC 非 risk_off、该币相对 BTC 不弱，且成交额足以承接目标名义价值。"
                ),
                cancel_if="任何一条确认条件失效；若 notional 超过薄流动性阈值则取消。",
                template_risk_note="高 beta 小币只允许确认后参与，禁止直接用市场单追入。",
                operator_steps="1. 先确认 trigger 2. 先极小仓试单 3. 放量确认前不追加",
                post_fill_protocol="若第一笔试单后流动性明显恶化，停止后续执行。",
                review_after_hours="2h",
            )
        else:
            rules.update(
                preferred_order_type="manual_judgement",
                staging_plan="single entry",
                confirmation_checklist="按 trigger / invalidation / zone 人工确认。",
                cancel_if="市场条件与 thesis 不再匹配。",
                template_risk_note="默认人工审查模板。",
                operator_steps="按触发价和失效价手工确认后再下单。",
                post_fill_protocol="成交后重新检查 thesis 是否仍有效。",
                review_after_hours="8h",
            )

        thin_threshold = float(getattr(self.config.risk, "thin_liquidity_market_order_notional_usdt", 15_000.0))
        if asset_tier == "high_beta_alt" and notional >= thin_threshold:
            rules["template_risk_note"] += " 当前名义价值对薄流动性币偏大，建议进一步拆单或放弃。"
            rules["preferred_order_type"] = "laddered_limit_only"
        if btc_regime in {"panic_flush", "risk_off_trend"} and asset_tier != "core":
            rules["template_risk_note"] += " BTC 风险关闭时，山寨币执行只能更保守。"
        if narrative_tag == "meme":
            rules["template_risk_note"] += " Meme 币叙事波动大，优先轻仓、分批、快进快出。"
            rules["max_slippage_bps"] = min(rules["max_slippage_bps"], 5)

        return rules

    def _build_manual_order_ticket(
        self,
        symbol: str,
        market: str,
        final_action: str,
        final_size_pct: float,
        final_leverage: int,
        plan: ExecutionPlan,
        account_summary: dict,
        positions: list[dict],
        api_configured: bool,
    ) -> Optional[dict]:
        """Build a human-readable manual order ticket."""
        if not api_configured:
            return None
        try:
            return self._prepare_order_context(
                symbol=symbol,
                market=market,
                final_action=final_action,
                final_size_pct=final_size_pct,
                final_leverage=final_leverage,
                plan=plan,
                account_summary=account_summary,
                positions=positions,
                require_live_data=not self.dry_run,
            )
        except Exception as exc:
            return {
                "symbol": symbol,
                "market": self._infer_market(market, final_action),
                "error": f"无法生成完整下单明细：{exc}",
                "entry_idea": plan.entry_idea,
                "rationale": plan.rationale,
            }

    def _do_execute(
        self,
        symbol: str,
        market: str,
        final_action: str,
        final_size_pct: float,
        final_leverage: int,
        plan: ExecutionPlan,
        account_summary: dict,
        positions: list[dict],   # fix 2+3: used for close qty and hedge mode
    ) -> ExecutionResult:
        """Place a real order via OrderManager."""
        try:
            binance = BinanceClient(self.config.binance, dry_run=False)
            order_mgr = OrderManager(binance)

            side_map = {
                "open_long":  "BUY",
                "close_long": "SELL",
                "open_short": "SELL",
                "close_short": "BUY",
                "buy_spot":  "BUY",
                "sell_spot": "SELL",
            }
            side = side_map.get(final_action)
            if not side:
                raise ValueError(f"Unknown action: {final_action!r}")

            reduce_only = final_action in _CLOSE_ACTIONS

            # fix 1: mark_price() now exists on UMFutures
            mark_price = self._get_mark_price(binance, symbol)
            if mark_price <= 0:
                raise ValueError(
                    f"Could not obtain mark price for {symbol} (got {mark_price}). "
                    "Check that mark_price() is implemented in UMFutures."
                )

            # fix 2: get exchange-defined stepSize before computing quantity
            step_size = binance.futures_client.get_symbol_lot_size(symbol)

            # For close actions use actual position size when available
            if final_action in ("close_long", "close_short"):
                pos_dir = "LONG" if final_action == "close_long" else "SHORT"
                matching = [
                    p for p in positions
                    if str(p.get("direction", "")).upper() == pos_dir
                ]
                if matching:
                    raw_qty = abs(float(matching[0]["amount"]))
                    quantity = _step_round(raw_qty, step_size)
                else:
                    # No position found; compute from plan (will likely fail at exchange)
                    balance = float(account_summary.get("futures_balance_usdt", 0))
                    margin = balance * (final_size_pct / 100.0)
                    quantity = _step_round(margin * final_leverage / mark_price, step_size)
            else:
                balance = float(account_summary.get("futures_balance_usdt", 0))
                margin = balance * (final_size_pct / 100.0)
                notional = margin * final_leverage
                quantity = _step_round(notional / mark_price, step_size)

            if quantity <= 0:
                raise ValueError(
                    f"Computed quantity <= 0 after step rounding "
                    f"(step_size={step_size}, balance={account_summary.get('futures_balance_usdt')}, "
                    f"size_pct={final_size_pct}, lev={final_leverage}, price={mark_price})"
                )

            # fix 1: change_leverage() now exists on UMFutures
            if final_action in ("open_long", "open_short"):
                try:
                    binance.futures_client.change_leverage(
                        symbol=symbol, leverage=final_leverage
                    )
                except Exception as exc:
                    console.print(f"[yellow]Warning: could not set leverage: {exc}[/yellow]")

            # fix 3: detect Hedge Mode; use positionSide instead of reduceOnly
            is_hedge = self._get_position_mode(binance)
            position_side: Optional[str] = (
                _ACTION_TO_POSITION_SIDE.get(final_action) if is_hedge else None
            )
            # In hedge mode reduceOnly must NOT be sent
            effective_reduce_only = reduce_only and not is_hedge

            # Main order
            resp = order_mgr.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                reduce_only=effective_reduce_only,
                position_side=position_side,
                dry_run=False,
            )
            order_ids = [str(resp.get("orderId", ""))] if resp else []

            # fix 5: track SL/TP status explicitly
            sl_status = "skipped"
            sl_order_id: Optional[str] = None
            tp_status = "skipped"
            tp_order_id: Optional[str] = None

            # SL/TP only for open actions
            if not reduce_only:
                # Stop-loss
                if plan.stop_loss_pct > 0:
                    try:
                        if side == "BUY":
                            sl_price = round(mark_price * (1 - plan.stop_loss_pct / 100), 2)
                            sl_side = "SELL"
                        else:
                            sl_price = round(mark_price * (1 + plan.stop_loss_pct / 100), 2)
                            sl_side = "BUY"
                        sl_resp = order_mgr.place_stop_market_order(
                            symbol=symbol,
                            side=sl_side,
                            quantity=quantity,
                            stop_price=sl_price,
                            reduce_only=not is_hedge,
                            position_side=position_side,
                            dry_run=False,
                        )
                        if sl_resp:
                            sl_order_id = str(sl_resp.get("orderId", ""))
                            order_ids.append(sl_order_id)
                            sl_status = "placed"
                        else:
                            sl_status = "failed"
                    except Exception as exc:
                        sl_status = "failed"
                        console.print(f"[yellow]Warning: stop-loss order failed: {exc}[/yellow]")

                # Take-profit
                if plan.take_profit_pct > 0:
                    try:
                        if side == "BUY":
                            tp_price = round(mark_price * (1 + plan.take_profit_pct / 100), 2)
                            tp_side = "SELL"
                        else:
                            tp_price = round(mark_price * (1 - plan.take_profit_pct / 100), 2)
                            tp_side = "BUY"
                        tp_resp = order_mgr.place_take_profit_market_order(
                            symbol=symbol,
                            side=tp_side,
                            quantity=quantity,
                            stop_price=tp_price,
                            reduce_only=not is_hedge,
                            position_side=position_side,
                            dry_run=False,
                        )
                        if tp_resp:
                            tp_order_id = str(tp_resp.get("orderId", ""))
                            order_ids.append(tp_order_id)
                            tp_status = "placed"
                        else:
                            tp_status = "failed"
                    except Exception as exc:
                        tp_status = "failed"
                        console.print(f"[yellow]Warning: take-profit order failed: {exc}[/yellow]")

            # Build informative message when SL/TP partially failed
            attached = []
            if sl_status == "placed":
                attached.append(f"SL#{sl_order_id}")
            elif sl_status == "failed":
                attached.append("SL:FAILED")
            if tp_status == "placed":
                attached.append(f"TP#{tp_order_id}")
            elif tp_status == "failed":
                attached.append("TP:FAILED")

            msg = f"Order executed: {side} {quantity} {symbol}"
            if attached:
                msg += f" | {', '.join(attached)}"

            return ExecutionResult(
                executed=True,
                status="executed",
                symbol=symbol,
                final_action=final_action,
                final_size_pct=final_size_pct,
                final_leverage=final_leverage,
                message=msg,
                order_ids=order_ids,
                exchange="binance",
                raw_response_json=resp,
                sl_status=sl_status,
                tp_status=tp_status,
                sl_order_id=sl_order_id,
                tp_order_id=tp_order_id,
            )

        except Exception as exc:
            return ExecutionResult(
                executed=False,
                status="execution_failed",
                symbol=symbol,
                final_action=final_action,
                final_size_pct=final_size_pct,
                final_leverage=final_leverage,
                message=f"执行失败: {exc}",
                order_ids=[],
                exchange="binance",
            )

    # ------------------------------------------------------------------
    # Vault report
    # ------------------------------------------------------------------

    def _write_vault_report(
        self,
        symbol: str,
        timestamp: str,
        account_summary: dict,
        market_snapshot: dict,
        positions: list[dict],
        research: ResearchDecision,
        plan: ExecutionPlan,
        risk: RiskDecision,
        result: ExecutionResult,
        reflections: list[dict],
        portfolio_snapshot: dict,
        portfolio_budget: dict,
    ) -> Optional[str]:
        try:
            vault = VaultReader(self.config.vault)
            content = _build_report(
                symbol=symbol,
                timestamp=timestamp,
                account_summary=account_summary,
                market_snapshot=market_snapshot,
                positions=positions,
                research=research,
                plan=plan,
                risk=risk,
                result=result,
                reflections=reflections,
                portfolio_snapshot=portfolio_snapshot,
                portfolio_budget=portfolio_budget,
            )
            path = vault.write_report(filename=f"交易执行-{symbol}-{timestamp}", content=content)
            console.print(f"[green]✓ Vault report: {path}[/green]")
            return str(path)
        except Exception as exc:
            console.print(f"[yellow]Warning: Vault report not written ({exc}).[/yellow]")
            return None


# ------------------------------------------------------------------
# Report builder
# ------------------------------------------------------------------

def _build_report(
    symbol: str,
    timestamp: str,
    account_summary: dict,
    market_snapshot: dict,
    positions: list[dict],
    research: ResearchDecision,
    plan: ExecutionPlan,
    risk: RiskDecision,
    result: ExecutionResult,
    reflections: list[dict],
    portfolio_snapshot: dict,
    portfolio_budget: dict,
) -> str:
    mode = "（模拟运行）" if account_summary.get("dry_run") else ""
    lines: list[str] = [
        f"# 交易执行报告 — {symbol} {mode}",
        f"\n生成时间：{timestamp}\n\n---\n",
        "## 一、账户快照\n",
        f"- 合约余额：${account_summary.get('futures_balance_usdt', 0):,.2f} USDT",
        f"- 现货余额：${account_summary.get('spot_balance_usdt', 0):,.2f} USDT",
        f"- 总余额：${account_summary.get('total_balance_usdt', 0):,.2f} USDT",
        f"- 未实现盈亏：${account_summary.get('unrealized_pnl', 0):,.2f}",
        f"- 回撤：{account_summary.get('drawdown_pct', 0):.2f}%",
        "",
        "## 二、当前仓位\n",
    ]

    if positions:
        for p in positions:
            lines.append(
                f"- {p['symbol']} {p.get('direction', '')} | "
                f"入场价：{p.get('price', 'N/A')} | "
                f"未实现盈亏：{p.get('unrealized_pnl', 'N/A')}"
            )
    else:
        lines.append("无持仓")

    lines += [
        "",
        "## 三、市场快照\n",
        f"- 现货价格：{market_snapshot.get('spot_price', 'N/A')}",
        f"- 合约标记价格：{market_snapshot.get('futures_mark_price', 'N/A')}",
        f"- 24h 涨跌：{market_snapshot.get('price_change_24h_pct', 'N/A')}%",
        f"- 7d 涨跌：{market_snapshot.get('price_change_7d_pct', 'N/A')}%",
        f"- 24h 成交额：{market_snapshot.get('quote_volume_24h_usdt', 'N/A')} USDT",
        f"- Funding：{market_snapshot.get('funding_rate', 'N/A')}",
        f"- Open Interest：{market_snapshot.get('open_interest', 'N/A')}",
        f"- Basis：{market_snapshot.get('basis_bps', 'N/A')} bps",
        f"- 24h 实现波动：{market_snapshot.get('realized_vol_24h_pct', 'N/A')}%",
        f"- 7d 实现波动：{market_snapshot.get('realized_vol_7d_pct', 'N/A')}%",
        f"- 波动状态：{market_snapshot.get('volatility_regime', 'N/A')}",
        f"- 动量状态：{market_snapshot.get('momentum_regime', 'N/A')}",
        f"- 叙事标签：{market_snapshot.get('narrative_tag', 'N/A')}",
        "",
        "## 四、组合层快照\n",
        f"- 总资产：${portfolio_snapshot.get('total_balance_usdt', 0):,.2f}",
        f"- Gross Exposure：{portfolio_snapshot.get('gross_exposure_pct', 0):.1f}%",
        f"- Net Exposure：{portfolio_snapshot.get('net_exposure_pct', 0):.1f}%",
        f"- 持仓数：{portfolio_snapshot.get('position_count', 0)}",
        f"- Narrative：{portfolio_budget.get('narrative_tag', 'N/A')}",
        f"- Portfolio Role：{portfolio_budget.get('portfolio_role', 'N/A')}",
        f"- 推荐最大仓位：{portfolio_budget.get('recommended_max_size_pct', 'N/A')}%",
        f"- Tier Hard Cap：{portfolio_budget.get('hard_cap_size_pct', 'N/A')}%",
        f"- 组合预算理由：{portfolio_budget.get('rationale', 'N/A')}",
        "",
        "## 五、研究结论 (ResearchDecision)\n",
        f"- **交易对**：{research.symbol}",
        f"- **方向**：{research.stance.upper()}",
        f"- **信心**：{research.confidence:.1%}",
        f"- **核心逻辑**：{research.thesis}",
        f"- **市场结构**：{research.market_structure}",
        "- **催化剂**：",
    ]
    if research.evidence:
        lines.append("- **市场证据**：")
        for ev in research.evidence[:5]:
            lines.append(f"  - {ev}")
    for c in research.catalysts:
        lines.append(f"  - {c}")
    lines.append("- **风险**：")
    for r in research.risks:
        lines.append(f"  - {r}")
    lines += [
        f"- **失效条件**：{research.invalidation}",
        f"- **时间周期**：{research.time_horizon}",
        f"- **首选市场**：{research.preferred_market}",
        "",
        "## 六、执行计划 (ExecutionPlan)\n",
        f"- **动作**：{plan.action}",
        f"- **仓位大小**：{plan.size_pct:.1f}% 账户",
        f"- **杠杆**：{plan.leverage}x",
        f"- **入场思路**：{plan.entry_idea}",
        f"- **止损**：{plan.stop_loss_pct:.1f}%",
        f"- **止盈**：{plan.take_profit_pct:.1f}%",
        f"- **理由**：{plan.rationale}",
        "",
        "## 七、风险审批 (RiskDecision)\n",
        f"- **审批结果**：{'✅ 通过' if risk.approved else '❌ 拒绝'}",
    ]

    if risk.violated_rules:
        lines.append("- **违规规则**：")
        for vr in risk.violated_rules:
            lines.append(f"  - {vr}")
    if risk.warnings:
        lines.append("- **警告**：")
        for w in risk.warnings:
            lines.append(f"  - {w}")

    adj_lev = f"{risk.adjusted_leverage}x" if risk.adjusted_leverage else "N/A"
    lines += [
        f"- **调整后动作**：{risk.adjusted_action}",
        f"- **调整后仓位**：{risk.adjusted_size_pct:.1f}%",
        f"- **调整后杠杆**：{adj_lev}",
        f"- **理由**：{risk.rationale}",
        "",
        "## 八、最终执行结果 (ExecutionResult)\n",
        f"- **状态**：`{result.status}`",
        f"- **是否执行**：{'是' if result.executed else '否'}",
        f"- **最终动作**：{result.final_action}",
        f"- **最终仓位**：{result.final_size_pct:.1f}%",
        f"- **交易所**：{result.exchange or '—'}",
        f"- **消息**：{result.message}",
        f"- **执行原因**：{result.execution_reason or '—'}",
        f"- **止损单**：{result.sl_status}" + (f" (#{result.sl_order_id})" if result.sl_order_id else ""),
        f"- **止盈单**：{result.tp_status}" + (f" (#{result.tp_order_id})" if result.tp_order_id else ""),
    ]

    if result.order_ids:
        lines.append(f"- **订单 ID**：{', '.join(result.order_ids)}")
    if result.manual_order_details:
        lines += ["", "## 九、手动下单明细\n"]
        for k, v in result.manual_order_details.items():
            lines.append(f"- **{k}**：{v}")

    if reflections:
        lines += ["", "## 十、历史反思摘要\n"]
        for ref in reflections[:3]:
            lines.append(f"### {ref.get('symbol', '—')} — {ref.get('created_at', '')}")
            lessons = ref.get("lessons") or []
            for lesson in (lessons if isinstance(lessons, list) else [])[:3]:
                lines.append(f"  - {lesson}")
            if ref.get("reflection_text"):
                lines.append(f"\n{ref['reflection_text'][:500]}")

    lines += [
        "",
        "## 十一、最终结论\n",
        f"本次 **{symbol}** 分析完成。",
        f"- AI 研判方向：**{research.stance.upper()}**，信心 {research.confidence:.1%}",
        f"- 建议动作：**{plan.action}**，仓位 {plan.size_pct:.1f}%，杠杆 {plan.leverage}x",
        f"- 风控结论：{'通过' if risk.approved else '拒绝'}",
        f"- 实际执行：**{result.status}** — {result.message}",
    ]

    return "\n".join(lines)
