"""Domain models for Indian Equities Algorithmic Trading Agent."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class OrderStatus(str, Enum):
    PENDING_RISK = "PENDING_RISK"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ProductType(str, Enum):
    MIS = "MIS"  # Margin Intraday Square-off
    CNC = "CNC"  # Cash & Carry / Delivery


class Bar(BaseModel):
    """OHLCV Bar representation with timezone-aware datetime."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: Optional[int] = 0

    def is_valid(self) -> bool:
        """Check basic price integrity."""
        return (
            self.open > 0
            and self.high > 0
            and self.low > 0
            and self.close > 0
            and self.high >= self.low
            and self.high >= max(self.open, self.close)
            and self.low <= min(self.open, self.close)
            and self.volume >= 0
        )


class Tick(BaseModel):
    """Real-time market tick / quote."""
    symbol: str
    timestamp: datetime
    last_price: float
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: int = 0
    ask_qty: int = 0
    volume: int = 0

    @property
    def spread(self) -> float:
        """Absolute spread."""
        if self.bid > 0 and self.ask > 0:
            return round(self.ask - self.bid, 2)
        return 0.0

    @property
    def spread_pct(self) -> float:
        """Relative spread percentage."""
        if self.last_price > 0 and self.spread > 0:
            return self.spread / self.last_price
        return 0.0


class Instrument(BaseModel):
    """NSE/BSE Equity instrument metadata."""
    symbol: str
    exchange: str = "NSE"
    tick_size: float = 0.05
    lot_size: int = 1
    upper_circuit: float = 0.0
    lower_circuit: float = 0.0
    sector: Optional[str] = "General"
    is_tradable: bool = True


class Order(BaseModel):
    """Order representation with lifecycle status."""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    product: ProductType = ProductType.MIS
    quantity: int = Field(gt=0)
    price: float = 0.0
    trigger_price: float = 0.0
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING_RISK
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: Optional[datetime] = None
    filled_quantity: int = 0
    average_fill_price: float = 0.0


class Position(BaseModel):
    """Open or closed trading position."""
    symbol: str
    product: ProductType = ProductType.MIS
    quantity: int = 0  # Positive for long, negative for short
    average_entry_price: float = 0.0
    current_price: float = 0.0
    stop_loss: float = 0.0
    target_price: float = 0.0
    realized_pnl: float = 0.0
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trailing_stop: Optional[float] = None

    @property
    def unrealized_pnl(self) -> float:
        """Mark to Market Unrealized PnL."""
        if self.quantity == 0 or self.average_entry_price == 0:
            return 0.0
        if self.quantity > 0:
            return (self.current_price - self.average_entry_price) * self.quantity
        else:
            return (self.average_entry_price - self.current_price) * abs(self.quantity)

    @property
    def position_value(self) -> float:
        """Total current market value of position (asset if positive, liability if negative)."""
        return self.quantity * self.current_price


class PortfolioState(BaseModel):
    """Instantaneous snapshot of account equity, cash, and risk limits."""
    cash: float
    total_equity: float
    peak_equity: float
    daily_starting_equity: float
    daily_realized_pnl: float = 0.0
    positions: Dict[str, Position] = Field(default_factory=dict)
    open_orders: Dict[str, Order] = Field(default_factory=dict)

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    @property
    def total_portfolio_value(self) -> float:
        if self.total_equity is not None:
            return max(0.0, self.total_equity)
        return max(0.0, self.cash + sum(pos.position_value for pos in self.positions.values()))

    @property
    def daily_total_pnl(self) -> float:
        return self.daily_realized_pnl + self.total_unrealized_pnl

    @property
    def daily_loss_pct(self) -> float:
        if self.daily_starting_equity <= 0:
            return 0.0
        pnl = self.daily_total_pnl
        if pnl < 0:
            return abs(pnl) / self.daily_starting_equity
        return 0.0

    @property
    def current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        dd = (self.peak_equity - self.total_portfolio_value) / self.peak_equity
        return max(0.0, dd)

    @property
    def total_exposure_pct(self) -> float:
        total_val = self.total_portfolio_value
        if total_val <= 0:
            return 0.0
        invested = sum(pos.position_value for pos in self.positions.values())
        return invested / total_val


class StrategySignal(BaseModel):
    """Quantitative signal generated by a Strategy."""
    symbol: str
    action: str  # "BUY", "SELL", "HOLD"
    strategy_name: str
    entry_price: float
    suggested_stop_loss: float
    suggested_target: float
    timeframe: str = "15m"
    indicators: Dict[str, float] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AIAnalysisOutput(BaseModel):
    """Structured Gemini AI output."""
    symbol: str
    action: str = Field(description="Must be BUY, SELL, or HOLD")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0")
    reason: str = Field(description="Explainable thesis for trade decision")
    market_regime: str = Field(description="e.g. BULLISH_TREND, BEARISH_TREND, RANGE_BOUND, HIGH_VOLATILITY")
    risk_level: str = Field(description="LOW, MEDIUM, HIGH, or EXTREME")
    invalidating_conditions: List[str] = Field(default_factory=list, description="Conditions that void the trade")


class RiskDecision(BaseModel):
    """Independent deterministic risk evaluation outcome."""
    approved: bool
    action: str  # "PROCEED", "REJECT", "HALT"
    symbol: str
    reason: str
    checked_rules: List[str] = Field(default_factory=list)
    adjusted_quantity: int = 0
    stop_loss: float = 0.0
    risk_amount_inr: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
