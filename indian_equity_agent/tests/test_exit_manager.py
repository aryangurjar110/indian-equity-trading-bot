"""Unit tests for ExitManager fee-aware exit logic and protections."""

import pytest
from datetime import datetime
import pytz
from indian_equity_agent.monitoring.exit_manager import ExitManager
from indian_equity_agent.core.models import Position, ProductType, OrderSide, OrderType
from indian_equity_agent.execution.cost_calculator import IndianCostCalculator


def test_target_hit_held_when_net_profit_negative_due_to_fees():
    """Verify that a target hit is held if Groww roundtrip charges exceed gross gain."""
    exit_mgr = ExitManager(min_net_profit_buffer_inr=0.50)
    
    # 1 share bought at 100.0, target is 100.50 (+0.50 gross gain)
    # Roundtrip charges on 100 INR order (brokerage + STT + GST etc.) is ~0.80 - 1.00 INR
    # Therefore Net P&L will be negative! The bot must NOT exit at target to avoid booking a net loss.
    pos = Position(
        symbol="PENNYSTOCK",
        quantity=1,
        average_entry_price=100.0,
        current_price=100.50,
        stop_loss=95.0,
        target_price=100.50,
        product=ProductType.MIS,
    )

    exit_order = exit_mgr.evaluate_position_exits(
        position=pos,
        current_price=100.50,
        current_time=datetime(2026, 10, 9, 10, 30, tzinfo=pytz.timezone("Asia/Kolkata")),
    )

    # Must be None because net profit < 0.50 INR after charges
    assert exit_order is None


def test_target_hit_exits_when_net_profit_sufficient():
    """Verify that a target hit generates an exit order when net profit exceeds the buffer."""
    exit_mgr = ExitManager(min_net_profit_buffer_inr=0.50)
    
    # 10 shares bought at 1000.0, target is 1020.0 (+200 gross gain)
    # Charges will be ~10-15 INR, net profit is ~185 INR > 0.50
    pos = Position(
        symbol="INFY",
        quantity=10,
        average_entry_price=1000.0,
        current_price=1020.0,
        stop_loss=990.0,
        target_price=1020.0,
        product=ProductType.MIS,
    )

    exit_order = exit_mgr.evaluate_position_exits(
        position=pos,
        current_price=1020.0,
        current_time=datetime(2026, 10, 9, 10, 30, tzinfo=pytz.timezone("Asia/Kolkata")),
    )

    assert exit_order is not None
    assert exit_order.symbol == "INFY"
    assert exit_order.side == OrderSide.SELL
    assert exit_order.quantity == 10
    assert exit_order.price == 1020.0


def test_hard_stop_loss_unconditional_exit():
    """Verify that Stop Loss exits unconditionally even with fees, to cap downside risk."""
    exit_mgr = ExitManager()

    pos = Position(
        symbol="TATASTEEL",
        quantity=50,
        average_entry_price=150.0,
        current_price=145.0,
        stop_loss=146.0,
        target_price=160.0,
        product=ProductType.MIS,
    )

    exit_order = exit_mgr.evaluate_position_exits(
        position=pos,
        current_price=145.0,
        current_time=datetime(2026, 10, 9, 11, 0, tzinfo=pytz.timezone("Asia/Kolkata")),
    )

    assert exit_order is not None
    assert exit_order.symbol == "TATASTEEL"
    assert exit_order.side == OrderSide.SELL
    assert exit_order.quantity == 50


def test_fee_covered_breakeven_lock_long():
    """Verify that when position gains >= 1.5R, SL ratchets to cover entry price + roundtrip fees."""
    exit_mgr = ExitManager(breakeven_trigger_r=1.5)

    # 10 shares bought at 500.0, initial SL 490.0 (risk = 10.0 per share, 1.5R = 15.0 pts gain)
    pos = Position(
        symbol="SBIN",
        quantity=10,
        average_entry_price=500.0,
        current_price=516.0, # gain = 16.0 >= 15.0 (1.6R)
        stop_loss=490.0,
        target_price=530.0,
        product=ProductType.MIS,
    )

    # Evaluate exits (not target yet)
    exit_order = exit_mgr.evaluate_position_exits(
        position=pos,
        current_price=516.0,
        current_time=datetime(2026, 10, 9, 11, 0, tzinfo=pytz.timezone("Asia/Kolkata")),
    )

    assert exit_order is None
    # SL should now be ratcheted above entry price (500.0) + breakeven points
    assert pos.stop_loss > 500.0
