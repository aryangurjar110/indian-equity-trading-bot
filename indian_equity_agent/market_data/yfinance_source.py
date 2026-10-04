"""YFinance market data source for Indian Equities (NSE/BSE)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict
import yfinance as yf
import pandas as pd
from ..core.models import Bar, Tick, Instrument
from ..core.exceptions import DataValidationError
from .base_source import MarketDataSource
from .calendar import IndianMarketCalendar


class YFinanceSource(MarketDataSource):
    """Fetches NSE/BSE historical OHLCV data using yfinance."""

    def __init__(self, default_exchange: str = "NS", cache_ttl_seconds: int = 60):
        self.default_exchange = default_exchange
        self.cache_ttl_seconds = cache_ttl_seconds
        self._bars_cache: Dict[Tuple[str, str], Tuple[float, List[Bar]]] = {}

    def _format_symbol(self, symbol: str) -> str:
        """Ensures symbol has appropriate exchange suffix (default .NS)."""
        clean_sym = symbol.strip().upper()
        if not clean_sym.endswith(".NS") and not clean_sym.endswith(".BO"):
            return f"{clean_sym}.{self.default_exchange}"
        return clean_sym

    def get_historical_bars(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[Bar]:
        """Fetch historical bars and convert to domain Bar models with fast TTL caching."""
        formatted_symbol = self._format_symbol(symbol)
        cache_key = (formatted_symbol, interval)
        now_ts = time.time()

        if cache_key in self._bars_cache:
            cached_time, cached_bars = self._bars_cache[cache_key]
            if now_ts - cached_time < self.cache_ttl_seconds and len(cached_bars) >= 20:
                return cached_bars

        # Download data
        df = yf.download(
            tickers=formatted_symbol,
            start=start_date.strftime("%Y-%m-%d"),
            end=(end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval=interval,
            progress=False,
            auto_adjust=False,
        )

        if df.empty:
            return []

        # Handle multi-index columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        bars: List[Bar] = []
        for index, row in df.iterrows():
            # Convert timestamp to IST
            ts = pd.to_datetime(index)
            if ts.tzinfo is None:
                ts = IndianMarketCalendar.to_ist(ts.to_pydatetime())
            else:
                ts = ts.tz_convert("Asia/Kolkata").to_pydatetime()

            try:
                o = float(row["Open"])
                h = float(row["High"])
                l = float(row["Low"])
                c = float(row["Close"])
                v = int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
            except (KeyError, ValueError, TypeError):
                continue

            # Basic NaN skip
            if any(pd.isna([o, h, l, c])):
                continue

            bar = Bar(
                symbol=symbol,
                timestamp=ts,
                open=round(o, 2),
                high=round(h, 2),
                low=round(l, 2),
                close=round(c, 2),
                volume=v,
            )
            if bar.is_valid():
                bars.append(bar)

        if len(bars) >= 20:
            self._bars_cache[cache_key] = (now_ts, bars)

        return bars

    def get_quote(self, symbol: str) -> Tick:
        """Fetch latest quote snapshot via yfinance."""
        formatted_symbol = self._format_symbol(symbol)
        ticker = yf.Ticker(formatted_symbol)
        last_price = 0.0
        vol = 0

        try:
            fast_info = getattr(ticker, "fast_info", None)
            if fast_info:
                p = getattr(fast_info, "last_price", None)
                if p is not None:
                    last_price = float(p)
                    vol = getattr(fast_info, "last_volume", 0) or 0
        except Exception:
            last_price = 0.0

        if last_price <= 0:
            try:
                hist = ticker.history(period="1d", interval="1m")
                if not hist.empty:
                    last_price = float(hist["Close"].iloc[-1])
                    vol = int(hist["Volume"].iloc[-1])
            except Exception:
                pass

        if last_price <= 0:
            try:
                hist = ticker.history(period="5d", interval="1d")
                if not hist.empty:
                    last_price = float(hist["Close"].iloc[-1])
                    vol = int(hist["Volume"].iloc[-1])
            except Exception:
                pass

        if last_price <= 0:
            raise DataValidationError(f"Could not retrieve real-time quote for {symbol}")

        bid = round(last_price * 0.9998, 2)
        ask = round(last_price * 1.0002, 2)

        return Tick(
            symbol=symbol,
            timestamp=IndianMarketCalendar.now_ist(),
            last_price=round(last_price, 2),
            bid=bid,
            ask=ask,
            bid_qty=100,
            ask_qty=100,
            volume=vol,
        )

    def get_instrument(self, symbol: str, price: Optional[float] = None) -> Instrument:
        """Fetch instrument metadata and circuit boundaries."""
        if price is not None and price > 0:
            ltp = float(price)
        else:
            quote = self.get_quote(symbol)
            ltp = quote.last_price
        # Standard NSE 20% circuit band approximation for equities
        return Instrument(
            symbol=symbol,
            exchange="NSE",
            tick_size=0.05,
            lot_size=1,
            upper_circuit=round(ltp * 1.20, 2),
            lower_circuit=round(ltp * 0.80, 2),
            sector="NSE Equities",
            is_tradable=True,
        )
