"""Technical indicators engine for Indian Equities.

Provides vectorized, robust calculations for ATR, RSI, MACD, Bollinger Bands,
EMA, SMA, Supertrend, ADX, and intraday session-anchored VWAP.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Tuple


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=period).mean()


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (Wilder's Smoothing)."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    # Wilder's smoothing equivalent to ewm(alpha=1/period)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's Smoothing)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_macd(
    close: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Moving Average Convergence Divergence (MACD, Signal, Histogram)."""
    ema_fast = compute_ema(close, fast_period)
    ema_slow = compute_ema(close, slow_period)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands (Middle, Upper, Lower, Bandwidth)."""
    middle = compute_sma(close, period)
    rolling_std = close.rolling(window=period, min_periods=period).std()
    upper = middle + (std_dev * rolling_std)
    lower = middle - (std_dev * rolling_std)
    bandwidth = (upper - lower) / middle.replace(0, np.nan)
    return middle, upper, lower, bandwidth


def compute_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> Tuple[pd.Series, pd.Series]:
    """Supertrend indicator. Returns (supertrend_line, trend_direction: 1 for Bullish, -1 for Bearish)."""
    atr = compute_atr(high, low, close, period).bfill().fillna(high - low)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)

    upper_band = pd.Series(index=close.index, dtype=float)
    lower_band = pd.Series(index=close.index, dtype=float)
    trend = pd.Series(index=close.index, dtype=int)

    for i in range(len(close)):
        if i == 0 or np.isnan(upper_band.iloc[i - 1]):
            upper_band.iloc[i] = basic_upper.iloc[i]
            lower_band.iloc[i] = basic_lower.iloc[i]
            trend.iloc[i] = 1
            continue

        prev_upper = upper_band.iloc[i - 1]
        prev_lower = lower_band.iloc[i - 1]
        prev_close = close.iloc[i - 1]
        curr_close = close.iloc[i]

        # Upper band calculation
        if basic_upper.iloc[i] < prev_upper or prev_close > prev_upper:
            upper_band.iloc[i] = basic_upper.iloc[i]
        else:
            upper_band.iloc[i] = prev_upper

        # Lower band calculation
        if basic_lower.iloc[i] > prev_lower or prev_close < prev_lower:
            lower_band.iloc[i] = basic_lower.iloc[i]
        else:
            lower_band.iloc[i] = prev_lower

        # Trend direction determination
        prev_trend = trend.iloc[i - 1]
        if prev_trend == 1 and curr_close < lower_band.iloc[i]:
            trend.iloc[i] = -1
        elif prev_trend == -1 and curr_close > upper_band.iloc[i]:
            trend.iloc[i] = 1
        else:
            trend.iloc[i] = prev_trend

    supertrend_line = pd.Series(
        np.where(trend == 1, lower_band, upper_band),
        index=close.index,
    )
    return supertrend_line, trend


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Average Directional Index. Returns (ADX, Plus_DI, Minus_DI)."""
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=low.index)

    atr = compute_atr(high, low, close, period)
    plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr)

    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0.0)
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return adx, plus_di, minus_di


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-Weighted Average Price anchored to each day's session."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"]

    # If datetime index has dates, group by date
    if isinstance(df.index, pd.DatetimeIndex):
        cum_vol = vol.groupby(df.index.date).cumsum()
        cum_tp_vol = (typical_price * vol).groupby(df.index.date).cumsum()
    else:
        cum_vol = vol.cumsum()
        cum_tp_vol = (typical_price * vol).cumsum()

    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return vwap.bfill().fillna(typical_price)
