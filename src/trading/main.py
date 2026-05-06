"""Trading Bot CLI entry point."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .core import AppConfig, TradeJournal, TradingLogger, VaultReader, load_config
from .exchange import AccountManager, BinanceClient, PositionManager
from .pipeline import TradePipeline, is_trading_api_configured

app = typer.Typer(
    name="trading",
    help="AI-powered cryptocurrency trading assistant",
    invoke_without_command=False,
)

console = Console()


def load_app_config(config_path: str = "config.yaml", dry_run: Optional[bool] = None) -> AppConfig:
    """Load AppConfig from file; print error and exit on failure."""
    try:
        return load_config(config_path=config_path, dry_run=dry_run)
    except Exception as e:
        console.print(f"[red]Failed to load config: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def account(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview mode - use mock data"),
    config_path: str = typer.Option("config.yaml", "--config", help="Path to config.yaml"),
) -> None:
    """Display account balance, futures positions, and P&L."""
    try:
        config = load_app_config(config_path=config_path, dry_run=dry_run)
        logger = TradingLogger("trading.account", config.logging)
        binance = BinanceClient(config.binance, dry_run=dry_run)
        account_mgr = AccountManager(binance)
        positions_mgr = PositionManager(binance)

        summary = account_mgr.get_account_summary()

        table = Table(title="Account Summary", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        if "error" in summary:
            console.print(f"[red]Error: {summary['error']}[/red]")
            raise typer.Exit(1)

        table.add_row(
            "Futures Balance",
            f"${summary['futures_balance_usdt']:,.2f} USDT" if summary.get("futures_balance_usdt") else "N/A",
        )
        table.add_row(
            "Spot Balance",
            f"${summary['spot_balance_usdt']:,.2f} USDT" if summary.get("spot_balance_usdt") else "N/A",
        )
        table.add_row("Total Balance", f"${summary['total_balance_usdt']:,.2f} USDT")
        table.add_row("Unrealized P&L", f"${summary['unrealized_pnl']:,.2f}")
        table.add_row(
            "P&L %",
            f"[green]{summary['pnl_pct']:.2f}%[/green]"
            if summary["pnl_pct"] >= 0
            else f"[red]{summary['pnl_pct']:.2f}%[/red]",
        )
        table.add_row("Drawdown %", f"[red]{summary['drawdown_pct']:.2f}%[/red]")

        if summary.get("dry_run"):
            table.add_row("Mode", "[yellow]DRY RUN[/yellow]")

        console.print(table)

        positions_summary = positions_mgr.get_positions_summary()
        futures_pos = positions_mgr.get_futures_positions()

        if futures_pos:
            pos_table = Table(title="Futures Positions", show_header=True, header_style="bold magenta")
            pos_table.add_column("Symbol", style="cyan")
            pos_table.add_column("Amount", justify="right")
            pos_table.add_column("Entry Price", justify="right")
            pos_table.add_column("Leverage", justify="right")
            pos_table.add_column("Unrealized P&L", justify="right")
            pos_table.add_column("P&L %", justify="right")

            for pos in futures_pos:
                color = "green" if pos.unrealized_pnl >= 0 else "red"
                pos_table.add_row(
                    pos.symbol,
                    f"{pos.amount:.0f}",
                    f"${pos.price:.6f}",
                    f"{pos.leverage:.1f}x",
                    f"[{color}]${pos.unrealized_pnl:,.2f}[/{color}]",
                    f"[{color}]{pos.unrealized_pnl_pct:+.2f}%[/{color}]",
                )

            console.print(pos_table)
        else:
            console.print("[yellow]No open futures positions[/yellow]")

        console.print(Panel(
            f"Total Notional: ${positions_summary['total_notional_usdt']:,.2f} | "
            f"Futures: {positions_summary['futures_count']} | "
            f"Long: {positions_summary['futures_long_count']} | "
            f"Short: {positions_summary['futures_short_count']}"
        ))

        logger.info("Account query completed", extra=summary)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def sync(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Preview mode (default) or actually write to Vault",
    ),
    config_path: str = typer.Option("config.yaml", "--config", help="Path to config.yaml"),
) -> None:
    """Sync Binance positions to Obsidian Vault (dry-run by default)."""
    try:
        config = load_app_config(config_path=config_path, dry_run=dry_run)
        logger = TradingLogger("trading.sync", config.logging)
        binance = BinanceClient(config.binance, dry_run=dry_run)
        positions_mgr = PositionManager(binance)
        vault = VaultReader(config.vault)

        if dry_run:
            console.print("[cyan]Loading mock positions for preview...[/cyan]")
        else:
            console.print("[cyan]Fetching live positions from Binance...[/cyan]")
        futures_pos = positions_mgr.get_futures_positions()
        spot_holdings = positions_mgr.get_spot_holdings()

        vault_content = "# 仓位追踪\n\n## 合约持仓\n\n"

        if futures_pos:
            vault_content += "| 交易对 | 数量 | 开仓价 | 杠杆 | 未实现PnL | PnL% |\n"
            vault_content += "|--------|------|--------|------|----------|------|\n"
            for pos in futures_pos:
                vault_content += (
                    f"| {pos.symbol} | {pos.amount:.0f} | ${pos.price:.6f} | "
                    f"{pos.leverage:.1f}x | ${pos.unrealized_pnl:,.2f} | {pos.unrealized_pnl_pct:+.2f}% |\n"
                )
        else:
            vault_content += "*无活跃合约持仓*\n"

        vault_content += "\n## 现货持仓\n\n"

        if spot_holdings:
            vault_content += "| 资产 | 数量 |\n|------|------|\n"
            for holding in spot_holdings:
                vault_content += f"| {holding.asset} | {holding.amount:.8f} |\n"
        else:
            vault_content += "*无现货持仓*\n"

        vault_content += f"\n\n---\n*同步于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"
        if dry_run:
            vault_content += "*[预览模式 - 未写入]*\n"

        console.print(Panel(vault_content, title="📝 Vault Content Preview"))

        if not dry_run:
            try:
                file_path = vault.update_position_tracking(vault_content)
                console.print(f"[green]✓ Successfully updated: {file_path}[/green]")
                logger.info(f"Position tracking updated: {file_path}")
            except Exception as e:
                console.print(f"[red]Failed to write to Vault: {e}[/red]")
                logger.error(f"Vault write failed: {e}")
                raise typer.Exit(1)
        else:
            console.print("[yellow]ℹ️  Dry-run mode: no changes made to Vault[/yellow]")
            console.print("[cyan]Use --no-dry-run to actually write to Vault[/cyan]")

        logger.info(
            "Sync completed",
            extra={"futures_count": len(futures_pos), "spot_count": len(spot_holdings), "dry_run": dry_run},
        )

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def status(
    config_path: str = typer.Option("config.yaml", "--config", help="Path to config.yaml"),
) -> None:
    """Check system component status."""
    try:
        config = load_app_config(config_path=config_path)
        vault = VaultReader(config.vault)
        vault_stats = vault.get_vault_stats()

        table = Table(title="🔍 System Status", show_header=True, header_style="bold cyan")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="green")

        table.add_row("Vault Directory", "OK" if vault_stats["trading_dir_exists"] else "Missing")
        table.add_row("System Guide", "OK" if vault_stats["system_guide_exists"] else "Missing")
        table.add_row("Reports", f"{vault_stats['reports_count']} files")
        table.add_row("Position Tracking", f"{vault_stats['position_tracking_count']} files")

        try:
            binance = BinanceClient(config.binance, dry_run=False)
            binance.get_api_status()
            binance_status = "OK"
        except ValueError:
            binance_status = "Not configured"
        except Exception as e:
            binance_status = f"Error: {str(e)[:40]}"

        table.add_row("Binance API", binance_status)
        table.add_row(
            "Claude API",
            "Configured" if config.claude.api_key else "Not configured (required for AI trade analysis)",
        )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def record(
    symbol: str = typer.Argument(..., help="交易对，如 CHZUSDT"),
    side: str = typer.Option(..., "--side", help="方向: long 或 short"),
    entry: float = typer.Option(..., "--entry", help="开仓价"),
    exit_price: float = typer.Option(..., "--exit", help="平仓价"),
    qty: float = typer.Option(..., "--qty", help="持仓数量"),
    leverage: float = typer.Option(1.0, "--leverage", help="杠杆倍数"),
    open_time: str = typer.Option(..., "--open", help="开仓时间，格式 'YYYY-MM-DD HH:MM'"),
    close_time: Optional[str] = typer.Option(None, "--close", help="平仓时间，默认当前时间"),
    pnl: Optional[float] = typer.Option(None, "--pnl", help="实际已实现P&L（覆盖自动计算，含手续费）"),
    notes: str = typer.Option("", "--notes", help="经验总结备注"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Record a closed trade and its P&L."""
    try:
        config = load_app_config(config_path=config_path)
        journal = TradeJournal(config.logging.sqlite_db)

        def _parse_dt(s: str) -> datetime:
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            raise ValueError(f"无法解析时间格式: {s}，请用 'YYYY-MM-DD HH:MM'")

        open_dt = _parse_dt(open_time)
        close_dt = _parse_dt(close_time) if close_time else None

        if side.upper() not in ("LONG", "SHORT"):
            console.print("[red]--side 必须为 long 或 short[/red]")
            raise typer.Exit(1)

        trade = journal.record_trade(
            symbol=symbol, direction=side, entry_price=entry, exit_price=exit_price,
            quantity=qty, leverage=leverage, open_time=open_dt, close_time=close_dt,
            realized_pnl=pnl, notes=notes,
        )

        color = "green" if trade.is_win else "red"
        console.print(
            f"[bold]已记录交易 #{trade.id}[/bold]  "
            f"{trade.symbol} {trade.direction}  "
            f"P&L: [{color}]{trade.realized_pnl:+,.2f} USDT ({trade.pnl_pct:+.1f}%)[/{color}]"
        )

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def journal(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="按交易对筛选"),
    limit: int = typer.Option(20, "--limit", help="显示最近 N 条记录"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Show trade history and P&L summary."""
    try:
        config = load_app_config(config_path=config_path)
        jnl = TradeJournal(config.logging.sqlite_db)
        summary = jnl.get_summary(symbol=symbol)

        if summary["total_trades"] == 0:
            console.print("[yellow]暂无交易记录。使用 'trading record' 录入第一笔。[/yellow]")
            return

        # Summary panel
        s = summary
        win_color = "green" if s["win_rate"] >= 50 else "red"
        pnl_color = "green" if s["total_pnl"] >= 0 else "red"
        best = s["best_trade"]
        worst = s["worst_trade"]
        hold_h = s["avg_hold_minutes"] / 60

        lines = [
            f"总交易次数: [bold]{s['total_trades']}[/bold]   "
            f"胜率: [{win_color}]{s['win_rate']:.1f}%[/{win_color}] "
            f"({s['wins']}胜 / {s['losses']}负)",
            f"累计P&L: [{pnl_color}]{s['total_pnl']:+,.2f} USDT[/{pnl_color}]   "
            f"平均P&L: [{pnl_color}]{s['avg_pnl']:+,.2f}[/{pnl_color}] "
            f"({s['avg_pnl_pct']:+.1f}%)",
            f"最佳: [green]{best.symbol} {best.realized_pnl:+,.2f}[/green]   "
            f"最差: [red]{worst.symbol} {worst.realized_pnl:+,.2f}[/red]",
            f"平均持仓: {hold_h:.1f} 小时   "
            f"涉及币种: {', '.join(s['symbols_traded'])}",
        ]
        console.print(Panel("\n".join(lines), title="交易统计", border_style="cyan"))

        # Trade table
        console.print(jnl.get_rich_table(symbol=symbol, limit=limit))

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def trade(
    symbol: str = typer.Argument(..., help="Trading symbol, e.g. BTCUSDT"),
    market: str = typer.Option("auto", "--market", help="Market type: spot | futures | auto"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Use mock account snapshot for analysis preview"),
    write_vault: bool = typer.Option(True, "--write-vault/--no-write-vault", help="Write Markdown report to Vault"),
    show_context: bool = typer.Option(False, "--show-context", help="Print Vault context before analysis"),
    allow_partial: bool = typer.Option(
        False,
        "--allow-partial",
        help="If risk gate adjusts size/leverage, generate the manual order ticket using adjusted values instead of blocking",
    ),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Run the full AI trading pipeline: research → plan → risk → manual order ticket."""
    try:
        config = load_app_config(config_path=config_path, dry_run=dry_run)

        # ── API configuration check (display early, pipeline also checks)
        api_ok = is_trading_api_configured(config)
        if not dry_run and not api_ok:
            console.print(
                "[yellow]⚠ 未配置交易api，无法执行交易。"
                "分析将正常进行，执行阶段会显示明确提示。[/yellow]"
            )

        if show_context:
            try:
                vault = VaultReader(config.vault)
                ctx = vault.build_knowledge_context([symbol.upper()])
                console.print(Panel(ctx[:3000], title="Vault Context Preview"))
            except Exception as exc:
                console.print(f"[yellow]Vault context unavailable: {exc}[/yellow]")

        mode_label = "[yellow][DRY RUN][/yellow]" if dry_run else "[cyan][MANUAL REVIEW][/cyan]"
        console.print(
            Panel(
                f"Symbol: [bold cyan]{symbol.upper()}[/bold cyan]  "
                f"Market: {market}  Mode: {mode_label}  "
                f"API: {'✓' if api_ok else '✗ not configured'}",
                title="Trading Pipeline",
            )
        )

        pipeline = TradePipeline(config=config, dry_run=dry_run)
        research, plan, risk, result = pipeline.run(
            symbol=symbol,
            market=market,
            write_vault=write_vault,
            allow_partial=allow_partial,
        )
        market_snapshot = pipeline.last_market_snapshot
        portfolio_snapshot = pipeline.last_portfolio_snapshot
        portfolio_budget = pipeline.last_portfolio_budget

        # ── Rich output ──────────────────────────────────────────────

        # Market Snapshot
        market_table = Table(title="Market Snapshot", show_header=True, header_style="bold blue")
        market_table.add_column("Field", style="cyan", min_width=18)
        market_table.add_column("Value")
        for label, key in [
            ("Spot Price", "spot_price"),
            ("Futures Mark", "futures_mark_price"),
            ("24h Move", "price_change_24h_pct"),
            ("7d Move", "price_change_7d_pct"),
            ("24h Volume", "quote_volume_24h_usdt"),
            ("Funding", "funding_rate"),
            ("Open Interest", "open_interest"),
            ("OI/Vol Ratio", "oi_to_volume_ratio"),
            ("Basis (bps)", "basis_bps"),
            ("EMA21 1h", "ema_21_1h"),
            ("EMA55 1h", "ema_55_1h"),
            ("EMA144 1h", "ema_144_1h"),
            ("Dist to EMA21", "distance_to_ema21_pct"),
            ("Dist to EMA55", "distance_to_ema55_pct"),
            ("Dist to 7d High", "distance_to_7d_high_pct"),
            ("Dist to 7d Low", "distance_to_7d_low_pct"),
            ("Hourly Trend", "hourly_trend_bias"),
            ("Asset Tier", "asset_tier"),
            ("Narrative", "narrative_tag"),
            ("Liquidity", "liquidity_regime"),
            ("Crowding", "crowding_regime"),
            ("24h Realized Vol", "realized_vol_24h_pct"),
            ("7d Realized Vol", "realized_vol_7d_pct"),
            ("Vol Regime", "volatility_regime"),
            ("Momentum", "momentum_regime"),
            ("BTC Regime", "btc_market_regime"),
            ("RS vs BTC 24h", "relative_strength_24h_pct"),
            ("RS vs BTC 7d", "relative_strength_7d_pct"),
            ("Exec Template", "execution_template"),
        ]:
            if key in market_snapshot:
                value = market_snapshot[key]
                suffix = "%" if "Move" in label or "Vol" in label else ""
                market_table.add_row(label, f"{value}{suffix}" if isinstance(value, (int, float)) else str(value))
        console.print(market_table)

        portfolio_table = Table(title="Portfolio Overlay", show_header=True, header_style="bold white")
        portfolio_table.add_column("Field", style="cyan", min_width=18)
        portfolio_table.add_column("Value")
        for label, value in [
            ("Total Balance", f"${portfolio_snapshot.get('total_balance_usdt', 0):,.2f}"),
            ("Gross Exposure", f"{portfolio_snapshot.get('gross_exposure_pct', 0):.1f}%"),
            ("Net Exposure", f"{portfolio_snapshot.get('net_exposure_pct', 0):.1f}%"),
            ("Position Count", str(portfolio_snapshot.get("position_count", 0))),
            ("Portfolio Role", str(portfolio_budget.get("portfolio_role", "N/A"))),
            ("Narrative", str(portfolio_budget.get("narrative_tag", "N/A"))),
            ("Recommended Max Size", f"{portfolio_budget.get('recommended_max_size_pct', 'N/A')}%"),
            ("Tier Hard Cap", f"{portfolio_budget.get('hard_cap_size_pct', 'N/A')}%"),
        ]:
            portfolio_table.add_row(label, value)
        if portfolio_budget.get("warnings"):
            portfolio_table.add_row("Budget Warnings", "\n".join(f"• {w}" for w in portfolio_budget["warnings"]))
        portfolio_table.add_row("Rationale", str(portfolio_budget.get("rationale", ""))[:220])
        console.print(portfolio_table)

        # ResearchDecision
        r_color = {"long": "green", "short": "red", "neutral": "yellow"}.get(research.stance, "white")
        research_table = Table(title="ResearchDecision", show_header=True, header_style="bold cyan")
        research_table.add_column("Field", style="cyan", min_width=18)
        research_table.add_column("Value")
        research_table.add_row("Symbol", research.symbol)
        research_table.add_row("Stance", f"[{r_color}]{research.stance.upper()}[/{r_color}]")
        research_table.add_row("Confidence", f"{research.confidence:.1%}")
        research_table.add_row("Thesis", research.thesis[:120])
        research_table.add_row("Market Structure", research.market_structure[:120])
        if research.evidence:
            research_table.add_row("Evidence", "\n".join(f"• {e}" for e in research.evidence[:5]))
        research_table.add_row("Catalysts", "\n".join(f"• {c}" for c in research.catalysts[:5]))
        research_table.add_row("Risks", "\n".join(f"• {r}" for r in research.risks[:5]))
        research_table.add_row("Invalidation", research.invalidation[:100])
        research_table.add_row("Time Horizon", research.time_horizon[:80])
        research_table.add_row("Market", research.preferred_market)
        console.print(research_table)

        # ExecutionPlan
        plan_table = Table(title="ExecutionPlan", show_header=True, header_style="bold magenta")
        plan_table.add_column("Field", style="cyan", min_width=18)
        plan_table.add_column("Value")
        plan_table.add_row("Action", f"[bold]{plan.action}[/bold]")
        plan_table.add_row("Size", f"{plan.size_pct:.1f}% of account")
        plan_table.add_row("Leverage", f"{plan.leverage}x")
        plan_table.add_row("Entry Idea", plan.entry_idea[:120])
        if plan.entry_style:
            plan_table.add_row("Entry Style", plan.entry_style)
        if plan.entry_zone_low is not None or plan.entry_zone_high is not None:
            plan_table.add_row("Entry Zone", f"{plan.entry_zone_low} - {plan.entry_zone_high}")
        if plan.trigger_price is not None:
            plan_table.add_row("Trigger Price", str(plan.trigger_price))
        if plan.invalidation_price is not None:
            plan_table.add_row("Invalidation Price", str(plan.invalidation_price))
        plan_table.add_row("Stop Loss", f"{plan.stop_loss_pct:.1f}%")
        plan_table.add_row("Take Profit", f"{plan.take_profit_pct:.1f}%")
        if plan.thesis_window_hours is not None:
            plan_table.add_row("Thesis Window", f"{plan.thesis_window_hours}h")
        plan_table.add_row("Rationale", plan.rationale[:120])
        console.print(plan_table)

        # RiskDecision
        risk_color = "green" if risk.approved else "red"
        risk_label = "✅ APPROVED" if risk.approved else "❌ REJECTED"
        risk_table = Table(title="RiskDecision", show_header=True, header_style="bold yellow")
        risk_table.add_column("Field", style="cyan", min_width=18)
        risk_table.add_column("Value")
        risk_table.add_row("Decision", f"[{risk_color}]{risk_label}[/{risk_color}]")
        if risk.violated_rules:
            risk_table.add_row(
                "Violated Rules", "\n".join(f"• {v}" for v in risk.violated_rules)
            )
        if risk.warnings:
            risk_table.add_row("Warnings", "\n".join(f"• {w}" for w in risk.warnings))
        risk_table.add_row("Adj. Action", str(risk.adjusted_action))
        risk_table.add_row("Adj. Size", f"{risk.adjusted_size_pct:.1f}%")
        risk_table.add_row("Adj. Leverage", f"{risk.adjusted_leverage}x" if risk.adjusted_leverage else "N/A")
        risk_table.add_row("Rationale", risk.rationale[:160])
        console.print(risk_table)

        # ExecutionResult
        status_colors = {
            "executed": "green",
            "dry_run": "yellow",
            "manual_review_required": "cyan",
            "blocked_by_risk": "red",
            "not_executed_missing_api": "red",
            "execution_failed": "red",
            "hold": "yellow",
        }
        sc = status_colors.get(result.status, "white")
        result_table = Table(title="ExecutionResult", show_header=True, header_style="bold green")
        result_table.add_column("Field", style="cyan", min_width=18)
        result_table.add_column("Value")
        result_table.add_row("Status", f"[{sc}]{result.status}[/{sc}]")
        result_table.add_row("Executed", "Yes" if result.executed else "No")
        result_table.add_row("Final Action", result.final_action)
        result_table.add_row("Final Size", f"{result.final_size_pct:.1f}%")
        if result.final_leverage:
            result_table.add_row("Final Leverage", f"{result.final_leverage}x")
        result_table.add_row("Exchange", result.exchange or "—")
        result_table.add_row("Message", result.message)
        if result.order_ids:
            result_table.add_row("Order IDs", ", ".join(result.order_ids))
        if result.execution_reason:
            result_table.add_row("Why", result.execution_reason[:240])
        console.print(result_table)

        if result.manual_order_details:
            manual_table = Table(title="Manual Order Ticket", show_header=True, header_style="bold blue")
            manual_table.add_column("Field", style="cyan", min_width=18)
            manual_table.add_column("Value")
            for key, value in result.manual_order_details.items():
                manual_table.add_row(key, str(value))
            console.print(manual_table)

        # Final banner
        if result.status == "not_executed_missing_api":
            console.print(
                Panel(
                    "[bold red]未配置交易api[/bold red]\n"
                    "请在 .env 设置 BINANCE_API_KEY / BINANCE_API_SECRET",
                    border_style="red",
                )
            )
        elif result.status == "dry_run":
            console.print(
                Panel("[yellow]分析预览完成 — 已输出手动下单草案，未自动下单[/yellow]", border_style="yellow")
            )
        elif result.status == "manual_review_required":
            console.print(
                Panel(
                    "[cyan]已生成手动下单明细，请你人工确认后自行下单[/cyan]",
                    border_style="cyan",
                )
            )
        elif result.status == "blocked_by_risk":
            console.print(
                Panel(
                    f"[red]✗ 风控拒绝 — {risk.rationale[:200]}[/red]",
                    border_style="red",
                )
            )
        elif result.status == "hold":
            console.print(
                Panel("[yellow]AI 最终建议 HOLD，本次无需下单[/yellow]", border_style="yellow")
            )

    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command()
def reflect(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="Symbol to reflect on (all if omitted)"),
    limit: int = typer.Option(10, "--limit", help="Max past trades to analyse"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Generate AI reflection from past closed trades; saved for future pipeline runs."""
    try:
        config = load_app_config(config_path=config_path)

        if not config.claude.api_key:
            console.print("[red]Anthropic API key not configured (ANTHROPIC_API_KEY).[/red]")
            raise typer.Exit(1)

        from .ai.advisor import TradingAdvisor
        from .ai.client import ClaudeClient
        from .pipeline.persistence import TradeRunDB

        journal = TradeJournal(config.logging.sqlite_db)
        db = TradeRunDB(config.logging.sqlite_db)
        claude = ClaudeClient(config.claude)
        advisor = TradingAdvisor(claude, config)

        trades = journal.get_trades(symbol=symbol, limit=limit)
        if not trades:
            target = symbol.upper() if symbol else "all symbols"
            console.print(f"[yellow]No closed trades found for {target}. Record some trades first.[/yellow]")
            return

        trade_sym = symbol.upper() if symbol else (trades[0].symbol if trades else "UNKNOWN")
        trade_dicts = [
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

        console.print(f"[cyan]Generating reflection from {len(trades)} trade(s) for {trade_sym}…[/cyan]")
        reflection = advisor.generate_reflection(symbol=trade_sym, trades=trade_dicts)

        trade_ids = [t.id for t in trades if t.id is not None]
        ref_id = db.save_reflection(
            symbol=trade_sym,
            model=config.claude.model,
            trades_analyzed=len(trades),
            reflection_data=reflection,
            source_trade_ids=trade_ids,
        )

        # Display
        ref_table = Table(title=f"Reflection — {trade_sym}", show_header=True, header_style="bold cyan")
        ref_table.add_column("Field", style="cyan", min_width=22)
        ref_table.add_column("Value")
        ref_table.add_row("Trades Analysed", str(len(trades)))
        ref_table.add_row("Direction Accuracy", reflection.get("direction_accuracy", "—")[:160])
        ref_table.add_row("Thesis Evaluation", reflection.get("thesis_evaluation", "—")[:160])
        lessons = reflection.get("lessons", [])
        if lessons:
            ref_table.add_row("Lessons", "\n".join(f"• {l}" for l in lessons[:5]))
        ref_table.add_row("Saved As ID", str(ref_id))
        console.print(ref_table)

        if reflection.get("reflection_text"):
            console.print(Panel(reflection["reflection_text"][:1200], title="Full Reflection"))

        console.print(
            f"[green]✓ Reflection saved (id={ref_id}). "
            "It will be injected into the next [bold]trading trade[/bold] run.[/green]"
        )

    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.callback()
def main() -> None:
    """Trading Bot - AI-powered cryptocurrency trading assistant."""
    pass


if __name__ == "__main__":
    app()
