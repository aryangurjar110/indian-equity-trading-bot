"""Position Exit and Trailing Stop-Loss Manager for Indian Equities.

Enforces:
1. Hard stop-loss execution
2. Profit target execution
3. Trailing stop-loss ratcheting (breakeven locks & ATR trailing)
4. Mandatory 15:15 IST intraday auto square-off
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional
from ..core.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
)
from ..market_data.calendar import IndianMarketCalendar

logger = logging.getLogger("indian_equity_agent.monitoring.exit_manager")


class ExitManager:
    """Monitors positions for stop-loss, target, trailing exits, and EOD squareoff."""

    def __init__(self, breakeven_trigger_r: float = 1.5):
        self.breakeven_trigger_r = breakeven_trigger_r

    def evaluate_position_exits(
        self,
        position: Position,
        current_price: float,
        atr: Optional[float] = None,
        force_eod_squareoff: bool = False,
        current_time: Optional[datetime] = None,
    ) -> Optional[Order]:
        """Evaluates whether an exit order must be generated for the given position.

        Returns Order to exit or None.
        """
        if position.quantity == 0:
            return None

        is_long = position.quantity > 0
        qty = abs(position.quantity)
        exit_side = OrderSide.SELL if is_long else OrderSide.BUY

        # 1. Mandatory Intraday EOD Square-off (at or after 15:15 IST for MIS)
        eval_time = current_time or IndianMarketCalendar.now_ist()
        if (force_eod_squareoff or IndianMarketCalendar.is_squareoff_time(eval_time)) and position.product == ProductType.MIS:
            logger.warning(f"⏰ Mandatory Intraday Square-off triggered at {eval_time.strftime('%H:%M:%S')} for {position.symbol}")
            return Order(
                order_id=f"EOD_EXIT_{uuid.uuid4().hex[:8]}",
                symbol=position.symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                product=position.product,
                quantity=qty,
                price=current_price,
            )

        # 2. Hard Stop-Loss Hit
        if is_long and position.stop_loss > 0 and current_price <= position.stop_loss:
            logger.info(f"🛑 Stop-Loss Hit for {position.symbol} Long: Current ₹{current_price:.2f} <= SL ₹{position.stop_loss:.2f}")
            return Order(
                order_id=f"SL_EXIT_{uuid.uuid4().hex[:8]}",
                symbol=position.symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                product=position.product,
                quantity=qty,
                price=current_price,
            )

        if not is_long and position.stop_loss > 0 and current_price >= position.stop_loss:
            logger.info(f"🛑 Stop-Loss Hit for {position.symbol} Short: Current ₹{current_price:.2f} >= SL ₹{position.stop_loss:.2f}")
            return Order(
                order_id=f"SL_EXIT_{uuid.uuid4().hex[:8]}",
                symbol=position.symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                product=position.product,
                quantity=qty,
                price=current_price,
            )

        # 3. Target Hit
        if is_long and position.target_price > 0 and current_price >= position.target_price:
            logger.info(f"🎯 Profit Target Hit for {position.symbol} Long: Current ₹{current_price:.2f} >= Target ₹{position.target_price:.2f}")
            return Order(
                order_id=f"TGT_EXIT_{uuid.uuid4().hex[:8]}",
                symbol=position.symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                product=position.product,
                quantity=qty,
                price=current_price,
            )

        if not is_long and position.target_price > 0 and current_price <= position.target_price:
            logger.info(f"🎯 Profit Target Hit for {position.symbol} Short: Current ₹{current_price:.2f} <= Target ₹{position.target_price:.2f}")
            return Order(
                order_id=f"TGT_EXIT_{uuid.uuid4().hex[:8]}",
                symbol=position.symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                product=position.product,
                quantity=qty,
                price=current_price,
            )

        # 4. Trailing Stop-Loss Ratcheting
        initial_risk = abs(position.average_entry_price - position.stop_loss) if position.stop_loss > 0 else 0.0
        if is_long and initial_risk > 0:
            gain = current_price - position.average_entry_price
            # Breakeven lock
            if gain >= self.breakeven_trigger_r * initial_risk:
                breakeven_sl = round(position.average_entry_price + (0.1 * initial_risk), 2)
                if breakeven_sl > position.stop_loss:
                    logger.info(f"🔒 Moving SL to Breakeven+ for {position.symbol}: Old SL ₹{position.stop_loss:.2f} -> New SL ₹{breakeven_sl:.2f}")
                    position.stop_loss = breakeven_sl

        return None
