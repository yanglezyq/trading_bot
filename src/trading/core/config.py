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
    # P0: Liquidation distance
    min_liquidation_distance_pct: float = 15.0
    # P0: Order book depth check
    depth_check_enabled: bool = True
    depth_warning_fill_pct: float = 0.5
    # P0: Funding rate velocity
    funding_velocity_block_threshold: float = 0.0005
    # P2: Dynamic leverage formula
    dynamic_leverage_enabled: bool = True
    vol_ceiling_pct: float = 20.0
    # P2: Equity curve protection
    equity_protection_enabled: bool = True
    equity_ema_period: int = 20
    equity_pause_factor: float = 0.95
    equity_reduce_factor: float = 0.5


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
    ensemble_enabled: bool = False
    ensemble_models: list[str] = field(default_factory=list)
    ensemble_min_agreement: float = 0.67
    adaptive_prompt_enabled: bool = True
    adaptive_prompt_min_trades: int = 5
    adaptive_prompt_lookback_trades: int = 12
    adaptive_prompt_lookback_runs: int = 40
    adaptive_prompt_loss_streak_threshold: int = 3


@dataclass
class TradingConfig:
    """Trading behavior configuration."""

    dry_run: bool = False
    default_stop_loss_pct: float = 0.05
    default_take_profit_pct: float = 0.15
    max_position_size_pct: float = 0.05
    event_driven_max_pct: float = 0.05
    # P1: Trailing stop and staged take-profit
    trailing_stop_activation_pct: float = 3.0
    trailing_stop_distance_pct: float = 1.5
    staged_tp_levels: list[dict] = field(
        default_factory=lambda: [
            {"pct": 50, "close_pct": 30},
            {"pct": 100, "close_pct": 40},
        ]
    )
    # P4: Combined research+execution in single AI call (saves ~30% tokens)
    combined_ai_calls: bool = True
    # Stable-mode preset: trade less, demand stronger setups
    stable_mode_enabled: bool = True
    stable_min_confidence: float = 0.72
    stable_min_consensus_strength: float = 0.67
    stable_min_quality_score: float = 70.0
    stable_min_reward_risk_ratio: float = 2.0
    stable_max_position_size_pct: float = 0.02
    stable_max_leverage: int = 5
    stable_same_symbol_loss_cooldown_hours: int = 24
    stable_loss_streak_block_count: int = 2
    conviction_override_enabled: bool = True
    conviction_override_confidence: float = 0.85
    conviction_override_consensus_strength: float = 0.75
    conviction_override_min_quality_score: float = 55.0
    conviction_override_min_reward_risk_ratio: float = 1.4
    conviction_override_size_pct: float = 0.035
    conviction_override_max_leverage: int = 8
    conviction_override_require_multi_model: bool = False
    auto_execute_enabled: bool = False
    auto_execute_require_cli_flag: bool = True
    auto_execute_allowed_symbols: list[str] = field(default_factory=list)
    auto_execute_allowed_markets: list[str] = field(default_factory=lambda: ["futures"])
    auto_execute_live_enabled: bool = False
    auto_execute_kill_switch: bool = False
    auto_execute_min_confidence: float = 0.88
    auto_execute_min_consensus_strength: float = 0.75
    auto_execute_min_quality_score: float = 60.0
    auto_execute_max_warning_count: int = 2
    auto_execute_max_daily_orders: int = 3
    auto_execute_max_daily_notional_usdt: float = 25_000.0
    auto_execute_symbol_cooldown_minutes: int = 60
    auto_execute_block_on_open_orders: bool = True
    auto_execute_require_sl_tp: bool = True
    auto_execute_require_entry_zone_match: bool = True
    auto_execute_allowed_edge_labels: list[str] = field(
        default_factory=lambda: ["allowlist_promoted", "promoted"]
    )
    auto_execute_min_edge_expectancy_pnl_pct: float = 0.0
    auto_execute_max_batch_capital_pct: float = 6.0
    edge_policy_enabled: bool = True
    edge_policy_min_samples: int = 3
    edge_policy_promote_min_expectancy_pnl_pct: float = 1.0
    edge_policy_promote_min_profit_factor: float = 1.2
    edge_policy_promote_size_multiplier: float = 1.15
    edge_policy_block_max_expectancy_pnl_pct: float = -0.25
    edge_policy_block_max_profit_factor: float = 0.9
    edge_policy_block_negative_slices: bool = True
    edge_policy_allowlist: list[str] = field(default_factory=list)
    edge_policy_denylist: list[str] = field(default_factory=list)
    edge_policy_allowlist_size_multiplier: float = 1.25
    edge_tune_max_entries_per_section: int = 2
    edge_policy_walk_forward_required: bool = True
    edge_policy_walk_forward_min_windows: int = 2
    edge_policy_walk_forward_min_test_expectancy_pct: float = 0.0
    edge_policy_walk_forward_min_positive_ratio: float = 50.0
    opportunity_score_enabled: bool = True
    opportunity_score_min_auto_execute: float = 72.0
    stable_allowed_tiers: list[str] = field(
        default_factory=lambda: ["core", "major_alt", "liquid_alt"]
    )
    stable_blocked_narratives: list[str] = field(default_factory=lambda: ["meme"])
    stable_allowed_btc_regimes: list[str] = field(
        default_factory=lambda: ["risk_on_trend", "rebound", "range"]
    )


