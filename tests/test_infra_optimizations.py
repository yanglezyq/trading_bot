"""Tests for P2/P3 infrastructure optimizations: retry, config validation, TTL cache."""

import time
from unittest.mock import MagicMock, patch

import pytest

from trading.core.retry import PermanentError, TransientError, is_transient, retry
from trading.exchange.market_data import TTLCache


# ---------------------------------------------------------------------------
# TTLCache tests
# ---------------------------------------------------------------------------

class TestTTLCache:
    def test_set_and_get(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("k1", {"data": 42})
        assert cache.get("k1") == {"data": 42}

    def test_expired_entry_returns_none(self):
        cache = TTLCache(ttl_seconds=0.01)
        cache.set("k1", "value")
        time.sleep(0.02)
        assert cache.get("k1") is None

    def test_max_size_evicts_oldest(self):
        cache = TTLCache(ttl_seconds=60, max_size=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)  # should evict "a"
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_invalidate_removes_key(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", "v")
        cache.invalidate("k")
        assert cache.get("k") is None

    def test_clear_removes_all(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None


# ---------------------------------------------------------------------------
# Retry decorator tests
# ---------------------------------------------------------------------------

class TestRetry:
    def test_success_on_first_call(self):
        @retry(max_attempts=3, base_delay=0.01)
        def succeed():
            return "ok"
        assert succeed() == "ok"

    def test_retries_transient_error(self):
        calls = {"count": 0}

        @retry(max_attempts=3, base_delay=0.01)
        def flaky():
            calls["count"] += 1
            if calls["count"] < 3:
                raise ConnectionError("timeout")
            return "recovered"

        assert flaky() == "recovered"
        assert calls["count"] == 3

    def test_permanent_error_not_retried(self):
        calls = {"count": 0}

        @retry(max_attempts=3, base_delay=0.01)
        def fail():
            calls["count"] += 1
            raise PermanentError("bad config")

        with pytest.raises(PermanentError):
            fail()
        assert calls["count"] == 1

    def test_non_transient_not_retried_when_transient_only(self):
        calls = {"count": 0}

        @retry(max_attempts=3, base_delay=0.01, transient_only=True)
        def fail():
            calls["count"] += 1
            raise ValueError("not transient")

        with pytest.raises(ValueError):
            fail()
        assert calls["count"] == 1

    def test_exceeds_max_attempts_raises(self):
        @retry(max_attempts=2, base_delay=0.01)
        def always_fail():
            raise TimeoutError("always")

        with pytest.raises(TimeoutError):
            always_fail()


class TestIsTransient:
    def test_connection_error_is_transient(self):
        assert is_transient(ConnectionError("reset")) is True

    def test_timeout_error_is_transient(self):
        assert is_transient(TimeoutError("timed out")) is True

    def test_value_error_not_transient(self):
        assert is_transient(ValueError("bad value")) is False

    def test_rate_limit_message_is_transient(self):
        assert is_transient(Exception("429 rate limit exceeded")) is True

    def test_503_message_is_transient(self):
        assert is_transient(Exception("503 Service Unavailable")) is True


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------

class TestValidateConfig:
    def _make_config(self, **overrides):
        """Create a valid config, then apply overrides."""
        from dataclasses import replace
        from trading.core.config import (
            AppConfig, BinanceConfig, ClaudeConfig,
            LoggingConfig, MonitorConfig, RiskConfig,
            TradingConfig, VaultConfig,
        )
        import tempfile, os
        vault_dir = tempfile.mkdtemp()
        cfg = AppConfig(
            vault=VaultConfig(path=vault_dir),
            risk=RiskConfig(),
            binance=BinanceConfig(api_key="key", api_secret="secret"),
            claude=ClaudeConfig(api_key="ck"),
            trading=TradingConfig(),
            monitor=MonitorConfig(),
            logging=LoggingConfig(),
        )
        for key, val in overrides.items():
            parts = key.split(".")
            if len(parts) == 2:
                sub = getattr(cfg, parts[0])
                object.__setattr__(sub, parts[1], val)
            else:
                object.__setattr__(cfg, key, val)
        return cfg

    def test_valid_config_has_no_errors(self):
        from trading.core.config import validate_config
        cfg = self._make_config()
        result = validate_config(cfg)
        assert result["errors"] == []

    def test_invalid_leverage_generates_error(self):
        from trading.core.config import validate_config
        cfg = self._make_config(**{"risk.max_leverage": 200})
        result = validate_config(cfg)
        assert any("max_leverage" in e for e in result["errors"])

    def test_position_cap_over_1_generates_error(self):
        from trading.core.config import validate_config
        cfg = self._make_config(**{"risk.core_position_cap_pct": 2.0})
        result = validate_config(cfg)
        assert any("core_position_cap_pct" in e for e in result["errors"])

    def test_vol_thresholds_ordering_generates_warning(self):
        from trading.core.config import validate_config
        cfg = self._make_config(
            **{"risk.high_volatility_24h_pct": 15.0, "risk.extreme_move_24h_pct": 10.0}
        )
        result = validate_config(cfg)
        assert any("high_volatility_24h_pct" in w for w in result["warnings"])

    def test_portfolio_exposure_ordering_warning(self):
        from trading.core.config import validate_config
        cfg = self._make_config(
            **{"risk.portfolio_soft_gross_exposure_pct": 150.0,
               "risk.portfolio_hard_gross_exposure_pct": 100.0}
        )
        result = validate_config(cfg)
        assert any("portfolio_soft" in w for w in result["warnings"])
