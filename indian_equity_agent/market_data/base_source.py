"""Abstract base class for Indian market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, List, Optional
from ..core.models import Bar, Tick, Instrument


class MarketDataSource(ABC):
    """Unified interface for historical and real-time Indian market feeds."""

    @abstractmethod
    def get_historical_bars(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "15m",
    ) -> List[Bar]:
        """Fetch historical bars for symbol."""
        pass

    @abstractmethod
    def get_quote(self, symbol: str) -> Tick:
        """Fetch latest market quote for symbol."""
        pass

    @abstractmethod
    def get_instrument(self, symbol: str) -> Instrument:
        """Fetch metadata including tick size, lot size, circuit limits."""
        pass
