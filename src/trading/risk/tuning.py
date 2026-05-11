"""Deterministic risk-parameter tuning suggestions from linked real outcomes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..core.config import AppConfig


@dataclass
class RiskTuningSuggestion:
    """A single conservative tuning suggestion for config parameters."""

    config_path: str
    current_value: Any
    suggested_value: Any
    priority: str
    reason: str
    evidence: str

    def to_dict(self) -> dict:
        return asdict(self)


class RiskTuningAdvisor:
    """Generate conservative config-tuning suggestions from replay and risk stats."""

    def __init__(self, config: AppConfig):
        self.config = config

    @staticmethod
    def _rows_by_group(rows: list[dict]) -> dict[str, dict]:
        """Index aggregate rows by their group key."""
        return {str(row.get("group", "unknown")): row for row in rows or []}

    @staticmethod
    def _effect_by_rule(rows: list[dict]) -> dict[str, dict]:
        """Index rule-effect rows by rule name."""
        return {str(row.get("rule", "")): row for row in rows or []}

    @staticmethod
    def _nested_patch_value(patch: dict, dotted_key: str, value: Any) -> None:
        """Set a nested value on a dict using dotted path notation."""
        parts = dotted_key.split(".")
        cur = patch
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value

    def _suggest(
        self,
        suggestions: dict[str, RiskTuningSuggestion],
        config_path: str,
        current_value: Any,
        suggested_value: Any,
        priority: str,
        reason: str,
        evidence: str,
    ) -> None:
        """Add or replace a suggestion, preferring the more conservative value."""
        if suggested_value == current_value:
            return

        existing = suggestions.get(config_path)
        candidate = RiskTuningSuggestion(
            config_path=config_path,
            current_value=current_value,
            suggested_value=suggested_value,
            priority=priority,
            reason=reason,
            evidence=evidence,
        )
        if existing is None:
            suggestions[config_path] = candidate
            return

        current_existing = existing.suggested_value
        if isinstance(suggested_value, (int, float)) and isinstance(current_existing, (int, float)):
            if suggested_value < current_existing:
                suggestions[config_path] = candidate
        else:
            suggestions[config_path] = candidate

    def suggest(
        self,
        replay_stats: dict,
        risk_stats: dict,
        walk_forward_summary: dict | None = None,
    ) -> list[RiskTuningSuggestion]:
        """Return a sorted list of conservative tuning suggestions."""
        suggestions: dict[str, RiskTuningSuggestion] = {}

        by_narrative = self._rows_by_group(replay_stats.get("by_narrative", []))
        by_template = self._rows_by_group(replay_stats.get("by_template", []))
        by_regime = self._rows_by_group(replay_stats.get("by_regime", []))
        by_profile = self._rows_by_group(replay_stats.get("by_profile_mode", []))
        by_opp_bucket = self._rows_by_group(replay_stats.get("by_opportunity_bucket", []))
        warning_effect = self._effect_by_rule(risk_stats.get("warning_effectiveness", []))
        wf_avg_test_expectancy = float((walk_forward_summary or {}).get("avg_test_expectancy_pct", 0.0) or 0.0)
        wf_windows = int((walk_forward_summary or {}).get("windows", 0) or 0)

        meme = by_narrative.get("meme")
        if meme and meme.get("samples", 0) >= 3 and (meme.get("avg_pnl_pct") or 0.0) < 0:
            current_lev = int(self.config.risk.meme_max_leverage)
            current_size = float(self.config.risk.meme_max_position_size_pct)
            self._suggest(
                suggestions,
                "risk.meme_max_leverage",
                current_lev,
                max(1, current_lev - 1),
                "high",
                "Meme narrative linked results are underperforming; tighten leverage.",
                (
                    f"meme samples={meme['samples']}, win_rate={meme.get('win_rate')}, "
                    f"avg_pnl_pct={meme.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )
            self._suggest(
                suggestions,
                "risk.meme_max_position_size_pct",
                current_size,
                round(max(0.001, current_size * 0.8), 4),
                "high",
                "Meme narrative linked results are underperforming; tighten position sizing.",
                (
                    f"meme samples={meme['samples']}, win_rate={meme.get('win_rate')}, "
                    f"avg_pnl_pct={meme.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )

        high_beta = by_template.get("high_beta_confirmation_only")
        if high_beta and high_beta.get("samples", 0) >= 3 and (high_beta.get("avg_pnl_pct") or 0.0) < 0:
            current_lev = int(self.config.risk.high_beta_alt_max_leverage)
            current_size = float(self.config.risk.high_beta_alt_max_position_size_pct)
            self._suggest(
                suggestions,
                "risk.high_beta_alt_max_leverage",
                current_lev,
                max(1, current_lev - 1),
                "high",
                "High-beta template linked outcomes are weak; reduce leverage cap.",
                (
                    f"template samples={high_beta['samples']}, win_rate={high_beta.get('win_rate')}, "
                    f"avg_pnl_pct={high_beta.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )
            self._suggest(
                suggestions,
                "risk.high_beta_alt_max_position_size_pct",
                current_size,
                round(max(0.001, current_size * 0.85), 4),
                "high",
                "High-beta template linked outcomes are weak; reduce max position size.",
                (
                    f"template samples={high_beta['samples']}, win_rate={high_beta.get('win_rate')}, "
                    f"avg_pnl_pct={high_beta.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )

        weak_regimes = []
        for name in ("panic_flush", "risk_off_trend"):
            row = by_regime.get(name)
            if row and row.get("samples", 0) >= 3 and (row.get("avg_pnl_pct") or 0.0) < 0:
                weak_regimes.append(row)
        if weak_regimes:
            combined_samples = sum(int(row.get("samples", 0)) for row in weak_regimes)
            worst_avg = min(float(row.get("avg_pnl_pct", 0.0) or 0.0) for row in weak_regimes)
            current_lev = int(self.config.risk.altcoin_max_leverage_when_btc_weak)
            current_size = float(self.config.risk.altcoin_max_position_size_when_btc_weak_pct)
            self._suggest(
                suggestions,
                "risk.altcoin_max_leverage_when_btc_weak",
                current_lev,
                max(1, current_lev - 1),
                "high",
                "Alt outcomes during BTC risk-off/panic regimes are weak; tighten leverage cap.",
                f"combined_samples={combined_samples}, worst_avg_pnl_pct={worst_avg:+.2f}%",
            )
            self._suggest(
                suggestions,
                "risk.altcoin_max_position_size_when_btc_weak_pct",
                current_size,
                round(max(0.001, current_size * 0.85), 4),
                "high",
                "Alt outcomes during BTC risk-off/panic regimes are weak; tighten size cap.",
                f"combined_samples={combined_samples}, worst_avg_pnl_pct={worst_avg:+.2f}%",
            )

        crowded_rule = None
        for name, row in warning_effect.items():
            if "funding" in name.lower() and row.get("linked_trades", 0) >= 3 and (row.get("avg_pnl_pct") or 0.0) < 0:
                crowded_rule = row
                break
        if crowded_rule:
            current_funding = float(self.config.risk.max_abs_funding_rate)
            self._suggest(
                suggestions,
                "risk.max_abs_funding_rate",
                current_funding,
                round(max(0.0001, current_funding * 0.8), 6),
                "medium",
                "Funding-related warnings are frequently followed by weak linked outcomes; tighten the funding cap.",
                (
                    f"rule={crowded_rule['rule']}, linked_trades={crowded_rule['linked_trades']}, "
                    f"avg_pnl_pct={crowded_rule.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )

        event_rule = None
        for name, row in warning_effect.items():
            lowered = name.lower()
            if ("event" in lowered or "unlock" in lowered or "macro" in lowered) and row.get("linked_trades", 0) >= 3 and (row.get("avg_pnl_pct") or 0.0) < 0:
                event_rule = row
                break
        if event_rule:
            current_reduce = float(self.config.events.high_impact_size_reduction)
            current_lev = int(self.config.events.high_impact_max_leverage)
            self._suggest(
                suggestions,
                "events.high_impact_size_reduction",
                current_reduce,
                round(min(0.9, current_reduce + 0.1), 2),
                "medium",
                "High-impact event exposure still produces weak linked outcomes; compress size more aggressively.",
                (
                    f"rule={event_rule['rule']}, linked_trades={event_rule['linked_trades']}, "
                    f"avg_pnl_pct={event_rule.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )
            self._suggest(
                suggestions,
                "events.high_impact_max_leverage",
                current_lev,
                max(1, current_lev - 1),
                "medium",
                "High-impact event exposure still produces weak linked outcomes; lower leverage cap.",
                (
                    f"rule={event_rule['rule']}, linked_trades={event_rule['linked_trades']}, "
                    f"avg_pnl_pct={event_rule.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )

        normal = by_profile.get("normal")
        defensive = by_profile.get("defensive")
        if (
            normal
            and defensive
            and normal.get("samples", 0) >= 5
            and (normal.get("avg_pnl_pct") or 0.0) < (defensive.get("avg_pnl_pct") or 0.0)
            and self.config.claude.adaptive_prompt_loss_streak_threshold > 1
        ):
            current_threshold = int(self.config.claude.adaptive_prompt_loss_streak_threshold)
            self._suggest(
                suggestions,
                "claude.adaptive_prompt_loss_streak_threshold",
                current_threshold,
                max(1, current_threshold - 1),
                "medium",
                "Defensive adaptive mode is outperforming normal mode; trigger it earlier.",
                (
                    f"normal_avg_pnl_pct={normal.get('avg_pnl_pct', 0.0):+.2f}%, "
                    f"defensive_avg_pnl_pct={defensive.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )

        if wf_windows >= 2 and wf_avg_test_expectancy < 0:
            current_size = float(self.config.trading.max_position_size_pct)
            self._suggest(
                suggestions,
                "trading.max_position_size_pct",
                current_size,
                round(max(0.005, current_size * 0.9), 4),
                "high",
                "Walk-forward test windows are negative on average; trim global risk per trade.",
                f"walk_forward windows={wf_windows}, avg_test_expectancy_pct={wf_avg_test_expectancy:+.2f}%",
            )

        avoid_bucket = by_opp_bucket.get("avoid")
        elite_bucket = by_opp_bucket.get("elite")
        if (
            avoid_bucket
            and elite_bucket
            and int(avoid_bucket.get("samples", 0) or 0) >= 5
            and float(avoid_bucket.get("avg_pnl_pct", 0.0) or 0.0) < 0
            and float(elite_bucket.get("avg_pnl_pct", 0.0) or 0.0) > 0
        ):
            current_threshold = float(self.config.trading.opportunity_score_min_auto_execute)
            self._suggest(
                suggestions,
                "trading.opportunity_score_min_auto_execute",
                current_threshold,
                min(95.0, current_threshold + 3.0),
                "medium",
                "Avoid bucket is consistently losing while elite bucket is positive; raise auto-execute score threshold.",
                (
                    f"avoid avg_pnl_pct={avoid_bucket.get('avg_pnl_pct', 0.0):+.2f}%, "
                    f"elite avg_pnl_pct={elite_bucket.get('avg_pnl_pct', 0.0):+.2f}%"
                ),
            )

        ordered = sorted(
            suggestions.values(),
            key=lambda s: ({"high": 0, "medium": 1, "low": 2}.get(s.priority, 9), s.config_path),
        )
        return ordered

    def export_patch(self, suggestions: list[RiskTuningSuggestion]) -> dict:
        """Convert suggestions into a nested patch dict suitable for YAML dump."""
        patch: dict[str, Any] = {}
        for suggestion in suggestions:
            self._nested_patch_value(patch, suggestion.config_path, suggestion.suggested_value)
        return patch
