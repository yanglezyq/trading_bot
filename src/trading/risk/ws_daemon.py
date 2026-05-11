"""Real-time WebSocket risk monitoring daemon for open positions."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import websocket
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from ..core.config import AppConfig
from ..notification import FeishuWebhook


@dataclass
class WatchTarget:
    symbol: str
    direction: str          # "LONG" | "SHORT" | "SPOT"
    entry_price: float
    quantity: float = 0.0
    invalidation_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None


@dataclass
class SymbolState:
    symbol: str
    mark_price: float = 0.0
    funding_rate: float = 0.0
    next_funding_time: int = 0
    last_updated: Optional[datetime] = None
    alerts: list[str] = field(default_factory=list)


class RiskDaemon:
    """Stream Binance UM Futures mark prices via WebSocket and alert on risk breaches."""

    _WS_LIVE = "wss://fstream.binance.com"
    _WS_TEST = "wss://stream.binancefuture.com"

    def __init__(self, config: AppConfig, targets: list[WatchTarget]) -> None:
        self.config = config
        self.targets: dict[str, WatchTarget] = {t.symbol.upper(): t for t in targets}
        self.state: dict[str, SymbolState] = {
            sym: SymbolState(symbol=sym) for sym in self.targets
        }
        self._lock = threading.Lock()
        self._running = False
        self._ws: Optional[websocket.WebSocketApp] = None
        self.console = Console()
        # Feishu notification (fire-and-forget)
        noti = config.notification
        self._feishu: Optional[FeishuWebhook] = None
        if noti.enabled and noti.on_risk_alert and noti.feishu_webhook_url:
            self._feishu = FeishuWebhook(noti.feishu_webhook_url)

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        base = self._WS_TEST if self.config.binance.futures_testnet else self._WS_LIVE
        streams = "/".join(f"{sym.lower()}@markPrice@1s" for sym in self.targets)
        if len(self.targets) == 1:
            return f"{base}/ws/{streams}"
        return f"{base}/stream?streams={streams}"

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self.console.print(f"[green]WebSocket connected ({len(self.targets)} stream(s))[/green]")

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        self.console.print(f"[red]WebSocket error: {error}[/red]")

    def _on_close(
        self, ws: websocket.WebSocketApp, close_status_code: Optional[int], close_msg: Optional[str]
    ) -> None:
        if self._running:
            self.console.print("[yellow]WebSocket disconnected — reconnecting in 3s…[/yellow]")
            time.sleep(3)
            self._start_ws()

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        try:
            msg = json.loads(raw)
            # Combined stream wraps payload: {"stream": "...", "data": {...}}
            data: dict = msg.get("data", msg)
            if data.get("e") != "markPriceUpdate":
                return
            symbol = str(data["s"]).upper()
            mark_price = float(data["p"])
            funding_rate = float(data.get("r", 0))
            next_funding_time = int(data.get("T", 0))

            with self._lock:
                if symbol in self.state:
                    st = self.state[symbol]
                    st.mark_price = mark_price
                    st.funding_rate = funding_rate
                    st.next_funding_time = next_funding_time
                    st.last_updated = datetime.now()
                    st.alerts = self._check_alerts(symbol, mark_price, funding_rate)
                    # Push alerts to Feishu (dedup handled inside)
                    if st.alerts and self._feishu:
                        target = self.targets.get(symbol)
                        self._feishu.notify_risk_alert(
                            symbol=symbol,
                            alerts=st.alerts,
                            direction=target.direction if target else "",
                            mark_price=mark_price,
                            funding_rate=funding_rate,
                        )
        except Exception:
            pass

    def _start_ws(self) -> None:
        url = self._ws_url()
        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        t = threading.Thread(target=self._ws.run_forever, kwargs={"ping_interval": 20}, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Alert logic
    # ------------------------------------------------------------------

    def _check_alerts(self, symbol: str, mark_price: float, funding_rate: float) -> list[str]:
        target = self.targets.get(symbol)
        if not target:
            return []
        alerts: list[str] = []

        # Invalidation breach
        if target.invalidation_price is not None:
            if target.direction == "LONG" and mark_price <= target.invalidation_price:
                alerts.append(
                    f"INVALIDATION HIT {mark_price:.4f} <= {target.invalidation_price:.4f}"
                )
            elif target.direction == "SHORT" and mark_price >= target.invalidation_price:
                alerts.append(
                    f"INVALIDATION HIT {mark_price:.4f} >= {target.invalidation_price:.4f}"
                )

        # Stop-loss proximity: warn when >50% of the way to stop
        if target.stop_loss_price is not None and target.entry_price > 0:
            dist_total = abs(target.entry_price - target.stop_loss_price)
            dist_to_stop = abs(mark_price - target.stop_loss_price)
            if dist_total > 0 and dist_to_stop / dist_total < 0.5:
                alerts.append(
                    f"NEAR STOP {mark_price:.4f} → stop {target.stop_loss_price:.4f}"
                )

        # Take-profit proximity: notify when >80% of the way to TP
        if target.take_profit_price is not None and target.entry_price > 0:
            dist_total = abs(target.take_profit_price - target.entry_price)
            dist_to_tp = abs(target.take_profit_price - mark_price)
            if dist_total > 0 and dist_to_tp / dist_total < 0.2:
                alerts.append(
                    f"NEAR TP {mark_price:.4f} → tp {target.take_profit_price:.4f}"
                )

        # Funding crowding
        max_funding = float(self.config.risk.max_abs_funding_rate)
        if funding_rate >= max_funding and target.direction == "LONG":
            alerts.append(f"FUNDING CROWDED LONG {funding_rate:.4f}")
        elif funding_rate <= -max_funding and target.direction == "SHORT":
            alerts.append(f"FUNDING CROWDED SHORT {funding_rate:.4f}")

        return alerts

    # ------------------------------------------------------------------
    # Rich display
    # ------------------------------------------------------------------

    def _build_table(self) -> Table:
        ts = datetime.now().strftime("%H:%M:%S")
        table = Table(
            title=f"Risk Daemon — Live Monitor  [{ts}]",
            header_style="bold cyan",
            show_lines=True,
        )
        table.add_column("Symbol", style="cyan", min_width=10)
        table.add_column("Dir", min_width=6)
        table.add_column("Entry", justify="right", min_width=10)
        table.add_column("Mark Price", justify="right", min_width=12)
        table.add_column("PnL%", justify="right", min_width=8)
        table.add_column("Invalidation", justify="right", min_width=12)
        table.add_column("Stop", justify="right", min_width=10)
        table.add_column("TP", justify="right", min_width=10)
        table.add_column("Funding", justify="right", min_width=8)
        table.add_column("Updated", min_width=8)
        table.add_column("Alerts")

        with self._lock:
            for symbol, state in sorted(self.state.items()):
                target = self.targets[symbol]
                mark = state.mark_price
                entry = target.entry_price

                pnl_pct = 0.0
                if entry > 0 and mark > 0:
                    pnl_pct = (mark - entry) / entry * 100
                    if target.direction == "SHORT":
                        pnl_pct = -pnl_pct

                pnl_color = "green" if pnl_pct >= 0 else "red"
                dir_color = "green" if target.direction == "LONG" else "red"
                alert_str = " | ".join(state.alerts) if state.alerts else "—"
                alert_style = "bold red" if state.alerts else "dim"
                upd = state.last_updated.strftime("%H:%M:%S") if state.last_updated else "…"

                table.add_row(
                    symbol,
                    f"[{dir_color}]{target.direction}[/{dir_color}]",
                    f"{entry:.4f}" if entry else "—",
                    f"{mark:.4f}" if mark else "…",
                    f"[{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]" if mark else "…",
                    f"{target.invalidation_price:.4f}" if target.invalidation_price else "—",
                    f"{target.stop_loss_price:.4f}" if target.stop_loss_price else "—",
                    f"{target.take_profit_price:.4f}" if target.take_profit_price else "—",
                    f"{state.funding_rate:+.4f}" if state.funding_rate else "…",
                    upd,
                    f"[{alert_style}]{alert_str}[/{alert_style}]",
                )
        return table

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block until Ctrl+C. Streams mark prices and displays a live risk dashboard."""
        if not self.targets:
            self.console.print("[yellow]No watch targets. Exiting.[/yellow]")
            return

        self.console.print(
            Panel(
                "\n".join(
                    f"  [cyan]{sym}[/cyan]  {t.direction}  entry={t.entry_price}"
                    + (f"  inv={t.invalidation_price}" if t.invalidation_price else "")
                    + (f"  stop={t.stop_loss_price}" if t.stop_loss_price else "")
                    for sym, t in sorted(self.targets.items())
                ),
                title="Watching positions — press Ctrl+C to stop",
                border_style="cyan",
            )
        )

        self._running = True
        self._start_ws()

        try:
            with Live(self._build_table(), refresh_per_second=2, console=self.console) as live:
                while self._running:
                    live.update(self._build_table())
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            if self._ws:
                self._ws.close()
            self.console.print("[yellow]Risk daemon stopped.[/yellow]")
