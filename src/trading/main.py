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
        binance = BinanceClient(config.binance, dry_run=False)
        positions_mgr = PositionManager(binance)
        vault = VaultReader(config.vault)

        console.print("[cyan]Fetching positions from Binance...[/cyan]")
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
            "Configured" if config.claude.api_key else "Not configured (Phase 2)",
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


@app.callback()
def main() -> None:
    """Trading Bot - AI-powered cryptocurrency trading assistant."""
    pass


if __name__ == "__main__":
    app()
