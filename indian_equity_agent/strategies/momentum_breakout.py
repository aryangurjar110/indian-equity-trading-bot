"""Momentum Breakout Strategy for Indian Equities.

Uses Donchian Channel breakout confirmed by volume surge (>1.5x 20-SMA)
and RSI momentum gating.
"""

from __future__ import annotations

import pandas as pd
from ..core.models import StrategySignal
from ..indicators.price_action import extract_features, get_latest_feature_snapshot
from .base import BaseStrategy


class MomentumBreakoutStrategy(BaseStrategy):
    """Donchian Breakout + Volume Surge + RSI Filter."""

    def __init__(
        self,
        donchian_period: int = 20,
        volume_surge_min: float = 1.5,
        rsi_bullish_min: float = 55.0,
        rsi_bullish_max: float = 75.0,
        atr_stop_multiplier: float = 1.5,
        risk_reward_ratio: float = 2.0,
    ):
        super().__init__(name="MomentumBreakout_Donchian_Volume")
        self.donchian_period = donchian_period
        self.volume_surge_min = volume_surge_min
        self.rsi_bullish_min = rsi_bullish_min
        self.rsi_bullish_max = rsi_bullish_max
        self.atr_stop_multiplier = atr_stop_multiplier
        self.risk_reward_ratio = risk_reward_ratio

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        if len(df) < self.donchian_period + 10:
            return StrategySignal(
                symbol=symbol,
                action="HOLD",
                strategy_name=self.name,
                entry_price=0.0,
                suggested_stop_loss=0.0,
                suggested_target=0.0,
                indicators={},
            )

        feat_df = df if "donchian_upper" in df.columns else extract_features(df)
        curr = feat_df.iloc[-1]
        prev = feat_df.iloc[-2]
        snapshot = get_latest_feature_snapshot(feat_df)

        close = float(curr["close"])
        high = float(curr["high"])
        low = float(curr["low"])
        prev_d_up = float(prev["donchian_upper"])
        prev_d_low = float(prev["donchian_lower"])
        vol_surge = float(curr["vol_surge"])
        rsi = float(curr["rsi_14"])
        atr = max(float(curr["atr_14"]), 1.0)

        # Bullish Breakout:
        # High breaks above previous 20-period upper channel
        # Volume is at least 1.5x 20-SMA volume
        # RSI shows strong momentum without extreme saturation
        if high >= prev_d_up and vol_surge >= self.volume_surge_min and self.rsi_bullish_min <= rsi <= self.rsi_bullish_max:
            stop_dist = self.atr_stop_multiplier * atr
            stop_loss = round(close - stop_dist, 2)
            target = round(close + (stop_dist * self.risk_reward_ratio), 2)
            return StrategySignal(
                symbol=symbol,
                action="BUY",
                strategy_name=self.name,
                entry_price=close,
                suggested_stop_loss=stop_loss,
                suggested_target=target,
                indicators=snapshot,
            )

        # Bearish Breakdown:
        if low <= prev_d_low and vol_surge >= self.volume_surge_min and (100.0 - self.rsi_bullish_max) <= rsi <= (100.0 - self.rsi_bullish_min):
            stop_dist = self.atr_stop_multiplier * atr
            stop_loss = round(close + stop_dist, 2)
            target = round(close - (stop_dist * self.risk_reward_ratio), 2)
            return StrategySignal(
                symbol=symbol,
                action="SELL",
                strategy_name=self.name,
                entry_price=close,
                suggested_stop_loss=stop_loss,
                suggested_target=target,
                indicators=snapshot,
            )

        return StrategySignal(
            symbol=symbol,
            action="HOLD",
            strategy_name=self.name,
            entry_price=close,
            suggested_stop_loss=0.0,
            suggested_target=0.0,
            indicators=snapshot,
        )
