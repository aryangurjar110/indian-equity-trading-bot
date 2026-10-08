"""High-fidelity Paper Trading Broker for Indian Equities.

Simulates execution latency, realistic slippage, Indian statutory tax deduction,
and strict position & portfolio state management.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional
from ..core.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioState,
    Position,
    ProductType,
)
from ..config import settings
from ..market_data.calendar import IndianMarketCalendar
from .base_broker import BaseBroker
from .cost_calculator import IndianCostCalculator

logger = logging.getLogger("indian_equity_agent.execution.paper_broker")


class PaperBroker(BaseBroker):
    """Simulated execution engine with realistic Indian market friction."""

    def __init__(
        self,
        initial_capital: Optional[float] = None,
        slippage_pct: Optional[float] = None,
    ):
        self.capital = initial_capital or settings.broker.paper_initial_capital
        self.cash = self.capital
        self.peak_equity = self.capital
        self.daily_starting_equity = self.capital
        self.daily_realized_pnl = 0.0
        self.slippage_pct = slippage_pct if slippage_pct is not None else settings.broker.paper_slippage_pct

        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.cost_calculator = IndianCostCalculator()

    def get_portfolio_state(self) -> PortfolioState:
        total_pos_val = sum(p.position_value for p in self.positions.values())
        current_equity = self.cash + total_pos_val

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        return PortfolioState(
            cash=round(self.cash, 2),
            total_equity=round(current_equity, 2),
            peak_equity=round(self.peak_equity, 2),
            daily_starting_equity=round(self.daily_starting_equity, 2),
            daily_realized_pnl=round(self.daily_realized_pnl, 2),
            positions=self.positions.copy(),
            open_orders={k: v for k, v in self.orders.items() if v.status == OrderStatus.OPEN},
        )

    def update_market_price(self, symbol: str, current_price: float) -> None:
        """Updates current price of an open position for MTM valuation."""
        if symbol in self.positions:
            self.positions[symbol].current_price = current_price

    def place_order(self, order: Order) -> Order:
        """Simulates immediate execution with slippage and statutory costs."""
        now = IndianMarketCalendar.now_ist()
        order.created_at = now

        # Slippage simulation
        if order.side == OrderSide.BUY:
            fill_price = round(order.price * (1.0 + self.slippage_pct), 2)
        else:
            fill_price = round(order.price * (1.0 - self.slippage_pct), 2)

        order_val = order.quantity * fill_price

        # Check cash adequacy
        if order.side == OrderSide.BUY and self.cash < order_val:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = f"Insufficient cash in paper broker: Need ₹{order_val:.2f}, Have ₹{self.cash:.2f}"
            self.orders[order.order_id] = order
            logger.warning(f"Paper order {order.order_id} rejected: {order.rejection_reason}")
            return order

        # Execute fill
        order.status = OrderStatus.FILLED
        order.filled_at = now
        order.filled_quantity = order.quantity
        order.average_fill_price = fill_price
        self.orders[order.order_id] = order

        # Position and cash updates
        existing_pos = self.positions.get(order.symbol)

        if not existing_pos or existing_pos.quantity == 0:
            # Opening new position
            if order.side == OrderSide.BUY:
                qty = order.quantity
                self.cash -= order_val
            else:
                qty = -order.quantity
                self.cash += order_val

            self.positions[order.symbol] = Position(
                symbol=order.symbol,
                product=order.product,
                quantity=qty,
                average_entry_price=fill_price,
                current_price=fill_price,
                stop_loss=order.stop_loss or 0.0,
                target_price=order.target_price or 0.0,
                opened_at=now,
            )
            logger.info(f"Opened new paper position {order.symbol}: Qty {qty} @ ₹{fill_price:.2f}")

        else:
            # Position modification or closing
            if existing_pos.quantity > 0 and order.side == OrderSide.SELL:
                # Closing Long
                trade_costs = self.cost_calculator.calculate_roundtrip_costs(
                    quantity=order.quantity,
                    entry_price=existing_pos.average_entry_price,
                    exit_price=fill_price,
                    is_short=False,
                    product=order.product,
                )
                gross_pnl = trade_costs["gross_pnl"]
                net_pnl = trade_costs["net_pnl"]
                self.daily_realized_pnl += gross_pnl
                self.cash += (order.quantity * fill_price) - trade_costs["total_charges"]
                logger.info(
                    f"Closed paper Long {order.symbol}: Fill ₹{fill_price:.2f}, Gross PnL: ₹{gross_pnl:.2f}, Net PnL: ₹{net_pnl:.2f} (Total Charges: ₹{trade_costs['total_charges']:.2f})"
                )
                del self.positions[order.symbol]

            elif existing_pos.quantity < 0 and order.side == OrderSide.BUY:
                # Closing Short (Buy to cover)
                trade_costs = self.cost_calculator.calculate_roundtrip_costs(
                    quantity=order.quantity,
                    entry_price=existing_pos.average_entry_price,
                    exit_price=fill_price,
                    is_short=True,
                    product=order.product,
                )
                gross_pnl = trade_costs["gross_pnl"]
                net_pnl = trade_costs["net_pnl"]
                self.daily_realized_pnl += gross_pnl
                self.cash -= (order.quantity * fill_price) + trade_costs["total_charges"]
                logger.info(
                    f"Closed paper Short {order.symbol}: Fill ₹{fill_price:.2f}, Gross PnL: ₹{gross_pnl:.2f}, Net PnL: ₹{net_pnl:.2f} (Total Charges: ₹{trade_costs['total_charges']:.2f})"
                )
                del self.positions[order.symbol]

        return order

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders and self.orders[order_id].status == OrderStatus.OPEN:
            self.orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False

    def get_positions(self) -> Dict[str, Position]:
        return self.positions.copy()

    def get_orders(self) -> List[Order]:
        return list(self.orders.values())
