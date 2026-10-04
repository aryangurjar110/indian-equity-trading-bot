"""Unit tests for Confidence Filter."""

from indian_equity_agent.confidence_filter.filter import ConfidenceFilter
from indian_equity_agent.core.models import StrategySignal, AIAnalysisOutput


def test_confidence_filter_consensus():
    conf_filter = ConfidenceFilter(min_confidence=0.70)

    signal = StrategySignal(
        symbol="TCS",
        action="BUY",
        strategy_name="TrendFollowing",
        entry_price=3900.0,
        suggested_stop_loss=3850.0,
        suggested_target=4000.0,
    )

    # 1. Matching AI BUY with high confidence (Should Pass)
    ai_ok = AIAnalysisOutput(
        symbol="TCS",
        action="BUY",
        confidence=0.82,
        reason="Uptrend confirmation",
        market_regime="BULLISH_TREND",
        risk_level="LOW",
    )
    ok, reason = conf_filter.evaluate(signal, ai_ok)
    assert ok is True
    assert "Consensus achieved" in reason

    # 2. Conflicting AI Action (Strategy BUY, AI HOLD) -> Should Reject
    ai_conflict = AIAnalysisOutput(
        symbol="TCS",
        action="HOLD",
        confidence=0.50,
        reason="Range-bound resistance",
        market_regime="SIDEWAYS_RANGE",
        risk_level="MEDIUM",
    )
    ok, reason = conf_filter.evaluate(signal, ai_conflict)
    assert ok is False
    assert "Consensus mismatch" in reason

    # 3. Matching Action but Low Confidence (< 0.70) -> Should Reject
    ai_low_conf = AIAnalysisOutput(
        symbol="TCS",
        action="BUY",
        confidence=0.58,
        reason="Weak signal",
        market_regime="BULLISH_TREND",
        risk_level="LOW",
    )
    ok, reason = conf_filter.evaluate(signal, ai_low_conf)
    assert ok is False
    assert "Insufficient AI confidence" in reason

    # 4. Elevated AI Risk Level (HIGH) -> Should Reject
    ai_high_risk = AIAnalysisOutput(
        symbol="TCS",
        action="BUY",
        confidence=0.80,
        reason="Volatile breakout",
        market_regime="HIGH_VOLATILITY_CHOP",
        risk_level="HIGH",
    )
    ok, reason = conf_filter.evaluate(signal, ai_high_risk)
    assert ok is False
    assert "Elevated risk level" in reason
