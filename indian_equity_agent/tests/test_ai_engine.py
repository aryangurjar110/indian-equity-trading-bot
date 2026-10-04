"""Unit tests for Gemini AI Analysis Engine and Schema validation."""

import pytest
from pydantic import ValidationError

from indian_equity_agent.ai_engine.schema import GeminiAnalysisResponse
from indian_equity_agent.ai_engine.gemini_analyst import GeminiMarketAnalyst


def test_ai_schema_valid():
    valid_data = {
        "symbol": "RELIANCE",
        "action": "BUY",
        "confidence": 0.85,
        "reason": "Clear breakout above 20 EMA with 2.2x volume surge",
        "market_regime": "BULLISH_TREND",
        "risk_level": "LOW",
        "invalidating_conditions": ["Break below 2880 support"],
    }
    parsed = GeminiAnalysisResponse(**valid_data)
    assert parsed.symbol == "RELIANCE"
    assert parsed.action == "BUY"
    assert parsed.confidence == 0.85


def test_ai_schema_invalid_action():
    invalid_data = {
        "symbol": "RELIANCE",
        "action": "STRONG_BUY_GUARANTEED_PROFIT",  # Disallowed
        "confidence": 0.95,
        "reason": "Should fail",
        "market_regime": "BULLISH_TREND",
        "risk_level": "LOW",
        "invalidating_conditions": [],
    }
    with pytest.raises(ValidationError):
        GeminiAnalysisResponse(**invalid_data)


def test_ai_schema_invalid_confidence():
    invalid_data = {
        "symbol": "RELIANCE",
        "action": "BUY",
        "confidence": 1.5,  # Exceeds 1.0 limit
        "reason": "Too confident",
        "market_regime": "BULLISH_TREND",
        "risk_level": "LOW",
        "invalidating_conditions": [],
    }
    with pytest.raises(ValidationError):
        GeminiAnalysisResponse(**invalid_data)


def test_ai_analyst_fallback_when_offline():
    # Without API key, should safely return HOLD with 0.0 confidence
    analyst = GeminiMarketAnalyst(api_key="")
    res = analyst.analyze("INFY", technical_context={"close": 1800.0})
    assert res.action == "HOLD"
    assert res.confidence == 0.0
    assert "Safe Fallback" in res.reason
