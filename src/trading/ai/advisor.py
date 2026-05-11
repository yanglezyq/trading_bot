"""TradingAdvisor: orchestrates Claude calls to produce structured trading decisions."""

from collections import Counter
from typing import Optional

from ..core.config import AppConfig
from .client import ClaudeClient
from .prompts import build_execution_prompt, build_reflection_prompt, build_research_prompt, build_combined_research_execution_prompt
from .schemas import ExecutionPlan, ResearchDecision


class TradingAdvisor:
    """Orchestrates AI calls for research, execution planning, and reflection."""

    def __init__(self, client: ClaudeClient, config: AppConfig):
        self.client = client
        self.config = config

    def _risk_params(self) -> dict:
        return {
            "max_leverage": self.config.risk.max_leverage,
            "max_position_size_pct": self.config.trading.max_position_size_pct,
            "event_driven_max_pct": self.config.trading.event_driven_max_pct,
            "default_stop_loss_pct": self.config.trading.default_stop_loss_pct,
            "default_take_profit_pct": self.config.trading.default_take_profit_pct,
        }

    def generate_research(
        self,
        symbol: str,
        account_summary: dict,
        positions: list[dict],
        market_snapshot: dict,
        vault_context: str,
        trade_history: list[dict],
        reflections: list[dict],
        onchain_snapshot: Optional[dict] = None,
        portfolio_snapshot: Optional[dict] = None,
        portfolio_budget: Optional[dict] = None,
        event_snapshot: Optional[dict] = None,
        adaptive_context: Optional[dict] = None,
    ) -> ResearchDecision:
        """Call Claude to generate a ResearchDecision for the given symbol."""
        system, user = build_research_prompt(
            symbol=symbol,
            account_summary=account_summary,
            positions=positions,
            market_snapshot=market_snapshot,
            onchain_snapshot=onchain_snapshot,
            vault_context=vault_context,
            trade_history=trade_history,
            reflections=reflections,
            risk_params=self._risk_params(),
            portfolio_snapshot=portfolio_snapshot,
            portfolio_budget=portfolio_budget,
            event_snapshot=event_snapshot,
            adaptive_context=adaptive_context,
        )
        raw = self.client.generate_json(system=system, user=user, max_retries=1, cache_system=True)
        decision = ResearchDecision.from_dict(raw)
        decision.symbol = symbol.upper()
        return decision

    def generate_research_ensemble(
        self,
        symbol: str,
        account_summary: dict,
        positions: list[dict],
        market_snapshot: dict,
        vault_context: str,
        trade_history: list[dict],
        reflections: list[dict],
        onchain_snapshot: Optional[dict] = None,
        portfolio_snapshot: Optional[dict] = None,
        portfolio_budget: Optional[dict] = None,
        event_snapshot: Optional[dict] = None,
        adaptive_context: Optional[dict] = None,
    ) -> ResearchDecision:
        """Generate research with multiple models and vote on stance."""
        system, user = build_research_prompt(
            symbol=symbol,
            account_summary=account_summary,
            positions=positions,
            market_snapshot=market_snapshot,
            onchain_snapshot=onchain_snapshot,
            vault_context=vault_context,
            trade_history=trade_history,
            reflections=reflections,
            risk_params=self._risk_params(),
            portfolio_snapshot=portfolio_snapshot,
            portfolio_budget=portfolio_budget,
            event_snapshot=event_snapshot,
            adaptive_context=adaptive_context,
        )

        models = [self.config.claude.model] + [
            m for m in self.config.claude.ensemble_models
            if m and m != self.config.claude.model
        ]
        decisions: list[tuple[str, ResearchDecision]] = []
        for idx, model_name in enumerate(models):
            raw = self.client.generate_json(
                system=system,
                user=user,
                max_retries=1,
                cache_system=(idx == 0),
                model_override=model_name,
            )
            decision = ResearchDecision.from_dict(raw)
            decision.symbol = symbol.upper()
            decisions.append((model_name, decision))

        stance_counts = Counter(d.stance for _, d in decisions)
        winning_stance, winning_count = stance_counts.most_common(1)[0]
        consensus_strength = winning_count / len(decisions)

        winner = max(
            (d for _, d in decisions if d.stance == winning_stance),
            key=lambda d: d.confidence,
        )
        disagreements = [
            f"{model}:{decision.stance}/{decision.confidence:.0%}"
            for model, decision in decisions
            if decision.stance != winning_stance
        ]
        winner.supporting_model_count = winning_count
        winner.consensus_strength = consensus_strength
        if disagreements:
            winner.disagreement_note = " | ".join(disagreements)
        return winner

    def generate_execution_plan(
        self,
        symbol: str,
        research: ResearchDecision,
        account_summary: dict,
        positions: list[dict],
        market_snapshot: dict,
        adaptive_context: Optional[dict] = None,
    ) -> ExecutionPlan:
        """Call Claude to generate an ExecutionPlan from a ResearchDecision."""
        system, user = build_execution_prompt(
            symbol=symbol,
            research=research,
            account_summary=account_summary,
            positions=positions,
            market_snapshot=market_snapshot,
            risk_params=self._risk_params(),
            adaptive_context=adaptive_context,
        )
        raw = self.client.generate_json(system=system, user=user, max_retries=1, cache_system=False)
        return ExecutionPlan.from_dict(raw)

    def generate_reflection(self, symbol: str, trades: list[dict]) -> dict:
        """Call Claude to generate trade reflection from past closed trades."""
        system, user = build_reflection_prompt(symbol=symbol, trades=trades)
        return self.client.generate_json(system=system, user=user, max_retries=1, cache_system=False)

    def generate_combined(
        self,
        symbol: str,
        account_summary: dict,
        positions: list[dict],
        market_snapshot: dict,
        vault_context: str,
        trade_history: list[dict],
        reflections: list[dict],
        onchain_snapshot: Optional[dict] = None,
        portfolio_snapshot: Optional[dict] = None,
        portfolio_budget: Optional[dict] = None,
        event_snapshot: Optional[dict] = None,
        adaptive_context: Optional[dict] = None,
    ) -> tuple[ResearchDecision, ExecutionPlan]:
        """Generate both ResearchDecision and ExecutionPlan in a single Claude call.

        Saves ~30% tokens compared to two separate calls.
        """
        system, user = build_combined_research_execution_prompt(
            symbol=symbol,
            account_summary=account_summary,
            positions=positions,
            market_snapshot=market_snapshot,
            vault_context=vault_context,
            trade_history=trade_history,
            reflections=reflections,
            risk_params=self._risk_params(),
            onchain_snapshot=onchain_snapshot,
            portfolio_snapshot=portfolio_snapshot,
            portfolio_budget=portfolio_budget,
            event_snapshot=event_snapshot,
            adaptive_context=adaptive_context,
        )
        raw = self.client.generate_json(system=system, user=user, max_retries=1, cache_system=True)

        # Parse combined response
        research_data = raw.get("research", {})
        execution_data = raw.get("execution", {})

        decision = ResearchDecision.from_dict(research_data)
        decision.symbol = symbol.upper()
        plan = ExecutionPlan.from_dict(execution_data)

        return decision, plan
