"""Price action, Indian market structural pivots, and feature extraction."""

from __future__ import annotations

from typing import Any, Dict, Tuple
import numpy as np
import pandas as pd
from .technical import (
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


def compute_pivot_points(
    prev_high: float,
    prev_low: float,
    prev_close: float,
) -> Dict[str, float]:
    """Computes Standard Floor Pivot Points for the day."""
    p = (prev_high + prev_low + prev_close) / 3.0
    r1 = (2 * p) - prev_low
    s1 = (2 * p) - prev_high
    r2 = p + (prev_high - prev_low)
    s2 = p - (prev_high - prev_low)
    r3 = prev_high + 2 * (p - prev_low)
    s3 = prev_low - 2 * (prev_high - p)
    return {
        "pivot": round(p, 2),
        "r1": round(r1, 2),
        "s1": round(s1, 2),
        "r2": round(r2, 2),
        "s2": round(s2, 2),
        "r3": round(r3, 2),
        "s3": round(s3, 2),
    }


def compute_donchian_channels(
    high: pd.Series,
    low: pd.Series,
    period: int = 20,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Donchian Channels (Upper, Lower, Middle)."""
    upper = high.rolling(window=period, min_periods=period).max()
    lower = low.rolling(window=period, min_periods=period).min()
    middle = (upper + lower) / 2.0
    return upper, lower, middle


def compute_volume_surge_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Calculates ratio of current volume to 20-period volume SMA."""
    vol_sma = compute_sma(volume, period)
    return (volume / vol_sma.replace(0, np.nan)).fillna(1.0)


def extract_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extracts complete feature set into a enriched DataFrame."""
    df = df.copy()
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # Moving Averages
    df["ema_9"] = compute_ema(c, 9)
    df["ema_20"] = compute_ema(c, 20)
    df["ema_50"] = compute_ema(c, 50)
    df["ema_200"] = compute_ema(c, 200)

    # Volatility
    df["atr_14"] = compute_atr(h, l, c, 14)
    bb_mid, bb_upper, bb_lower, bb_width = compute_bollinger_bands(c, 20, 2.0)
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_upper
    df["bb_lower"] = bb_lower
    df["bb_width"] = bb_width

    # Momentum
    df["rsi_14"] = compute_rsi(c, 14)
    macd_line, signal_line, hist = compute_macd(c, 12, 26, 9)
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = hist

    # Trend & Strength
    adx, plus_di, minus_di = compute_adx(h, l, c, 14)
    df["adx"] = adx
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    st_line, st_dir = compute_supertrend(h, l, c, 10, 3.0)
    df["supertrend"] = st_line
    df["supertrend_dir"] = st_dir

    # Breakouts & Volume
    d_up, d_low, d_mid = compute_donchian_channels(h, l, 20)
    df["donchian_upper"] = d_up
    df["donchian_lower"] = d_low
    df["vol_surge"] = compute_volume_surge_ratio(v, 20)

    # VWAP
    df["vwap"] = compute_vwap(df)

    return df


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely converts value to float, replacing NaN, inf, or errors with default."""
    try:
        f = float(val)
        return default if np.isnan(f) or np.isinf(f) else f
    except (ValueError, TypeError):
        return default


def get_latest_feature_snapshot(df: pd.DataFrame) -> Dict[str, Any]:
    """Builds a structured dictionary snapshot of the latest candle's indicators."""
    if df.empty:
        return {}
    row = df.iloc[-1]
    return {
        "close": round(_safe_float(row.get("close", 0.0)), 2),
        "open": round(_safe_float(row.get("open", 0.0)), 2),
        "high": round(_safe_float(row.get("high", 0.0)), 2),
        "low": round(_safe_float(row.get("low", 0.0)), 2),
        "volume": int(_safe_float(row.get("volume", 0))),
        "ema_9": round(_safe_float(row.get("ema_9", 0.0)), 2),
        "ema_20": round(_safe_float(row.get("ema_20", 0.0)), 2),
        "ema_50": round(_safe_float(row.get("ema_50", 0.0)), 2),
        "atr_14": round(_safe_float(row.get("atr_14", 0.0)), 2),
        "rsi_14": round(_safe_float(row.get("rsi_14", 50.0), default=50.0), 2),
        "macd": round(_safe_float(row.get("macd", 0.0)), 2),
        "macd_signal": round(_safe_float(row.get("macd_signal", 0.0)), 2),
        "adx": round(_safe_float(row.get("adx", 0.0)), 2),
        "supertrend": round(_safe_float(row.get("supertrend", 0.0)), 2),
        "supertrend_dir": int(_safe_float(row.get("supertrend_dir", 1), default=1)),
        "bb_upper": round(_safe_float(row.get("bb_upper", 0.0)), 2),
        "bb_lower": round(_safe_float(row.get("bb_lower", 0.0)), 2),
        "vol_surge": round(_safe_float(row.get("vol_surge", 1.0), default=1.0), 2),
        "vwap": round(_safe_float(row.get("vwap", 0.0)), 2),
    }
