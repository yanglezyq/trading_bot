"""Public crypto market data helpers for price-driven trade validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from statistics import pstdev

from binance.spot import Spot as BinanceSpot

from ..core.config import AppConfig, BinanceConfig
from .um_futures import UMFutures


@dataclass
class MarketSnapshot:
    """Compact market snapshot for one crypto symbol."""

    symbol: str
    spot_price: float
    futures_mark_price: float
    basis_bps: float
    price_change_24h_pct: float
    price_change_7d_pct: float
    high_24h: float
    low_24h: float
    quote_volume_24h_usdt: float
    funding_rate: float
    open_interest: float
    open_interest_notional_usdt: float
    oi_to_volume_ratio: float
    realized_vol_24h_pct: float
    realized_vol_7d_pct: float
    ema_21_1h: float
    ema_55_1h: float
    ema_144_1h: float
    distance_to_ema21_pct: float
    distance_to_ema55_pct: float
    distance_to_7d_high_pct: float
    distance_to_7d_low_pct: float
    hourly_trend_bias: str
    asset_tier: str
    liquidity_regime: str
    crowding_regime: str
    volatility_regime: str
    momentum_regime: str
    benchmark_symbol: str | None = None
    benchmark_price_change_24h_pct: float | None = None
    benchmark_price_change_7d_pct: float | None = None
    benchmark_volatility_regime: str | None = None
    benchmark_momentum_regime: str | None = None
    relative_strength_24h_pct: float | None = None
    relative_strength_7d_pct: float | None = None
    btc_market_regime: str | None = None
    execution_template: str | None = None
    dry_run: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class MarketDataManager:
    """Fetch public spot/futures data and derive a price-focused snapshot."""

    def __init__(self, config: BinanceConfig | AppConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run

        if hasattr(config, "binance"):
            binance_config = config.binance  # type: ignore[assignment]
        else:
            binance_config = config

        futures_base_url = (
            "https://testnet.binancefuture.com" if binance_config.futures_testnet else "https://fapi.binance.com"
        )
        spot_base_url = (
            "https://testnet.binance.vision" if binance_config.spot_testnet else "https://api.binance.com"
        )
        self.spot_client = BinanceSpot(base_url=spot_base_url)
        self.futures_client = UMFutures(base_url=futures_base_url)

    def _cfg(self, attr: str, default):
        if hasattr(self.config, "risk") and hasattr(self.config.risk, attr):
            return getattr(self.config.risk, attr)
        return default

    def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        symbol = symbol.upper()
        if self.dry_run:
            return self._mock_snapshot(symbol)

        snapshot = self._get_single_snapshot(symbol)
        if symbol != "BTCUSDT":
            btc_snapshot = self._get_single_snapshot("BTCUSDT")
            snapshot.benchmark_symbol = btc_snapshot.symbol
            snapshot.benchmark_price_change_24h_pct = btc_snapshot.price_change_24h_pct
            snapshot.benchmark_price_change_7d_pct = btc_snapshot.price_change_7d_pct
            snapshot.benchmark_volatility_regime = btc_snapshot.volatility_regime
            snapshot.benchmark_momentum_regime = btc_snapshot.momentum_regime
            snapshot.relative_strength_24h_pct = (
                snapshot.price_change_24h_pct - btc_snapshot.price_change_24h_pct
            )
            snapshot.relative_strength_7d_pct = (
                snapshot.price_change_7d_pct - btc_snapshot.price_change_7d_pct
            )
            snapshot.btc_market_regime = self._btc_market_regime(btc_snapshot)
            snapshot.execution_template = self._execution_template(snapshot)
        else:
            snapshot.btc_market_regime = self._btc_market_regime(snapshot)
            snapshot.execution_template = self._execution_template(snapshot)
        return snapshot

    def _get_single_snapshot(self, symbol: str) -> MarketSnapshot:
        symbol = symbol.upper()

        spot_price = self._get_spot_price(symbol)
        mark_data = self.futures_client.mark_price(symbol=symbol)
        futures_mark_price = float(mark_data.get("markPrice", 0.0))
        funding_rate = float(mark_data.get("lastFundingRate", 0.0))

        ticker_24h = self.futures_client.ticker_24hr(symbol=symbol)
        price_change_24h_pct = float(ticker_24h.get("priceChangePercent", 0.0))
        high_24h = float(ticker_24h.get("highPrice", 0.0))
        low_24h = float(ticker_24h.get("lowPrice", 0.0))
        quote_volume_24h_usdt = float(ticker_24h.get("quoteVolume", 0.0))

        oi_data = self.futures_client.open_interest(symbol=symbol)
        open_interest = float(oi_data.get("openInterest", 0.0))
        open_interest_notional_usdt = open_interest * futures_mark_price
        oi_to_volume_ratio = (
            open_interest_notional_usdt / quote_volume_24h_usdt
            if quote_volume_24h_usdt > 0
            else 0.0
        )

        klines = self.futures_client.klines(symbol=symbol, interval="1h", limit=168)
        closes = [float(k[4]) for k in klines if len(k) > 4]
        price_change_7d_pct = self._calc_pct_change(closes[0], closes[-1]) if len(closes) >= 2 else 0.0
        realized_vol_24h_pct = self._realized_vol_pct(closes[-25:]) if len(closes) >= 25 else 0.0
        realized_vol_7d_pct = self._realized_vol_pct(closes) if len(closes) >= 25 else realized_vol_24h_pct
        ema_21 = self._ema(closes, 21)
        ema_55 = self._ema(closes, 55)
        ema_144 = self._ema(closes, 144)
        distance_to_ema21_pct = self._distance_pct(futures_mark_price, ema_21)
        distance_to_ema55_pct = self._distance_pct(futures_mark_price, ema_55)
        distance_to_7d_high_pct = self._distance_pct(futures_mark_price, max(closes) if closes else futures_mark_price)
        distance_to_7d_low_pct = self._distance_pct(futures_mark_price, min(closes) if closes else futures_mark_price)

        basis_bps = 0.0
        if spot_price > 0:
            basis_bps = (futures_mark_price - spot_price) / spot_price * 10000

        return MarketSnapshot(
            symbol=symbol,
            spot_price=spot_price,
            futures_mark_price=futures_mark_price,
            basis_bps=basis_bps,
            price_change_24h_pct=price_change_24h_pct,
            price_change_7d_pct=price_change_7d_pct,
            high_24h=high_24h,
            low_24h=low_24h,
            quote_volume_24h_usdt=quote_volume_24h_usdt,
            funding_rate=funding_rate,
            open_interest=open_interest,
            open_interest_notional_usdt=open_interest_notional_usdt,
            oi_to_volume_ratio=oi_to_volume_ratio,
            realized_vol_24h_pct=realized_vol_24h_pct,
            realized_vol_7d_pct=realized_vol_7d_pct,
            ema_21_1h=ema_21,
            ema_55_1h=ema_55,
            ema_144_1h=ema_144,
            distance_to_ema21_pct=distance_to_ema21_pct,
            distance_to_ema55_pct=distance_to_ema55_pct,
            distance_to_7d_high_pct=distance_to_7d_high_pct,
            distance_to_7d_low_pct=distance_to_7d_low_pct,
            hourly_trend_bias=self._hourly_trend_bias(futures_mark_price, ema_21, ema_55, ema_144),
            asset_tier=self._asset_tier(symbol, quote_volume_24h_usdt),
            liquidity_regime=self._liquidity_regime(quote_volume_24h_usdt),
            crowding_regime=self._crowding_regime(
                funding_rate=funding_rate,
                basis_bps=basis_bps,
                oi_to_volume_ratio=oi_to_volume_ratio,
                move_24h_pct=price_change_24h_pct,
            ),
            volatility_regime=self._volatility_regime(realized_vol_24h_pct),
            momentum_regime=self._momentum_regime(price_change_24h_pct, price_change_7d_pct),
            dry_run=False,
        )

    def _get_spot_price(self, symbol: str) -> float:
        data = self.spot_client.ticker_price(symbol=symbol)
        if isinstance(data, list):
            raise ValueError(f"Unexpected list response for ticker_price({symbol})")
        return float(data.get("price", 0.0))

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
    def _hourly_trend_bias(price: float, ema_21: float, ema_55: float, ema_144: float) -> str:
        if price > ema_21 > ema_55 > ema_144:
            return "bullish"
        if price < ema_21 < ema_55 < ema_144:
            return "bearish"
        return "range"

    def _asset_tier(self, symbol: str, quote_volume_24h_usdt: float) -> str:
        core = {"BTCUSDT", "ETHUSDT"}
        major_alt = {"SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"}
        if symbol in core:
            return "core"
        if symbol in major_alt:
            return "major_alt"
        if quote_volume_24h_usdt >= self._cfg("liquid_alt_quote_volume_24h_usdt", 250_000_000.0):
            return "liquid_alt"
        if quote_volume_24h_usdt >= self._cfg("mid_alt_quote_volume_24h_usdt", 50_000_000.0):
            return "mid_alt"
        return "high_beta_alt"

    @staticmethod
    def _liquidity_regime(quote_volume_24h_usdt: float) -> str:
        if quote_volume_24h_usdt >= 1_000_000_000:
            return "deep"
        if quote_volume_24h_usdt >= 250_000_000:
            return "liquid"
        if quote_volume_24h_usdt >= 50_000_000:
            return "medium"
        return "thin"

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

    @staticmethod
    def _btc_market_regime(snapshot: MarketSnapshot) -> str:
        if snapshot.momentum_regime == "strong_down" and snapshot.volatility_regime == "extreme":
            return "panic_flush"
        if snapshot.momentum_regime == "strong_down":
            return "risk_off_trend"
        if (
            snapshot.momentum_regime in {"up", "strong_up"}
            and snapshot.hourly_trend_bias == "bullish"
            and snapshot.distance_to_7d_high_pct >= -2.0
        ):
            return "risk_on_trend"
        if snapshot.price_change_24h_pct >= 3.0 and snapshot.hourly_trend_bias != "bullish":
            return "rebound"
        if snapshot.funding_rate >= 0.0015 and snapshot.momentum_regime == "strong_up":
            return "short_squeeze"
        return "range"

    @staticmethod
    def _execution_template(snapshot: MarketSnapshot) -> str:
        btc_regime = snapshot.btc_market_regime or "range"
        if snapshot.asset_tier == "core":
            if btc_regime in {"risk_on_trend", "short_squeeze"}:
                return "core_trend_follow"
            if btc_regime in {"panic_flush", "rebound"}:
                return "core_reclaim_wait"
            return "core_range_trade"
        if snapshot.asset_tier in {"major_alt", "liquid_alt"}:
            if btc_regime in {"risk_on_trend", "rebound"}:
                return "alt_follow_with_confirmation"
            if btc_regime in {"panic_flush", "risk_off_trend"}:
                return "alt_defensive_only"
            return "alt_selective_range"
        if snapshot.asset_tier == "mid_alt":
            return "mid_alt_staged_entry"
        return "high_beta_confirmation_only"

    def _crowding_regime(
        self,
        *,
        funding_rate: float,
        basis_bps: float,
        oi_to_volume_ratio: float,
        move_24h_pct: float,
    ) -> str:
        crowded_oi = self._cfg("crowded_oi_to_volume_ratio", 0.75)
        crowded_funding = self._cfg("max_abs_funding_rate", 0.0010)
        if (
            funding_rate >= crowded_funding
            and basis_bps >= 80
            and oi_to_volume_ratio >= crowded_oi
            and move_24h_pct > 0
        ):
            return "crowded_long"
        if (
            funding_rate <= -crowded_funding
            and basis_bps <= -80
            and oi_to_volume_ratio >= crowded_oi
            and move_24h_pct < 0
        ):
            return "crowded_short"
        if oi_to_volume_ratio >= crowded_oi:
            return "heavy_positioning"
        return "balanced"

    @staticmethod
    def _mock_snapshot(symbol: str) -> MarketSnapshot:
        base = MarketSnapshot(
            symbol=symbol,
            spot_price=100.0,
            futures_mark_price=100.2,
            basis_bps=20.0,
            price_change_24h_pct=2.5,
            price_change_7d_pct=8.0,
            high_24h=103.0,
            low_24h=97.0,
            quote_volume_24h_usdt=250_000_000.0,
            funding_rate=0.00012,
            open_interest=125_000_000.0,
            open_interest_notional_usdt=12_525_000_000.0,
            oi_to_volume_ratio=0.5,
            realized_vol_24h_pct=4.5,
            realized_vol_7d_pct=5.8,
            ema_21_1h=99.2,
            ema_55_1h=97.8,
            ema_144_1h=94.5,
            distance_to_ema21_pct=1.0,
            distance_to_ema55_pct=2.4,
            distance_to_7d_high_pct=-1.7,
            distance_to_7d_low_pct=8.5,
            hourly_trend_bias="bullish",
            asset_tier="core" if symbol == "BTCUSDT" else "liquid_alt",
            liquidity_regime="liquid",
            crowding_regime="balanced",
            volatility_regime="normal",
            momentum_regime="up",
            dry_run=True,
        )
        base.btc_market_regime = "risk_on_trend" if symbol == "BTCUSDT" else None
        base.execution_template = "core_trend_follow" if symbol == "BTCUSDT" else None
        if symbol != "BTCUSDT":
            base.benchmark_symbol = "BTCUSDT"
            base.benchmark_price_change_24h_pct = 1.5
            base.benchmark_price_change_7d_pct = 5.0
            base.benchmark_volatility_regime = "normal"
            base.benchmark_momentum_regime = "up"
            base.relative_strength_24h_pct = 1.0
            base.relative_strength_7d_pct = 3.0
            base.btc_market_regime = "risk_on_trend"
            base.execution_template = "alt_follow_with_confirmation"
        return base
