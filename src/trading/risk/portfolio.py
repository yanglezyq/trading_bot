"""Portfolio-layer sizing and concentration management for multi-coin books."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..core.config import AppConfig
from ..exchange.market_data import infer_narrative_tag


@dataclass
class PortfolioSnapshot:
    total_balance_usdt: float
    futures_balance_usdt: float
    gross_notional_usdt: float
    net_notional_usdt: float
    gross_exposure_pct: float
    net_exposure_pct: float
    position_count: int
    long_count: int
    short_count: int
    narrative_counts: dict[str, int]
    narrative_notional_usdt: dict[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PortfolioBudget:
    asset_tier: str
    narrative_tag: str
    portfolio_role: str
    gross_exposure_pct: float
    narrative_position_count: int
    same_symbol_present: bool
    recommended_max_size_pct: float
    hard_cap_size_pct: float
    recommended_addition_scale: float
    warnings: list[str]
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


class PortfolioManager:
    """Deterministic portfolio sizing helper for crypto multi-coin books."""

    def __init__(self, config: AppConfig):
        self.config = config

    def build_snapshot(self, account_summary: dict, positions: list[dict]) -> PortfolioSnapshot:
        total_balance = float(account_summary.get("total_balance_usdt", 0.0) or 0.0)
        futures_balance = float(account_summary.get("futures_balance_usdt", 0.0) or 0.0)

        gross = 0.0
        net = 0.0
        long_count = 0
        short_count = 0
        narrative_counts: dict[str, int] = {}
        narrative_notional: dict[str, float] = {}

        for pos in positions:
            symbol = str(pos.get("symbol", "")).upper()
            direction = str(pos.get("direction", "")).upper()
            amount = float(pos.get("amount", 0.0) or 0.0)
            price = float(pos.get("price", 0.0) or 0.0)
            notional = abs(amount * price)

            gross += notional
            net += notional if direction == "LONG" else -notional
            long_count += int(direction == "LONG")
            short_count += int(direction == "SHORT")

            narrative = infer_narrative_tag(symbol)
            narrative_counts[narrative] = narrative_counts.get(narrative, 0) + 1
            narrative_notional[narrative] = narrative_notional.get(narrative, 0.0) + notional

        gross_pct = gross / total_balance * 100.0 if total_balance > 0 else 0.0
        net_pct = net / total_balance * 100.0 if total_balance > 0 else 0.0

        return PortfolioSnapshot(
            total_balance_usdt=total_balance,
            futures_balance_usdt=futures_balance,
            gross_notional_usdt=gross,
            net_notional_usdt=net,
            gross_exposure_pct=gross_pct,
            net_exposure_pct=net_pct,
            position_count=len(positions),
            long_count=long_count,
            short_count=short_count,
            narrative_counts=narrative_counts,
            narrative_notional_usdt=narrative_notional,
        )

    def recommend_budget(
        self,
        *,
        symbol: str,
        market_snapshot: dict,
        positions: list[dict],
        account_summary: dict,
    ) -> PortfolioBudget:
        snapshot = self.build_snapshot(account_summary, positions)
        symbol = symbol.upper()

        asset_tier = str(market_snapshot.get("asset_tier", "liquid_alt"))
        narrative_tag = str(market_snapshot.get("narrative_tag", infer_narrative_tag(symbol)))
        btc_regime = str(market_snapshot.get("btc_market_regime", "range"))

        tier_caps = {
            "core": self.config.risk.core_position_cap_pct * 100,
            "major_alt": self.config.risk.major_alt_position_cap_pct * 100,
            "liquid_alt": self.config.risk.liquid_alt_position_cap_pct * 100,
            "mid_alt": self.config.risk.mid_alt_position_cap_pct * 100,
            "high_beta_alt": self.config.risk.high_beta_alt_max_position_size_pct * 100,
        }
        base_cap = tier_caps.get(asset_tier, self.config.trading.max_position_size_pct * 100)
        role_map = {
            "core": "anchor",
            "major_alt": "satellite",
            "liquid_alt": "satellite",
            "mid_alt": "tactical_alt",
            "high_beta_alt": "speculative_probe",
        }
        portfolio_role = role_map.get(asset_tier, "satellite")

        warnings: list[str] = []
        recommended = base_cap

        remaining_gross = max(0.0, self.config.risk.portfolio_hard_gross_exposure_pct - snapshot.gross_exposure_pct)
        if snapshot.gross_exposure_pct >= self.config.risk.portfolio_soft_gross_exposure_pct:
            warnings.append(
                f"Gross exposure already {snapshot.gross_exposure_pct:.1f}% — new risk should be lighter."
            )
            recommended = min(recommended, max(0.5, remaining_gross))

        same_symbol_present = any(str(p.get("symbol", "")).upper() == symbol for p in positions)
        if same_symbol_present:
            warnings.append("Existing position in the same symbol — treat new trade as an add, not a fresh slot.")
            recommended *= self.config.risk.same_symbol_addition_scale

        narrative_count = snapshot.narrative_counts.get(narrative_tag, 0)
        if narrative_count >= self.config.risk.max_positions_per_narrative:
            warnings.append(
                f"Narrative '{narrative_tag}' already has {narrative_count} position(s) — concentration cap applied."
            )
            recommended = min(recommended, 1.5)

        if snapshot.position_count >= self.config.risk.portfolio_max_positions:
            warnings.append(
                f"Portfolio already has {snapshot.position_count} positions — prefer rotation over adding new names."
            )
            recommended = min(recommended, 1.0)

        if narrative_tag == "meme":
            recommended = min(recommended, self.config.risk.meme_max_position_size_pct * 100)
            warnings.append("Meme narrative — keep this as a tactical probe, not a portfolio core position.")

        if btc_regime in {"panic_flush", "risk_off_trend", "risk_off"} and asset_tier != "core":
            recommended = min(recommended, self.config.risk.altcoin_max_position_size_when_btc_weak_pct * 100)
            warnings.append("BTC regime is risk-off — non-core positions should be cut to defensive size.")

        rationale = (
            f"Role={portfolio_role}; tier cap {base_cap:.1f}% → recommended {recommended:.1f}%; "
            f"gross exposure {snapshot.gross_exposure_pct:.1f}%."
        )

        return PortfolioBudget(
            asset_tier=asset_tier,
            narrative_tag=narrative_tag,
            portfolio_role=portfolio_role,
            gross_exposure_pct=snapshot.gross_exposure_pct,
            narrative_position_count=narrative_count,
            same_symbol_present=same_symbol_present,
            recommended_max_size_pct=round(recommended, 2),
            hard_cap_size_pct=round(base_cap, 2),
            recommended_addition_scale=self.config.risk.same_symbol_addition_scale,
            warnings=warnings,
            rationale=rationale,
        )
