"""Minimal USDT-M Futures client for binance-connector 3.x (which dropped um_futures)."""

from typing import Optional
from binance.api import API


class UMFutures(API):
    """USDT-M Perpetual Futures REST client wrapping the /fapi endpoints."""

    def __init__(self, key: Optional[str] = None, secret: Optional[str] = None, base_url: str = "https://fapi.binance.com", **kwargs):
        super().__init__(api_key=key, api_secret=secret, base_url=base_url, **kwargs)

    def time(self) -> dict:
        """GET /fapi/v1/time — server time."""
        return self.query("/fapi/v1/time")

    def exchange_info(self) -> dict:
        """GET /fapi/v1/exchangeInfo — exchange trading rules."""
        return self.query("/fapi/v1/exchangeInfo")

    def ticker_24hr(self, symbol: str, **kwargs) -> dict:
        """GET /fapi/v1/ticker/24hr — rolling 24h stats for one symbol."""
        return self.query("/fapi/v1/ticker/24hr", {"symbol": symbol, **kwargs})

    def open_interest(self, symbol: str, **kwargs) -> dict:
        """GET /fapi/v1/openInterest — open interest for one symbol."""
        return self.query("/fapi/v1/openInterest", {"symbol": symbol, **kwargs})

    def klines(self, symbol: str, interval: str = "1h", limit: int = 168, **kwargs) -> list:
        """GET /fapi/v1/klines — historical candlesticks for one symbol."""
        params = {"symbol": symbol, "interval": interval, "limit": limit, **kwargs}
        return self.query("/fapi/v1/klines", params)

    def account(self, **kwargs) -> dict:
        """GET /fapi/v2/account — signed account balance and positions."""
        return self.sign_request("GET", "/fapi/v2/account", kwargs)

    def new_order(self, **kwargs) -> dict:
        """POST /fapi/v1/order — signed place new order."""
        return self.sign_request("POST", "/fapi/v1/order", kwargs)

    def cancel_order(self, symbol: str, orderId: Optional[int] = None, origClientOrderId: Optional[str] = None, **kwargs) -> dict:
        """DELETE /fapi/v1/order — signed cancel order."""
        params = {"symbol": symbol, "orderId": orderId, "origClientOrderId": origClientOrderId, **kwargs}
        return self.sign_request("DELETE", "/fapi/v1/order", params)

    def get_open_orders(self, symbol: Optional[str] = None, **kwargs) -> list:
        """GET /fapi/v1/openOrders — signed list of open orders."""
        params = {"symbol": symbol, **kwargs}
        return self.sign_request("GET", "/fapi/v1/openOrders", params)

    # ------------------------------------------------------------------ #
    # Fix 1: methods that were called in runner.py but never existed here  #
    # ------------------------------------------------------------------ #

    def mark_price(self, symbol: str, **kwargs) -> dict:
        """GET /fapi/v1/premiumIndex — mark price and funding rate for one symbol."""
        return self.query("/fapi/v1/premiumIndex", {"symbol": symbol, **kwargs})

    def change_leverage(self, symbol: str, leverage: int, **kwargs) -> dict:
        """POST /fapi/v1/leverage — change initial leverage for a symbol (signed)."""
        params = {"symbol": symbol, "leverage": leverage, **kwargs}
        return self.sign_request("POST", "/fapi/v1/leverage", params)

    def get_position_mode(self, **kwargs) -> dict:
        """GET /fapi/v1/positionSide/dual — get current position mode (signed).

        Returns {"dualSidePosition": true} for Hedge Mode, false for One-Way Mode.
        """
        return self.sign_request("GET", "/fapi/v1/positionSide/dual", kwargs)

    def get_symbol_lot_size(self, symbol: str) -> float:
        """Return the LOT_SIZE stepSize for a symbol from /fapi/v1/exchangeInfo.

        Returns 0.001 as a conservative fallback when the symbol is not found
        or the API call fails — callers must be aware this may truncate too
        aggressively or too loosely for some symbols.
        """
        try:
            info = self.query("/fapi/v1/exchangeInfo")
            for sym in info.get("symbols", []):
                if sym.get("symbol") == symbol:
                    for f in sym.get("filters", []):
                        if f.get("filterType") == "LOT_SIZE":
                            return float(f["stepSize"])
        except Exception:
            pass
        return 0.001
