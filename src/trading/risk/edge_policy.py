"""Expectancy-driven policy overlay to amplify historically profitable slices."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from ..core.config import AppConfig


@dataclass
class EdgePolicyDecision:
    """Decision from edge research for the current setup."""

    label: str
    size_multiplier: float
    matched_groups: list[str]
    reasons: list[str]
    expectancy_pnl_pct: Optional[float] = None
    profit_factor: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EdgePolicySuggestion:
    """A suggested allowlist or denylist entry derived from edge stats."""

    policy: str
    entry: str
    samples: int
    expectancy_pnl_pct: float
    profit_factor: Optional[float]
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class EdgePolicyAdvisor:
    """Classify current setup as promoted / neutral / blocked based on edge stats."""

    def __init__(self, config: AppConfig):
        self.config = config

    @staticmethod
    def _current_aliases(current: dict[str, str]) -> dict[str, str]:
        """Return user-facing aliases for current slice values."""
        return {
            "regime": current["by_regime"],
            "template": current["by_template"],
            "narrative": current["by_narrative"],
            "quality": current["by_quality_grade"],
            "profile": current["by_profile_mode"],
        }

    @staticmethod
    def _matches_policy_entry(entry: str, current: dict[str, str], aliases: dict[str, str]) -> bool:
        """Return True when a policy entry matches the current setup slice."""
        if ":" not in entry:
            return False
        left, right = entry.split(":", 1)
        key = left.strip()
        value = right.strip()
        if key in current:
            return current[key] == value
        if key in aliases:
            return aliases[key] == value
        return False

    @staticmethod
    def _lookup(rows: list[dict], group: str) -> Optional[dict]:
        """Find a row by group name."""
        for row in rows or []:
            if str(row.get("group")) == str(group):
                return row
        return None

    def evaluate(
        self,
        *,
        market_snapshot: dict,
        risk_decision: dict,
        edge_stats: dict,
        walk_forward_summary: dict | None = None,
    ) -> EdgePolicyDecision:
        """Evaluate the current setup against historical edge slices."""
        tc = self.config.trading
        if not tc.edge_policy_enabled:
            return EdgePolicyDecision(
                label="disabled",
                size_multiplier=1.0,
                matched_groups=[],
                reasons=["edge policy disabled"],
            )

        current = {
            "by_regime": str(market_snapshot.get("btc_market_regime", "unknown")),
            "by_template": str(market_snapshot.get("execution_template", "unknown")),
            "by_narrative": str(market_snapshot.get("narrative_tag", "unknown")),
            "by_quality_grade": str(risk_decision.get("setup_quality_grade", "")),
            "by_profile_mode": str(risk_decision.get("gating_profile", "")),
        }
        aliases = self._current_aliases(current)

        positive_hits: list[dict] = []
        negative_hits: list[dict] = []
        reasons: list[str] = []
        matched_names: list[str] = []

        for entry in tc.edge_policy_denylist:
            if self._matches_policy_entry(str(entry), current, aliases):
                return EdgePolicyDecision(
                    label="denylist_blocked",
                    size_multiplier=0.0,
                    matched_groups=[str(entry)],
                    reasons=[f"denylist matched: {entry}"],
                )

        matched_allowlist = [str(entry) for entry in tc.edge_policy_allowlist if self._matches_policy_entry(str(entry), current, aliases)]
        if matched_allowlist:
            return EdgePolicyDecision(
                label="allowlist_promoted",
                size_multiplier=tc.edge_policy_allowlist_size_multiplier,
                matched_groups=matched_allowlist,
                reasons=[f"allowlist matched: {matched_allowlist[0]}"],
            )

        wf_summary = walk_forward_summary or {}
        wf_windows = int(wf_summary.get("windows", 0) or 0)
        wf_avg_test_exp = float(wf_summary.get("avg_test_expectancy_pct", 0.0) or 0.0)
        wf_positive_ratio = float(wf_summary.get("positive_test_ratio", 0.0) or 0.0)
        wf_gate_ready = (
            tc.edge_policy_walk_forward_required
            and wf_windows >= tc.edge_policy_walk_forward_min_windows
        )
        wf_gate_pass = (
            wf_gate_ready
            and wf_avg_test_exp >= tc.edge_policy_walk_forward_min_test_expectancy_pct
            and wf_positive_ratio >= tc.edge_policy_walk_forward_min_positive_ratio
        )

        for section, group in current.items():
            row = self._lookup(edge_stats.get(section, []), group)
            if not row:
                continue
            if int(row.get("samples", 0) or 0) < tc.edge_policy_min_samples:
                continue
            matched_names.append(f"{section}:{group}")
            exp_pct = float(row.get("expectancy_pnl_pct", 0.0) or 0.0)
            pf = row.get("profit_factor")
            pf_value = float(pf) if pf is not None else None
            if exp_pct >= tc.edge_policy_promote_min_expectancy_pnl_pct and (
                pf_value is None or pf_value >= tc.edge_policy_promote_min_profit_factor
            ):
                positive_hits.append(row)
            if exp_pct <= tc.edge_policy_block_max_expectancy_pnl_pct or (
                pf_value is not None and pf_value <= tc.edge_policy_block_max_profit_factor
            ):
                negative_hits.append(row)

        if tc.edge_policy_block_negative_slices and negative_hits:
            worst = sorted(
                negative_hits,
                key=lambda row: (
                    float(row.get("expectancy_pnl_pct", 0.0) or 0.0),
                    float(row.get("profit_factor", 999.0) or 999.0),
                ),
            )[0]
            reasons.append(
                f"historical edge negative for {worst['group']} "
                f"(expectancy {float(worst.get('expectancy_pnl_pct', 0.0) or 0.0):+.2f}%"
                + (
                    f", PF {float(worst.get('profit_factor', 0.0)):.2f}"
                    if worst.get("profit_factor") is not None
                    else ""
                )
                + ")"
            )
            return EdgePolicyDecision(
                label="blocked",
                size_multiplier=0.0,
                matched_groups=matched_names,
                reasons=reasons,
                expectancy_pnl_pct=float(worst.get("expectancy_pnl_pct", 0.0) or 0.0),
                profit_factor=float(worst.get("profit_factor")) if worst.get("profit_factor") is not None else None,
            )

        if positive_hits:
            best = sorted(
                positive_hits,
                key=lambda row: (
                    -float(row.get("expectancy_pnl_pct", 0.0) or 0.0),
                    -(float(row.get("profit_factor", 0.0) or 0.0)),
                    -int(row.get("samples", 0) or 0),
                ),
            )[0]
            if wf_gate_ready and not wf_gate_pass:
                reasons.append(
                    f"walk-forward weak (avg_test_expectancy={wf_avg_test_exp:+.2f}%, "
                    f"positive_ratio={wf_positive_ratio:.1f}%)"
                )
                return EdgePolicyDecision(
                    label="wf_neutralized",
                    size_multiplier=1.0,
                    matched_groups=matched_names,
                    reasons=reasons,
                    expectancy_pnl_pct=float(best.get("expectancy_pnl_pct", 0.0) or 0.0),
                    profit_factor=float(best.get("profit_factor")) if best.get("profit_factor") is not None else None,
                )
            reasons.append(
                f"historical edge positive for {best['group']} "
                f"(expectancy {float(best.get('expectancy_pnl_pct', 0.0) or 0.0):+.2f}%"
                + (
                    f", PF {float(best.get('profit_factor', 0.0)):.2f}"
                    if best.get("profit_factor") is not None
                    else ""
                )
                + ")"
            )
            return EdgePolicyDecision(
                label="promoted",
                size_multiplier=tc.edge_policy_promote_size_multiplier,
                matched_groups=matched_names,
                reasons=reasons,
                expectancy_pnl_pct=float(best.get("expectancy_pnl_pct", 0.0) or 0.0),
                profit_factor=float(best.get("profit_factor")) if best.get("profit_factor") is not None else None,
            )

        if matched_names:
            reasons.append("historical slices matched but do not yet justify promotion or block")
            return EdgePolicyDecision(
                label="neutral",
                size_multiplier=1.0,
                matched_groups=matched_names,
                reasons=reasons,
            )

        return EdgePolicyDecision(
            label="insufficient_data",
            size_multiplier=1.0,
            matched_groups=[],
            reasons=["not enough linked samples for current slice"],
        )

    def suggest(self, edge_stats: dict, walk_forward_summary: dict | None = None) -> list[EdgePolicySuggestion]:
        """Generate allowlist / denylist suggestions from grouped edge stats."""
        tc = self.config.trading
        suggestions: list[EdgePolicySuggestion] = []
        wf_ok = True
        if tc.edge_policy_walk_forward_required:
            wf_windows = int((walk_forward_summary or {}).get("windows", 0) or 0)
            wf_avg_test_exp = float((walk_forward_summary or {}).get("avg_test_expectancy_pct", 0.0) or 0.0)
            wf_ok = (
                wf_windows >= tc.edge_policy_walk_forward_min_windows
                and wf_avg_test_exp >= tc.edge_policy_walk_forward_min_test_expectancy_pct
            )
        sections = {
            "regime": edge_stats.get("by_regime", []),
            "template": edge_stats.get("by_template", []),
            "narrative": edge_stats.get("by_narrative", []),
        }

        for section_name, rows in sections.items():
            promoted = [
                row for row in rows
                if int(row.get("samples", 0) or 0) >= tc.edge_policy_min_samples
                and float(row.get("expectancy_pnl_pct", 0.0) or 0.0) >= tc.edge_policy_promote_min_expectancy_pnl_pct
                and (
                    row.get("profit_factor") is None
                    or float(row.get("profit_factor", 0.0) or 0.0) >= tc.edge_policy_promote_min_profit_factor
                )
            ]
            blocked = [
                row for row in rows
                if int(row.get("samples", 0) or 0) >= tc.edge_policy_min_samples
                and (
                    float(row.get("expectancy_pnl_pct", 0.0) or 0.0) <= tc.edge_policy_block_max_expectancy_pnl_pct
                    or (
                        row.get("profit_factor") is not None
                        and float(row.get("profit_factor", 0.0) or 0.0) <= tc.edge_policy_block_max_profit_factor
                    )
                )
            ]

            promoted.sort(
                key=lambda row: (
                    -float(row.get("expectancy_pnl_pct", 0.0) or 0.0),
                    -(float(row.get("profit_factor", 0.0) or 0.0)),
                    -int(row.get("samples", 0) or 0),
                )
            )
            blocked.sort(
                key=lambda row: (
                    float(row.get("expectancy_pnl_pct", 0.0) or 0.0),
                    float(row.get("profit_factor", 999.0) or 999.0),
                    -int(row.get("samples", 0) or 0),
                )
            )

            if wf_ok:
                for row in promoted[: tc.edge_tune_max_entries_per_section]:
                    suggestions.append(
                        EdgePolicySuggestion(
                            policy="allowlist",
                            entry=f"{section_name}:{row['group']}",
                            samples=int(row.get("samples", 0) or 0),
                            expectancy_pnl_pct=float(row.get("expectancy_pnl_pct", 0.0) or 0.0),
                            profit_factor=float(row.get("profit_factor")) if row.get("profit_factor") is not None else None,
                            reason="historically strong positive edge",
                        )
                    )
            for row in blocked[: tc.edge_tune_max_entries_per_section]:
                suggestions.append(
                    EdgePolicySuggestion(
                        policy="denylist",
                        entry=f"{section_name}:{row['group']}",
                        samples=int(row.get("samples", 0) or 0),
                        expectancy_pnl_pct=float(row.get("expectancy_pnl_pct", 0.0) or 0.0),
                        profit_factor=float(row.get("profit_factor")) if row.get("profit_factor") is not None else None,
                        reason="historically negative edge",
                    )
                )

        suggestions.sort(
            key=lambda s: (
                0 if s.policy == "allowlist" else 1,
                -(s.expectancy_pnl_pct if s.policy == "allowlist" else -s.expectancy_pnl_pct),
                -s.samples,
                s.entry,
            )
        )
        return suggestions
