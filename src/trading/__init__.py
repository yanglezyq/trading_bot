"""AI-powered cryptocurrency trading assistant backed by Obsidian vault."""

__version__ = "0.1.0"
__author__ = "Trading Bot"

from .core import AppConfig, TradingLogger, VaultReader, load_config
from .exchange import AccountManager, BinanceClient, OrderManager, PositionManager

__all__ = [
    "AppConfig",
    "load_config",
    "TradingLogger",
    "VaultReader",
    "BinanceClient",
    "AccountManager",
    "PositionManager",
    "OrderManager",
]
