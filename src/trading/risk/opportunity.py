"""Unified opportunity scoring for ranking and auto-execution decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..ai.schemas import ExecutionPlan, ResearchDecision, RiskDecision
from ..core.config import AppConfig


@dataclass
class OpportunityDecision:
    """A normalized 0-100 score for current trade attractiveness."""

    score: float
    bucket: str
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class OpportunityScorer:
    """Combine confidence, quality, edge, and warnings into one score."""

    def __init__(self, config: AppConfig):
        self.config = config

    @staticmethod
    def _bucket(score: float) -> str:
        """Map numeric score to an easy-to-scan label."""
        if score >= 85:
            return "elite"
        if score >= 72:
            return "strong"
        if score >= 58:
            return "watchlist"
        return "avoid"

    def evaluate(
        self,
        *,
        research: ResearchDecision,
        plan: ExecutionPlan,
        risk: RiskDecision,
    ) -> OpportunityDecision:
        """Return a single opportunity score for the current setup."""
        score = 0.0
        reasons: list[str] = []

        confidence_score = research.confidence * 25.0
        score += confidence_score
        reasons.append(f"confidence contributes {confidence_score:.1f}")

        consensus = research.consensus_strength if research.supporting_model_count > 1 else 0.8
        consensus_score = consensus * 12.0
        score += consensus_score
        reasons.append(f"consensus contributes {consensus_score:.1f}")

        quality = float(risk.setup_quality_score or 0.0)
        quality_score = quality * 0.35
        score += quality_score
        reasons.append(f"setup quality contributes {quality_score:.1f}")

        rr_score = 0.0
        if plan.stop_loss_pct > 0 and plan.take_profit_pct > 0:
            rr = plan.take_profit_pct / plan.stop_loss_pct
            rr_score = min(rr, 3.0) / 3.0 * 10.0
            score += rr_score
            reasons.append(f"reward/risk contributes {rr_score:.1f}")

        edge_expectancy = risk.edge_policy_expectancy_pnl_pct
        if edge_expectancy is not None:
            edge_score = max(-10.0, min(15.0, edge_expectancy * 4.0))
            score += edge_score
            reasons.append(f"edge expectancy contributes {edge_score:.1f}")

        label = risk.edge_policy_label or ""
        if label in {"allowlist_promoted", "promoted"}:
            score += 8.0
            reasons.append("promoted edge policy bonus +8")
        elif label in {"blocked", "denylist_blocked"}:
            score -= 20.0
            reasons.append("negative edge policy penalty -20")

        warning_penalty = min(len(risk.warnings), 5) * 3.0
        if warning_penalty:
            score -= warning_penalty
            reasons.append(f"warning penalty -{warning_penalty:.1f}")

        if not risk.approved:
            score -= 15.0
            reasons.append("risk gate rejection penalty -15")

        score = max(0.0, min(100.0, round(score, 1)))
        return OpportunityDecision(score=score, bucket=self._bucket(score), reasons=reasons)
