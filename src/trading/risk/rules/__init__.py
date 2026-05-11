"""Risk rule registry — defines DEFAULT_RULES execution order."""

from .base import BaseRule, RuleContext, RuleResult, OPEN_ACTIONS, CLOSE_ACTIONS
from .position_limits import MaxSizeRule, EventDrivenWarningRule, MaxLeverageRule
from .drawdown import DrawdownGateRule, EquityCurveRule
from .defaults import DefaultStopLossRule, DefaultTakeProfitRule
from .conflicts import PositionConflictRule, LiquidationDistanceRule, ProfitLockRule, NarrativeConcentrationRule
from .price_geometry import PriceGeometryRule
from .market_conditions import VolatilityRule, FundingRateRule, BasisRule, TrendBiasRule, CrowdingRule, EntryDriftRule
from .crypto_specific import AssetTierRule, MemeRule, BtcRegimeRule, AiNarrativeRule
from .p0_p1 import FundingVelocityRule, DepthRule, MultiTfRule, DynamicLeverageRule
from .stable_behavior import LossCooldownRule, RewardRiskRule
from .stable_setup import StableSetupRule
from .template import TemplateExecutionRule
from .events import EventRiskRule

# Execution order matches the original gate.py evaluate() exactly.
DEFAULT_RULES: list[BaseRule] = [
    # Phase 1: Hard limits
    MaxSizeRule(),
    EventDrivenWarningRule(),
    MaxLeverageRule(),
    # Phase 2: Account-level gates
    DrawdownGateRule(),
    EquityCurveRule(),
    # Phase 3: Defaults
    DefaultStopLossRule(),
    DefaultTakeProfitRule(),
    # Phase 4: Position-level checks
    PositionConflictRule(),
    LiquidationDistanceRule(),
    ProfitLockRule(),
    NarrativeConcentrationRule(),
    # Phase 5: Price geometry
    PriceGeometryRule(),
    # Phase 6: Market conditions (dynamic leverage first as suggested ceiling)
    DynamicLeverageRule(),
    VolatilityRule(),
    FundingRateRule(),
    BasisRule(),
    AssetTierRule(),
    MemeRule(),
    TrendBiasRule(),
    BtcRegimeRule(),
    CrowdingRule(),
    EntryDriftRule(),
    # Phase 7: P0/P1 additions
    FundingVelocityRule(),
    DepthRule(),
    MultiTfRule(),
    # Phase 8: Template overlays
    TemplateExecutionRule(),
    AiNarrativeRule(),
    # Phase 9: Event-driven risk
    EventRiskRule(),
    # Phase 10: Stable-mode behavior gates
    RewardRiskRule(),
    LossCooldownRule(),
    # Phase 11: Stable-mode quality gate
    StableSetupRule(),
]

__all__ = [
    "BaseRule",
    "RuleContext",
    "RuleResult",
    "OPEN_ACTIONS",
    "CLOSE_ACTIONS",
    "DEFAULT_RULES",
]
