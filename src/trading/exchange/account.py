"""Account management: balance queries and P&L calculations."""

from dataclasses import dataclass
from typing import Optional

from .client import BinanceClient


@dataclass
class Balance:
    """Spot asset balance."""

    asset: str
    free: float
    locked: float

    @property
    def total(self) -> float:
        """Total balance (free + locked)."""
        return self.free + self.locked


@dataclass
class FuturesBalance:
    """Futures account balance."""

    asset: str
    free: float
    locked: float
    available: float

    @property
    def total(self) -> float:
        """Total balance (free + locked)."""
        return self.free + self.locked


@dataclass
class AccountPnL:
    """Account-level profit and loss."""

    total_balance: float
    unrealized_pnl: float
    realized_pnl: float

    @property
    def pnl_pct(self) -> float:
        """P&L as a percentage of approximate initial balance."""
        if self.total_balance == 0:
            return 0.0
        initial = self.total_balance - self.unrealized_pnl
        if initial <= 0:
            return 0.0
        return (self.unrealized_pnl / initial) * 100

    @property
    def drawdown_pct(self) -> float:
        """Drawdown percentage (negative if loss)."""
        return self.pnl_pct


class AccountManager:
    """Manage account balances and P&L."""

    def __init__(self, binance_client: BinanceClient):
        """Initialize with a BinanceClient instance."""
        self.client = binance_client

    def get_futures_balance(self) -> Optional[float]:
        """Return USDT wallet balance from futures account, or None if not found."""
        if self.client.dry_run:
            return 10000.0

        try:
            account = self.client.futures_client.account()
            for asset in account.get("assets", []):
                if asset["asset"] == "USDT":
                    return float(asset["walletBalance"])
            return None
        except Exception as e:
            raise RuntimeError(f"Failed to get futures balance: {e}")

    def get_spot_balance(self, asset: str = "USDT") -> Optional[float]:
        """Return total spot balance for the given asset, or None if not found."""
        if self.client.dry_run:
            return 5000.0

        try:
            account = self.client.spot_client.account()
            for balance in account.get("balances", []):
                if balance["asset"] == asset:
                    return float(balance["free"]) + float(balance["locked"])
            return None
        except Exception as e:
            raise RuntimeError(f"Failed to get spot balance: {e}")

    def get_futures_pnl(self) -> AccountPnL:
        """Return futures account P&L summary."""
        if self.client.dry_run:
            return AccountPnL(
                total_balance=10000.0,
                unrealized_pnl=500.0,
                realized_pnl=0.0,
            )

        try:
            account = self.client.futures_client.account()

            total_balance = 0.0
            for asset in account.get("assets", []):
                if asset["asset"] == "USDT":
                    total_balance = float(asset["walletBalance"])
                    break

            unrealized_pnl = float(account.get("totalUnrealizedProfit", 0))
            # realized_pnl requires trade history API; account summary only provides unrealized
            realized_pnl = 0.0

            return AccountPnL(
                total_balance=total_balance,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=realized_pnl,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to get futures P&L: {e}")

    def validate_credentials(self) -> bool:
        """Return True if API credentials are valid; always True in dry_run."""
        if self.client.dry_run:
            return True
        try:
            _ = self.client.futures_client.account()  # type: ignore[union-attr]
            return True
        except Exception:
            return False

    def get_account_summary(self) -> dict:
        """Return account balance, P&L, and drawdown as a flat dict."""
        try:
            futures_balance = self.get_futures_balance()
            spot_balance = self.get_spot_balance()
            pnl = self.get_futures_pnl()

            return {
                "futures_balance_usdt": futures_balance,
                "spot_balance_usdt": spot_balance,
                "total_balance_usdt": (futures_balance or 0) + (spot_balance or 0),
                "unrealized_pnl": pnl.unrealized_pnl,
                "pnl_pct": pnl.pnl_pct,
                "drawdown_pct": pnl.drawdown_pct,
                "dry_run": self.client.dry_run,
            }
        except Exception as e:
            return {
                "error": str(e),
                "dry_run": self.client.dry_run,
            }
