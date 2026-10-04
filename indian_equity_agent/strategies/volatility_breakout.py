"""Volatility Breakout Strategy for Indian Equities.

Detects volatility compression squeezes followed by directional expansion
using Bollinger Bandwidth and ATR expansion.
"""

from __future__ import annotations

import pandas as pd
from ..core.models import StrategySignal
from ..indicators.price_action import extract_features, get_latest_feature_snapshot
from .base import BaseStrategy


class VolatilityBreakoutStrategy(BaseStrategy):
    """Volatility Compression Squeeze & Expansion Model."""

    def __init__(
        self,
        bandwidth_threshold: float = 0.04,  # Narrow band indicating tight consolidation
        atr_multiplier_stop: float = 1.5,
        atr_multiplier_target: float = 3.0,
    ):
        super().__init__(name="VolatilityBreakout_Squeeze_Expansion")
        self.bandwidth_threshold = bandwidth_threshold
        self.atr_multiplier_stop = atr_multiplier_stop
        self.atr_multiplier_target = atr_multiplier_target

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 30:
            return StrategySignal(
                symbol=symbol,
                action="HOLD",
                strategy_name=self.name,
                entry_price=0.0,
                suggested_stop_loss=0.0,
                suggested_target=0.0,
                indicators={},
            )

        feat_df = df if "bb_width" in df.columns else extract_features(df)
        curr = feat_df.iloc[-1]
        prev = feat_df.iloc[-2]
        snapshot = get_latest_feature_snapshot(feat_df)

        close = float(curr["close"])
        bb_upper = float(curr["bb_upper"])
        bb_lower = float(curr["bb_lower"])
        prev_width = float(prev["bb_width"])
        curr_width = float(curr["bb_width"])
        atr = max(float(curr["atr_14"]), 1.0)
        vol_surge = float(curr["vol_surge"])

        # Squeeze breakout:
        # Prior candle was squeezed (narrow bandwidth)
        # Current candle breaks outside band with rising bandwidth and volume confirmation
        if prev_width <= self.bandwidth_threshold and curr_width > prev_width:
            if close > bb_upper and vol_surge >= 1.2:
                stop_loss = round(close - (self.atr_multiplier_stop * atr), 2)
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
            elif close < bb_lower and vol_surge >= 1.2:
                stop_loss = round(close + (self.atr_multiplier_stop * atr), 2)
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

        return StrategySignal(
            symbol=symbol,
            action="HOLD",
            strategy_name=self.name,
            entry_price=close,
            suggested_stop_loss=0.0,
            suggested_target=0.0,
            indicators=snapshot,
        )
