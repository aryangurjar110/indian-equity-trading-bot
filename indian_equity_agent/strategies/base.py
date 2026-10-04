"""Base Strategy interface for Indian Equities."""

from __future__ import annotations

from abc import ABC, abstractmethod
import pandas as pd
from ..core.models import StrategySignal


class BaseStrategy(ABC):
    """Abstract class for quantitative trading strategies.

    IMPORTANT: No strategy is assumed to be profitable. All strategies must
    be subjected to walk-forward validation and independent risk filtering.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        """Evaluates latest bar and indicators to generate a trading signal.

        Must return action="HOLD" if setup conditions are not strictly met.
        """
        pass
