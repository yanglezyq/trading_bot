"""Historical kline replay for saved trade runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from math import sqrt
from statistics import pstdev
from typing import Optional

from binance.spot import Spot as BinanceSpot

from ..core.config import AppConfig
from ..exchange.um_futures import UMFutures
from .persistence import TradeRunDB


@dataclass
class BacktestOutcome:
    """Outcome of replaying one saved trade setup across historical klines."""

    run_id: int
    symbol: str
    action: str
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    holding_hours: int
    entry_trend_bias: str
    entry_volatility_regime: str
    entry_momentum_regime: str
    entry_distance_to_ema21_pct: float
    btc_market_regime: str
    execution_template: str
    narrative_tag: str

    def to_dict(self) -> dict:
        return asdict(self)


class BacktestRunner:
    """Replay saved trade runs on historical Binance klines."""

    def __init__(self, config: AppConfig):
        self.config = config
        futures_base_url = (
            "https://testnet.binancefuture.com"
            if config.binance.futures_testnet
            else "https://fapi.binance.com"
        )
        spot_base_url = (
            "https://testnet.binance.vision"
            if config.binance.spot_testnet
            else "https://api.binance.com"
        )
        self.db = TradeRunDB(config.logging.sqlite_db)
        self.futures = UMFutures(base_url=futures_base_url, timeout=10)
        self.spot = BinanceSpot(base_url=spot_base_url, timeout=10)
        self._replay_cache: dict[tuple[int, int], Optional[BacktestOutcome]] = {}
        self._lookback_cache: dict[tuple[str, str, int], list[list]] = {}

    @staticmethod
    def _parse_created_at(raw: str) -> datetime:
        """Parse sqlite timestamp into timezone-aware datetime."""
        try:
            dt = datetime.fromisoformat(str(raw))
        except ValueError:
            dt = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def _trade_direction(action: str) -> str:
        """Return long/short semantic direction from final action."""
        if action in {"open_long", "buy_spot"}:
            return "long"
        if action == "open_short":
            return "short"
        return "flat"

    def _fetch_klines(
        self,
        symbol: str,
        market: str,
        start: datetime,
        hours: int,
    ) -> list[list]:
        """Fetch 1h klines for the replay window."""
        params = {
            "symbol": symbol,
            "interval": "1h",
            "limit": max(2, hours + 2),
            "startTime": int(start.timestamp() * 1000),
        }
        if market == "spot":
            return self.spot.klines(**params)
        return self.futures.klines(**params)

    def _fetch_lookback_klines(
        self,
        symbol: str,
        market: str,
        end: datetime,
        bars: int = 168,
    ) -> list[list]:
        """Fetch lookback klines ending at the entry timestamp."""
        end_ms = int(end.timestamp() * 1000)
        cache_key = (symbol, market, end_ms // (60 * 60 * 1000), bars)
        if cache_key in self._lookback_cache:
            return self._lookback_cache[cache_key]
        params = {
            "symbol": symbol,
            "interval": "1h",
            "limit": max(2, bars),
            "endTime": end_ms,
        }
        if market == "spot":
            klines = self.spot.klines(**params)
        else:
            klines = self.futures.klines(**params)
        self._lookback_cache[cache_key] = klines
        return klines

    @staticmethod
    def _calc_pct_change(start: float, end: float) -> float:
        if start == 0:
            return 0.0
        return (end - start) / start * 100.0

    @staticmethod
    def _realized_vol_pct(closes: list[float]) -> float:
        if len(closes) < 2:
            return 0.0
        returns = []
        for prev, curr in zip(closes[:-1], closes[1:]):
            if prev <= 0 or curr <= 0:
                continue
            returns.append((curr - prev) / prev)
        if len(returns) < 2:
            return 0.0
        return pstdev(returns) * sqrt(24) * 100.0

    @staticmethod
    def _ema(closes: list[float], period: int) -> float:
        if not closes:
            return 0.0
        if len(closes) < period:
            return sum(closes) / len(closes)
        multiplier = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for close in closes[period:]:
            ema = (close - ema) * multiplier + ema
        return ema

    @staticmethod
    def _distance_pct(price: float, reference: float) -> float:
        if reference == 0:
            return 0.0
        return (price - reference) / reference * 100.0

    @staticmethod
    def _trend_bias(price: float, ema_21: float, ema_55: float, ema_144: float) -> str:
        if price > ema_21 > ema_55 > ema_144:
            return "bullish"
        if price < ema_21 < ema_55 < ema_144:
            return "bearish"
        return "range"

    @staticmethod
    def _volatility_regime(vol_24h_pct: float) -> str:
        if vol_24h_pct >= 12.0:
            return "extreme"
        if vol_24h_pct >= 8.0:
            return "high"
        if vol_24h_pct >= 4.0:
            return "normal"
        return "low"

    @staticmethod
    def _momentum_regime(change_24h_pct: float, change_7d_pct: float) -> str:
        if change_24h_pct >= 5.0 and change_7d_pct >= 10.0:
            return "strong_up"
        if change_24h_pct <= -5.0 and change_7d_pct <= -10.0:
            return "strong_down"
        if change_24h_pct > 0 and change_7d_pct > 0:
            return "up"
        if change_24h_pct < 0 and change_7d_pct < 0:
            return "down"
        return "mixed"

    def _entry_state(self, symbol: str, market: str, entry_time: datetime, entry_price: float) -> dict:
        """Reconstruct point-in-time market state from lookback klines."""
        klines = self._fetch_lookback_klines(symbol, market, entry_time, bars=168)
        closes = [float(k[4]) for k in klines if len(k) > 4]
        if not closes:
            return {
                "entry_trend_bias": "unknown",
                "entry_volatility_regime": "unknown",
                "entry_momentum_regime": "unknown",
                "entry_distance_to_ema21_pct": 0.0,
            }
        ema_21 = self._ema(closes, 21)
        ema_55 = self._ema(closes, 55)
        ema_144 = self._ema(closes, 144)
        change_24 = self._calc_pct_change(closes[-25], closes[-1]) if len(closes) >= 25 else 0.0
        change_7d = self._calc_pct_change(closes[0], closes[-1]) if len(closes) >= 2 else 0.0
        vol_24 = self._realized_vol_pct(closes[-25:]) if len(closes) >= 25 else 0.0
        return {
            "entry_trend_bias": self._trend_bias(entry_price, ema_21, ema_55, ema_144),
            "entry_volatility_regime": self._volatility_regime(vol_24),
            "entry_momentum_regime": self._momentum_regime(change_24, change_7d),
            "entry_distance_to_ema21_pct": round(self._distance_pct(entry_price, ema_21), 4),
        }

    def _replay_one(self, row: dict, default_hours: int = 48) -> Optional[BacktestOutcome]:
        """Replay a single saved run over historical klines."""
        cache_key = (int(row["id"]), int(default_hours))
        if cache_key in self._replay_cache:
            return self._replay_cache[cache_key]

        plan = self.db._safe_json_loads(row.get("execution_plan_json"))
        result = self.db._safe_json_loads(row.get("execution_result_json"))
        market_snapshot = self.db._safe_json_loads(row.get("market_snapshot_json"))
        if not plan:
            self._replay_cache[cache_key] = None
            return None

        action = str(result.get("final_action", plan.get("action", "")))
        direction = self._trade_direction(action)
        if direction == "flat":
            self._replay_cache[cache_key] = None
            return None

        details = result.get("manual_order_details") or {}
        market = str(details.get("market") or plan.get("preferred_market") or "futures")
        entry_price = float(
            details.get("mark_price")
            or market_snapshot.get("futures_mark_price")
            or market_snapshot.get("spot_price")
            or 0.0
        )
        if entry_price <= 0:
            self._replay_cache[cache_key] = None
            return None

        stop_price = details.get("stop_loss_price")
        tp_price = details.get("take_profit_price")
        if stop_price is not None:
            stop_price = float(stop_price)
        if tp_price is not None:
            tp_price = float(tp_price)

        hours = int(plan.get("thesis_window_hours") or default_hours)
        created_at = self._parse_created_at(str(row.get("created_at")))
        entry_state = self._entry_state(str(row["symbol"]), market, created_at, entry_price)
        klines = self._fetch_klines(str(row["symbol"]), market, created_at, hours)
        if not klines:
            self._replay_cache[cache_key] = None
            return None

        exit_price = entry_price
        exit_reason = "time_exit"
        holding_hours = 0

        for idx, candle in enumerate(klines[1:], start=1):
            high = float(candle[2])
            low = float(candle[3])
            close = float(candle[4])
            holding_hours = idx
            if direction == "long":
                hit_sl = stop_price is not None and low <= stop_price
                hit_tp = tp_price is not None and high >= tp_price
                if hit_sl and hit_tp:
                    exit_price = stop_price if stop_price is not None else close
                    exit_reason = "stop_loss_and_take_profit_same_bar"
                    break
                if hit_sl:
                    exit_price = stop_price if stop_price is not None else close
                    exit_reason = "stop_loss"
                    break
                if hit_tp:
                    exit_price = tp_price if tp_price is not None else close
                    exit_reason = "take_profit"
                    break
            else:
                hit_sl = stop_price is not None and high >= stop_price
                hit_tp = tp_price is not None and low <= tp_price
                if hit_sl and hit_tp:
                    exit_price = stop_price if stop_price is not None else close
                    exit_reason = "stop_loss_and_take_profit_same_bar"
                    break
                if hit_sl:
                    exit_price = stop_price if stop_price is not None else close
                    exit_reason = "stop_loss"
                    break
                if hit_tp:
                    exit_price = tp_price if tp_price is not None else close
                    exit_reason = "take_profit"
                    break
            exit_price = close

        if direction == "long":
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        outcome = BacktestOutcome(
            run_id=int(row["id"]),
            symbol=str(row["symbol"]),
            action=action,
            entry_price=entry_price,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl_pct=round(pnl_pct, 4),
            holding_hours=holding_hours,
            entry_trend_bias=str(entry_state["entry_trend_bias"]),
            entry_volatility_regime=str(entry_state["entry_volatility_regime"]),
            entry_momentum_regime=str(entry_state["entry_momentum_regime"]),
            entry_distance_to_ema21_pct=float(entry_state["entry_distance_to_ema21_pct"]),
            btc_market_regime=str(market_snapshot.get("btc_market_regime", "unknown")),
            execution_template=str(market_snapshot.get("execution_template", "unknown")),
            narrative_tag=str(market_snapshot.get("narrative_tag", "unknown")),
        )
        self._replay_cache[cache_key] = outcome
        return outcome

    def replay_runs(
        self,
        symbol: Optional[str] = None,
        limit: int = 20,
        default_hours: int = 48,
    ) -> list[BacktestOutcome]:
        """Replay recent saved runs against historical klines."""
        runs = self.db.get_runs(symbol=symbol, limit=limit)
        outcomes: list[BacktestOutcome] = []
        for row in runs:
            outcome = self._replay_one(row, default_hours=default_hours)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def replay_windows(
        self,
        symbol: Optional[str] = None,
        limit: int = 60,
        default_hours: int = 48,
        window_size: int = 10,
        step: int = 5,
    ) -> list[dict]:
        """Replay rolling windows of saved runs to inspect time-varying behavior."""
        runs = self.db.get_runs(symbol=symbol, limit=limit)
        if not runs or window_size < 1:
            return []

        windows: list[dict] = []
        start_idx = 0
        while start_idx < len(runs):
            window_runs = runs[start_idx : start_idx + window_size]
            if not window_runs:
                break
            outcomes: list[BacktestOutcome] = []
            for row in window_runs:
                outcome = self._replay_one(row, default_hours=default_hours)
                if outcome is not None:
                    outcomes.append(outcome)
            summary = self.summarize(outcomes)
            if outcomes:
                pnl_path = []
                cumulative = 0.0
                max_drawdown = 0.0
                peak = 0.0
                for outcome in outcomes:
                    cumulative += outcome.pnl_pct
                    pnl_path.append(cumulative)
                    peak = max(peak, cumulative)
                    max_drawdown = min(max_drawdown, cumulative - peak)
                windows.append(
                    {
                        "window_index": len(windows) + 1,
                        "start_run_id": int(window_runs[-1]["id"]),
                        "end_run_id": int(window_runs[0]["id"]),
                        "trades": summary.get("trades", 0),
                        "win_rate": summary.get("win_rate"),
                        "expectancy_pct": summary.get("expectancy_pct"),
                        "profit_factor": summary.get("profit_factor"),
                        "total_pnl_pct": summary.get("total_pnl_pct"),
                        "max_drawdown_pct": max_drawdown,
                    }
                )
            if start_idx + window_size >= len(runs):
                break
            start_idx += max(1, step)
        return windows

    def walk_forward(
        self,
        symbol: Optional[str] = None,
        limit: int = 80,
        default_hours: int = 48,
        train_size: int = 20,
        test_size: int = 5,
        step: int = 5,
    ) -> list[dict]:
        """Run a simple walk-forward analysis on replayed run outcomes."""
        runs = list(reversed(self.db.get_runs(symbol=symbol, limit=limit)))
        if not runs or train_size < 1 or test_size < 1:
            return []

        windows: list[dict] = []
        idx = 0
        while idx + train_size + test_size <= len(runs):
            train_rows = runs[idx : idx + train_size]
            test_rows = runs[idx + train_size : idx + train_size + test_size]

            train_outcomes = [
                outcome
                for row in train_rows
                if (outcome := self._replay_one(row, default_hours=default_hours)) is not None
            ]
            test_outcomes = [
                outcome
                for row in test_rows
                if (outcome := self._replay_one(row, default_hours=default_hours)) is not None
            ]

            train_summary = self.summarize(train_outcomes)
            test_summary = self.summarize(test_outcomes)
            if train_outcomes or test_outcomes:
                windows.append(
                    {
                        "window_index": len(windows) + 1,
                        "train_start_run_id": int(train_rows[0]["id"]),
                        "train_end_run_id": int(train_rows[-1]["id"]),
                        "test_start_run_id": int(test_rows[0]["id"]),
                        "test_end_run_id": int(test_rows[-1]["id"]),
                        "train_trades": train_summary.get("trades", 0),
                        "train_expectancy_pct": train_summary.get("expectancy_pct"),
                        "train_profit_factor": train_summary.get("profit_factor"),
                        "test_trades": test_summary.get("trades", 0),
                        "test_expectancy_pct": test_summary.get("expectancy_pct"),
                        "test_profit_factor": test_summary.get("profit_factor"),
                        "test_total_pnl_pct": test_summary.get("total_pnl_pct"),
                    }
                )
            idx += max(1, step)
        return windows

    @staticmethod
    def summarize_walk_forward(windows: list[dict]) -> dict:
        """Summarize walk-forward windows into stability metrics."""
        if not windows:
            return {"windows": 0}
        test_expectancies = [float(w.get("test_expectancy_pct") or 0.0) for w in windows if w.get("test_expectancy_pct") is not None]
        positive = [x for x in test_expectancies if x > 0]
        non_positive = [x for x in test_expectancies if x <= 0]
        avg_test_expectancy = sum(test_expectancies) / len(test_expectancies) if test_expectancies else 0.0
        avg_test_pnl = sum(float(w.get("test_total_pnl_pct") or 0.0) for w in windows) / len(windows)
        return {
            "windows": len(windows),
            "positive_test_windows": len(positive),
            "non_positive_test_windows": len(non_positive),
            "positive_test_ratio": len(positive) / len(windows) * 100 if windows else None,
            "avg_test_expectancy_pct": avg_test_expectancy,
            "avg_test_total_pnl_pct": avg_test_pnl,
        }

    @staticmethod
    def summarize(outcomes: list[BacktestOutcome]) -> dict:
        """Summarize replay outcomes into backtest-like metrics."""
        if not outcomes:
            return {"trades": 0}
        wins = [o for o in outcomes if o.pnl_pct > 0]
        losses = [o for o in outcomes if o.pnl_pct <= 0]
        total_pnl_pct = sum(o.pnl_pct for o in outcomes)
        avg_win = sum(o.pnl_pct for o in wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(o.pnl_pct for o in losses) / len(losses)) if losses else 0.0
        profit_factor = (
            sum(o.pnl_pct for o in wins) / abs(sum(o.pnl_pct for o in losses))
            if losses and sum(o.pnl_pct for o in losses) != 0
            else None
        )
        win_rate = len(wins) / len(outcomes) * 100
        expectancy = (len(wins) / len(outcomes) * avg_win) - (len(losses) / len(outcomes) * avg_loss)
        return {
            "trades": len(outcomes),
            "win_rate": win_rate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "profit_factor": profit_factor,
            "expectancy_pct": expectancy,
            "total_pnl_pct": total_pnl_pct,
        }
