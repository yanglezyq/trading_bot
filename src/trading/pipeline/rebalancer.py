"""Portfolio rebalancer: compute deviation from target and auto-execute adjustments."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

from rich.console import Console

from ..core.config import AppConfig
from ..exchange.client import BinanceClient
from ..exchange.market_data import MarketDataManager
from ..exchange.orders import OrderManager
from ..exchange.positions import PositionManager
from ..risk.portfolio import PortfolioManager

console = Console()


# ------------------------------------------------------------------
# Data models
# ------------------------------------------------------------------

@dataclass
class RebalanceOrder:
    """A single rebalance instruction for one position."""

    symbol: str
    direction: str           # "LONG" | "SHORT"
    action: str              # "reduce" | "increase" | "close"
    current_notional: float
    target_notional: float
    delta_notional: float    # negative = reduce
    delta_quantity: float    # abs qty to trade
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------
# Step-size rounding (reused from runner.py)
# ------------------------------------------------------------------

def _step_round(quantity: float, step_size: float) -> float:
    """Floor-round quantity to the nearest valid step_size increment."""
    if step_size <= 0:
        return round(quantity, 3)
    floored = math.floor(quantity / step_size) * step_size
    step_str = f"{step_size:.10f}".rstrip("0")
    precision = len(step_str.split(".")[1]) if "." in step_str else 0
    return round(floored, precision)


# ------------------------------------------------------------------
# Rebalancer
# ------------------------------------------------------------------

class Rebalancer:
    """Compute and execute portfolio rebalance orders."""

    def __init__(self, config: AppConfig, live_client: BinanceClient):
        self.config = config
        self.live_client = live_client
        self._portfolio_mgr = PortfolioManager(config)

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def compute_rebalance_plan(
        self,
        account_summary: dict,
        positions: list[dict],
        market_snapshots: dict[str, dict] | None = None,
    ) -> list[RebalanceOrder]:
        """Compute rebalance orders by comparing current vs target exposure.

        Args:
            account_summary: Account info dict with futures_balance_usdt.
            positions: List of position dicts (symbol, direction, amount, price, leverage).
            market_snapshots: Optional per-symbol market snapshot for budget calculation.

        Returns:
            List of RebalanceOrder sorted by |delta_notional| descending.
        """
        if not positions:
            return []

        rebalance_cfg = self.config.rebalance
        balance = float(account_summary.get("futures_balance_usdt", 0) or 0)
        if balance <= 0:
            return []

        # Build portfolio snapshot to check gross exposure
        snapshot = self._portfolio_mgr.build_snapshot(account_summary, positions)
        gross_pct = snapshot.gross_exposure_pct

        orders: list[RebalanceOrder] = []

        for pos in positions:
            symbol = str(pos.get("symbol", "")).upper()
            direction = str(pos.get("direction", "")).upper()
            amount = float(pos.get("amount", 0) or 0)
            price = float(pos.get("price", 0) or 0)
            leverage = float(pos.get("leverage", 1) or 1)

            if amount == 0 or price <= 0:
                continue

            current_notional = abs(amount * price)

            # Get market snapshot for this symbol (fallback to minimal)
            ms = (market_snapshots or {}).get(symbol, {})
            if not ms:
                ms = {"asset_tier": "liquid_alt", "narrative_tag": "unknown"}

            # Compute recommended budget
            budget = self._portfolio_mgr.recommend_budget(
                symbol=symbol,
                market_snapshot=ms,
                positions=positions,
                account_summary=account_summary,
            )

            # Target notional = balance * recommended_max_size_pct/100 * leverage
            target_size_pct = budget.recommended_max_size_pct
            target_notional = balance * (target_size_pct / 100.0) * leverage

            # If gross exposure exceeds target, proportionally scale down
            if gross_pct > rebalance_cfg.target_gross_exposure_pct:
                scale = rebalance_cfg.target_gross_exposure_pct / gross_pct
                target_notional *= scale

            delta = target_notional - current_notional
            deviation_pct = abs(delta) / current_notional * 100 if current_notional > 0 else 0.0

            # Only act if deviation exceeds threshold
            if deviation_pct < rebalance_cfg.deviation_threshold_pct:
                continue

            # Apply max_single_reduce_pct safety cap
            if delta < 0:
                max_reduce_notional = current_notional * (rebalance_cfg.max_single_reduce_pct / 100.0)
                if abs(delta) > max_reduce_notional:
                    delta = -max_reduce_notional

            # Determine action
            if delta < 0 and abs(delta) >= current_notional * 0.95:
                action = "close"
                delta_qty = abs(amount)
            else:
                action = "reduce" if delta < 0 else "increase"
                delta_qty = abs(delta) / price if price > 0 else 0.0

            reason_parts = [f"deviation {deviation_pct:.1f}%"]
            if gross_pct > rebalance_cfg.target_gross_exposure_pct:
                reason_parts.append(f"gross {gross_pct:.1f}% > target {rebalance_cfg.target_gross_exposure_pct:.1f}%")
            if budget.warnings:
                reason_parts.append(budget.warnings[0][:60])

            orders.append(RebalanceOrder(
                symbol=symbol,
                direction=direction,
                action=action,
                current_notional=round(current_notional, 2),
                target_notional=round(target_notional, 2),
                delta_notional=round(delta, 2),
                delta_quantity=round(delta_qty, 6),
                reason="; ".join(reason_parts),
            ))

        # Sort by largest absolute deviation first
        orders.sort(key=lambda o: abs(o.delta_notional), reverse=True)
        return orders

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute_rebalance(
        self,
        orders: list[RebalanceOrder],
        dry_run: bool = False,
    ) -> list[dict]:
        """Execute rebalance orders via market orders.

        Args:
            orders: List of RebalanceOrder to execute.
            dry_run: If True, only print orders without placing them.

        Returns:
            List of result dicts with symbol, action, status, message.
        """
        if not orders:
            return []

        order_mgr = OrderManager(self.live_client)
        results: list[dict] = []

        # Detect hedge mode once
        is_hedge = self._detect_hedge_mode()

        for rb_order in orders:
            result = self._execute_single(order_mgr, rb_order, is_hedge, dry_run)
            results.append(result)

        return results

    def _execute_single(
        self,
        order_mgr: OrderManager,
        rb_order: RebalanceOrder,
        is_hedge: bool,
        dry_run: bool,
    ) -> dict:
        """Execute a single rebalance order."""
        symbol = rb_order.symbol
        direction = rb_order.direction

        try:
            # Get step size for quantity rounding
            step_size = self.live_client.futures_client.get_symbol_lot_size(symbol)
            quantity = _step_round(rb_order.delta_quantity, step_size)

            if quantity <= 0:
                return {
                    "symbol": symbol,
                    "action": rb_order.action,
                    "status": "skipped",
                    "message": f"Quantity too small after rounding (raw={rb_order.delta_quantity})",
                }

            # Determine side
            if rb_order.action in ("reduce", "close"):
                # Reduce long → SELL; reduce short → BUY
                side = "SELL" if direction == "LONG" else "BUY"
                reduce_only = True
            else:
                # Increase long → BUY; increase short → SELL
                side = "BUY" if direction == "LONG" else "SELL"
                reduce_only = False

            # Hedge mode: use positionSide
            position_side: Optional[str] = direction if is_hedge else None
            effective_reduce_only = reduce_only and not is_hedge

            if dry_run:
                msg = f"[DRY_RUN] {rb_order.action} {side} {quantity} {symbol}"
                console.print(f"[yellow]{msg}[/yellow]")
                return {
                    "symbol": symbol,
                    "action": rb_order.action,
                    "status": "dry_run",
                    "message": msg,
                }

            resp = order_mgr.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                reduce_only=effective_reduce_only,
                position_side=position_side,
                dry_run=False,
            )

            order_id = str(resp.get("orderId", "")) if resp else ""
            msg = f"{rb_order.action} {side} {quantity} {symbol} (order #{order_id})"
            console.print(f"[green]✓ Rebalance: {msg}[/green]")

            return {
                "symbol": symbol,
                "action": rb_order.action,
                "status": "executed",
                "message": msg,
                "order_id": order_id,
            }

        except Exception as exc:
            msg = f"Failed {rb_order.action} {symbol}: {exc}"
            console.print(f"[red]✗ Rebalance: {msg}[/red]")
            return {
                "symbol": symbol,
                "action": rb_order.action,
                "status": "failed",
                "message": msg,
            }

    def _detect_hedge_mode(self) -> bool:
        """Detect if account is in Hedge Mode."""
        try:
            resp = self.live_client.futures_client.get_position_mode()
            return bool(resp.get("dualSidePosition", False))
        except Exception:
            return False
