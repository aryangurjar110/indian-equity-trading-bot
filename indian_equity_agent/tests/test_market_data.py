"""Unit tests for Indian Market Data Engine, Calendar, and Validator."""

from datetime import date, datetime, time, timedelta
import pytz
import pytest

from indian_equity_agent.market_data.calendar import IndianMarketCalendar, IST
from indian_equity_agent.market_data.validator import MarketDataValidator
from indian_equity_agent.core.models import Bar, Tick, Instrument


def test_indian_market_calendar_hours():
    # Tuesday at 10:30 AM IST (Trading day, open)
    dt_open = IST.localize(datetime(2026, 9, 22, 10, 30, 0))
    assert IndianMarketCalendar.is_trading_day(dt_open) is True
    assert IndianMarketCalendar.is_market_open(dt_open) is True
    assert IndianMarketCalendar.is_entry_allowed(dt_open) is True
    assert IndianMarketCalendar.is_squareoff_time(dt_open) is False

    # Tuesday at 09:14 AM IST (Pre-market / before open, entry forbidden)
    dt_early = IST.localize(datetime(2026, 9, 22, 9, 14, 0))
    assert IndianMarketCalendar.is_market_open(dt_early) is False
    assert IndianMarketCalendar.is_entry_allowed(dt_early) is False

    # Tuesday at 15:16 PM IST (Market open, but square-off triggered)
    dt_squareoff = IST.localize(datetime(2026, 9, 22, 15, 16, 0))
    assert IndianMarketCalendar.is_market_open(dt_squareoff) is True
    assert IndianMarketCalendar.is_entry_allowed(dt_squareoff) is False
    assert IndianMarketCalendar.is_squareoff_time(dt_squareoff) is True

    # Tuesday at 20:00 PM IST (Market closed)
    dt_closed = IST.localize(datetime(2026, 9, 22, 20, 0, 0))
    assert IndianMarketCalendar.is_market_open(dt_closed) is False

    # Sunday (Market closed)
    dt_sunday = IST.localize(datetime(2026, 9, 20, 11, 0, 0))
    assert IndianMarketCalendar.is_trading_day(dt_sunday) is False
    assert IndianMarketCalendar.is_market_open(dt_sunday) is False

    # Republic Day (Holiday)
    dt_holiday = IST.localize(datetime(2026, 1, 26, 11, 0, 0))
    assert IndianMarketCalendar.is_trading_day(dt_holiday) is False


def test_market_data_validator_bar():
    validator = MarketDataValidator()
    now_ist = IST.localize(datetime(2026, 9, 22, 11, 0, 0))

    valid_bar = Bar(
        symbol="RELIANCE",
        timestamp=now_ist - timedelta(minutes=15),
        open=2900.0,
        high=2920.0,
        low=2895.0,
        close=2910.0,
        volume=50000,
    )
    ok, err = validator.validate_bar(valid_bar, reference_time=now_ist)
    assert ok is True
    assert err is None

    # Corrupt bar: High < Low
    bad_bar = Bar(
        symbol="RELIANCE",
        timestamp=now_ist - timedelta(minutes=15),
        open=2900.0,
        high=2880.0,
        low=2895.0,
        close=2890.0,
        volume=50000,
    )
    ok, err = validator.validate_bar(bad_bar, reference_time=now_ist)
    assert ok is False
    assert "Structural OHLC failure" in err

    # Abnormal jump > 20%
    prev_bar = Bar(
        symbol="RELIANCE",
        timestamp=now_ist - timedelta(minutes=30),
        open=2900.0,
        high=2910.0,
        low=2890.0,
        close=2900.0,
        volume=50000,
    )
    jump_bar = Bar(
        symbol="RELIANCE",
        timestamp=now_ist - timedelta(minutes=15),
        open=3700.0,
        high=3750.0,
        low=3690.0,
        close=3720.0,
        volume=50000,
    )
    ok, err = validator.validate_bar(jump_bar, previous_bar=prev_bar, reference_time=now_ist)
    assert ok is False
    assert "Abnormal price jump" in err


def test_market_data_validator_tick():
    validator = MarketDataValidator()
    now_ist = IST.localize(datetime(2026, 9, 22, 11, 0, 0))

    # Valid tick
    valid_tick = Tick(
        symbol="TCS",
        timestamp=now_ist - timedelta(seconds=5),
        last_price=3900.0,
        bid=3899.5,
        ask=3900.5,
        volume=10000,
    )
    ok, err = validator.validate_tick(valid_tick, reference_time=now_ist)
    assert ok is True

    # Stale tick (> 30s during trading hours)
    stale_tick = Tick(
        symbol="TCS",
        timestamp=now_ist - timedelta(seconds=45),
        last_price=3900.0,
        bid=3899.5,
        ask=3900.5,
        volume=10000,
    )
    ok, err = validator.validate_tick(stale_tick, reference_time=now_ist)
    assert ok is False
    assert "Stale tick" in err

    # Inverted spread (Bid > Ask)
    inverted_tick = Tick(
        symbol="TCS",
        timestamp=now_ist - timedelta(seconds=5),
        last_price=3900.0,
        bid=3905.0,
        ask=3900.0,
        volume=10000,
    )
    ok, err = validator.validate_tick(inverted_tick, reference_time=now_ist)
    assert ok is False
    assert "Inverted spread" in err
