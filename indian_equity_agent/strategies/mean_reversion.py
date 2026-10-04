"""Mean Reversion Strategy for Indian Equities.

Identifies statistically stretched prices outside Bollinger Bands (2-std)
with oversold/overbought RSI conditions, targeting reversion to intraday VWAP.
"""

from __future__ import annotations

import pandas as pd
from ..core.models import StrategySignal
from ..indicators.price_action import extract_features, get_latest_feature_snapshot
from .base import BaseStrategy


class MeanReversionStrategy(BaseStrategy):
    """Bollinger Band & RSI Mean Reversion to VWAP."""

    def __init__(
        self,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        atr_stop_multiplier: float = 1.5,
    ):
        super().__init__(name="MeanReversion_BB_RSI_VWAP")
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_stop_multiplier = atr_stop_multiplier

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

        feat_df = df if "bb_upper" in df.columns else extract_features(df)
        curr = feat_df.iloc[-1]
        snapshot = get_latest_feature_snapshot(feat_df)

        close = float(curr["close"])
        low = float(curr["low"])
        high = float(curr["high"])
        bb_upper = float(curr["bb_upper"])
        bb_lower = float(curr["bb_lower"])
        rsi = float(curr["rsi_14"])
        vwap = float(curr["vwap"])
        atr = max(float(curr["atr_14"]), 1.0)

        # Bullish Mean Reversion:
        # Price punctured below Lower Bollinger Band
        # RSI is oversold (< 30)
        # Price is currently closing back above the low or reverting
        if low <= bb_lower and rsi <= self.rsi_oversold and vwap > close:
            stop_loss = round(close - (self.atr_stop_multiplier * atr), 2)
            target = round(vwap, 2)
            # Ensure target provides at least 1:1 risk-reward
            if (target - close) >= (close - stop_loss):
                return StrategySignal(
                    symbol=symbol,
                    action="BUY",
                    strategy_name=self.name,
                    entry_price=close,
                    suggested_stop_loss=stop_loss,
                    suggested_target=target,
                    indicators=snapshot,
                )

        # Bearish Mean Reversion:
        # Price punctured above Upper Bollinger Band
        # RSI is overbought (> 70)
        if high >= bb_upper and rsi >= self.rsi_overbought and vwap < close:
            stop_loss = round(close + (self.atr_stop_multiplier * atr), 2)
            target = round(vwap, 2)
            if (close - target) >= (stop_loss - close):
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
