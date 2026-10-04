"""Data validation engine for Indian equities market feeds.

Enforces strict integrity checks: missing data, duplicates, stale quotes,
abnormal price spikes, and circuit limit bounds. Stale or corrupted data
triggers immediate trade rejection.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from ..core.models import Bar, Tick, Instrument
from ..core.exceptions import DataValidationError, StaleDataError
from .calendar import IndianMarketCalendar


class MarketDataValidator:
    """Deterministic market data validator."""

    def __init__(
        self,
        max_tick_age_seconds: float = 30.0,
        max_bar_jump_pct: float = 0.20,      # 20% max price move between adjacent bars
        max_spread_pct: float = 0.0025,       # 0.25% max bid-ask spread
        circuit_buffer_pct: float = 0.015,   # 1.5% buffer to circuit limits
    ):
        self.max_tick_age_seconds = max_tick_age_seconds
        self.max_bar_jump_pct = max_bar_jump_pct
        self.max_spread_pct = max_spread_pct
        self.circuit_buffer_pct = circuit_buffer_pct

    def validate_bar(
        self,
        bar: Bar,
        previous_bar: Optional[Bar] = None,
        instrument: Optional[Instrument] = None,
        reference_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Validates an OHLCV bar. Returns (is_valid, failure_reason)."""
        # 1. Structural OHLC integrity
        if not bar.is_valid():
            return False, f"Structural OHLC failure for {bar.symbol}: O={bar.open}, H={bar.high}, L={bar.low}, C={bar.close}, V={bar.volume}"

        # 2. Timestamp check (not in future)
        now_dt = IndianMarketCalendar.to_ist(reference_time or IndianMarketCalendar.now_ist())
        bar_dt = IndianMarketCalendar.to_ist(bar.timestamp)
        if bar_dt > now_dt + timedelta(seconds=60):
            return False, f"Future timestamp detected for {bar.symbol}: bar={bar_dt} > now={now_dt}"

        # 3. Abnormal price jump compared to previous bar
        if previous_bar and previous_bar.close > 0:
            jump = abs(bar.close - previous_bar.close) / previous_bar.close
            if jump > self.max_bar_jump_pct:
                return False, f"Abnormal price jump of {jump*100:.2f}% detected for {bar.symbol} (prev={previous_bar.close}, curr={bar.close})"

        # 4. Circuit limit proximity check
        if instrument and instrument.upper_circuit > 0 and instrument.lower_circuit > 0:
            upper_limit = instrument.upper_circuit * (1.0 - self.circuit_buffer_pct)
            lower_limit = instrument.lower_circuit * (1.0 + self.circuit_buffer_pct)
            if bar.close >= upper_limit:
                return False, f"Price {bar.close} dangerously close to upper circuit {instrument.upper_circuit} for {bar.symbol}"
            if bar.close <= lower_limit:
                return False, f"Price {bar.close} dangerously close to lower circuit {instrument.lower_circuit} for {bar.symbol}"

        return True, None

    def validate_tick(
        self,
        tick: Tick,
        instrument: Optional[Instrument] = None,
        reference_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Validates a real-time quote/tick."""
        if tick.last_price <= 0:
            return False, f"Non-positive LTP {tick.last_price} for {tick.symbol}"

        now_dt = IndianMarketCalendar.to_ist(reference_time or IndianMarketCalendar.now_ist())
        tick_dt = IndianMarketCalendar.to_ist(tick.timestamp)

        # 1. Future timestamp
        if tick_dt > now_dt + timedelta(seconds=10):
            return False, f"Future tick timestamp for {tick.symbol}: tick={tick_dt}, now={now_dt}"

        # 2. Stale tick check (only during market hours)
        if IndianMarketCalendar.is_market_open(now_dt):
            age_sec = (now_dt - tick_dt).total_seconds()
            if age_sec > self.max_tick_age_seconds:
                return False, f"Stale tick for {tick.symbol}: {age_sec:.1f}s old (max allowed: {self.max_tick_age_seconds}s)"

        # 3. Bid-Ask spread check
        if tick.bid > 0 and tick.ask > 0:
            if tick.bid > tick.ask:
                return False, f"Inverted spread: Bid {tick.bid} > Ask {tick.ask} for {tick.symbol}"
            if tick.spread_pct > self.max_spread_pct:
                return False, f"Spread {tick.spread_pct*100:.3f}% exceeds max threshold {self.max_spread_pct*100:.3f}% for {tick.symbol}"

        # 4. Circuit limit check
        if instrument and instrument.upper_circuit > 0 and instrument.lower_circuit > 0:
            upper_limit = instrument.upper_circuit * (1.0 - self.circuit_buffer_pct)
            lower_limit = instrument.lower_circuit * (1.0 + self.circuit_buffer_pct)
            if tick.last_price >= upper_limit or tick.last_price <= lower_limit:
                return False, f"LTP {tick.last_price} violates circuit buffer for {tick.symbol}"

        return True, None

    def validate_sequence(self, bars: List[Bar]) -> Tuple[bool, Optional[str]]:
        """Validates a chronological sequence of bars."""
        if not bars:
            return False, "Empty bar series provided"

        seen_timestamps = set()
        prev_bar: Optional[Bar] = None

        for idx, bar in enumerate(bars):
            # Duplicate detection
            if bar.timestamp in seen_timestamps:
                return False, f"Duplicate timestamp {bar.timestamp} at index {idx} for {bar.symbol}"
            seen_timestamps.add(bar.timestamp)

            # Chronological order
            if prev_bar and bar.timestamp <= prev_bar.timestamp:
                return False, f"Out-of-order timestamp {bar.timestamp} after {prev_bar.timestamp}"

            # Bar integrity
            valid, err = self.validate_bar(bar, previous_bar=prev_bar)
            if not valid:
                return False, f"Sequence bar error at index {idx}: {err}"

            prev_bar = bar

        return True, None