@dataclass
class NotificationConfig:
    """Notification push configuration (Feishu webhook)."""

    feishu_webhook_url: str = ""
    enabled: bool = True
    on_risk_alert: bool = True
    on_pipeline_complete: bool = True
    on_batch_scan: bool = True
    on_sl_tp_trigger: bool = True


@dataclass
class EventsConfig:
    """Event source configuration (macro calendar + token unlocks)."""

    enabled: bool = True
    macro_calendar_enabled: bool = True
    token_unlocks_enabled: bool = True
    # High-impact event: hours before event to trigger position compression
    high_impact_hours_before: int = 24
    # Position size reduction factor (0.5 = reduce by 50%)
    high_impact_size_reduction: float = 0.5
    # Max leverage allowed near high-impact events
    high_impact_max_leverage: int = 10


@dataclass
class RebalanceConfig:
    """Portfolio rebalance configuration."""

    enabled: bool = True
    target_gross_exposure_pct: float = 70.0
    deviation_threshold_pct: float = 15.0
    max_single_reduce_pct: float = 50.0
    auto_after_batch: bool = True


@dataclass
class MonitorConfig:
    """Monitoring and WebSocket stream configuration."""

    interval_seconds: int = 60
    price_stream_reconnect: bool = True
    stream_symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    scan_symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    batch_top_n_signals: int = 5
    batch_auto_execute_max_candidates: int = 2


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
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    events: EventsConfig = field(default_factory=EventsConfig)
    rebalance: RebalanceConfig = field(default_factory=RebalanceConfig)

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
    notification_dict = config_dict.get("notification", {})
    events_dict = config_dict.get("events", {})
    rebalance_dict = config_dict.get("rebalance", {})

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
        notification=NotificationConfig(**_filter(NotificationConfig, notification_dict)),
        events=EventsConfig(**_filter(EventsConfig, events_dict)),
        rebalance=RebalanceConfig(**_filter(RebalanceConfig, rebalance_dict)),
    )


