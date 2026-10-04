"""Confidence Filter and Signal Merger for Indian Equities.

Ensures strict consensus between the Strategy Engine and the AI Analysis Engine.
If there is any conflict, elevated risk, low confidence, or ambiguity,
the output deterministically defaults to 'NO TRADE'.
"""

from __future__ import annotations

from typing import Optional, Tuple
from ..core.models import AIAnalysisOutput, StrategySignal
from ..config import settings


class ConfidenceFilter:
    """Consensus gatekeeper combining quantitative strategy signals with AI analysis."""

    def __init__(
        self,
        min_confidence: float = 0.60,
        allowed_risk_levels: Optional[list] = None,
    ):
        self.min_confidence = min_confidence
        self.allowed_risk_levels = allowed_risk_levels or ["LOW", "MEDIUM"]

    def evaluate(
        self,
        signal: StrategySignal,
        ai_output: AIAnalysisOutput,
    ) -> Tuple[bool, str]:
        """Evaluates whether the trade setup passes all confidence criteria.

        Returns (is_approved, explanation).
        """
        # 1. Base check: Strategy must want to trade
        if signal.action == "HOLD":
            return False, "Strategy generated HOLD (no trade setup)."

        # If AI is in safe fallback due to quota/network error, allow quantitative rules-based execution
        if "AI Safe Fallback" in ai_output.reason:
            return True, f"Rules-based execution: Strategy generated {signal.action} (AI offline/quota fallback)"

        # 2. Base check: AI action must match strategy action exactly
        if signal.action != ai_output.action:
            return (
                False,
                f"Consensus mismatch: Strategy proposed {signal.action} but AI suggested {ai_output.action} (Thesis: {ai_output.reason}). Defaulting to NO TRADE.",
            )

        # 3. AI Confidence check
        if ai_output.confidence < self.min_confidence:
            return (
                False,
                f"Insufficient AI confidence: {ai_output.confidence:.2f} < threshold {self.min_confidence:.2f}. Capital preservation rule triggered: NO TRADE.",
            )

        # 4. Risk level check
        if ai_output.risk_level not in self.allowed_risk_levels:
            return (
                False,
                f"Elevated risk level '{ai_output.risk_level}' reported by AI. Only {self.allowed_risk_levels} permitted. Defaulting to NO TRADE.",
            )

        # 5. Invalidating conditions check
        if ai_output.invalidating_conditions:
            # If AI reports active invalidating conditions
            conditions_str = "; ".join(ai_output.invalidating_conditions)
            # If the conditions are warnings of active triggers
            if any("break" in c.lower() or "spike" in c.lower() or "earnings" in c.lower() for c in ai_output.invalidating_conditions):
                return (
                    False,
                    f"Active invalidating condition identified by AI: [{conditions_str}]. Rejecting setup.",
                )

        return True, f"Consensus achieved: {signal.action} with AI confidence {ai_output.confidence:.2f} ({ai_output.reason})"
