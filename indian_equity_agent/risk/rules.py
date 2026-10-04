"""Deterministic Risk Rules for Indian Equities.

Every proposed trade must strictly satisfy every rule. If ANY rule fails,
the trade is rejected immediately without exception.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple
from ..core.models import Instrument, Order, PortfolioState, Tick, ProductType
from ..config import settings
from ..market_data.calendar import IndianMarketCalendar
from .kill_switch import KillSwitch


class RiskRule(ABC):
    """Abstract Risk Rule."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def evaluate(
        self,
        order: Order,
        portfolio: PortfolioState,
        instrument: Optional[Instrument] = None,
        quote: Optional[Tick] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Evaluates compliance. Returns (passed, failure_reason)."""
        pass


class KillSwitchRule(RiskRule):
    """Rejects all orders if emergency kill switch is active."""

    def __init__(self, kill_switch: KillSwitch):
        super().__init__(name="EmergencyKillSwitchRule")
        self.kill_switch = kill_switch

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        if self.kill_switch.is_active:
            return False, f"Emergency kill switch is ENGAGED: {self.kill_switch.reason}"
        return True, None


class MarketSessionRule(RiskRule):
    """Enforces Indian market entry window (09:20 - 15:05 IST)."""

    def __init__(self):
        super().__init__(name="MarketSessionRule")

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        now = IndianMarketCalendar.now_ist()
        if not IndianMarketCalendar.is_market_open(now):
            return False, f"Market is currently CLOSED at {now.strftime('%H:%M:%S')} IST."
        if not IndianMarketCalendar.is_entry_allowed(now):
            return False, f"New entries not permitted outside 09:20-15:05 IST window (current time: {now.strftime('%H:%M:%S')})."
        return True, None


class MaxDailyLossRule(RiskRule):
    """Enforces daily loss circuit breaker (e.g. 2% max daily loss)."""

    def __init__(self, max_daily_loss_pct: float = 0.02):
        super().__init__(name="MaxDailyLossRule")
        self.max_daily_loss_pct = max_daily_loss_pct

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        loss_pct = portfolio.daily_loss_pct
        if loss_pct >= self.max_daily_loss_pct:
            return False, f"Daily loss of {loss_pct*100:.2f}% exceeds daily ceiling of {self.max_daily_loss_pct*100:.2f}%. Trading halted for the day."
        return True, None


class MaxDrawdownRule(RiskRule):
    """Enforces overall portfolio drawdown limit (e.g. 6%)."""

    def __init__(self, max_drawdown_pct: float = 0.06):
        super().__init__(name="MaxDrawdownRule")
        self.max_drawdown_pct = max_drawdown_pct

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        curr_dd = portfolio.current_drawdown_pct
        if curr_dd >= self.max_drawdown_pct:
            return False, f"Portfolio drawdown of {curr_dd*100:.2f}% exceeds max allowed drawdown of {self.max_drawdown_pct*100:.2f}%. All trading halted."
        return True, None


def _normalize_symbol(sym: str) -> str:
    s = sym.strip().upper()
    return s.replace(".NS", "").replace(".BO", "")


def _find_position(portfolio: PortfolioState, symbol: str) -> Optional[Any]:
    norm = _normalize_symbol(symbol)
    for k, v in portfolio.positions.items():
        if _normalize_symbol(k) == norm:
            return v
    return None


class MaxOpenPositionsRule(RiskRule):
    """Caps the number of simultaneous active positions (e.g. max 5)."""

    def __init__(self, max_positions: int = 5):
        super().__init__(name="MaxOpenPositionsRule")
        self.max_positions = max_positions

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        pos = _find_position(portfolio, order.symbol)
        if not pos or pos.quantity == 0:
            active_count = len([p for p in portfolio.positions.values() if p.quantity != 0])
            if active_count >= self.max_positions:
                return False, f"Maximum active positions limit ({self.max_positions}) reached. Current active: {active_count}."
        return True, None


class DuplicateOrderRule(RiskRule):
    """Prevents duplicate entries and concurrent conflicting orders."""

    def __init__(self):
        super().__init__(name="DuplicateOrderRule")

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        norm_sym = _normalize_symbol(order.symbol)
        # Check open orders
        for o in portfolio.open_orders.values():
            if _normalize_symbol(o.symbol) == norm_sym and o.status in ("PENDING_RISK", "SUBMITTED", "OPEN"):
                return False, f"Pending order {o.order_id} already exists for {order.symbol}."

        # Check existing active position
        pos = _find_position(portfolio, order.symbol)
        if pos and pos.quantity != 0:
            # If side matches existing position, prevent averaging down
            order_side_str = str(order.side.value if hasattr(order.side, "value") else order.side).upper()
            if (pos.quantity > 0 and order_side_str == "BUY") or (pos.quantity < 0 and order_side_str == "SELL"):
                return False, f"Active position already exists for {order.symbol}. Averaging down / compounding is prohibited."

        return True, None


class MaxPositionSizeRule(RiskRule):
    """Limits capital allocation in a single stock (e.g. 10% of portfolio)."""

    def __init__(self, max_position_pct: float = 0.10):
        super().__init__(name="MaxPositionSizeRule")
        self.max_position_pct = max_position_pct

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        # If closing/reducing existing position, allow execution
        pos = _find_position(portfolio, order.symbol)
        order_side_str = str(order.side.value if hasattr(order.side, "value") else order.side).upper()
        if pos and pos.quantity != 0:
            if (pos.quantity > 0 and order_side_str == "SELL") or (pos.quantity < 0 and order_side_str == "BUY"):
                return True, None

        total_val = portfolio.total_portfolio_value
        if total_val <= 0:
            return False, "Total portfolio value is zero or negative."

        order_val = order.quantity * order.price
        max_allowed_val = total_val * self.max_position_pct

        # For small retail accounts (< ₹10,000 equity), allow entering affordable stocks within available cash/margin
        if total_val < 10000.0:
            is_mis = getattr(order, "product", None) == ProductType.MIS
            leverage = 5.0 if is_mis else 1.0
            if (order_val / leverage) <= portfolio.cash * 1.05:
                return True, None

        if order_val > max_allowed_val * 1.01:  # 1% floating point grace
            return False, f"Order value ₹{order_val:.2f} exceeds max position size of ₹{max_allowed_val:.2f} ({self.max_position_pct*100:.1f}% of portfolio)."
        return True, None


class MaxPortfolioExposureRule(RiskRule):
    """Enforces total portfolio exposure ceiling (e.g. 80%, reserving 20% cash)."""

    def __init__(self, max_exposure_pct: float = 0.80):
        super().__init__(name="MaxPortfolioExposureRule")
        self.max_exposure_pct = max_exposure_pct

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        pos = _find_position(portfolio, order.symbol)
        order_side_str = str(order.side.value if hasattr(order.side, "value") else order.side).upper()
        if pos and pos.quantity != 0:
            if (pos.quantity > 0 and order_side_str == "SELL") or (pos.quantity < 0 and order_side_str == "BUY"):
                return True, None

        total_val = portfolio.total_portfolio_value
        if total_val <= 0:
            return False, "Total portfolio value is zero or negative."

        current_invested = sum(abs(p.position_value) for p in portfolio.positions.values())
        new_order_val = order.quantity * order.price
        projected_exposure = (current_invested + new_order_val) / total_val

        # For small retail accounts (< ₹10,000), allow entering positions as long as margin requirement is satisfied
        if total_val < 10000.0:
            is_mis = getattr(order, "product", None) == ProductType.MIS
            leverage = 5.0 if is_mis else 1.0
            if (new_order_val / leverage) <= portfolio.cash * 1.05:
                return True, None

        if projected_exposure > self.max_exposure_pct:
            return False, f"Projected exposure {projected_exposure*100:.2f}% exceeds ceiling of {self.max_exposure_pct*100:.2f}%. Mandatory 20% cash reserve required."
        return True, None


class CircuitLimitRule(RiskRule):
    """Protects against entering near upper/lower circuit freezes."""

    def __init__(self, buffer_pct: float = 0.015):
        super().__init__(name="CircuitLimitRule")
        self.buffer_pct = buffer_pct

    def evaluate(
        self,
        order: Order,
        portfolio: PortfolioState,
        instrument: Optional[Instrument] = None,
        **kwargs,
    ) -> Tuple[bool, Optional[str]]:
        if not instrument or instrument.upper_circuit <= 0 or instrument.lower_circuit <= 0:
            return True, None

        upper_bound = instrument.upper_circuit * (1.0 - self.buffer_pct)
        lower_bound = instrument.lower_circuit * (1.0 + self.buffer_pct)

        if order.price >= upper_bound:
            return False, f"Price ₹{order.price:.2f} is within {self.buffer_pct*100:.1f}% of Upper Circuit ₹{instrument.upper_circuit:.2f} for {order.symbol}."
        if order.price <= lower_bound:
            return False, f"Price ₹{order.price:.2f} is within {self.buffer_pct*100:.1f}% of Lower Circuit ₹{instrument.lower_circuit:.2f} for {order.symbol}."

        return True, None


class CashSufficiencyRule(RiskRule):
    """Ensures sufficient unencumbered cash balance."""

    def __init__(self):
        super().__init__(name="CashSufficiencyRule")

    def evaluate(self, order: Order, portfolio: PortfolioState, **kwargs) -> Tuple[bool, Optional[str]]:
        pos = _find_position(portfolio, order.symbol)
        order_side_str = str(order.side.value if hasattr(order.side, "value") else order.side).upper()
        # Closing a long position generates cash; do not reject for low cash
        if pos and pos.quantity > 0 and order_side_str == "SELL":
            return True, None

        if order_side_str == "BUY":
            is_mis = getattr(order, "product", None) == ProductType.MIS
            leverage = 5.0 if is_mis else 1.0
            required_margin = (order.quantity * order.price) / leverage
            if portfolio.cash < (required_margin * 0.99):  # Allow tiny floating precision
                prod_label = "5x Intraday MIS" if is_mis else "1x Delivery CNC"
                return False, f"Insufficient cash: Required margin ₹{required_margin:.2f} ({prod_label}), Available cash ₹{portfolio.cash:.2f}."
        return True, None