def validate_config(config: AppConfig) -> dict[str, list[str]]:
    """Return validation results with 'errors' (block startup) and 'warnings' (advisory)."""
    errors: list[str] = []
    warnings: list[str] = []

    # --- Critical errors (block startup) ---
    if not Path(config.vault.path).exists():
        errors.append(f"Vault path does not exist: {config.vault.path}")

    if not config.binance.api_key or not config.binance.api_secret:
        errors.append("Binance API credentials not set in .env or config")

    if config.risk.max_account_drawdown >= 0:
        errors.append("max_account_drawdown must be negative")

    if config.risk.alert_drawdown >= 0:
        errors.append("alert_drawdown must be negative")

    # Leverage range
    if not (1 <= config.risk.max_leverage <= 125):
        errors.append(f"max_leverage must be 1~125, got {config.risk.max_leverage}")

    # Position cap percentages must be 0~1
    for field_name in ("core_position_cap_pct", "major_alt_position_cap_pct",
                       "liquid_alt_position_cap_pct", "mid_alt_position_cap_pct"):
        val = getattr(config.risk, field_name, 0)
        if not (0 < val <= 1.0):
            errors.append(f"{field_name} must be (0, 1.0], got {val}")

    # Stop loss / take profit range
    if not (0 < config.trading.default_stop_loss_pct <= 1.0):
        errors.append(f"default_stop_loss_pct must be (0, 1.0], got {config.trading.default_stop_loss_pct}")
    if not (0 < config.trading.default_take_profit_pct <= 1.0):
        errors.append(f"default_take_profit_pct must be (0, 1.0], got {config.trading.default_take_profit_pct}")

    # Equity curve protection params
    if config.risk.equity_ema_period < 2:
        errors.append(f"equity_ema_period must be >= 2, got {config.risk.equity_ema_period}")
    if not (0 < config.risk.equity_pause_factor <= 1.0):
        errors.append(f"equity_pause_factor must be (0, 1.0], got {config.risk.equity_pause_factor}")
    if config.risk.vol_ceiling_pct <= 0:
        errors.append(f"vol_ceiling_pct must be > 0, got {config.risk.vol_ceiling_pct}")
    if config.claude.adaptive_prompt_min_trades < 1:
        errors.append(
            f"adaptive_prompt_min_trades must be >= 1, got {config.claude.adaptive_prompt_min_trades}"
        )
    if config.claude.adaptive_prompt_lookback_trades < 1:
        errors.append(
            "adaptive_prompt_lookback_trades must be >= 1, "
            f"got {config.claude.adaptive_prompt_lookback_trades}"
        )
    if config.claude.adaptive_prompt_lookback_runs < 1:
        errors.append(
            "adaptive_prompt_lookback_runs must be >= 1, "
            f"got {config.claude.adaptive_prompt_lookback_runs}"
        )
    if config.claude.adaptive_prompt_loss_streak_threshold < 1:
        errors.append(
            "adaptive_prompt_loss_streak_threshold must be >= 1, "
            f"got {config.claude.adaptive_prompt_loss_streak_threshold}"
        )
    if config.trading.stable_min_reward_risk_ratio <= 0:
        errors.append(
            "stable_min_reward_risk_ratio must be > 0, "
            f"got {config.trading.stable_min_reward_risk_ratio}"
        )
    if config.trading.stable_same_symbol_loss_cooldown_hours < 0:
        errors.append(
            "stable_same_symbol_loss_cooldown_hours must be >= 0, "
            f"got {config.trading.stable_same_symbol_loss_cooldown_hours}"
        )
    if config.trading.stable_loss_streak_block_count < 1:
        errors.append(
            "stable_loss_streak_block_count must be >= 1, "
            f"got {config.trading.stable_loss_streak_block_count}"
        )
    if not (0.0 <= config.trading.conviction_override_confidence <= 1.0):
        errors.append(
            "conviction_override_confidence must be in [0, 1], "
            f"got {config.trading.conviction_override_confidence}"
        )
    if not (0.0 <= config.trading.conviction_override_consensus_strength <= 1.0):
        errors.append(
            "conviction_override_consensus_strength must be in [0, 1], "
            f"got {config.trading.conviction_override_consensus_strength}"
        )
    if config.trading.conviction_override_min_reward_risk_ratio <= 0:
        errors.append(
            "conviction_override_min_reward_risk_ratio must be > 0, "
            f"got {config.trading.conviction_override_min_reward_risk_ratio}"
        )
    if config.trading.conviction_override_size_pct <= 0:
        errors.append(
            f"conviction_override_size_pct must be > 0, got {config.trading.conviction_override_size_pct}"
        )
    if config.trading.conviction_override_max_leverage < 1:
        errors.append(
            "conviction_override_max_leverage must be >= 1, "
            f"got {config.trading.conviction_override_max_leverage}"
        )
    if not (0.0 <= config.trading.auto_execute_min_confidence <= 1.0):
        errors.append(
            "auto_execute_min_confidence must be in [0, 1], "
            f"got {config.trading.auto_execute_min_confidence}"
        )
    if not (0.0 <= config.trading.auto_execute_min_consensus_strength <= 1.0):
        errors.append(
            "auto_execute_min_consensus_strength must be in [0, 1], "
            f"got {config.trading.auto_execute_min_consensus_strength}"
        )
    if config.trading.auto_execute_min_quality_score < 0:
        errors.append(
            "auto_execute_min_quality_score must be >= 0, "
            f"got {config.trading.auto_execute_min_quality_score}"
        )
    if config.trading.auto_execute_max_warning_count < 0:
        errors.append(
            "auto_execute_max_warning_count must be >= 0, "
            f"got {config.trading.auto_execute_max_warning_count}"
        )
    if config.trading.auto_execute_max_daily_orders < 1:
        errors.append(
            "auto_execute_max_daily_orders must be >= 1, "
            f"got {config.trading.auto_execute_max_daily_orders}"
        )
    if config.trading.auto_execute_symbol_cooldown_minutes < 0:
        errors.append(
            "auto_execute_symbol_cooldown_minutes must be >= 0, "
            f"got {config.trading.auto_execute_symbol_cooldown_minutes}"
        )
    if config.trading.auto_execute_max_daily_notional_usdt < 0:
        errors.append(
            "auto_execute_max_daily_notional_usdt must be >= 0, "
            f"got {config.trading.auto_execute_max_daily_notional_usdt}"
        )
    if config.trading.auto_execute_max_batch_capital_pct <= 0:
        errors.append(
            "auto_execute_max_batch_capital_pct must be > 0, "
            f"got {config.trading.auto_execute_max_batch_capital_pct}"
        )
    if config.trading.edge_tune_max_entries_per_section < 1:
        errors.append(
            "edge_tune_max_entries_per_section must be >= 1, "
            f"got {config.trading.edge_tune_max_entries_per_section}"
        )
    if config.trading.edge_policy_min_samples < 1:
        errors.append(
            "edge_policy_min_samples must be >= 1, "
            f"got {config.trading.edge_policy_min_samples}"
        )
    if config.trading.edge_policy_promote_size_multiplier <= 0:
        errors.append(
            "edge_policy_promote_size_multiplier must be > 0, "
            f"got {config.trading.edge_policy_promote_size_multiplier}"
        )
    if config.trading.edge_policy_allowlist_size_multiplier <= 0:
        errors.append(
            "edge_policy_allowlist_size_multiplier must be > 0, "
            f"got {config.trading.edge_policy_allowlist_size_multiplier}"
        )
    if config.monitor.batch_top_n_signals < 1:
        errors.append(
            f"batch_top_n_signals must be >= 1, got {config.monitor.batch_top_n_signals}"
        )
    if config.monitor.batch_auto_execute_max_candidates < 1:
        errors.append(
            "batch_auto_execute_max_candidates must be >= 1, "
            f"got {config.monitor.batch_auto_execute_max_candidates}"
        )
    if not (0.0 <= config.trading.opportunity_score_min_auto_execute <= 100.0):
        errors.append(
            "opportunity_score_min_auto_execute must be in [0, 100], "
            f"got {config.trading.opportunity_score_min_auto_execute}"
        )
    if config.trading.edge_policy_walk_forward_min_windows < 1:
        errors.append(
            "edge_policy_walk_forward_min_windows must be >= 1, "
            f"got {config.trading.edge_policy_walk_forward_min_windows}"
        )
    if not (0.0 <= config.trading.edge_policy_walk_forward_min_positive_ratio <= 100.0):
        errors.append(
            "edge_policy_walk_forward_min_positive_ratio must be in [0, 100], "
            f"got {config.trading.edge_policy_walk_forward_min_positive_ratio}"
        )

    # --- Warnings (advisory, non-blocking) ---
    # Volatility thresholds ordering
    if config.risk.high_volatility_24h_pct >= config.risk.extreme_move_24h_pct:
        warnings.append(
            f"high_volatility_24h_pct ({config.risk.high_volatility_24h_pct}) "
            f"should be < extreme_move_24h_pct ({config.risk.extreme_move_24h_pct})"
        )

    # Funding rate sanity
    if config.risk.max_abs_funding_rate > 0.01:
        warnings.append(f"max_abs_funding_rate ({config.risk.max_abs_funding_rate}) seems unusually high (> 0.01)")

    # Portfolio exposure ordering
    if config.risk.portfolio_soft_gross_exposure_pct >= config.risk.portfolio_hard_gross_exposure_pct:
        warnings.append(
            f"portfolio_soft_gross_exposure_pct ({config.risk.portfolio_soft_gross_exposure_pct}) "
            f"should be < portfolio_hard_gross_exposure_pct ({config.risk.portfolio_hard_gross_exposure_pct})"
        )

    # Narrative concentration vs max positions
    if config.risk.max_positions_per_narrative > config.risk.portfolio_max_positions:
        warnings.append(
            f"max_positions_per_narrative ({config.risk.max_positions_per_narrative}) "
            f"> portfolio_max_positions ({config.risk.portfolio_max_positions})"
        )

    # Drawdown ordering
    if config.risk.alert_drawdown <= config.risk.suspend_drawdown:
        warnings.append(
            f"alert_drawdown ({config.risk.alert_drawdown}) should be > suspend_drawdown ({config.risk.suspend_drawdown})"
        )

    return {"errors": errors, "warnings": warnings}
