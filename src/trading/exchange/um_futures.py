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
