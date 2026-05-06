"""Order management: place, cancel, and query futures orders."""

from dataclasses import dataclass
from typing import Optional

from rich.console import Console

from .client import BinanceClient

console = Console()


@dataclass
class Order:
    """Order information."""

    order_id: int
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float
    status: str
    filled_qty: float = 0.0
    avg_price: float = 0.0


class OrderManager:
    """Manage trading orders."""

    def __init__(self, binance_client: BinanceClient):
        """Initialize with a BinanceClient instance."""
        self.client = binance_client

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_position_side(params: dict, reduce_only: bool, position_side: Optional[str]) -> None:
        """Mutate params in-place with correct close/position-side field.

        Binance rule:
        - One-Way Mode (position_side is None): use reduceOnly for close orders.
        - Hedge Mode (position_side="LONG"|"SHORT"): use positionSide, never reduceOnly.
          Binance rejects the combination of positionSide + reduceOnly.
        """
        if position_side:
            params["positionSide"] = position_side
        elif reduce_only:
            params["reduceOnly"] = "true"

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        reduce_only: bool = False,
        position_side: Optional[str] = None,   # fix 3: hedge mode support
        dry_run: Optional[bool] = None,
    ) -> Optional[dict]:
        """Place a futures market order; returns None in dry_run."""
        dry_run = dry_run if dry_run is not None else self.client.dry_run

        if dry_run:
            console.print(
                f"[yellow][DRY_RUN] Market order: {side} {quantity} {symbol} "
                f"(reduce_only={reduce_only}, position_side={position_side})[/yellow]"
            )
            return None

        try:
            params: dict = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": quantity}
            self._apply_position_side(params, reduce_only, position_side)
            return self.client.futures_client.new_order(**params)
        except Exception as e:
            raise RuntimeError(f"Failed to place market order: {e}")

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        reduce_only: bool = False,
        position_side: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> Optional[dict]:
        """Place a futures limit order (GTC); returns None in dry_run."""
        dry_run = dry_run if dry_run is not None else self.client.dry_run

        if dry_run:
            console.print(
                f"[yellow][DRY_RUN] Limit order: {side} {quantity} {symbol} @ {price} "
                f"(reduce_only={reduce_only}, position_side={position_side})[/yellow]"
            )
            return None

        try:
            params: dict = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": quantity,
                "price": price,
            }
            self._apply_position_side(params, reduce_only, position_side)
            return self.client.futures_client.new_order(**params)
        except Exception as e:
            raise RuntimeError(f"Failed to place limit order: {e}")

    def place_stop_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        reduce_only: bool = True,
        position_side: Optional[str] = None,   # fix 3
        dry_run: Optional[bool] = None,
    ) -> Optional[dict]:
        """Place a STOP_MARKET order; returns None in dry_run."""
        dry_run = dry_run if dry_run is not None else self.client.dry_run

        if dry_run:
            console.print(
                f"[yellow][DRY_RUN] Stop market: {side} {quantity} {symbol} "
                f"(stop @ {stop_price}, position_side={position_side})[/yellow]"
            )
            return None

        try:
            params: dict = {
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "quantity": quantity,
                "stopPrice": stop_price,
            }
            self._apply_position_side(params, reduce_only, position_side)
            return self.client.futures_client.new_order(**params)
        except Exception as e:
            raise RuntimeError(f"Failed to place stop market order: {e}")

    def place_take_profit_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        reduce_only: bool = True,
        position_side: Optional[str] = None,   # fix 3
        dry_run: Optional[bool] = None,
    ) -> Optional[dict]:
        """Place a TAKE_PROFIT_MARKET order; returns None in dry_run."""
        dry_run = dry_run if dry_run is not None else self.client.dry_run

        if dry_run:
            console.print(
                f"[yellow][DRY_RUN] Take profit: {side} {quantity} {symbol} "
                f"(tp @ {stop_price}, position_side={position_side})[/yellow]"
            )
            return None

        try:
            params: dict = {
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "quantity": quantity,
                "stopPrice": stop_price,
            }
            self._apply_position_side(params, reduce_only, position_side)
            return self.client.futures_client.new_order(**params)
        except Exception as e:
            raise RuntimeError(f"Failed to place take profit order: {e}")

    def cancel_order(
        self,
        symbol: str,
        order_id: int,
        dry_run: Optional[bool] = None,
    ) -> Optional[dict]:
        """Cancel an open order by ID; returns None in dry_run."""
        dry_run = dry_run if dry_run is not None else self.client.dry_run

        if dry_run:
            console.print(f"[yellow][DRY_RUN] Cancel order: {symbol} #{order_id}[/yellow]")
            return None

        try:
            return self.client.futures_client.cancel_order(symbol=symbol, orderId=order_id)
        except Exception as e:
            raise RuntimeError(f"Failed to cancel order: {e}")

    def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """Return all open orders, optionally filtered by symbol; returns empty list in dry_run."""
        if self.client.dry_run:
            return []

        try:
            raw = (
                self.client.futures_client.get_open_orders(symbol=symbol)
                if symbol
                else self.client.futures_client.get_open_orders()
            )
            return [
                Order(
                    order_id=int(o["orderId"]),
                    symbol=o["symbol"],
                    side=o["side"],
                    order_type=o["type"],
                    quantity=float(o["origQty"]),
                    price=float(o.get("price", 0)),
                    status=o["status"],
                    filled_qty=float(o["executedQty"]),
                    avg_price=float(o.get("avgPrice", 0)),
                )
                for o in raw
            ]
        except Exception as e:
            raise RuntimeError(f"Failed to get open orders: {e}")
