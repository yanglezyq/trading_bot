"""Stable-mode behavioral rules: minimum reward/risk and post-loss cooldown."""

from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS
from .stable_common import is_conviction_setup


class RewardRiskRule(BaseRule):
    """Block low reward/risk setups when stable mode is enabled."""

    name = "reward_risk_gate"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return ctx.config.trading.stable_mode_enabled and ctx.plan.action in OPEN_ACTIONS

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        conviction = bool(getattr(ctx.config.trading, "conviction_override_enabled", True) and is_conviction_setup(ctx))
        stop_loss_pct = float(result.stop_loss_pct or 0.0)
        take_profit_pct = float(result.take_profit_pct or 0.0)
        if stop_loss_pct <= 0 or take_profit_pct <= 0:
            result.violated_rules.append("stable mode requires both stop_loss_pct and take_profit_pct to define reward/risk.")
            result.adjusted_action = "hold"
            result.adjusted_size_pct = 0.0
            return

        rr = take_profit_pct / stop_loss_pct
        min_rr = float(
            ctx.config.trading.conviction_override_min_reward_risk_ratio
            if conviction
            else ctx.config.trading.stable_min_reward_risk_ratio
        )
        if rr < min_rr:
            result.violated_rules.append(
                f"reward/risk {rr:.2f} is below stable minimum {min_rr:.2f}"
            )
            result.adjusted_action = "hold"
            result.adjusted_size_pct = 0.0


class LossCooldownRule(BaseRule):
    """After recent losses, force the system to cool down on the same symbol."""

    name = "loss_cooldown_gate"

    def applies(self, ctx: RuleContext, result: RuleResult) -> bool:
        return (
            ctx.config.trading.stable_mode_enabled
            and ctx.plan.action in OPEN_ACTIONS
            and bool(ctx.recent_trade_history)
        )

    def evaluate(self, ctx: RuleContext, result: RuleResult) -> None:
        conviction = bool(getattr(ctx.config.trading, "conviction_override_enabled", True) and is_conviction_setup(ctx))
        history = ctx.recent_trade_history or []
        cooldown_hours = int(ctx.config.trading.stable_same_symbol_loss_cooldown_hours)
        loss_streak_block_count = int(ctx.config.trading.stable_loss_streak_block_count)
        if cooldown_hours <= 0:
            return

        recent_losses = 0
        latest_loss_at: datetime | None = None

        for trade in history:
            pnl = float(trade.get("realized_pnl", 0.0) or 0.0)
            close_time = trade.get("close_time")
            close_dt: datetime | None = None
            if close_time:
                try:
                    close_dt = datetime.fromisoformat(str(close_time))
                except ValueError:
                    close_dt = None

            if pnl < 0:
                recent_losses += 1
                if latest_loss_at is None and close_dt is not None:
                    latest_loss_at = close_dt
            else:
                break

        if latest_loss_at is None:
            return

        now = datetime.now()
        cooldown_until = latest_loss_at + timedelta(hours=cooldown_hours)
        if now < cooldown_until:
            reason = (
                f"recent closed loss cooldown active until {cooldown_until.isoformat(timespec='minutes')}"
            )
            if conviction and recent_losses < loss_streak_block_count:
                result.warnings.append(
                    f"{reason}; high-conviction setup allowed, but size should stay disciplined."
                )
                result.adjusted_size_pct = min(result.adjusted_size_pct, ctx.config.trading.conviction_override_size_pct * 100)
                return
            if recent_losses >= loss_streak_block_count:
                result.violated_rules.append(
                    f"{reason}; consecutive loss streak={recent_losses}"
                )
            else:
                result.violated_rules.append(reason)
            result.adjusted_action = "hold"
            result.adjusted_size_pct = 0.0
