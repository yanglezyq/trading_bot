"""Trading Bot CLI entry point."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .core import AppConfig, ConfigPatchManager, TradeJournal, TradingLogger, VaultReader, load_config
from .exchange import AccountManager, BinanceClient, PositionManager
from .notification import FeishuWebhook
from .pipeline import BacktestRunner, TradePipeline, Rebalancer, is_trading_api_configured
from .risk import EdgePolicyAdvisor, RiskDaemon, RiskTuningAdvisor, WatchTarget

app = typer.Typer(
    name="trading",
    help="AI-powered cryptocurrency trading assistant",
    invoke_without_command=False,
)

console = Console()


def _batch_edge_sort_key(item: dict) -> tuple:
    """Sort batch results by edge policy, expectancy, confidence, and quality."""
    edge_label = str(item.get("edge_policy_label", ""))
    edge_rank = {
        "allowlist_promoted": 0,
        "promoted": 1,
        "neutral": 2,
        "insufficient_data": 3,
        "blocked": 4,
        "denylist_blocked": 5,
    }.get(edge_label, 6)
    opportunity_score = float(item.get("opportunity_score", 0.0) or 0.0)
    expectancy = float(item.get("edge_policy_expectancy_pnl_pct", 0.0) or 0.0)
    confidence = float(item.get("confidence", 0.0) or 0.0)
    quality = float(item.get("setup_quality_score", 0.0) or 0.0)
    return (edge_rank, -opportunity_score, -expectancy, -confidence, -quality, str(item.get("symbol", "")))


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
        if config.trading.auto_execute_kill_switch:
            auto_exec_status = "Blocked by kill switch"
        elif config.trading.auto_execute_enabled and not config.trading.auto_execute_require_cli_flag:
            auto_exec_status = (
                "Live enabled"
                if config.trading.auto_execute_live_enabled or config.is_testnet
                else "Config enabled, but live disabled"
            )
        else:
            auto_exec_status = (
                "CLI flag required"
                if config.trading.auto_execute_live_enabled or config.is_testnet
                else "Manual / guarded only"
            )
        table.add_row("Auto Execute", auto_exec_status)

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
    run_id: Optional[int] = typer.Option(None, "--run-id", help="显式关联某次 pipeline run id"),
    auto_link_run: bool = typer.Option(
        True,
        "--auto-link-run/--no-auto-link-run",
        help="若未指定 --run-id，则自动关联最近一次同 symbol 的 trade pipeline 记录",
    ),
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
        linked_run_id = run_id

        if side.upper() not in ("LONG", "SHORT"):
            console.print("[red]--side 必须为 long 或 short[/red]")
            raise typer.Exit(1)

        if linked_run_id is None and auto_link_run:
            from .pipeline.persistence import TradeRunDB

            run_db = TradeRunDB(config.logging.sqlite_db)
            candidate = run_db.find_run_link_candidate(symbol=symbol, reference_time=open_dt)
            if candidate:
                linked_run_id = int(candidate["id"])

        trade = journal.record_trade(
            symbol=symbol, direction=side, entry_price=entry, exit_price=exit_price,
            quantity=qty, leverage=leverage, open_time=open_dt, close_time=close_dt,
            realized_pnl=pnl, notes=notes, linked_run_id=linked_run_id,
        )

        color = "green" if trade.is_win else "red"
        console.print(
            f"[bold]已记录交易 #{trade.id}[/bold]  "
            f"{trade.symbol} {trade.direction}  "
            f"P&L: [{color}]{trade.realized_pnl:+,.2f} USDT ({trade.pnl_pct:+.1f}%)[/{color}]"
        )
        if trade.linked_run_id:
            console.print(f"[dim]已关联 pipeline run #{trade.linked_run_id}[/dim]")

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


@app.command("risk-stats")
def risk_stats(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="按交易对筛选"),
    limit: int = typer.Option(200, "--limit", help="统计最近 N 条 pipeline 记录"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Summarise which deterministic risk rules most often warn/block runs."""
    try:
        config = load_app_config(config_path=config_path)
        from .pipeline.persistence import TradeRunDB

        db = TradeRunDB(config.logging.sqlite_db)
        stats = db.get_risk_rule_stats(symbol=symbol, limit=limit)

        summary = Table(title="Risk Rule Stats", show_header=True, header_style="bold cyan")
        summary.add_column("Metric", style="cyan")
        summary.add_column("Value")
        for key in (
            "runs_scanned",
            "approved_runs",
            "blocked_runs",
            "warning_runs",
            "linked_runs",
            "linked_trades",
        ):
            summary.add_row(key, str(stats.get(key, 0)))
        linked_win_rate = stats.get("linked_win_rate")
        if linked_win_rate is not None:
            summary.add_row("linked_win_rate", f"{linked_win_rate:.1f}%")
        linked_total_pnl = stats.get("linked_total_realized_pnl")
        if linked_total_pnl is not None:
            summary.add_row("linked_total_realized_pnl", f"{linked_total_pnl:+,.2f} USDT")
        console.print(summary)

        if stats.get("top_warnings"):
            warn_table = Table(title="Top Warning Rules", show_header=True, header_style="bold yellow")
            warn_table.add_column("Rule")
            warn_table.add_column("Count", justify="right")
            for rule, count in stats["top_warnings"]:
                warn_table.add_row(rule, str(count))
            console.print(warn_table)

        if stats.get("top_violations"):
            block_table = Table(title="Top Blocking Rules", show_header=True, header_style="bold red")
            block_table.add_column("Rule")
            block_table.add_column("Count", justify="right")
            for rule, count in stats["top_violations"]:
                block_table.add_row(rule, str(count))
            console.print(block_table)

        if stats.get("warning_effectiveness"):
            eff_table = Table(
                title="Warning Rule Outcome Context",
                show_header=True,
                header_style="bold green",
            )
            eff_table.add_column("Rule")
            eff_table.add_column("Count", justify="right")
            eff_table.add_column("Linked", justify="right")
            eff_table.add_column("Win Rate", justify="right")
            eff_table.add_column("Avg P&L", justify="right")
            for row in stats["warning_effectiveness"][:10]:
                win_rate = "—" if row["win_rate"] is None else f"{row['win_rate']:.1f}%"
                avg_pnl = "—" if row["linked_trades"] == 0 else f"{row['avg_realized_pnl']:+,.2f}"
                eff_table.add_row(
                    row["rule"],
                    str(row["count"]),
                    str(row["linked_trades"]),
                    win_rate,
                    avg_pnl,
                )
            console.print(eff_table)

        if stats.get("violation_effectiveness"):
            eff_table = Table(
                title="Blocking Rule Outcome Context",
                show_header=True,
                header_style="bold magenta",
            )
            eff_table.add_column("Rule")
            eff_table.add_column("Count", justify="right")
            eff_table.add_column("Linked", justify="right")
            eff_table.add_column("Win Rate", justify="right")
            eff_table.add_column("Avg P&L", justify="right")
            for row in stats["violation_effectiveness"][:10]:
                win_rate = "—" if row["win_rate"] is None else f"{row['win_rate']:.1f}%"
                avg_pnl = "—" if row["linked_trades"] == 0 else f"{row['avg_realized_pnl']:+,.2f}"
                eff_table.add_row(
                    row["rule"],
                    str(row["count"]),
                    str(row["linked_trades"]),
                    win_rate,
                    avg_pnl,
                )
            console.print(eff_table)
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command("replay-stats")
def replay_stats(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="按交易对筛选"),
    limit: int = typer.Option(200, "--limit", help="统计最近 N 条已关联结果样本"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Replay linked trade outcomes and summarise what worked by regime/template/narrative."""
    try:
        config = load_app_config(config_path=config_path)
        from .pipeline.persistence import TradeRunDB

        db = TradeRunDB(config.logging.sqlite_db)
        stats = db.get_replay_stats(symbol=symbol, limit=limit)

        if stats.get("samples_scanned", 0) == 0:
            console.print(
                "[yellow]暂无可回放样本。先用 trading trade 生成 run，并用 trading record 关联已平仓结果。[/yellow]"
            )
            return

        summary = Table(title="Replay Stats", show_header=True, header_style="bold cyan")
        summary.add_column("Metric", style="cyan")
        summary.add_column("Value")
        summary.add_row("samples_scanned", str(stats.get("samples_scanned", 0)))
        summary.add_row("unique_symbols", str(stats.get("unique_symbols", 0)))
        if stats.get("win_rate") is not None:
            summary.add_row("win_rate", f"{stats['win_rate']:.1f}%")
        summary.add_row("total_realized_pnl", f"{stats.get('total_realized_pnl', 0.0):+,.2f} USDT")
        if stats.get("avg_realized_pnl") is not None:
            summary.add_row("avg_realized_pnl", f"{stats['avg_realized_pnl']:+,.2f} USDT")
        if stats.get("avg_pnl_pct") is not None:
            summary.add_row("avg_pnl_pct", f"{stats['avg_pnl_pct']:+.2f}%")
        if stats.get("avg_confidence") is not None:
            summary.add_row("avg_confidence", f"{stats['avg_confidence']:.1%}")
        console.print(summary)

        def _render_group_table(title: str, rows: list[dict]) -> None:
            if not rows:
                return
            table = Table(title=title, show_header=True, header_style="bold green")
            table.add_column("Group", style="cyan")
            table.add_column("Samples", justify="right")
            table.add_column("Win Rate", justify="right")
            table.add_column("Avg P&L", justify="right")
            table.add_column("Avg P&L%", justify="right")
            for row in rows[:10]:
                win_rate = "—" if row["win_rate"] is None else f"{row['win_rate']:.1f}%"
                table.add_row(
                    row["group"],
                    str(row["samples"]),
                    win_rate,
                    f"{row['avg_realized_pnl']:+,.2f}",
                    f"{row['avg_pnl_pct']:+.2f}%",
                )
            console.print(table)

        _render_group_table("Replay By BTC Regime", stats.get("by_regime", []))
        _render_group_table("Replay By Execution Template", stats.get("by_template", []))
        _render_group_table("Replay By Narrative", stats.get("by_narrative", []))
        _render_group_table("Replay By Final Action", stats.get("by_action", []))
        _render_group_table("Replay By Adaptive Profile", stats.get("by_profile_mode", []))
        _render_group_table("Replay By Setup Quality Grade", stats.get("by_quality_grade", []))
        _render_group_table("Replay By Edge Policy", stats.get("by_edge_policy", []))
        _render_group_table("Replay By Opportunity Bucket", stats.get("by_opportunity_bucket", []))

        if stats.get("loss_warning_patterns"):
            warn_table = Table(
                title="Warnings Most Common In Losing Samples",
                show_header=True,
                header_style="bold red",
            )
            warn_table.add_column("Warning")
            warn_table.add_column("Count", justify="right")
            for warning, count in stats["loss_warning_patterns"]:
                warn_table.add_row(str(warning), str(count))
            console.print(warn_table)
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command("edge-stats")
def edge_stats(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="按交易对筛选"),
    limit: int = typer.Option(200, "--limit", help="统计最近 N 条已关联结果样本"),
    min_samples: int = typer.Option(2, "--min-samples", help="每个 edge slice 最少样本数"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Summarise where the current linked samples show the strongest trading edge."""
    try:
        config = load_app_config(config_path=config_path)
        from .pipeline.persistence import TradeRunDB

        db = TradeRunDB(config.logging.sqlite_db)
        stats = db.get_edge_stats(symbol=symbol, limit=limit, min_samples=min_samples)

        if stats.get("samples_scanned", 0) == 0:
            console.print(
                "[yellow]暂无可分析的 linked 样本。先运行 trade，并用 record 关联真实平仓结果。[/yellow]"
            )
            return

        overall = stats.get("overall", {})
        summary = Table(title="Edge Stats", show_header=True, header_style="bold cyan")
        summary.add_column("Metric", style="cyan")
        summary.add_column("Value")
        summary.add_row("samples_scanned", str(stats.get("samples_scanned", 0)))
        summary.add_row("unique_symbols", str(stats.get("unique_symbols", 0)))
        if overall:
            summary.add_row("win_rate", f"{(overall.get('win_rate') or 0.0):.1f}%")
            summary.add_row("avg_win_pnl", f"{(overall.get('avg_win_pnl') or 0.0):+,.2f} USDT")
            summary.add_row("avg_loss_pnl", f"{(overall.get('avg_loss_pnl') or 0.0):+,.2f} USDT")
            payoff_ratio = overall.get("payoff_ratio")
            profit_factor = overall.get("profit_factor")
            summary.add_row("expectancy_pnl", f"{(overall.get('expectancy_pnl') or 0.0):+,.2f} USDT")
            summary.add_row("expectancy_pnl_pct", f"{(overall.get('expectancy_pnl_pct') or 0.0):+,.2f}%")
            summary.add_row("payoff_ratio", "—" if payoff_ratio is None else f"{payoff_ratio:.2f}")
            summary.add_row("profit_factor", "—" if profit_factor is None else f"{profit_factor:.2f}")
        console.print(summary)

        def _render_edge_table(title: str, rows: list[dict]) -> None:
            if not rows:
                return
            table = Table(title=title, show_header=True, header_style="bold green")
            table.add_column("Group", style="cyan")
            table.add_column("Samples", justify="right")
            table.add_column("Win Rate", justify="right")
            table.add_column("Expectancy", justify="right")
            table.add_column("PF", justify="right")
            table.add_column("Payoff", justify="right")
            for row in rows[:10]:
                profit_factor = "—" if row.get("profit_factor") is None else f"{row['profit_factor']:.2f}"
                payoff = "—" if row.get("payoff_ratio") is None else f"{row['payoff_ratio']:.2f}"
                table.add_row(
                    row["group"],
                    str(row["samples"]),
                    f"{(row.get('win_rate') or 0.0):.1f}%",
                    f"{(row.get('expectancy_pnl') or 0.0):+,.2f}",
                    profit_factor,
                    payoff,
                )
            console.print(table)

        _render_edge_table("Edge By BTC Regime", stats.get("by_regime", []))
        _render_edge_table("Edge By Execution Template", stats.get("by_template", []))
        _render_edge_table("Edge By Narrative", stats.get("by_narrative", []))
        _render_edge_table("Edge By Setup Quality Grade", stats.get("by_quality_grade", []))
        _render_edge_table("Edge By Gating Profile", stats.get("by_profile_mode", []))
        _render_edge_table("Edge By Edge Policy", stats.get("by_edge_policy", []))
        _render_edge_table("Edge By Opportunity Bucket", stats.get("by_opportunity_bucket", []))
        _render_edge_table("Top Positive Edges", stats.get("top_positive_edges", []))
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command("backtest")
def backtest(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="按交易对筛选 recent runs"),
    limit: int = typer.Option(20, "--limit", help="回放最近 N 条 run"),
    hours: int = typer.Option(48, "--hours", help="默认 thesis window 小时数"),
    window_size: int = typer.Option(10, "--window-size", help="rolling backtest 窗口大小"),
    step: int = typer.Option(5, "--step", help="rolling backtest 步长"),
    train_size: int = typer.Option(20, "--train-size", help="walk-forward 训练窗样本数"),
    test_size: int = typer.Option(5, "--test-size", help="walk-forward 测试窗样本数"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Replay saved trade runs across historical Binance klines."""
    try:
        config = load_app_config(config_path=config_path)
        runner = BacktestRunner(config)
        outcomes = runner.replay_runs(symbol=symbol, limit=limit, default_hours=hours)
        summary = runner.summarize(outcomes)

        if summary.get("trades", 0) == 0:
            console.print("[yellow]暂无可回放的 run 样本。先运行 trade 生成一些记录。[/yellow]")
            return

        summary_table = Table(title="Backtest Summary", show_header=True, header_style="bold cyan")
        summary_table.add_column("Metric", style="cyan")
        summary_table.add_column("Value")
        summary_table.add_row("trades", str(summary.get("trades", 0)))
        summary_table.add_row("win_rate", f"{summary.get('win_rate', 0.0):.1f}%")
        summary_table.add_row("avg_win_pct", f"{summary.get('avg_win_pct', 0.0):+.2f}%")
        summary_table.add_row("avg_loss_pct", f"{summary.get('avg_loss_pct', 0.0):+.2f}%")
        pf = summary.get("profit_factor")
        summary_table.add_row("profit_factor", "—" if pf is None else f"{pf:.2f}")
        summary_table.add_row("expectancy_pct", f"{summary.get('expectancy_pct', 0.0):+.2f}%")
        summary_table.add_row("total_pnl_pct", f"{summary.get('total_pnl_pct', 0.0):+.2f}%")
        console.print(summary_table)

        detail = Table(title="Backtest Outcomes", show_header=True, header_style="bold green")
        detail.add_column("Run", justify="right")
        detail.add_column("Symbol", style="cyan")
        detail.add_column("Action")
        detail.add_column("Exit Reason")
        detail.add_column("PnL%", justify="right")
        detail.add_column("Entry Trend")
        detail.add_column("Entry Vol")
        detail.add_column("Regime")
        detail.add_column("Template")
        for outcome in outcomes[:20]:
            color = "green" if outcome.pnl_pct > 0 else "red" if outcome.pnl_pct < 0 else "yellow"
            detail.add_row(
                str(outcome.run_id),
                outcome.symbol,
                outcome.action,
                outcome.exit_reason,
                f"[{color}]{outcome.pnl_pct:+.2f}%[/{color}]",
                outcome.entry_trend_bias,
                outcome.entry_volatility_regime,
                outcome.btc_market_regime,
                outcome.execution_template,
            )
        console.print(detail)

        windows = runner.replay_windows(
            symbol=symbol,
            limit=limit,
            default_hours=hours,
            window_size=window_size,
            step=step,
        )
        if windows:
            wtable = Table(title="Rolling Windows", show_header=True, header_style="bold magenta")
            wtable.add_column("Window", justify="right")
            wtable.add_column("Runs")
            wtable.add_column("Trades", justify="right")
            wtable.add_column("Win Rate", justify="right")
            wtable.add_column("Expectancy", justify="right")
            wtable.add_column("PF", justify="right")
            wtable.add_column("Max DD", justify="right")
            for row in windows[:10]:
                pf = "—" if row.get("profit_factor") is None else f"{row['profit_factor']:.2f}"
                wtable.add_row(
                    str(row["window_index"]),
                    f"{row['start_run_id']}→{row['end_run_id']}",
                    str(row["trades"]),
                    "—" if row.get("win_rate") is None else f"{row['win_rate']:.1f}%",
                    "—" if row.get("expectancy_pct") is None else f"{row['expectancy_pct']:+.2f}%",
                    pf,
                    f"{row['max_drawdown_pct']:+.2f}%",
                )
            console.print(wtable)

        wf = runner.walk_forward(
            symbol=symbol,
            limit=max(limit, train_size + test_size),
            default_hours=hours,
            train_size=train_size,
            test_size=test_size,
            step=step,
        )
        if wf:
            wf_summary = runner.summarize_walk_forward(wf)
            summary_wf_table = Table(title="Walk-Forward Summary", show_header=True, header_style="bold yellow")
            summary_wf_table.add_column("Metric", style="cyan")
            summary_wf_table.add_column("Value")
            summary_wf_table.add_row("windows", str(wf_summary.get("windows", 0)))
            summary_wf_table.add_row("positive_test_windows", str(wf_summary.get("positive_test_windows", 0)))
            summary_wf_table.add_row(
                "positive_test_ratio",
                "—" if wf_summary.get("positive_test_ratio") is None else f"{wf_summary['positive_test_ratio']:.1f}%",
            )
            summary_wf_table.add_row(
                "avg_test_expectancy_pct",
                f"{wf_summary.get('avg_test_expectancy_pct', 0.0):+.2f}%",
            )
            summary_wf_table.add_row(
                "avg_test_total_pnl_pct",
                f"{wf_summary.get('avg_test_total_pnl_pct', 0.0):+.2f}%",
            )
            console.print(summary_wf_table)

            wf_table = Table(title="Walk-Forward Windows", show_header=True, header_style="bold yellow")
            wf_table.add_column("Window", justify="right")
            wf_table.add_column("Train Runs")
            wf_table.add_column("Train Exp", justify="right")
            wf_table.add_column("Train PF", justify="right")
            wf_table.add_column("Test Runs")
            wf_table.add_column("Test Exp", justify="right")
            wf_table.add_column("Test PF", justify="right")
            wf_table.add_column("Test PnL", justify="right")
            for row in wf[:10]:
                train_pf = "—" if row.get("train_profit_factor") is None else f"{row['train_profit_factor']:.2f}"
                test_pf = "—" if row.get("test_profit_factor") is None else f"{row['test_profit_factor']:.2f}"
                wf_table.add_row(
                    str(row["window_index"]),
                    f"{row['train_start_run_id']}→{row['train_end_run_id']}",
                    "—" if row.get("train_expectancy_pct") is None else f"{row['train_expectancy_pct']:+.2f}%",
                    train_pf,
                    f"{row['test_start_run_id']}→{row['test_end_run_id']}",
                    "—" if row.get("test_expectancy_pct") is None else f"{row['test_expectancy_pct']:+.2f}%",
                    test_pf,
                    "—" if row.get("test_total_pnl_pct") is None else f"{row['test_total_pnl_pct']:+.2f}%",
                )
            console.print(wf_table)
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command("tune-edge")
def tune_edge(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="按交易对筛选 linked 样本"),
    limit: int = typer.Option(200, "--limit", help="分析最近 N 条 linked 样本"),
    min_samples: int = typer.Option(3, "--min-samples", help="每个 slice 的最少样本数"),
    output: Optional[str] = typer.Option(None, "--output", help="将 alpha allow/deny 建议导出为 YAML patch"),
    apply: bool = typer.Option(False, "--apply", help="备份当前配置后直接应用 edge policy patch"),
    yes: bool = typer.Option(False, "--yes", help="与 --apply 一起使用，确认修改 config 文件"),
    backup_dir: Optional[str] = typer.Option(None, "--backup-dir", help="配置备份目录，默认与 config 同级"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Suggest alpha allowlist / denylist entries from edge stats."""
    try:
        config = load_app_config(config_path=config_path)
        from .pipeline.persistence import TradeRunDB

        db = TradeRunDB(config.logging.sqlite_db)
        edge_stats_payload = db.get_edge_stats(symbol=symbol, limit=limit, min_samples=min_samples)
        if edge_stats_payload.get("samples_scanned", 0) == 0:
            console.print("[yellow]暂无可分析的 linked 样本用于 tune-edge。[/yellow]")
            return

        advisor = EdgePolicyAdvisor(config)
        wf_runner = BacktestRunner(config)
        wf_windows = wf_runner.walk_forward(symbol=symbol, limit=max(limit, 25), default_hours=48, train_size=20, test_size=5, step=5)
        wf_summary = wf_runner.summarize_walk_forward(wf_windows)
        suggestions = advisor.suggest(edge_stats_payload, walk_forward_summary=wf_summary)
        if not suggestions:
            if wf_summary.get("windows", 0):
                console.print(
                    f"[yellow]当前 walk-forward 平均测试期 expectancy 为 {wf_summary.get('avg_test_expectancy_pct', 0.0):+.2f}% ，"
                    "因此没有输出新的 alpha allow/deny 建议。[/yellow]"
                )
            else:
                console.print("[green]当前没有明确的 alpha allow/deny 建议。[/green]")
            return

        if wf_summary.get("windows", 0):
            console.print(
                f"[dim]Walk-forward: windows={wf_summary['windows']}, "
                f"avg_test_expectancy={wf_summary.get('avg_test_expectancy_pct', 0.0):+.2f}%, "
                f"positive_ratio={wf_summary.get('positive_test_ratio', 0.0):.1f}%[/dim]"
            )

        table = Table(title="Edge Policy Suggestions", show_header=True, header_style="bold cyan")
        table.add_column("Policy", style="cyan")
        table.add_column("Entry")
        table.add_column("Samples", justify="right")
        table.add_column("Expectancy", justify="right")
        table.add_column("PF", justify="right")
        table.add_column("Reason")
        for s in suggestions:
            pf = "—" if s.profit_factor is None else f"{s.profit_factor:.2f}"
            table.add_row(
                s.policy,
                s.entry,
                str(s.samples),
                f"{s.expectancy_pnl_pct:+.2f}%",
                pf,
                s.reason,
            )
        console.print(table)

        allowlist = list(dict.fromkeys(config.trading.edge_policy_allowlist + [s.entry for s in suggestions if s.policy == "allowlist"]))
        denylist = list(dict.fromkeys(config.trading.edge_policy_denylist + [s.entry for s in suggestions if s.policy == "denylist"]))
        patch = {
            "trading": {
                "edge_policy_allowlist": allowlist,
                "edge_policy_denylist": denylist,
            }
        }

        if output:
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8")
            console.print(f"[green]✓ Edge policy 建议已导出到 {output_path}[/green]")

        if apply:
            if not yes:
                console.print("[red]使用 --apply 时必须同时传入 --yes。[/red]")
                raise typer.Exit(1)
            mgr = ConfigPatchManager(config_path=config_path, backup_dir=backup_dir)
            backup_path, _ = mgr.apply_patch(patch, reason="tune-edge")
            console.print(f"[green]✓ 已应用 edge policy 建议到 {config_path}[/green]")
            console.print(f"[cyan]备份已保存到 {backup_path}[/cyan]")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command("tune-risk")
def tune_risk(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="按交易对筛选 linked 样本"),
    limit: int = typer.Option(200, "--limit", help="分析最近 N 条 linked 样本 / run"),
    output: Optional[str] = typer.Option(None, "--output", help="将建议导出为 YAML patch 文件"),
    apply: bool = typer.Option(False, "--apply", help="备份当前配置后直接应用建议 patch"),
    yes: bool = typer.Option(False, "--yes", help="与 --apply 一起使用，确认修改 config 文件"),
    backup_dir: Optional[str] = typer.Option(None, "--backup-dir", help="配置备份目录，默认与 config 同级"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Suggest conservative risk-parameter adjustments from linked real outcomes."""
    try:
        config = load_app_config(config_path=config_path)
        from .pipeline.persistence import TradeRunDB

        db = TradeRunDB(config.logging.sqlite_db)
        replay_stats = db.get_replay_stats(symbol=symbol, limit=limit)
        risk_stats = db.get_risk_rule_stats(symbol=symbol, limit=limit)

        if replay_stats.get("samples_scanned", 0) == 0:
            console.print(
                "[yellow]暂无足够的 linked 样本用于调优。先运行 trade，并用 record 关联真实平仓结果。[/yellow]"
            )
            return

        advisor = RiskTuningAdvisor(config)
        wf_runner = BacktestRunner(config)
        wf_windows = wf_runner.walk_forward(symbol=symbol, limit=max(limit, 25), default_hours=48, train_size=20, test_size=5, step=5)
        wf_summary = wf_runner.summarize_walk_forward(wf_windows)
        suggestions = advisor.suggest(replay_stats=replay_stats, risk_stats=risk_stats, walk_forward_summary=wf_summary)

        if not suggestions:
            console.print("[green]当前没有明确的保守调优建议；现有 linked 样本暂未显示出需要收紧的参数。[/green]")
            return

        if wf_summary.get("windows", 0):
            console.print(
                f"[dim]Walk-forward: windows={wf_summary['windows']}, "
                f"avg_test_expectancy={wf_summary.get('avg_test_expectancy_pct', 0.0):+.2f}%, "
                f"positive_ratio={wf_summary.get('positive_test_ratio', 0.0):.1f}%[/dim]"
            )

        table = Table(title="Risk Tuning Suggestions", show_header=True, header_style="bold cyan")
        table.add_column("Priority", style="cyan")
        table.add_column("Config Key")
        table.add_column("Current", justify="right")
        table.add_column("Suggested", justify="right")
        table.add_column("Reason", max_width=32)
        table.add_column("Evidence", max_width=42)
        for suggestion in suggestions:
            table.add_row(
                suggestion.priority,
                suggestion.config_path,
                str(suggestion.current_value),
                str(suggestion.suggested_value),
                suggestion.reason,
                suggestion.evidence,
            )
        console.print(table)

        if output:
            patch = advisor.export_patch(suggestions)
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8")
            console.print(f"[green]✓ 调优建议已导出到 {output_path}[/green]")

        if apply:
            if not yes:
                console.print("[red]使用 --apply 时必须同时传入 --yes，以避免误改配置。[/red]")
                raise typer.Exit(1)
            patch = advisor.export_patch(suggestions)
            mgr = ConfigPatchManager(config_path=config_path, backup_dir=backup_dir)
            backup_path, _ = mgr.apply_patch(patch, reason="tune-risk")
            console.print(f"[green]✓ 已应用调优建议到 {config_path}[/green]")
            console.print(f"[cyan]备份已保存到 {backup_path}[/cyan]")

    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command("rollback-config")
def rollback_config(
    config_path: str = typer.Option("config.yaml", "--config"),
    backup: Optional[str] = typer.Option(None, "--backup", help="指定要恢复的 backup 文件路径"),
    list_backups: bool = typer.Option(False, "--list", help="只列出可用 backup，不执行回滚"),
    yes: bool = typer.Option(False, "--yes", help="执行回滚前确认"),
    backup_dir: Optional[str] = typer.Option(None, "--backup-dir", help="配置备份目录，默认与 config 同级"),
) -> None:
    """Rollback config.yaml from a previously created backup."""
    try:
        mgr = ConfigPatchManager(config_path=config_path, backup_dir=backup_dir)
        if list_backups:
            backups = mgr.list_backups()
            if not backups:
                console.print("[yellow]暂无可用配置备份。[/yellow]")
                return
            table = Table(title="Config Backups", show_header=True, header_style="bold cyan")
            table.add_column("Backup Path")
            for item in backups[:20]:
                table.add_row(str(item))
            console.print(table)
            return

        if not yes:
            console.print("[red]执行回滚前必须传入 --yes。[/red]")
            raise typer.Exit(1)

        restored_from, safety_backup = mgr.rollback(backup_path=backup)
        console.print(f"[green]✓ 已从备份恢复配置：{restored_from}[/green]")
        console.print(f"[cyan]当前配置的回滚前快照已保存到：{safety_backup}[/cyan]")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
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
    auto_execute: bool = typer.Option(
        False,
        "--auto-execute",
        help="If enabled, place a real live order only when auto-execution guardrails all pass",
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

        if dry_run:
            mode_label = "[yellow][DRY RUN][/yellow]"
        elif auto_execute:
            mode_label = "[red][AUTO EXECUTE REQUESTED][/red]"
        else:
            mode_label = "[cyan][MANUAL REVIEW][/cyan]"
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
            auto_execute=auto_execute,
        )
        market_snapshot = pipeline.last_market_snapshot
        portfolio_snapshot = pipeline.last_portfolio_snapshot
        portfolio_budget = pipeline.last_portfolio_budget
        onchain_snapshot = pipeline.last_onchain_snapshot

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

        if onchain_snapshot.get("has_onchain_data"):
            onchain_table = Table(title="On-Chain Data (DeFiLlama)", show_header=True, header_style="bold green")
            onchain_table.add_column("Field", style="cyan", min_width=22)
            onchain_table.add_column("Value")
            if onchain_snapshot.get("protocol_name"):
                tvl = onchain_snapshot.get("protocol_tvl_usd")
                tvl_str = f"${tvl / 1e6:.1f}M" if tvl else "N/A"
                onchain_table.add_row("Protocol", f"{onchain_snapshot['protocol_name']} ({onchain_snapshot.get('protocol_category', 'DeFi')})")
                onchain_table.add_row("Protocol TVL", tvl_str)
                if onchain_snapshot.get("tvl_change_24h_pct") is not None:
                    v = onchain_snapshot["tvl_change_24h_pct"]
                    color = "green" if v >= 0 else "red"
                    onchain_table.add_row("TVL 24h", f"[{color}]{v:+.1f}%[/{color}]")
                if onchain_snapshot.get("tvl_change_7d_pct") is not None:
                    v = onchain_snapshot["tvl_change_7d_pct"]
                    color = "green" if v >= 0 else "red"
                    onchain_table.add_row("TVL 7d", f"[{color}]{v:+.1f}%[/{color}]")
                if onchain_snapshot.get("protocol_chains"):
                    onchain_table.add_row("Chains", ", ".join(onchain_snapshot["protocol_chains"][:4]))
            if onchain_snapshot.get("chain_name") and onchain_snapshot.get("chain_tvl_usd") is not None:
                chain_tvl = onchain_snapshot["chain_tvl_usd"]
                onchain_table.add_row(f"{onchain_snapshot['chain_name']} TVL", f"${chain_tvl / 1e9:.2f}B")
            if onchain_snapshot.get("stablecoin_total_usd") is not None:
                st = onchain_snapshot["stablecoin_total_usd"]
                onchain_table.add_row("Stablecoin Supply", f"${st / 1e9:.1f}B")
            console.print(onchain_table)

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
        research_table.add_row("Consensus", f"{research.supporting_model_count} model(s), {research.consensus_strength:.0%}")
        if research.disagreement_note:
            research_table.add_row("Disagreement", research.disagreement_note[:160])
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
        if risk.gating_profile:
            risk_table.add_row("Profile", risk.gating_profile)
        if risk.setup_quality_score is not None:
            grade = risk.setup_quality_grade or "?"
            risk_table.add_row("Quality", f"{risk.setup_quality_score:.1f} / 100 ({grade})")
        if risk.opportunity_score is not None:
            risk_table.add_row("Opportunity", f"{risk.opportunity_score:.1f} / 100 ({risk.opportunity_bucket or '—'})")
        if risk.edge_policy_label:
            risk_table.add_row("Edge Policy", risk.edge_policy_label)
        if risk.edge_policy_expectancy_pnl_pct is not None:
            risk_table.add_row("Edge Exp", f"{risk.edge_policy_expectancy_pnl_pct:+.2f}%")
        if risk.edge_policy_reasons:
            risk_table.add_row("Edge Why", "\n".join(f"• {r}" for r in risk.edge_policy_reasons[:3]))
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
            "auto_execute_blocked": "yellow",
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
        elif result.status == "auto_execute_blocked":
            console.print(
                Panel(
                    "[yellow]自动执行已被安全闸门拦截，已降级为手动复核[/yellow]",
                    border_style="yellow",
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


@app.command()
def watch(
    symbols: Optional[list[str]] = typer.Argument(None, help="Symbols to watch (e.g. BTCUSDT ETHUSDT). Auto-detects live positions if omitted."),
    direction: Optional[str] = typer.Option(None, "--direction", help="LONG or SHORT (only used when specifying a single symbol manually)"),
    entry: Optional[float] = typer.Option(None, "--entry", help="Entry price for PnL display"),
    invalidation: Optional[float] = typer.Option(None, "--invalidation", help="Invalidation price — alerts when breached"),
    stop: Optional[float] = typer.Option(None, "--stop", help="Stop-loss price — warns when within 50%"),
    tp: Optional[float] = typer.Option(None, "--tp", help="Take-profit price — notifies when within 20%"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Start real-time WebSocket risk monitor for open positions.

    Examples:\n
      trading watch                            # auto-detect from live Binance positions\n
      trading watch BTCUSDT ETHUSDT            # load params from last pipeline run per symbol\n
      trading watch BTCUSDT --direction long --entry 95000 --invalidation 93000 --stop 92000
    """
    try:
        config = load_app_config(config_path=config_path)
        targets: list[WatchTarget] = []

        # ── Case 1: manual single-symbol entry with explicit flags ──────
        if symbols and len(symbols) == 1 and (direction or entry or invalidation or stop):
            sym = symbols[0].upper()
            if not direction:
                console.print("[red]--direction (long|short) required when specifying prices manually.[/red]")
                raise typer.Exit(1)
            targets.append(
                WatchTarget(
                    symbol=sym,
                    direction=direction.upper(),
                    entry_price=entry or 0.0,
                    invalidation_price=invalidation,
                    stop_loss_price=stop,
                    take_profit_price=tp,
                )
            )

        else:
            # ── Case 2: load from live Binance positions + SQLite pipeline runs ──
            from .pipeline.persistence import TradeRunDB

            api_ok = is_trading_api_configured(config)
            live_positions: list[dict] = []

            if api_ok:
                try:
                    binance = BinanceClient(config.binance, dry_run=False)
                    pos_mgr = PositionManager(binance)
                    raw = pos_mgr.get_futures_positions()
                    live_positions = [
                        {
                            "symbol": p.symbol,
                            "direction": "LONG" if p.is_long else "SHORT",
                            "entry_price": float(p.price),
                            "quantity": float(abs(p.amount)),
                        }
                        for p in raw
                    ]
                    console.print(f"[cyan]Loaded {len(live_positions)} live position(s) from Binance.[/cyan]")
                except Exception as exc:
                    console.print(f"[yellow]Could not fetch live positions: {exc}[/yellow]")
            else:
                console.print("[yellow]Binance API not configured — no live positions available.[/yellow]")

            # Filter to requested symbols if specified
            watch_syms = [s.upper() for s in symbols] if symbols else None
            if watch_syms:
                live_positions = [p for p in live_positions if p["symbol"] in watch_syms]
                # Add placeholders for symbols not in live positions
                found = {p["symbol"] for p in live_positions}
                for sym in watch_syms:
                    if sym not in found:
                        live_positions.append(
                            {"symbol": sym, "direction": "LONG", "entry_price": 0.0, "quantity": 0.0}
                        )

            if not live_positions:
                console.print(
                    "[yellow]No positions found. Use explicit args: "
                    "trading watch BTCUSDT --direction long --entry 95000[/yellow]"
                )
                raise typer.Exit(0)

            # Enrich with invalidation/stop from last pipeline run in SQLite
            db = TradeRunDB(config.logging.sqlite_db)
            for pos in live_positions:
                sym = pos["symbol"]
                runs = db.get_runs(symbol=sym, limit=1)
                inv_price: Optional[float] = None
                stop_price: Optional[float] = None
                tp_price: Optional[float] = None

                if runs:
                    import json as _json
                    plan_json = runs[0].get("execution_plan_json")
                    result_json = runs[0].get("execution_result_json")
                    if plan_json:
                        try:
                            plan = _json.loads(plan_json)
                            inv_price = plan.get("invalidation_price")
                        except Exception:
                            pass
                    if result_json:
                        try:
                            result = _json.loads(result_json)
                            details = result.get("manual_order_details") or {}
                            stop_price = details.get("stop_loss_price")
                            tp_price = details.get("take_profit_price")
                        except Exception:
                            pass

                targets.append(
                    WatchTarget(
                        symbol=sym,
                        direction=pos.get("direction", "LONG"),
                        entry_price=pos.get("entry_price", 0.0),
                        quantity=pos.get("quantity", 0.0),
                        invalidation_price=inv_price,
                        stop_loss_price=stop_price,
                        take_profit_price=tp_price,
                    )
                )

        daemon = RiskDaemon(config=config, targets=targets)
        daemon.run()

    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command()
def batch_trade(
    symbols: Optional[list[str]] = typer.Argument(None, help="Symbols to scan (e.g. BTCUSDT ETHUSDT). Uses config scan_symbols if omitted."),
    market: str = typer.Option("auto", "--market", help="Market type: spot | futures | auto"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Use mock account data"),
    write_vault: bool = typer.Option(True, "--write-vault/--no-write-vault", help="Write reports to Vault"),
    allow_partial: bool = typer.Option(False, "--allow-partial", help="Allow risk-adjusted partial execution"),
    auto_execute: bool = typer.Option(False, "--auto-execute", help="Allow live auto execution when all guardrails pass"),
    top_n: Optional[int] = typer.Option(None, "--top-n", help="只突出展示最值得做的前 N 笔机会"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Batch-scan multiple symbols through the AI pipeline and push summary to Feishu."""
    try:
        config = load_app_config(config_path=config_path, dry_run=dry_run)

        # Resolve symbol list
        scan_symbols = [s.upper() for s in symbols] if symbols else config.monitor.scan_symbols
        if not scan_symbols:
            console.print("[red]No symbols specified and monitor.scan_symbols is empty in config.[/red]")
            raise typer.Exit(1)

        console.print(f"[cyan]Batch scanning {len(scan_symbols)} symbols: {', '.join(scan_symbols)}[/cyan]")

        pipeline = TradePipeline(config=config, dry_run=dry_run)
        analyses: list[dict] = []
        results: list[dict] = []
        errors: list[str] = []

        for i, sym in enumerate(scan_symbols, 1):
            console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
            console.print(f"[bold cyan][{i}/{len(scan_symbols)}] Scanning {sym}...[/bold cyan]")
            console.print(f"[bold cyan]{'='*60}[/bold cyan]")
            try:
                bundle = pipeline.analyze(sym)
                analyses.append({
                    "bundle": bundle,
                    "symbol": sym,
                    "stance": bundle.research.stance,
                    "confidence": bundle.research.confidence,
                    "action": bundle.plan.action,
                    "size_pct": bundle.plan.size_pct,
                    "leverage": bundle.plan.leverage,
                    "risk_approved": bundle.risk.approved,
                    "status": "analyzed",
                    "setup_quality_score": bundle.risk.setup_quality_score,
                    "setup_quality_grade": bundle.risk.setup_quality_grade,
                    "opportunity_score": bundle.risk.opportunity_score,
                    "opportunity_bucket": bundle.risk.opportunity_bucket,
                    "edge_policy_label": bundle.risk.edge_policy_label,
                    "edge_policy_expectancy_pnl_pct": bundle.risk.edge_policy_expectancy_pnl_pct,
                })
            except Exception as exc:
                errors.append(f"{sym}: {exc}")
                console.print(f"[red]Failed {sym}: {exc}[/red]")

        # Summary
        signals = [r for r in analyses if r["stance"] != "neutral"]
        signals.sort(key=_batch_edge_sort_key)
        top_limit = top_n if top_n is not None else config.monitor.batch_top_n_signals
        top_opportunities = signals[:top_limit] if top_limit and top_limit > 0 else signals

        auto_exec_cap = max(1, config.monitor.batch_auto_execute_max_candidates)
        auto_exec_symbols = {r["symbol"] for r in top_opportunities[:auto_exec_cap]}
        batch_auto_cap_pct = float(config.trading.auto_execute_max_batch_capital_pct)
        batch_auto_budget_used = 0.0

        if auto_execute and not dry_run and top_opportunities:
            auto_candidates = [r for r in top_opportunities[:auto_exec_cap] if r["risk_approved"]]
            if auto_candidates:
                console.print(
                    f"\n[bold yellow]Auto-executing top {len(auto_candidates)} candidate(s) after full-batch ranking…[/bold yellow]"
                )
                skipped_top = len(top_opportunities) - len(top_opportunities[:auto_exec_cap])
                if skipped_top > 0:
                    console.print(f"[dim]另有 {skipped_top} 个 Top-N 信号因 auto-exec 名额上限未进入自动执行。[/dim]")

        for item in analyses:
            bundle = item["bundle"]
            try:
                candidate_auto_execute = auto_execute and bundle.symbol in auto_exec_symbols and item["risk_approved"]
                if candidate_auto_execute:
                    requested_size = float(bundle.risk.adjusted_size_pct or bundle.plan.size_pct or 0.0)
                    if batch_auto_budget_used + requested_size > batch_auto_cap_pct:
                        candidate_auto_execute = False
                        console.print(
                            f"[dim]{bundle.symbol} 超出本轮 auto-exec 资金预算上限 {batch_auto_cap_pct:.1f}% ，已保留为手动复核。[/dim]"
                        )
                final_result = pipeline.finalize_analysis(
                    bundle,
                    market=market,
                    write_vault=write_vault,
                    allow_partial=allow_partial,
                    auto_execute=candidate_auto_execute,
                )
                if candidate_auto_execute and final_result.executed:
                    batch_auto_budget_used += float(bundle.risk.adjusted_size_pct or bundle.plan.size_pct or 0.0)
                results.append(
                    {
                        **{k: v for k, v in item.items() if k != "bundle"},
                        "status": final_result.status,
                        "final_result": final_result,
                    }
                )
            except Exception as exc:
                errors.append(f"{bundle.symbol} finalize: {exc}")
                console.print(f"[red]Finalize failed {bundle.symbol}: {exc}[/red]")

        console.print(f"\n[bold green]Batch complete: {len(results)} scanned, {len(signals)} signals, {len(errors)} errors[/bold green]")

        if signals:
            if top_opportunities:
                top_table = Table(title=f"Top {len(top_opportunities)} Opportunities", show_header=True, header_style="bold green")
                top_table.add_column("Rank", justify="right")
                top_table.add_column("Symbol", style="cyan")
                top_table.add_column("Edge")
                top_table.add_column("Score", justify="right")
                top_table.add_column("Quality", justify="right")
                top_table.add_column("Confidence", justify="right")
                top_table.add_column("Action")
                for idx, r in enumerate(top_opportunities, 1):
                    quality = r.get("setup_quality_score")
                    opp = r.get("opportunity_score")
                    top_table.add_row(
                        str(idx),
                        r["symbol"],
                        str(r.get("edge_policy_label") or "—"),
                        "—" if opp is None else f"{float(opp):.0f}",
                        "—" if quality is None else f"{float(quality):.0f}",
                        f"{r['confidence']:.0%}",
                        r["action"],
                    )
                console.print(top_table)
                remaining = len(signals) - len(top_opportunities)
                if remaining > 0:
                    console.print(f"[dim]还有 {remaining} 个较低优先级信号未进入 Top-N 机会池。[/dim]")

            sig_table = Table(title="Signals Found", show_header=True, header_style="bold cyan")
            sig_table.add_column("Symbol", style="cyan")
            sig_table.add_column("Stance")
            sig_table.add_column("Confidence", justify="right")
            sig_table.add_column("Score", justify="right")
            sig_table.add_column("Quality", justify="right")
            sig_table.add_column("Edge", justify="center")
            sig_table.add_column("Action")
            sig_table.add_column("Size", justify="right")
            sig_table.add_column("Leverage", justify="right")
            sig_table.add_column("Risk", justify="center")
            for r in signals:
                color = "green" if r["stance"] == "long" else "red"
                risk_icon = "✅" if r["risk_approved"] else "❌"
                edge = r.get("edge_policy_label") or "—"
                opp = r.get("opportunity_score")
                quality = r.get("setup_quality_score")
                sig_table.add_row(
                    r["symbol"],
                    f"[{color}]{r['stance']}[/{color}]",
                    f"{r['confidence']:.0%}",
                    "—" if opp is None else f"{float(opp):.0f}",
                    "—" if quality is None else f"{float(quality):.0f}",
                    str(edge),
                    r["action"],
                    f"{r['size_pct']:.1f}%",
                    f"{r['leverage']}x",
                    risk_icon,
                )
            console.print(sig_table)

        # Push to Feishu
        noti = config.notification
        if noti.enabled and noti.on_batch_scan and noti.feishu_webhook_url and signals:
            feishu = FeishuWebhook(noti.feishu_webhook_url)
            feishu.notify_batch_scan(signals)
            console.print("[green]✓ Batch results pushed to Feishu[/green]")

        # Auto-rebalance after batch if configured
        if config.rebalance.enabled and config.rebalance.auto_after_batch and is_trading_api_configured(config) and not dry_run:
            console.print("\n[bold cyan]⚖️  Running post-batch portfolio rebalance…[/bold cyan]")
            try:
                from .exchange.client import BinanceClient as _BC
                from .exchange.positions import PositionManager as _PM
                from .exchange.account import AccountManager as _AM
                _client = _BC(config.binance, dry_run=False)
                _acct = _AM(_client).get_futures_account_summary()
                _positions = [
                    {"symbol": p.symbol, "direction": "LONG" if p.is_long else "SHORT",
                     "amount": p.amount, "price": p.price, "leverage": p.leverage}
                    for p in _PM(_client).get_futures_positions()
                ]
                rebalancer = Rebalancer(config=config, live_client=_client)
                rb_orders = rebalancer.compute_rebalance_plan(_acct, _positions)
                if rb_orders:
                    rb_results = rebalancer.execute_rebalance(rb_orders, dry_run=False)
                    if noti.enabled and noti.feishu_webhook_url:
                        feishu_rb = FeishuWebhook(noti.feishu_webhook_url)
                        feishu_rb.notify_rebalance(rb_results)
                    console.print(f"[green]✓ Rebalance complete: {len(rb_results)} order(s)[/green]")
                else:
                    console.print("[dim]No rebalance needed — all positions within threshold.[/dim]")
            except Exception as rb_exc:
                console.print(f"[yellow]Warning: post-batch rebalance failed: {rb_exc}[/yellow]")

        if errors:
            for e in errors:
                console.print(f"[red]  ✗ {e}[/red]")

    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.command()
def rebalance(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview rebalance plan without placing orders"),
    force: bool = typer.Option(False, "--force", help="Ignore deviation threshold; rebalance all positions"),
    config_path: str = typer.Option("config.yaml", "--config"),
) -> None:
    """Compute portfolio deviation and auto-rebalance positions to target exposure."""
    try:
        config = load_app_config(config_path=config_path, dry_run=dry_run)

        if not config.rebalance.enabled:
            console.print("[yellow]Rebalance is disabled in config (rebalance.enabled=false).[/yellow]")
            raise typer.Exit(0)

        if not is_trading_api_configured(config):
            console.print("[red]Binance API not configured. Cannot rebalance.[/red]")
            raise typer.Exit(1)

        from .exchange.client import BinanceClient as _BC
        from .exchange.positions import PositionManager as _PM
        from .exchange.account import AccountManager as _AM

        _client = _BC(config.binance, dry_run=dry_run)
        acct = _AM(_client).get_futures_account_summary()
        positions = [
            {"symbol": p.symbol, "direction": "LONG" if p.is_long else "SHORT",
             "amount": p.amount, "price": p.price, "leverage": p.leverage}
            for p in _PM(_client).get_futures_positions()
        ]

        if not positions:
            console.print("[dim]No open positions found — nothing to rebalance.[/dim]")
            return

        # Optionally override threshold to 0 for --force
        if force:
            config.rebalance.deviation_threshold_pct = 0.0

        rebalancer = Rebalancer(config=config, live_client=_client)
        orders = rebalancer.compute_rebalance_plan(acct, positions)

        if not orders:
            console.print("[green]✓ All positions within target threshold — no rebalance needed.[/green]")
            return

        # Display plan table
        plan_table = Table(title="Rebalance Plan", show_header=True, header_style="bold cyan")
        plan_table.add_column("Symbol", style="cyan")
        plan_table.add_column("Dir")
        plan_table.add_column("Action")
        plan_table.add_column("Current", justify="right")
        plan_table.add_column("Target", justify="right")
        plan_table.add_column("Delta", justify="right")
        plan_table.add_column("Reason")
        for o in orders:
            color = "red" if o.action == "reduce" else "green" if o.action == "increase" else "yellow"
            plan_table.add_row(
                o.symbol,
                o.direction,
                f"[{color}]{o.action}[/{color}]",
                f"${o.current_notional:,.0f}",
                f"${o.target_notional:,.0f}",
                f"${o.delta_notional:+,.0f}",
                o.reason[:60],
            )
        console.print(plan_table)

        # Execute
        console.print(f"\n[bold]Executing {len(orders)} rebalance order(s)…[/bold]")
        results = rebalancer.execute_rebalance(orders, dry_run=dry_run)

        # Summary
        executed = sum(1 for r in results if r["status"] == "executed")
        failed = sum(1 for r in results if r["status"] == "failed")
        console.print(
            f"\n[bold green]✓ Rebalance done: {executed} executed, {failed} failed, "
            f"{len(results) - executed - failed} skipped[/bold green]"
        )

        # Feishu notification
        noti = config.notification
        if noti.enabled and noti.feishu_webhook_url and not dry_run:
            feishu = FeishuWebhook(noti.feishu_webhook_url)
            feishu.notify_rebalance(results)
            console.print("[green]✓ Rebalance results pushed to Feishu[/green]")

    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@app.callback()
def main() -> None:
    """Trading Bot - AI-powered cryptocurrency trading assistant."""
    pass


if __name__ == "__main__":
    app()
