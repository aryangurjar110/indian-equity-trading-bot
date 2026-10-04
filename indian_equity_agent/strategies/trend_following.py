"""Trend-following strategy for Indian Equities.

Uses Dual EMA (20/50), ADX(14) trend strength filter, and Supertrend
for entry confirmation and trailing stop-loss management.
"""

from __future__ import annotations

import pandas as pd
from ..core.models import StrategySignal
from ..indicators.price_action import extract_features, get_latest_feature_snapshot
from .base import BaseStrategy


class TrendFollowingStrategy(BaseStrategy):
    """Dual EMA + ADX + Supertrend Trend-Following Model."""

    def __init__(
        self,
        ema_fast: int = 20,
        ema_slow: int = 50,
        min_adx: float = 20.0,
        atr_multiplier_stop: float = 2.0,
        atr_multiplier_target: float = 3.5,
    ):
        super().__init__(name="TrendFollowing_EMA_ADX_Supertrend")
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.min_adx = min_adx
        self.atr_multiplier_stop = atr_multiplier_stop
        self.atr_multiplier_target = atr_multiplier_target

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        if len(df) < max(self.ema_slow, 35):
            return StrategySignal(
                symbol=symbol,
                action="HOLD",
                strategy_name=self.name,
                entry_price=0.0,
                suggested_stop_loss=0.0,
                suggested_target=0.0,
                indicators={},
            )

        feat_df = df if "ema_20" in df.columns else extract_features(df)
        curr = feat_df.iloc[-1]
        prev = feat_df.iloc[-2]
        snapshot = get_latest_feature_snapshot(feat_df)

        close = float(curr["close"])
        atr = max(float(curr["atr_14"]), 1.0)
        ema_f = float(curr[f"ema_{self.ema_fast}"])
        ema_s = float(curr[f"ema_{self.ema_slow}"])
        prev_ema_f = float(prev[f"ema_{self.ema_fast}"])
        prev_ema_s = float(prev[f"ema_{self.ema_slow}"])
        adx = float(curr["adx"])
        st_dir = int(curr["supertrend_dir"])
        supertrend_val = float(curr["supertrend"])

        # Bullish setup:
        # 1. Fast EMA above Slow EMA (or recent crossover)
        # 2. ADX > min_adx indicating non-choppy market
        # 3. Supertrend is Bullish (1) and price > Supertrend
        # 4. Close > EMA Fast
        if (
            ema_f > ema_s
            and adx >= self.min_adx
            and st_dir == 1
            and close > supertrend_val
            and close > ema_f
        ):
            # Bullish crossover or continuation pull-back to EMA 20
            stop_loss = round(max(supertrend_val, close - (self.atr_multiplier_stop * atr)), 2)
            target = round(close + (self.atr_multiplier_target * atr), 2)
            return StrategySignal(
                symbol=symbol,
                action="BUY",
                strategy_name=self.name,
                entry_price=close,
                suggested_stop_loss=stop_loss,
                suggested_target=target,
                indicators=snapshot,
            )

        # Bearish setup:
        if (
            ema_f < ema_s
            and adx >= self.min_adx
            and st_dir == -1
            and close < supertrend_val
            and close < ema_f
        ):
            stop_loss = round(min(supertrend_val, close + (self.atr_multiplier_stop * atr)), 2)
            target = round(close - (self.atr_multiplier_target * atr), 2)
            return StrategySignal(
                symbol=symbol,
                action="SELL",
                strategy_name=self.name,
                entry_price=close,
                suggested_stop_loss=stop_loss,
                suggested_target=target,
                indicators=snapshot,
            )

        # Safe default: No trade
        return StrategySignal(
            symbol=symbol,
            action="HOLD",
            strategy_name=self.name,
            entry_price=close,
            suggested_stop_loss=0.0,
            suggested_target=0.0,
            indicators=snapshot,
        )
