"""Shared helpers for stable-mode and conviction-override logic."""

from __future__ import annotations

from .base import RuleContext


def is_conviction_setup(ctx: RuleContext) -> bool:
    """Return True when research confidence is high enough to relax soft stable gates."""
    rd = ctx.research_decision or {}
    tc = ctx.config.trading
    if not getattr(tc, "stable_mode_enabled", False):
        return False

    confidence = float(rd.get("confidence", 0.0) or 0.0)
    consensus = float(rd.get("consensus_strength", 1.0) or 1.0)
    model_count = int(rd.get("supporting_model_count", 1) or 1)

    confidence_gate = getattr(tc, "conviction_override_confidence", 0.85)
    consensus_gate = getattr(tc, "conviction_override_consensus_strength", 0.75)
    require_multi = bool(getattr(tc, "conviction_override_require_multi_model", False))

    if confidence < confidence_gate:
        return False
    if model_count > 1 and consensus < consensus_gate:
        return False
    if require_multi and model_count < 2:
        return False
    return True
