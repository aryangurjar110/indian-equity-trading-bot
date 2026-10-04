"""Unit tests for Strategy Engine modules."""

import numpy as np
import pandas as pd
import pytest

from indian_equity_agent.strategies.trend_following import TrendFollowingStrategy
from indian_equity_agent.strategies.momentum_breakout import MomentumBreakoutStrategy
from indian_equity_agent.strategies.mean_reversion import MeanReversionStrategy
from indian_equity_agent.strategies.volatility_breakout import VolatilityBreakoutStrategy


@pytest.fixture
def trending_up_df():
    """Generates strong upward trending prices."""
    n = 80
    dates = pd.date_range("2026-09-01 09:15", periods=n, freq="15min")
    prices = np.linspace(2000.0, 2500.0, n)  # Clear uptrend
    return pd.DataFrame({
        "open": prices - 2.0,
        "high": prices + 5.0,
        "low": prices - 3.0,
        "close": prices,
        "volume": np.full(n, 20000),
    }, index=dates)


def test_insufficient_data():
    strat = TrendFollowingStrategy()
    short_df = pd.DataFrame({
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1000, 1000],
    })
    sig = strat.generate_signal("TCS", short_df)
    assert sig.action == "HOLD"


def test_trend_following_signal(trending_up_df):
    strat = TrendFollowingStrategy()
    sig = strat.generate_signal("RELIANCE", trending_up_df)
    assert sig.symbol == "RELIANCE"
    assert sig.action in ("BUY", "SELL", "HOLD")
    if sig.action == "BUY":
        assert sig.suggested_stop_loss < sig.entry_price
        assert sig.suggested_target > sig.entry_price


def test_momentum_breakout(trending_up_df):
    strat = MomentumBreakoutStrategy()
    sig = strat.generate_signal("INFY", trending_up_df)
    assert sig.symbol == "INFY"
    assert sig.action in ("BUY", "SELL", "HOLD")


def test_mean_reversion():
    strat = MeanReversionStrategy()
    # Create oversold setup
    n = 60
    prices = np.linspace(1500.0, 1200.0, n)
    dates = pd.date_range("2026-09-01 09:15", periods=n, freq="15min")
    df = pd.DataFrame({
        "open": prices,
        "high": prices + 2,
        "low": prices - 10,
        "close": prices - 8,
        "volume": np.full(n, 25000),
    }, index=dates)
    sig = strat.generate_signal("HDFCBANK", df)
    assert sig.symbol == "HDFCBANK"
    assert sig.action in ("BUY", "SELL", "HOLD")
