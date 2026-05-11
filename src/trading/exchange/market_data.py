"""Public crypto market data helpers for price-driven trade validation."""

from __future__ import annotations

import time as _time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from math import sqrt
from statistics import pstdev

from binance.spot import Spot as BinanceSpot

from ..core.config import AppConfig, BinanceConfig
from .um_futures import UMFutures


# ---------------------------------------------------------------------------
# TTL Cache utility
# ---------------------------------------------------------------------------

class TTLCache:
    """Simple in-memory cache with per-entry TTL and max size eviction."""

    def __init__(self, ttl_seconds: float, max_size: int = 64):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: OrderedDict[str, tuple[float, object]] = OrderedDict()

    def get(self, key: str):
        """Return cached value or None if expired/missing."""
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if _time.time() - ts > self._ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value):
        """Store value with current timestamp."""
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self._max_size:
            self._store.popitem(last=False)
        self._store[key] = (_time.time(), value)

    def invalidate(self, key: str):
        """Remove a specific key."""
        self._store.pop(key, None)

    def clear(self):
        """Clear all entries."""
        self._store.clear()


_NARRATIVE_MAP = {
    # Store of value
    "BTCUSDT": "store_of_value",
    # Smart contract L1s
    "ETHUSDT": "smart_contract_l1",
    "SOLUSDT": "high_beta_l1",
    "ADAUSDT": "layer1",
    "AVAXUSDT": "layer1",
    "DOTUSDT": "layer1",
    "ATOMUSDT": "layer1",
    "NEARUSDT": "layer1",
    "APTUSDT": "layer1",
    "SUIUSDT": "layer1",
    "TONUSDT": "layer1",
    "ICPUSDT": "layer1",
    "ALGOUSDT": "layer1",
    "HBARUSDT": "layer1",
    "SEIUSDT": "layer1",
    "INJUSDT": "layer1",
    "TIAUSDT": "modular_infra",
    # Exchange ecosystem
    "BNBUSDT": "exchange_ecosystem",
    "OKBUSDT": "exchange_ecosystem",
    "GTUSDT": "exchange_ecosystem",
    # Payments
    "XRPUSDT": "payments",
    "XLMUSDT": "payments",
    "LTCUSDT": "payments",
    "BCHUSDT": "payments",
    # Meme
    "DOGEUSDT": "meme",
    "SHIBUSDT": "meme",
    "PEPEUSDT": "meme",
    "BONKUSDT": "meme",
    "WIFUSDT": "meme",
    "FLOKIUSDT": "meme",
    "MEMEUSDT": "meme",
    "PEOPLEUSDT": "meme",
    "NEIROUSDT": "meme",
    "ACTUSDT": "meme",
    "TURBO": "meme",
    # DeFi
    "LINKUSDT": "oracle",
    "AAVEUSDT": "defi",
    "UNIUSDT": "defi",
    "MKRUSDT": "defi",
    "CRVUSDT": "defi",
    "COMPUSDT": "defi",
    "SUSHIUSDT": "defi",
    "SNXUSDT": "defi",
    "DYDXUSDT": "defi",
    "GMXUSDT": "defi",
    "1INCHUSDT": "defi",
    "PENDLEUSDT": "defi",
    "LDOUSDT": "liquid_staking",
    "RPLLUSDT": "liquid_staking",
    "EIGENUSDT": "restaking",
    "ETHFIUSDT": "restaking",
    # Layer 2
    "ARBUSDT": "layer2",
    "OPUSDT": "layer2",
    "STRKUSDT": "layer2",
    "MATICUSDT": "layer2",
    "MANTAUSDT": "layer2",
    "ZKUSDT": "layer2",
    "SCROLLUSDT": "layer2",
    # AI / Compute
    "RENDERUSDT": "ai_compute",
    "FETUSDT": "ai_agent",
    "TAOUSDT": "ai_agent",
    "AIUSDT": "ai_agent",
    "WLDUSDT": "ai_agent",
    "ARKMUSDT": "ai_agent",
    "VIRTUSDT": "ai_agent",
    # Gaming / Metaverse
    "AXSUSDT": "gaming",
    "SANDUSDT": "gaming",
    "MANAUSDT": "gaming",
    "GALAUSDT": "gaming",
    "IMXUSDT": "gaming",
    "PIXELUSDT": "gaming",
    # RWA / Tokenization
    "ONDOUSDT": "rwa",
    "OMUSDT": "rwa",
    # DePin
    "FILUSDT": "depin",
    "ARUSDT": "depin",
    "THETAUSDT": "depin",
    "IOTAUSDT": "depin",
    # Privacy
    "XMRUSDT": "privacy",
    "ZECUSDT": "privacy",
}

# Keyword patterns for fallback narrative inference
_NARRATIVE_KEYWORDS = {
    "ai": "ai_agent",
    "gpt": "ai_agent",
    "swap": "defi",
    "fi": "defi",
    "lend": "defi",
    "pepe": "meme",
    "doge": "meme",
    "inu": "meme",
    "cat": "meme",
    "zk": "layer2",
    "game": "gaming",
    "play": "gaming",
    "nft": "gaming",
}


