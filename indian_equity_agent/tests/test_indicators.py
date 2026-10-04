"""Unit tests for Technical Indicators and Feature Extraction."""

import numpy as np
import pandas as pd
import pytest

from indian_equity_agent.indicators.technical import (
    compute_atr,
    compute_bollinger_bands,
    compute_ema,
    compute_macd,
    compute_rsi,
    compute_sma,
    compute_supertrend,
    compute_adx,
    compute_vwap,
)
from indian_equity_agent.indicators.price_action import (
    compute_donchian_channels,
    compute_pivot_points,
    compute_volume_surge_ratio,
    extract_features,
)


@pytest.fixture
def sample_ohlcv_df():
    """Generates synthetic 100-bar OHLCV DataFrame."""
    np.random.seed(42)
    n = 100
    prices = 1000.0 + np.cumsum(np.random.randn(n) * 5.0)
    highs = prices + np.random.uniform(2.0, 10.0, n)
    lows = prices - np.random.uniform(2.0, 10.0, n)
    volumes = np.random.randint(10000, 50000, n)

    dates = pd.date_range(start="2026-09-01 09:15", periods=n, freq="15min")
    return pd.DataFrame({
        "open": prices + np.random.randn(n),
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes,
    }, index=dates)


def test_moving_averages(sample_ohlcv_df):
    c = sample_ohlcv_df["close"]
    sma_20 = compute_sma(c, 20)
    ema_20 = compute_ema(c, 20)

    assert len(sma_20) == len(c)
    assert len(ema_20) == len(c)
    assert not np.isnan(sma_20.iloc[-1])
    assert not np.isnan(ema_20.iloc[-1])


def test_atr(sample_ohlcv_df):
    atr = compute_atr(sample_ohlcv_df["high"], sample_ohlcv_df["low"], sample_ohlcv_df["close"], 14)
    assert len(atr) == len(sample_ohlcv_df)
    assert (atr.dropna() > 0).all()


def test_rsi(sample_ohlcv_df):
    rsi = compute_rsi(sample_ohlcv_df["close"], 14)
    assert len(rsi) == len(sample_ohlcv_df)
    assert (rsi >= 0.0).all() and (rsi <= 100.0).all()


def test_bollinger_bands(sample_ohlcv_df):
    mid, up, low, width = compute_bollinger_bands(sample_ohlcv_df["close"], 20, 2.0)
    valid_mask = ~mid.isna()
    assert (up[valid_mask] >= mid[valid_mask]).all()
    assert (mid[valid_mask] >= low[valid_mask]).all()
    assert (width[valid_mask] >= 0.0).all()


def test_supertrend(sample_ohlcv_df):
    st_line, st_dir = compute_supertrend(
        sample_ohlcv_df["high"],
        sample_ohlcv_df["low"],
        sample_ohlcv_df["close"],
        10,
        3.0,
    )
    assert len(st_line) == len(sample_ohlcv_df)
    assert set(st_dir.unique()).issubset({-1, 1})


def test_adx(sample_ohlcv_df):
    adx, plus_di, minus_di = compute_adx(
        sample_ohlcv_df["high"],
        sample_ohlcv_df["low"],
        sample_ohlcv_df["close"],
        14,
    )
    valid_adx = adx.dropna()
    assert (valid_adx >= 0.0).all() and (valid_adx <= 100.0).all()


def test_pivot_points():
    pivots = compute_pivot_points(prev_high=1050.0, prev_low=950.0, prev_close=1000.0)
    assert pivots["pivot"] == 1000.0
    assert pivots["r1"] > pivots["pivot"]
    assert pivots["s1"] < pivots["pivot"]


def test_feature_extraction(sample_ohlcv_df):
    feat_df = extract_features(sample_ohlcv_df)
    assert "ema_20" in feat_df.columns
    assert "atr_14" in feat_df.columns
    assert "rsi_14" in feat_df.columns
    assert "vwap" in feat_df.columns
    assert "donchian_upper" in feat_df.columns
    assert "vol_surge" in feat_df.columns
