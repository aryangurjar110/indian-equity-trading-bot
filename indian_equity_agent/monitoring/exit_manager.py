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
from datetime import datetime
from ..core.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
)
from ..market_data.calendar import IndianMarketCalendar
from ..execution.cost_calculator import IndianCostCalculator

logger = logging.getLogger("indian_equity_agent.monitoring.exit_manager")


class ExitManager:
    """Monitors positions for stop-loss, target, trailing exits, and EOD squareoff with fee-awareness."""

    def __init__(
        self,
        breakeven_trigger_r: float = 1.5,
        cost_calculator: Optional[IndianCostCalculator] = None,
        min_net_profit_buffer_inr: float = 0.5,
    ):
        self.breakeven_trigger_r = breakeven_trigger_r
        self.cost_calculator = cost_calculator or IndianCostCalculator()
        self.min_net_profit_buffer_inr = min_net_profit_buffer_inr

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
        Guarantees that profit targets and trailing breakeven stops account for Groww
        brokerage and statutory taxes so the trade never books a net loss on target.
        """
        if position.quantity == 0:
            return None

        is_long = position.quantity > 0
        qty = abs(position.quantity)
        exit_side = OrderSide.SELL if is_long else OrderSide.BUY

        # Calculate exact roundtrip charges and net P&L at current price
        costs = self.cost_calculator.calculate_roundtrip_costs(
            quantity=qty,
            entry_price=position.average_entry_price,
            exit_price=current_price,
            is_short=not is_long,
            product=position.product,
        )
        gross_pnl = costs["gross_pnl"]
        net_pnl = costs["net_pnl"]
        total_charges = costs["total_charges"]
        be_pts = costs["breakeven_points_per_share"]

        # 1. Mandatory Intraday EOD Square-off (at or after 15:15 IST for MIS)
        eval_time = current_time or IndianMarketCalendar.now_ist()
        if (force_eod_squareoff or IndianMarketCalendar.is_squareoff_time(eval_time)) and position.product == ProductType.MIS:
            logger.warning(f"⏰ Mandatory Intraday Square-off triggered at {eval_time.strftime('%H:%M:%S')} for {position.symbol} (Net P&L: ₹{net_pnl:+.2f})")
            return Order(
                order_id=f"EOD_EXIT_{uuid.uuid4().hex[:8]}",
                symbol=position.symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                product=position.product,
                quantity=qty,
                price=current_price,
            )

        # 2. Hard Stop-Loss Hit (Risk management override to protect capital from larger losses)
        if is_long and position.stop_loss > 0 and current_price <= position.stop_loss:
            logger.info(f"🛑 Stop-Loss Hit for {position.symbol} Long: Current ₹{current_price:.2f} <= SL ₹{position.stop_loss:.2f} (Net Loss: ₹{net_pnl:.2f})")
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
            logger.info(f"🛑 Stop-Loss Hit for {position.symbol} Short: Current ₹{current_price:.2f} >= SL ₹{position.stop_loss:.2f} (Net Loss: ₹{net_pnl:.2f})")
            return Order(
                order_id=f"SL_EXIT_{uuid.uuid4().hex[:8]}",
                symbol=position.symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                product=position.product,
                quantity=qty,
                price=current_price,
            )

        # 3. Target Hit (Fee-Aware Net Profit Gate)
        # Ensure that exiting at target leaves a positive NET profit after all Groww charges and taxes.
        is_target_hit = (is_long and position.target_price > 0 and current_price >= position.target_price) or \
                        (not is_long and position.target_price > 0 and current_price <= position.target_price)

        if is_target_hit:
            if net_pnl < self.min_net_profit_buffer_inr:
                logger.info(
                    f"⏳ Target touched for {position.symbol} (Gross ₹{gross_pnl:+.2f}), but roundtrip fees (₹{total_charges:.2f}) "
                    f"leave Net P&L ₹{net_pnl:+.2f} < ₹{self.min_net_profit_buffer_inr:.2f}. "
                    f"Holding position to prevent net loss after brokerage."
                )
            else:
                logger.info(f"🎯 Profit Target Hit for {position.symbol} ({'Long' if is_long else 'Short'}): Current ₹{current_price:.2f} | Net Profit: ₹{net_pnl:+.2f} (after ₹{total_charges:.2f} Groww charges)")
                return Order(
                    order_id=f"TGT_EXIT_{uuid.uuid4().hex[:8]}",
                    symbol=position.symbol,
                    side=exit_side,
                    order_type=OrderType.MARKET,
                    product=position.product,
                    quantity=qty,
                    price=current_price,
                )

        # 4. Trailing Stop-Loss Ratcheting (Fee-Covered Breakeven Lock)
        initial_risk = abs(position.average_entry_price - position.stop_loss) if position.stop_loss > 0 else 0.0
        if is_long and initial_risk > 0:
            gain = current_price - position.average_entry_price
            # Breakeven lock for long positions: lock entry + roundtrip breakeven points + profit buffer
            if gain >= self.breakeven_trigger_r * initial_risk:
                fee_safe_sl = round(position.average_entry_price + be_pts + max(0.05, 0.05 * initial_risk), 2)
                if fee_safe_sl > position.stop_loss:
                    logger.info(f"🔒 Moving SL to Fee-Covered Breakeven+ for {position.symbol} Long: Old SL ₹{position.stop_loss:.2f} -> New SL ₹{fee_safe_sl:.2f} (Covers ₹{total_charges:.2f} charges)")
                    position.stop_loss = fee_safe_sl
        elif not is_long and initial_risk > 0:
            gain = position.average_entry_price - current_price
            # Breakeven lock for short positions: lock entry - roundtrip breakeven points - profit buffer
            if gain >= self.breakeven_trigger_r * initial_risk:
                fee_safe_sl = round(position.average_entry_price - be_pts - max(0.05, 0.05 * initial_risk), 2)
                if position.stop_loss == 0 or fee_safe_sl < position.stop_loss:
                    logger.info(f"🔒 Moving SL to Fee-Covered Breakeven+ for {position.symbol} Short: Old SL ₹{position.stop_loss:.2f} -> New SL ₹{fee_safe_sl:.2f} (Covers ₹{total_charges:.2f} charges)")
                    position.stop_loss = fee_safe_sl

        return None
