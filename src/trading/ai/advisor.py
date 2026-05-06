"""TradingAdvisor: orchestrates Claude calls to produce structured trading decisions."""

from ..core.config import AppConfig
from .client import ClaudeClient
from .prompts import build_execution_prompt, build_reflection_prompt, build_research_prompt
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
    ) -> ResearchDecision:
        """Call Claude to generate a ResearchDecision for the given symbol."""
        system, user = build_research_prompt(
            symbol=symbol,
            account_summary=account_summary,
            positions=positions,
            market_snapshot=market_snapshot,
            vault_context=vault_context,
            trade_history=trade_history,
            reflections=reflections,
            risk_params=self._risk_params(),
        )
        raw = self.client.generate_json(system=system, user=user, max_retries=1, cache_system=True)
        decision = ResearchDecision.from_dict(raw)
        decision.symbol = symbol.upper()
        return decision

    def generate_execution_plan(
        self,
        symbol: str,
        research: ResearchDecision,
        account_summary: dict,
        positions: list[dict],
        market_snapshot: dict,
    ) -> ExecutionPlan:
        """Call Claude to generate an ExecutionPlan from a ResearchDecision."""
        system, user = build_execution_prompt(
            symbol=symbol,
            research=research,
            account_summary=account_summary,
            positions=positions,
            market_snapshot=market_snapshot,
            risk_params=self._risk_params(),
        )
        raw = self.client.generate_json(system=system, user=user, max_retries=1, cache_system=False)
        return ExecutionPlan.from_dict(raw)

    def generate_reflection(self, symbol: str, trades: list[dict]) -> dict:
        """Call Claude to generate trade reflection from past closed trades."""
        system, user = build_reflection_prompt(symbol=symbol, trades=trades)
        return self.client.generate_json(system=system, user=user, max_retries=1, cache_system=False)
