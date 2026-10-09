"""Mock and simulated streaming market data source for Indian Equities.

Allows deterministic testing, offline simulation, and fault injection
(stale ticks, missing bars, abnormal spikes, circuit proximity).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from ..core.models import Bar, Tick, Instrument
from .base_source import MarketDataSource
from .calendar import IndianMarketCalendar


class MockMarketDataSource(MarketDataSource):
    """Generates synthetic realistic Indian equity price action."""

    def __init__(self, seed: int = 42):
        random.seed(seed)
        self.instruments: Dict[str, Instrument] = {
            "RELIANCE": Instrument(symbol="RELIANCE", exchange="NSE", upper_circuit=3400.0, lower_circuit=2400.0),
            "TCS": Instrument(symbol="TCS", exchange="NSE", upper_circuit=4600.0, lower_circuit=3200.0),
            "INFY": Instrument(symbol="INFY", exchange="NSE", upper_circuit=2200.0, lower_circuit=1500.0),
            "HDFCBANK": Instrument(symbol="HDFCBANK", exchange="NSE", upper_circuit=1900.0, lower_circuit=1300.0),
        }
        self.current_prices: Dict[str, float] = {
            "RELIANCE": 2950.0,
            "TCS": 3920.0,
            "INFY": 1820.0,
            "HDFCBANK": 1650.0,
        }

    def get_historical_bars(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        interval: str = "15m",
        limit: Optional[int] = None,
        **kwargs,
    ) -> List[Bar]:
        """Generates synthetic historical bars using geometric random walk."""
        bars: List[Bar] = []
        base_price = self.current_prices.get(symbol, 1000.0)
        curr_price = base_price
        
        step_minutes = 15 if interval == "15m" else 1440
        if end_date is None:
            end_date = IndianMarketCalendar.now_ist()
        if start_date is None:
            days_back = 7 if limit and limit <= 5 else 30
            start_date = end_date - timedelta(days=days_back)

        curr_dt = IndianMarketCalendar.to_ist(start_date)
        end_dt = IndianMarketCalendar.to_ist(end_date)

        while curr_dt <= end_dt:
            # Only generate for valid trading hours (09:15 - 15:30)
            if IndianMarketCalendar.is_trading_day(curr_dt):
                drift = 0.0001
                vol = 0.004
                ret = random.gauss(drift, vol)
                open_p = curr_price
                close_p = round(open_p * (1.0 + ret), 2)
                high_p = round(max(open_p, close_p) * (1.0 + abs(random.gauss(0, 0.002))), 2)
                low_p = round(min(open_p, close_p) * (1.0 - abs(random.gauss(0, 0.002))), 2)
                vol_val = int(random.uniform(5000, 50000))

                bar = Bar(
                    symbol=symbol,
                    timestamp=curr_dt,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=vol_val,
                )
                bars.append(bar)
                curr_price = close_p

            curr_dt += timedelta(minutes=step_minutes)

        if limit is not None and limit > 0:
            return bars[-limit:]

        return bars

    def get_quote(
        self,
        symbol: str,
        inject_stale_seconds: float = 0.0,
        inject_spread_pct: Optional[float] = None,
        inject_abnormal_price: Optional[float] = None,
    ) -> Tick:
        clean_sym = symbol.replace(".NS", "").replace(".BO", "").strip()
        base_price = self.current_prices.get(symbol, self.current_prices.get(clean_sym, 1000.0))
        ltp = inject_abnormal_price if inject_abnormal_price else base_price

        spread_pct = inject_spread_pct if inject_spread_pct is not None else 0.0005
        half_spread = round(ltp * (spread_pct / 2.0), 2)
        bid = round(ltp - half_spread, 2)
        ask = round(ltp + half_spread, 2)

        ts = IndianMarketCalendar.now_ist() - timedelta(seconds=inject_stale_seconds)

        return Tick(
            symbol=symbol,
            timestamp=ts,
            last_price=ltp,
            bid=bid,
            ask=ask,
            bid_qty=500,
            ask_qty=500,
            volume=120000,
        )

    def get_instrument(self, symbol: str) -> Instrument:
        return self.instruments.get(
            symbol,
            Instrument(
                symbol=symbol,
                exchange="NSE",
                tick_size=0.05,
                lot_size=1,
                upper_circuit=2000.0,
                lower_circuit=1000.0,
            ),
        )
