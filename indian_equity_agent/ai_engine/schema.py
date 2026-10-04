"""Structured JSON output schema for Gemini AI Analysis."""

from __future__ import annotations

from typing import List, Literal
from pydantic import BaseModel, Field


class GeminiAnalysisResponse(BaseModel):
    """Rigidly structured AI analysis output. Free-form text is forbidden."""
    symbol: str = Field(description="NSE/BSE Equity symbol")
    action: Literal["BUY", "SELL", "HOLD"] = Field(
        description="Must be BUY, SELL, or HOLD. Uncertain conditions must yield HOLD."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0. Minimum 0.70 required to trade.",
    )
    reason: str = Field(
        description="Concise, explainable thesis citing indicators, price structure, and volume."
    )
    market_regime: Literal[
        "BULLISH_TREND",
        "BEARISH_TREND",
        "SIDEWAYS_RANGE",
        "HIGH_VOLATILITY_CHOP",
        "COMPRESSION_SQUEEZE",
        "UNCERTAIN",
    ] = Field(description="Identified macro/micro market state.")
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "EXTREME"] = Field(
        description="Assessed risk. Only LOW or MEDIUM may proceed."
    )
    invalidating_conditions: List[str] = Field(
        default_factory=list,
        description="Specific events or price levels that immediately void this thesis.",
    )
