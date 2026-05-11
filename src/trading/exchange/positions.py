"""Position management: query futures and spot positions."""

from dataclasses import dataclass
from typing import Optional

from .client import BinanceClient


@dataclass
class FuturesPosition:
    """Futures contract position."""

    symbol: str
    amount: float
    price: float
    leverage: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    margin_type: str
    liquidation_price: Optional[float] = None

    @property
    def is_long(self) -> bool:
        """True if position is long (positive amount)."""
        return self.amount > 0

    @property
    def is_short(self) -> bool:
        """True if position is short (negative amount)."""
        return self.amount < 0

    @property
    def notional_value(self) -> float:
        """Position notional value in USDT (abs(amount * price))."""
        return abs(self.amount * self.price)

    @property
    def liquidation_distance_pct(self) -> Optional[float]:
        """Percentage distance from entry price to liquidation price.

        Returns None if liquidation_price is unavailable or invalid.
        """
        if not self.liquidation_price or self.liquidation_price <= 0 or self.price <= 0:
            return None
        return abs(self.price - self.liquidation_price) / self.price * 100


@dataclass
class SpotPosition:
    """Spot market holding."""

    asset: str
    amount: float
    price: Optional[float] = None


class PositionManager:
    """Manage and query futures and spot positions."""

    def __init__(self, binance_client: BinanceClient):
        """Initialize with a BinanceClient instance."""
        self.client = binance_client

    def get_futures_positions(self) -> list[FuturesPosition]:
        """Return all open futures positions; returns mock data in dry_run."""
        if self.client.dry_run:
            return [
                FuturesPosition(
                    symbol="CHZUSDT",
                    amount=128000,
                    price=0.050,
                    leverage=30,
                    unrealized_pnl=5000,
                    unrealized_pnl_pct=120.76,
                    margin_type="isolated",
                ),
                FuturesPosition(
                    symbol="ASTERUSDT",
                    amount=-700,
                    price=0.058,
                    leverage=30,
                    unrealized_pnl=-1327,
                    unrealized_pnl_pct=-327.78,
                    margin_type="isolated",
                ),
            ]

        try:
            positions = []
            account = self.client.futures_client.account()

            for pos in account.get("positions", []):
                if float(pos["positionAmt"]) != 0:
                    symbol = pos["symbol"]
                    amount = float(pos["positionAmt"])
                    price = float(pos["entryPrice"])
                    unrealized_pnl = float(pos["unrealizedProfit"])
                    leverage = float(pos.get("leverage", 1))

                    notional = abs(amount * price)
                    unrealized_pnl_pct = (unrealized_pnl / notional * 100) if notional > 0 else 0.0

                    liq_raw = float(pos.get("liquidationPrice", 0))
                    positions.append(
                        FuturesPosition(
                            symbol=symbol,
                            amount=amount,
                            price=price,
                            leverage=leverage,
                            unrealized_pnl=unrealized_pnl,
                            unrealized_pnl_pct=unrealized_pnl_pct,
                            margin_type=pos.get("marginType", "crossed"),
                            liquidation_price=liq_raw if liq_raw > 0 else None,
                        )
                    )

            return positions
        except Exception as e:
            raise RuntimeError(f"Failed to get futures positions: {e}")

    def get_spot_holdings(self) -> list[SpotPosition]:
        """Return all spot holdings with non-zero balance."""
        if self.client.dry_run:
            return [
                SpotPosition(asset="USDT", amount=5000),
                SpotPosition(asset="BTC", amount=0.5),
            ]

        try:
            holdings = []
            account = self.client.spot_client.account()

            for balance in account.get("balances", []):
                total = float(balance["free"]) + float(balance["locked"])
                if total > 0:
                    holdings.append(SpotPosition(asset=balance["asset"], amount=total))

            return holdings
        except Exception as e:
            raise RuntimeError(f"Failed to get spot holdings: {e}")

    def get_all_positions(self) -> dict:
        """Return both futures and spot positions keyed by 'futures' and 'spot'."""
        return {
            "futures": self.get_futures_positions(),
            "spot": self.get_spot_holdings(),
        }

    def get_position(self, symbol: str) -> Optional[FuturesPosition]:
        """Return a specific futures position by symbol, or None if not found."""
        for pos in self.get_futures_positions():
            if pos.symbol == symbol:
                return pos
        return None

    def get_total_notional_value(self) -> float:
        """Return total notional value of all open futures positions in USDT."""
        return sum(p.notional_value for p in self.get_futures_positions())

    def get_positions_summary(self) -> dict:
        """Return count and aggregate stats for current positions."""
        futures_pos = self.get_futures_positions()
        spot_holdings = self.get_spot_holdings()

        return {
            "futures_count": len(futures_pos),
            "futures_long_count": sum(1 for p in futures_pos if p.is_long),
            "futures_short_count": sum(1 for p in futures_pos if p.is_short),
            "total_notional_usdt": sum(p.notional_value for p in futures_pos),
            "total_unrealized_pnl": sum(p.unrealized_pnl for p in futures_pos),
            "spot_assets_count": len(spot_holdings),
        }
