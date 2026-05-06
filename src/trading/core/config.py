"""Configuration management: loads from config.yaml, .env, and CLI overrides."""

import os
from dataclasses import dataclass, field, fields as dc_fields
from pathlib import Path
from typing import Optional, Type, TypeVar

_T = TypeVar("_T")

import yaml
from dotenv import load_dotenv


@dataclass
class VaultConfig:
    """Obsidian Vault path and subdirectory configuration."""

    path: str
    trading_dir: str = "体系化交易"
    system_guide: str = "合约交易体系-完整指南.md"
    position_tracking_dir: str = "【持仓管理】"
    reports_dir: str = "【报告】"
    decision_spec: str = "决策建议生成规范.md"
    research_spec: str = "币种调研报告规范(v2.0-通用版).md"

    @property
    def trading_path(self) -> Path:
        """Full path to the trading subdirectory."""
        return Path(self.path) / self.trading_dir


@dataclass
class RiskConfig:
    """Risk management parameters from 合约交易体系-完整指南.md."""

    max_account_drawdown: float = -0.20
    alert_drawdown: float = -0.10
    suspend_drawdown: float = -0.30
    max_leverage: float = 35.0
    btc_crash_threshold: float = -0.10
    btc_black_swan: float = -0.15
    btc_reduce_ratio: float = 0.30
    btc_reduce_ratio_swan: float = 0.50
    profit_lock_threshold: float = 0.50
    profit_lock_ratio: float = 0.50
    high_volatility_24h_pct: float = 8.0
    extreme_move_24h_pct: float = 12.0
    max_leverage_high_vol: int = 10
    max_position_size_high_vol_pct: float = 0.02
    max_abs_funding_rate: float = 0.0010
    btc_risk_off_24h_pct: float = -4.0
    altcoin_max_leverage_when_btc_weak: int = 5
    altcoin_max_position_size_when_btc_weak_pct: float = 0.015
    alt_relative_strength_warning_pct: float = -5.0
    liquid_alt_quote_volume_24h_usdt: float = 250_000_000.0
    mid_alt_quote_volume_24h_usdt: float = 50_000_000.0
    high_beta_alt_max_leverage: int = 3
    high_beta_alt_max_position_size_pct: float = 0.01
    crowded_oi_to_volume_ratio: float = 0.75
    btc_rebound_trigger_24h_pct: float = 3.0
    btc_squeeze_funding_threshold: float = 0.0015
    thin_liquidity_market_order_notional_usdt: float = 15_000.0
    meme_max_leverage: int = 2
    meme_max_position_size_pct: float = 0.0075
    narrative_thesis_window_cap_hours: int = 24
    portfolio_soft_gross_exposure_pct: float = 80.0
    portfolio_hard_gross_exposure_pct: float = 120.0
    portfolio_max_positions: int = 8
    max_positions_per_narrative: int = 2
    same_symbol_addition_scale: float = 0.5
    core_position_cap_pct: float = 0.06
    major_alt_position_cap_pct: float = 0.04
    liquid_alt_position_cap_pct: float = 0.03
    mid_alt_position_cap_pct: float = 0.02


@dataclass
class BinanceConfig:
    """Binance API credentials and endpoint configuration."""

    api_key: str = ""
    api_secret: str = ""
    futures_testnet: bool = False
    spot_testnet: bool = False

    @property
    def testnet_mode(self) -> bool:
        """True if either futures or spot testnet is enabled."""
        return self.futures_testnet or self.spot_testnet


@dataclass
class ClaudeConfig:
    """Anthropic Claude API configuration."""

    api_key: str = ""
    model: str = "claude-sonnet-4-6"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300
    max_tokens: int = 2000


@dataclass
class TradingConfig:
    """Trading behavior configuration."""

    dry_run: bool = False
    default_stop_loss_pct: float = 0.05
    default_take_profit_pct: float = 0.15
    max_position_size_pct: float = 0.05
    event_driven_max_pct: float = 0.05


@dataclass
class MonitorConfig:
    """Monitoring and WebSocket stream configuration."""

    interval_seconds: int = 60
    price_stream_reconnect: bool = True
    stream_symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    sqlite_db: str = "data/trades.db"
    console_format: str = "rich"
    max_log_size_mb: int = 100
    keep_logs_days: int = 30


@dataclass
class AppConfig:
    """Top-level application configuration."""

    vault: VaultConfig
    risk: RiskConfig
    binance: BinanceConfig
    claude: ClaudeConfig
    trading: TradingConfig
    monitor: MonitorConfig
    logging: LoggingConfig

    @property
    def is_testnet(self) -> bool:
        """True if Binance testnet mode is active."""
        return self.binance.testnet_mode


def _filter(cls: Type[_T], d: dict) -> dict:
    """Return only the keys from d that are valid fields of cls."""
    valid = {f.name for f in dc_fields(cls)}  # type: ignore[arg-type]
    return {k: v for k, v in d.items() if k in valid}


def load_config(
    config_path: str = "config.yaml",
    env_path: str = ".env",
    dry_run: Optional[bool] = None,
) -> AppConfig:
    """Load and merge config from yaml, .env, and optional CLI dry_run override."""
    if Path(env_path).exists():
        load_dotenv(env_path)

    if not Path(config_path).exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        config_dict = yaml.safe_load(f) or {}

    vault_dict = config_dict.get("vault", {})
    risk_dict = config_dict.get("risk", {})
    binance_dict = config_dict.get("binance", {})
    claude_dict = config_dict.get("claude", {})
    trading_dict = config_dict.get("trading", {})
    monitor_dict = config_dict.get("monitor", {})
    logging_dict = config_dict.get("logging", {})

    binance_dict["api_key"] = os.getenv("BINANCE_API_KEY", binance_dict.get("api_key", ""))
    binance_dict["api_secret"] = os.getenv("BINANCE_API_SECRET", binance_dict.get("api_secret", ""))
    claude_dict["api_key"] = os.getenv("ANTHROPIC_API_KEY", claude_dict.get("api_key", ""))

    if vault_path := os.getenv("OBSIDIAN_VAULT_PATH"):
        vault_dict["path"] = vault_path

    trading_dict["dry_run"] = dry_run if dry_run is not None else trading_dict.get("dry_run", False)

    return AppConfig(
        vault=VaultConfig(**_filter(VaultConfig, vault_dict)),
        risk=RiskConfig(**_filter(RiskConfig, risk_dict)),
        binance=BinanceConfig(**_filter(BinanceConfig, binance_dict)),
        claude=ClaudeConfig(**_filter(ClaudeConfig, claude_dict)),
        trading=TradingConfig(**_filter(TradingConfig, trading_dict)),
        monitor=MonitorConfig(**_filter(MonitorConfig, monitor_dict)),
        logging=LoggingConfig(**_filter(LoggingConfig, logging_dict)),
    )


def validate_config(config: AppConfig) -> list[str]:
    """Return list of configuration validation errors; empty list means valid."""
    errors = []

    if not Path(config.vault.path).exists():
        errors.append(f"Vault path does not exist: {config.vault.path}")

    if not config.binance.api_key or not config.binance.api_secret:
        errors.append("Binance API credentials not set in .env or config")

    if config.risk.max_account_drawdown >= 0:
        errors.append("max_account_drawdown must be negative")

    if config.risk.alert_drawdown >= 0:
        errors.append("alert_drawdown must be negative")

    return errors