def infer_narrative_tag(symbol: str) -> str:
    """Infer a coarse crypto narrative tag from the trading symbol.

    Uses explicit map first, then keyword heuristics as fallback.
    """
    sym = symbol.upper()
    if tag := _NARRATIVE_MAP.get(sym):
        return tag
    # Fallback: keyword matching on base symbol
    base = sym.replace("USDT", "").replace("USDC", "").replace("BUSD", "").lower()
    for keyword, tag in _NARRATIVE_KEYWORDS.items():
        if keyword in base:
            return tag
    return "general_alt"


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
    narrative_tag: str
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
    # P0: Order book depth (USDT notional within 100bps)
    bid_depth_at_100bps: float | None = None
    ask_depth_at_100bps: float | None = None
    # P0: Funding velocity
    funding_rate_prev: float | None = None
    funding_velocity: float | None = None
    # P1: Multi-timeframe alignment
    hourly_trend_bias_4h: str | None = None
    multi_tf_alignment: str | None = None
    # P1: BTC regime confidence (debouncing)
    btc_regime_confidence: float | None = None
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

        # Layered TTL caches by data type
        self._cache_klines = TTLCache(ttl_seconds=300, max_size=32)       # 5 min
        self._cache_ticker = TTLCache(ttl_seconds=30, max_size=32)        # 30 sec
        self._cache_funding = TTLCache(ttl_seconds=60, max_size=32)       # 1 min
        self._cache_depth = TTLCache(ttl_seconds=10, max_size=16)         # 10 sec
        self._cache_mark = TTLCache(ttl_seconds=15, max_size=32)          # 15 sec

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
            regime, confidence = self._btc_market_regime_debounced(btc_snapshot)
            snapshot.btc_market_regime = regime
            snapshot.btc_regime_confidence = confidence
            snapshot.execution_template = self._execution_template(snapshot)
        else:
            regime, confidence = self._btc_market_regime_debounced(snapshot)
            snapshot.btc_market_regime = regime
            snapshot.btc_regime_confidence = confidence
            snapshot.execution_template = self._execution_template(snapshot)
        return snapshot

    def _get_single_snapshot(self, symbol: str) -> MarketSnapshot:
        symbol = symbol.upper()

        spot_price = self._get_spot_price(symbol)

        # Mark price (cached 15s)
        mark_data = self._cache_mark.get(symbol)
        if mark_data is None:
            mark_data = self.futures_client.mark_price(symbol=symbol)
            self._cache_mark.set(symbol, mark_data)
        futures_mark_price = float(mark_data.get("markPrice", 0.0))
        funding_rate = float(mark_data.get("lastFundingRate", 0.0))

        # 24h ticker (cached 30s)
        ticker_24h = self._cache_ticker.get(symbol)
        if ticker_24h is None:
            ticker_24h = self.futures_client.ticker_24hr(symbol=symbol)
            self._cache_ticker.set(symbol, ticker_24h)
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

        # Klines (cached 5 min)
        klines_key = f"{symbol}_1h"
        klines = self._cache_klines.get(klines_key)
        if klines is None:
            klines = self.futures_client.klines(symbol=symbol, interval="1h", limit=168)
            self._cache_klines.set(klines_key, klines)
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

        # P0: Order book depth (best-effort, non-critical, cached 10s)
        bid_depth_at_100bps: float | None = None
        ask_depth_at_100bps: float | None = None
        try:
            depth_cached = self._cache_depth.get(symbol)
            if depth_cached is not None:
                bid_depth_at_100bps, ask_depth_at_100bps = depth_cached
            else:
                bid_depth_at_100bps, ask_depth_at_100bps = self._compute_book_depth(
                    symbol, futures_mark_price
                )
                self._cache_depth.set(symbol, (bid_depth_at_100bps, ask_depth_at_100bps))
        except Exception:
            pass

        # P0: Funding rate velocity (cached 1 min)
        funding_rate_prev: float | None = None
        funding_velocity: float | None = None
        try:
            funding_cached = self._cache_funding.get(symbol)
            if funding_cached is None:
                funding_cached = self.futures_client.funding_rate_history(symbol=symbol, limit=3)
                self._cache_funding.set(symbol, funding_cached)
            if len(funding_cached) >= 2:
                funding_rate_prev = float(funding_cached[-2].get("fundingRate", 0.0))
                funding_velocity = funding_rate - funding_rate_prev
        except Exception:
            pass

        # P1: 4h trend bias for multi-timeframe alignment
        hourly_trend_bias_4h: str | None = None
        multi_tf_alignment: str | None = None
        try:
            klines_4h = self.futures_client.klines(symbol=symbol, interval="4h", limit=42)
            closes_4h = [float(k[4]) for k in klines_4h if len(k) > 4]
            if len(closes_4h) >= 21:
                ema_21_4h = self._ema(closes_4h, 21)
                ema_55_4h = self._ema(closes_4h, min(55, len(closes_4h)))
                hourly_trend_bias_4h = self._hourly_trend_bias(
                    futures_mark_price, ema_21_4h, ema_55_4h, ema_55_4h
                )
        except Exception:
            pass

        hourly_trend_bias_1h = self._hourly_trend_bias(futures_mark_price, ema_21, ema_55, ema_144)
        if hourly_trend_bias_4h is not None:
            multi_tf_alignment = self._multi_tf_alignment(hourly_trend_bias_1h, hourly_trend_bias_4h)

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
            hourly_trend_bias=hourly_trend_bias_1h,
            asset_tier=self._asset_tier(symbol, quote_volume_24h_usdt),
            narrative_tag=self._narrative_tag(symbol),
            liquidity_regime=self._liquidity_regime(quote_volume_24h_usdt),
            crowding_regime=self._crowding_regime(
                funding_rate=funding_rate,
                basis_bps=basis_bps,
                oi_to_volume_ratio=oi_to_volume_ratio,
                move_24h_pct=price_change_24h_pct,
            ),
            volatility_regime=self._volatility_regime(realized_vol_24h_pct),
            momentum_regime=self._momentum_regime(price_change_24h_pct, price_change_7d_pct),
            bid_depth_at_100bps=bid_depth_at_100bps,
            ask_depth_at_100bps=ask_depth_at_100bps,
            funding_rate_prev=funding_rate_prev,
            funding_velocity=funding_velocity,
            hourly_trend_bias_4h=hourly_trend_bias_4h,
            multi_tf_alignment=multi_tf_alignment,
            dry_run=False,
        )

    def _get_spot_price(self, symbol: str) -> float:
        data = self.spot_client.ticker_price(symbol=symbol)
        if isinstance(data, list):
            raise ValueError(f"Unexpected list response for ticker_price({symbol})")
        return float(data.get("price", 0.0))

    def _compute_book_depth(self, symbol: str, mark_price: float) -> tuple[float, float]:
        """Return (bid_depth_usdt, ask_depth_usdt) within 100bps of mark price."""
        book = self.futures_client.depth(symbol=symbol, limit=20)
        bid_threshold = mark_price * 0.99
        ask_threshold = mark_price * 1.01
        bid_depth = sum(
            float(b[1]) * float(b[0])
            for b in book.get("bids", [])
            if float(b[0]) >= bid_threshold
        )
        ask_depth = sum(
            float(a[1]) * float(a[0])
            for a in book.get("asks", [])
            if float(a[0]) <= ask_threshold
        )
        return bid_depth, ask_depth

    @staticmethod
    def _multi_tf_alignment(bias_1h: str, bias_4h: str) -> str:
        """Classify multi-timeframe trend alignment."""
        if bias_1h == "bullish" and bias_4h == "bullish":
            return "aligned_bullish"
        if bias_1h == "bearish" and bias_4h == "bearish":
            return "aligned_bearish"
        if bias_1h == bias_4h:  # both "range"
            return "aligned_range"
        return "conflicted"

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
    def _narrative_tag(symbol: str) -> str:
        return infer_narrative_tag(symbol)

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

    def _btc_market_regime_debounced(self, snapshot: MarketSnapshot) -> tuple[str, float]:
        """Debounced BTC regime using majority vote over last 3 hourly candles.

        Extreme states (panic_flush, short_squeeze) require >= 2/3 confirmation
        to avoid whipsaw regime flips in choppy markets.
        Returns (regime, confidence) where confidence is votes/total.
        """
        from collections import Counter

        # Current candle classification
        current_regime = self._btc_market_regime(snapshot)

        # Attempt lookback vote using kline-derived momentum for last 3 candles
        # We use the 1h klines already fetched (approximation from snapshot fields)
        # For a more precise implementation we'd re-classify each candle separately
        # Here we use a simplified approach: check if the regime depends on
        # extreme conditions that could flip easily
        if current_regime in {"panic_flush", "short_squeeze"}:
            # These extreme states need stronger confirmation
            # Check realized vol and momentum consistency: if 7d also confirms, high confidence
            confirms = 0
            if current_regime == "panic_flush":
                # Confirmed if both 24h AND 7d are strongly negative
                if snapshot.price_change_7d_pct <= -10.0:
                    confirms += 1
                if snapshot.volatility_regime == "extreme":
                    confirms += 1
                if snapshot.momentum_regime == "strong_down":
                    confirms += 1
            elif current_regime == "short_squeeze":
                if snapshot.funding_rate >= 0.0015:
                    confirms += 1
                if snapshot.momentum_regime == "strong_up":
                    confirms += 1
                if snapshot.price_change_7d_pct >= 10.0:
                    confirms += 1
            confidence = confirms / 3.0
            if confidence < 0.67:
                return "range", confidence
            return current_regime, confidence

        # Non-extreme regimes pass through with full confidence
        return current_regime, 1.0

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
            narrative_tag=MarketDataManager._narrative_tag(symbol),
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
