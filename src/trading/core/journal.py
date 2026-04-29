"""Trade journal: record closed trades and summarize P&L experience."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.table import Table


@dataclass
class TradeRecord:
    """A single closed futures trade."""

    symbol: str
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    quantity: float
    leverage: float
    realized_pnl: float
    pnl_pct: float          # % return on margin
    open_time: datetime
    close_time: datetime
    notes: str = ""
    id: Optional[int] = None

    @property
    def duration_minutes(self) -> float:
        """Hold duration in minutes."""
        return (self.close_time - self.open_time).total_seconds() / 60

    @property
    def is_win(self) -> bool:
        """True if trade was profitable."""
        return self.realized_pnl > 0


def _calc_pnl(direction: str, entry: float, exit_p: float, qty: float, leverage: float) -> tuple[float, float]:
    """Return (realized_pnl, pnl_pct) from trade parameters."""
    if direction.upper() == "LONG":
        pnl = (exit_p - entry) * qty
    else:
        pnl = (entry - exit_p) * qty
    margin = entry * abs(qty) / leverage if leverage > 0 else 1.0
    pnl_pct = (pnl / margin * 100) if margin > 0 else 0.0
    return pnl, pnl_pct


class TradeJournal:
    """Persist and query trade records in SQLite."""

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS trade_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price  REAL NOT NULL,
            quantity    REAL NOT NULL,
            leverage    REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            pnl_pct     REAL NOT NULL,
            open_time   TEXT NOT NULL,
            close_time  TEXT NOT NULL,
            notes       TEXT DEFAULT '',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """

    def __init__(self, db_path: str):
        """Open (or create) the SQLite DB and ensure the trade_records table exists."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(self._CREATE_TABLE)
            conn.commit()

    def record_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        leverage: float,
        open_time: datetime,
        close_time: Optional[datetime] = None,
        realized_pnl: Optional[float] = None,
        notes: str = "",
    ) -> TradeRecord:
        """Insert a closed trade; auto-calculates P&L if not provided."""
        close_time = close_time or datetime.now()
        direction = direction.upper()

        if realized_pnl is None:
            realized_pnl, pnl_pct = _calc_pnl(direction, entry_price, exit_price, quantity, leverage)
        else:
            margin = entry_price * abs(quantity) / leverage if leverage > 0 else 1.0
            pnl_pct = (realized_pnl / margin * 100) if margin > 0 else 0.0

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO trade_records
                   (symbol, direction, entry_price, exit_price, quantity, leverage,
                    realized_pnl, pnl_pct, open_time, close_time, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol.upper(), direction, entry_price, exit_price, quantity, leverage,
                    realized_pnl, pnl_pct,
                    open_time.isoformat(), close_time.isoformat(), notes,
                ),
            )
            conn.commit()
            record_id = cur.lastrowid

        return TradeRecord(
            id=record_id, symbol=symbol.upper(), direction=direction,
            entry_price=entry_price, exit_price=exit_price,
            quantity=quantity, leverage=leverage,
            realized_pnl=realized_pnl, pnl_pct=pnl_pct,
            open_time=open_time, close_time=close_time, notes=notes,
        )

    def get_trades(self, symbol: Optional[str] = None, limit: int = 50) -> list[TradeRecord]:
        """Return recent trades, optionally filtered by symbol."""
        sql = "SELECT id, symbol, direction, entry_price, exit_price, quantity, leverage, " \
              "realized_pnl, pnl_pct, open_time, close_time, notes FROM trade_records"
        params: list = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY close_time DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            TradeRecord(
                id=r[0], symbol=r[1], direction=r[2],
                entry_price=r[3], exit_price=r[4], quantity=r[5], leverage=r[6],
                realized_pnl=r[7], pnl_pct=r[8],
                open_time=datetime.fromisoformat(r[9]),
                close_time=datetime.fromisoformat(r[10]),
                notes=r[11] or "",
            )
            for r in rows
        ]

    def get_summary(self, symbol: Optional[str] = None) -> dict:
        """Return aggregate stats: win rate, total P&L, avg P&L, best/worst trade."""
        trades = self.get_trades(symbol=symbol, limit=10000)
        if not trades:
            return {"total_trades": 0}

        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]
        total_pnl = sum(t.realized_pnl for t in trades)
        symbols = sorted({t.symbol for t in trades})
        avg_hold = sum(t.duration_minutes for t in trades) / len(trades)

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trades) * 100,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / len(trades),
            "avg_pnl_pct": sum(t.pnl_pct for t in trades) / len(trades),
            "best_trade": max(trades, key=lambda t: t.realized_pnl),
            "worst_trade": min(trades, key=lambda t: t.realized_pnl),
            "symbols_traded": symbols,
            "avg_hold_minutes": avg_hold,
        }

    def get_rich_table(self, symbol: Optional[str] = None, limit: int = 20) -> Table:
        """Return a Rich Table of recent trades for CLI display."""
        trades = self.get_trades(symbol=symbol, limit=limit)

        title = f"Trade Journal — {symbol}" if symbol else "Trade Journal"
        table = Table(title=title, show_header=True, header_style="bold cyan")
        table.add_column("平仓时间", style="dim", min_width=16)
        table.add_column("交易对", style="cyan")
        table.add_column("方向", justify="center")
        table.add_column("开仓价", justify="right")
        table.add_column("平仓价", justify="right")
        table.add_column("数量", justify="right")
        table.add_column("杠杆", justify="right")
        table.add_column("实现P&L", justify="right")
        table.add_column("P&L%", justify="right")
        table.add_column("持仓", justify="right")
        table.add_column("备注", max_width=30)

        for t in trades:
            color = "green" if t.is_win else "red"
            dir_label = "[green]多[/green]" if t.direction == "LONG" else "[red]空[/red]"
            hold = f"{t.duration_minutes:.0f}m" if t.duration_minutes < 1440 else f"{t.duration_minutes/1440:.1f}d"
            table.add_row(
                t.close_time.strftime("%m-%d %H:%M"),
                t.symbol,
                dir_label,
                f"{t.entry_price:.6g}",
                f"{t.exit_price:.6g}",
                f"{t.quantity:.0f}",
                f"{t.leverage:.0f}x",
                f"[{color}]{t.realized_pnl:+,.2f}[/{color}]",
                f"[{color}]{t.pnl_pct:+.1f}%[/{color}]",
                hold,
                t.notes[:30] if t.notes else "—",
            )

        return table
