"""Institutional Intraday VWAP Mean Reversion Strategy for Indian Equities.

Identifies intraday institutional overextension away from Volume-Weighted Average Price (VWAP)
combined with RSI exhaustion and volume surge to capture high-probability reversion back to mean.
"""

from __future__ import annotations

import logging
from typing import Optional
import numpy as np
import pandas as pd

from .base import BaseStrategy, StrategySignal
from ..indicators.price_action import extract_features, get_latest_feature_snapshot

logger = logging.getLogger("indian_equity_agent.strategies.vwap_reversion")


class VWAPReversionStrategy(BaseStrategy):
    """Institutional Intraday VWAP Mean Reversion Strategy."""

    def __init__(
        self,
        vwap_deviation_threshold: float = 0.015,  # 1.5% extension away from VWAP
        rsi_oversold: float = 34.0,
        rsi_overbought: float = 66.0,
        min_vol_surge: float = 1.15,
        atr_multiplier: float = 1.5,
    ):
        super().__init__(name="VWAP Institutional Reversion")
        self.vwap_deviation_threshold = vwap_deviation_threshold
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.min_vol_surge = min_vol_surge
        self.atr_multiplier = atr_multiplier

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        """Generates BUY on exhausted selloff below VWAP, SELL on exhausted surge above VWAP."""
        if len(df) < 20:
            return StrategySignal(
                symbol=symbol,
                action="HOLD",
                strategy_name=self.name,
                entry_price=0.0,
                rationale="Insufficient historical data (< 20 bars)",
            )

        features = extract_features(df)
        snap = get_latest_feature_snapshot(features)

        close = snap.get("close", 0.0)
        vwap = snap.get("vwap", close)
        rsi = snap.get("rsi_14", 50.0)
        atr = snap.get("atr_14", close * 0.01)
        vol_surge = snap.get("vol_surge", 1.0)
        bb_lower = snap.get("bb_lower", close * 0.98)
        bb_upper = snap.get("bb_upper", close * 1.02)

        if close <= 0 or vwap <= 0 or atr <= 0:
            return StrategySignal(
                symbol=symbol,
                action="HOLD",
                strategy_name=self.name,
                entry_price=close,
                rationale="Invalid price or VWAP values",
            )

        vwap_dev = (close - vwap) / vwap

        # LONG REVERSION SETUP: Price stretched below VWAP, RSI oversold, volume confirmed
        if (vwap_dev <= -self.vwap_deviation_threshold or close <= bb_lower) and rsi <= self.rsi_oversold and vol_surge >= self.min_vol_surge:
            stop_loss = round(close - (self.atr_multiplier * atr), 2)
            target = round(vwap, 2)
            # Ensure favorable risk-reward >= 1.5:1
            if (target - close) >= 1.3 * (close - stop_loss):
                return StrategySignal(
                    symbol=symbol,
                    action="BUY",
                    strategy_name=self.name,
                    entry_price=close,
                    confidence=0.82,
                    suggested_stop_loss=stop_loss,
                    suggested_target=target,
                    indicators=snap,
                    rationale=(
                        f"Institutional VWAP Reversion BUY: Price ₹{close:.2f} stretched {abs(vwap_dev)*100:.1f}% below "
                        f"VWAP ₹{vwap:.2f} with RSI {rsi:.1f} oversold and {vol_surge:.1f}x volume surge"
                    ),
                )

        # SHORT REVERSION SETUP: Price stretched above VWAP, RSI overbought, volume confirmed
        if (vwap_dev >= self.vwap_deviation_threshold or close >= bb_upper) and rsi >= self.rsi_overbought and vol_surge >= self.min_vol_surge:
            stop_loss = round(close + (self.atr_multiplier * atr), 2)
            target = round(vwap, 2)
            # Ensure favorable risk-reward >= 1.5:1
            if (close - target) >= 1.3 * (stop_loss - close):
                return StrategySignal(
                    symbol=symbol,
                    action="SELL",
                    strategy_name=self.name,
                    entry_price=close,
                    confidence=0.82,
                    suggested_stop_loss=stop_loss,
                    suggested_target=target,
                    indicators=snap,
                    rationale=(
                        f"Institutional VWAP Reversion SELL: Price ₹{close:.2f} stretched {vwap_dev*100:.1f}% above "
                        f"VWAP ₹{vwap:.2f} with RSI {rsi:.1f} overbought and {vol_surge:.1f}x volume surge"
                    ),
                )

        return StrategySignal(
            symbol=symbol,
            action="HOLD",
            strategy_name=self.name,
            entry_price=close,
            suggested_stop_loss=0.0,
            suggested_target=0.0,
            indicators=snap,
            rationale=f"Price near VWAP (dev: {vwap_dev*100:+.1f}%, RSI: {rsi:.1f}); awaiting institutional dislocation",
        )
