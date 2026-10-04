"""Tests for Codebase Audit Fixes:
1. Supertrend NaN propagation fix.
2. VWAP zero-volume typical price fallback.
3. PositionSizer NaN/inf price & stop loss protection.
4. CashSufficiencyRule and MaxPortfolioExposureRule exit allowance.
5. Symbol canonicalization for positions and orders (.NS vs clean).
"""

import math
import numpy as np
import pandas as pd
import pytest

from indian_equity_agent.indicators.technical import compute_supertrend, compute_vwap
from indian_equity_agent.indicators.price_action import get_latest_feature_snapshot, _safe_float
from indian_equity_agent.risk.position_sizer import PositionSizer
from indian_equity_agent.risk.rules import (
    CashSufficiencyRule,
    MaxPortfolioExposureRule,
    MaxPositionSizeRule,
    MaxOpenPositionsRule,
    DuplicateOrderRule,
    _normalize_symbol,
    _find_position,
)
from indian_equity_agent.core.models import Order, OrderSide, OrderType, PortfolioState, Position, ProductType


def test_supertrend_nan_fix():
    """Verify compute_supertrend never outputs all NaNs even with Wilder's ATR warmup."""
    h = pd.Series([100.0 + i for i in range(35)])
    l = pd.Series([95.0 + i for i in range(35)])
    c = pd.Series([98.0 + i for i in range(35)])

    st_line, trend = compute_supertrend(h, l, c, period=10, multiplier=3.0)

    # Must be 100% finite and non-NaN
    assert not st_line.isna().any(), "Supertrend line must not contain NaNs"
    assert not trend.isna().any(), "Supertrend trend must not contain NaNs"
    assert all(t in (1, -1) for t in trend), "Trend direction must be 1 or -1"


def test_vwap_zero_volume_fallback():
    """Verify compute_vwap falls back to typical price when volume is zero."""
    df = pd.DataFrame({
        "high": [105.0, 106.0, 107.0],
        "low": [95.0, 96.0, 97.0],
        "close": [100.0, 101.0, 102.0],
        "volume": [0, 0, 0],
    })
    vwap = compute_vwap(df)
    assert not vwap.isna().any(), "VWAP must not be NaN even with zero volume"
    assert vwap.iloc[0] == (105.0 + 95.0 + 100.0) / 3.0


def test_feature_snapshot_safe_float():
    """Verify get_latest_feature_snapshot sanitizes NaN and inf."""
    df = pd.DataFrame({
        "close": [np.nan],
        "open": [100.0],
        "high": [np.inf],
        "low": [90.0],
        "volume": [1000],
    })
    snap = get_latest_feature_snapshot(df)
    assert not math.isnan(snap["close"])
    assert not math.isinf(snap["high"])
    assert snap["close"] == 0.0


def test_position_sizer_nan_protection():
    """Verify PositionSizer rejects NaN or inf price/stops without ValueError."""
    sizer = PositionSizer()
    portfolio = PortfolioState(
        cash=100000.0,
        total_equity=100000.0,
        peak_equity=100000.0,
        daily_starting_equity=100000.0,
    )

    qty, risk, reason = sizer.calculate_quantity("RELIANCE.NS", float("nan"), 2400.0, portfolio)
    assert qty == 0
    assert "Invalid" in reason or "NaN" in reason

    qty, risk, reason = sizer.calculate_quantity("RELIANCE.NS", 2500.0, float("nan"), portfolio)
    assert qty == 0
    assert "Undefined risk" in reason or "NaN" in reason


def test_risk_rules_exit_order_not_blocked():
    """Verify exit/squareoff orders are never blocked by cash sufficiency or exposure rules."""
    # Portfolio with very low cash but high position value
    portfolio = PortfolioState(
        cash=50.0,
        total_equity=20050.0,
        peak_equity=20050.0,
        daily_starting_equity=20050.0,
        positions={
            "RELIANCE": Position(
                symbol="RELIANCE",
                quantity=10,
                average_entry_price=2000.0,
                current_price=2000.0,
            )
        }
    )

    exit_order = Order(
        order_id="EXIT_01",
        symbol="RELIANCE.NS",  # Note symbol has .NS while position has RELIANCE
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=10,
        price=2000.0,
    )

    # Cash rule must pass because selling generates cash
    cash_rule = CashSufficiencyRule()
    passed, reason = cash_rule.evaluate(exit_order, portfolio)
    assert passed, f"CashSufficiencyRule should pass for exit order, got: {reason}"

    # Exposure rule must pass because selling reduces exposure
    exp_rule = MaxPortfolioExposureRule(max_exposure_pct=0.80)
    passed, reason = exp_rule.evaluate(exit_order, portfolio)
    assert passed, f"MaxPortfolioExposureRule should pass for exit order, got: {reason}"

    # Position size rule must pass
    size_rule = MaxPositionSizeRule(max_position_pct=0.10)
    passed, reason = size_rule.evaluate(exit_order, portfolio)
    assert passed, f"MaxPositionSizeRule should pass for exit order, got: {reason}"


def test_symbol_normalization():
    """Verify canonical symbol lookup between NSE suffixes and plain symbols."""
    assert _normalize_symbol("RELIANCE.NS") == "RELIANCE"
    assert _normalize_symbol("reliance.ns") == "RELIANCE"
    assert _normalize_symbol("TCS.BO") == "TCS"
    assert _normalize_symbol("INFY") == "INFY"

    portfolio = PortfolioState(
        cash=1000.0,
        total_equity=1000.0,
        peak_equity=1000.0,
        daily_starting_equity=1000.0,
        positions={
            "RELIANCE": Position(symbol="RELIANCE", quantity=5, average_entry_price=2000.0, current_price=2000.0)
        }
    )

    pos = _find_position(portfolio, "RELIANCE.NS")
    assert pos is not None
    assert pos.symbol == "RELIANCE"
    assert pos.quantity == 5
