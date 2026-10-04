"""Indian equity market calendar and trading session manager (NSE/BSE).

Enforces Indian Standard Time (IST / Asia/Kolkata), trading sessions,
pre-open, post-closing, weekends, and standard NSE trading holidays.
"""

from __future__ import annotations

from datetime import date, datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")

# Standard NSE Trading Holidays (Example for current calendar years, easily extendable)
NSE_HOLIDAYS = {
    # 2025/2026/2027 standard holidays
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr
    date(2025, 4, 10),   # Shri Mahavir Jayanti
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti
    date(2025, 10, 21),  # Diwali (Laxmi Pujan)
    date(2025, 11, 5),   # Prakash Gurpurb Sri Guru Nanak Dev
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 26),
    date(2026, 3, 3),
    date(2026, 3, 20),
    date(2026, 4, 3),
    date(2026, 4, 14),
    date(2026, 5, 1),
    date(2026, 8, 15),
    date(2026, 10, 2),
    date(2026, 11, 8),
    date(2026, 12, 25),
}


class IndianMarketCalendar:
    """Manages NSE/BSE trading sessions and market states."""

    PRE_MARKET_START = time(9, 0)
    PRE_MARKET_END = time(9, 8)
    REGULAR_OPEN = time(9, 15)
    ENTRY_ALLOWED_START = time(9, 20)      # Avoid first 5 minutes of high volatility/auction noise
    ENTRY_ALLOWED_END = time(15, 5)        # Disallow new intraday entries after 15:05
    INTRADAY_SQUAREOFF = time(15, 15)      # Mandatory auto-squareoff of MIS positions
    REGULAR_CLOSE = time(15, 30)

    @classmethod
    def now_ist(cls) -> datetime:
        """Returns current datetime in IST."""
        return datetime.now(IST)

    @classmethod
    def to_ist(cls, dt: datetime) -> datetime:
        """Converts any datetime to IST."""
        if dt.tzinfo is None:
            return IST.localize(dt)
        return dt.astimezone(IST)

    @classmethod
    def is_trading_day(cls, dt: datetime | date) -> bool:
        """Checks if a given date is a weekday and not an NSE holiday."""
        d = dt.date() if isinstance(dt, datetime) else dt
        # 0 = Monday, 6 = Sunday
        if d.weekday() >= 5:
            return False
        if d in NSE_HOLIDAYS:
            return False
        return True

    @classmethod
    def is_market_open(cls, dt: datetime | None = None) -> bool:
        """Checks if regular trading hours (09:15 - 15:30 IST) are currently active."""
        ist_dt = cls.to_ist(dt) if dt else cls.now_ist()
        if not cls.is_trading_day(ist_dt):
            return False
        curr_time = ist_dt.time()
        return cls.REGULAR_OPEN <= curr_time <= cls.REGULAR_CLOSE

    @classmethod
    def is_entry_allowed(cls, dt: datetime | None = None) -> bool:
        """Checks if new trade entries are permissible (09:20 - 15:05 IST)."""
        ist_dt = cls.to_ist(dt) if dt else cls.now_ist()
        if not cls.is_trading_day(ist_dt):
            return False
        curr_time = ist_dt.time()
        return cls.ENTRY_ALLOWED_START <= curr_time <= cls.ENTRY_ALLOWED_END

    @classmethod
    def is_squareoff_time(cls, dt: datetime | None = None) -> bool:
        """Checks if intraday auto-squareoff window (>= 15:15 IST) is reached."""
        ist_dt = cls.to_ist(dt) if dt else cls.now_ist()
        if not cls.is_trading_day(ist_dt):
            return False
        curr_time = ist_dt.time()
        return curr_time >= cls.INTRADAY_SQUAREOFF
