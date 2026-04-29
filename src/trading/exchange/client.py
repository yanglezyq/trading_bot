"""Binance API client: Spot + UMFutures with dry_run support."""

import time
from typing import Any, Optional

from binance.spot import Spot as BinanceSpot

from .um_futures import UMFutures as BinanceFutures

from ..core.config import BinanceConfig


class BinanceClient:
    """Unified Binance Spot + UMFutures client with testnet and dry_run modes."""

    def __init__(self, config: BinanceConfig, dry_run: bool = False):
        """Initialize Spot and UMFutures clients; skip credential check in dry_run mode."""
        self.config = config
        self.dry_run = dry_run
        self.spot_client: Optional[BinanceSpot] = None
        self.futures_client: Optional[BinanceFutures] = None

        if dry_run:
            return

        if not config.api_key or not config.api_secret:
            raise ValueError("Binance API credentials must be configured in .env")

        futures_base_url = (
            "https://testnet.binancefuture.com" if config.futures_testnet else "https://fapi.binance.com"
        )
        spot_base_url = (
            "https://testnet.binance.vision" if config.spot_testnet else "https://api.binance.com"
        )

        self.spot_client = BinanceSpot(
            api_key=config.api_key,
            api_secret=config.api_secret,
            base_url=spot_base_url,
        )

        self.futures_client = BinanceFutures(
            key=config.api_key,
            secret=config.api_secret,
            base_url=futures_base_url,
        )

    def get_server_time(self) -> int:
        """Return Binance server timestamp in milliseconds."""
        if self.dry_run:
            return int(time.time() * 1000)

        try:
            response = self.futures_client.time()
            return response["serverTime"]
        except Exception as e:
            raise RuntimeError(f"Failed to get server time: {e}")

    def _preview_request(self, method: str, params: dict[str, Any]) -> str:
        """Format a dry_run preview string for a Binance API call."""
        param_str = ", ".join(f"{k}={v}" for k, v in params.items())
        return f"[DRY_RUN] {method}({param_str})"

    def get_api_status(self) -> dict[str, Any]:
        """Return exchange info dict for connectivity check; returns preview dict in dry_run."""
        if self.dry_run:
            return {"dry_run": True, "status": "preview_mode"}

        try:
            exchange_info = self.futures_client.exchange_info()
            return {
                "timezone": exchange_info.get("timezone"),
                "serverTime": exchange_info.get("serverTime"),
                "status": "ok",
            }
        except Exception as e:
            raise RuntimeError(f"Failed to get API status: {e}")
