"""Equity curve protection: pause or reduce size when balance drops below its own EMA."""

from __future__ import annotations

from ..core.config import AppConfig
from ..pipeline.persistence import TradeRunDB


class EquityCurveTracker:
    """Track equity curve and determine if trading should be paused or reduced."""

    def __init__(self, config: AppConfig, db: TradeRunDB):
        self.config = config
        self.db = db

    def record_balance(self, balance: float) -> None:
        """Save a new balance data point."""
        self.db.save_equity_snapshot(balance)

    def compute_ema(self, history: list[float], period: int) -> float | None:
        """Compute EMA of the given history. Returns None if insufficient data."""
        if len(history) < period:
            return None
        multiplier = 2.0 / (period + 1)
        ema = sum(history[:period]) / period
        for val in history[period:]:
            ema = (val - ema) * multiplier + ema
        return ema

    def get_protection_status(self, current_balance: float) -> dict:
        """Return protection status dict with keys: mode, ema, factor.

        mode: 'normal' | 'reduced' | 'paused'
        ema: float or None
        factor: size multiplier (1.0 for normal, 0.5 for reduced, 0.0 for paused)
        """
        if not self.config.risk.equity_protection_enabled:
            return {"mode": "normal", "ema": None, "factor": 1.0}

        period = self.config.risk.equity_ema_period
        history = self.db.get_equity_history(limit=period + 10)

        ema = self.compute_ema(history, period)
        if ema is None:
            return {"mode": "normal", "ema": None, "factor": 1.0}

        pause_threshold = ema * self.config.risk.equity_pause_factor
        reduce_factor = self.config.risk.equity_reduce_factor

        if current_balance < pause_threshold:
            return {"mode": "paused", "ema": ema, "factor": 0.0}
        elif current_balance < ema:
            return {"mode": "reduced", "ema": ema, "factor": reduce_factor}
        else:
            return {"mode": "normal", "ema": ema, "factor": 1.0}
