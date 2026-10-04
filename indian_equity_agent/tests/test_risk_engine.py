"""Unit tests for Independent Deterministic Risk Engine."""

import pytest
from pathlib import Path
import tempfile

from indian_equity_agent.risk.engine import RiskEngine
from indian_equity_agent.risk.kill_switch import KillSwitch
from indian_equity_agent.risk.rules import (
    CashSufficiencyRule,
    CircuitLimitRule,
    DuplicateOrderRule,
    KillSwitchRule,
    MaxDailyLossRule,
    MaxDrawdownRule,
    MaxOpenPositionsRule,
    MaxPositionSizeRule,
)
from indian_equity_agent.core.models import (
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioState,
    Position,
    ProductType,
)


@pytest.fixture
def clean_kill_switch():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    ks = KillSwitch(state_file=path)
    yield ks
    if path.exists():
        path.unlink()


@pytest.fixture
def sample_portfolio():
    return PortfolioState(
        cash=500000.0,
        total_equity=500000.0,
        peak_equity=500000.0,
        daily_starting_equity=500000.0,
        daily_realized_pnl=0.0,
        positions={},
        open_orders={},
    )


def test_kill_switch_blocks_orders(clean_kill_switch, sample_portfolio):
    engine = RiskEngine(kill_switch=clean_kill_switch)
    clean_kill_switch.trigger("Manual emergency test halt")

    order = Order(
        order_id="ORD1",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        quantity=10,
        price=2900.0,
        stop_loss=2850.0,
    )

    decision = engine.evaluate_order(order, sample_portfolio, skip_market_hours=True)
    assert decision.approved is False
    assert decision.action == "REJECT"
    assert "Emergency kill switch is ENGAGED" in decision.reason


def test_max_daily_loss_rejection(clean_kill_switch):
    # Portfolio with daily loss of ₹15,000 on ₹500,000 starting equity = 3% loss (exceeds 2% cap)
    loss_portfolio = PortfolioState(
        cash=485000.0,
        total_equity=485000.0,
        peak_equity=500000.0,
        daily_starting_equity=500000.0,
        daily_realized_pnl=-15000.0,
    )
    engine = RiskEngine(kill_switch=clean_kill_switch)

    order = Order(
        order_id="ORD1",
        symbol="TCS",
        side=OrderSide.BUY,
        quantity=5,
        price=3900.0,
        stop_loss=3850.0,
    )

    decision = engine.evaluate_order(order, loss_portfolio, skip_market_hours=True)
    assert decision.approved is False
    assert "Daily loss" in decision.reason


def test_max_drawdown_rejection(clean_kill_switch):
    # Portfolio with 8% drawdown from peak ₹500,000 -> ₹460,000 (exceeds 6% cap)
    dd_portfolio = PortfolioState(
        cash=460000.0,
        total_equity=460000.0,
        peak_equity=500000.0,
        daily_starting_equity=470000.0,
        daily_realized_pnl=0.0,
    )
    engine = RiskEngine(kill_switch=clean_kill_switch)

    order = Order(
        order_id="ORD1",
        symbol="INFY",
        side=OrderSide.BUY,
        quantity=10,
        price=1800.0,
        stop_loss=1750.0,
    )

    decision = engine.evaluate_order(order, dd_portfolio, skip_market_hours=True)
    assert decision.approved is False
    assert "drawdown" in decision.reason.lower()


def test_max_open_positions_rejection(clean_kill_switch, sample_portfolio):
    # Fill up 5 active positions
    for sym in ["SYM1", "SYM2", "SYM3", "SYM4", "SYM5"]:
        sample_portfolio.positions[sym] = Position(
            symbol=sym,
            quantity=10,
            average_entry_price=100.0,
            current_price=100.0,
            stop_loss=90.0,
            target_price=120.0,
        )
    sample_portfolio.cash = 500000.0 - 5000.0

    engine = RiskEngine(kill_switch=clean_kill_switch)
    order = Order(
        order_id="ORD_6",
        symbol="SYM6",  # 6th position
        side=OrderSide.BUY,
        quantity=5,
        price=500.0,
        stop_loss=480.0,
    )

    decision = engine.evaluate_order(order, sample_portfolio, skip_market_hours=True)
    assert decision.approved is False
    assert "Maximum active positions limit (5) reached" in decision.reason


def test_duplicate_order_rejection(clean_kill_switch, sample_portfolio):
    # Existing active position in RELIANCE
    sample_portfolio.positions["RELIANCE"] = Position(
        symbol="RELIANCE",
        quantity=20,
        average_entry_price=2900.0,
        current_price=2900.0,
        stop_loss=2850.0,
        target_price=3000.0,
    )
    sample_portfolio.cash = 500000.0 - (20 * 2900.0)

    engine = RiskEngine(kill_switch=clean_kill_switch)
    # Attempt to average down / add more
    order = Order(
        order_id="ORD2",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        quantity=10,
        price=2900.0,
        stop_loss=2850.0,
    )

    decision = engine.evaluate_order(order, sample_portfolio, skip_market_hours=True)
    assert decision.approved is False
    assert "Averaging down / compounding is prohibited" in decision.reason


def test_max_position_size_cap(clean_kill_switch, sample_portfolio):
    engine = RiskEngine(kill_switch=clean_kill_switch)
    # Portfolio equity = ₹500,000. Max 10% = ₹50,000.
    # Order value = 30 * ₹3,000 = ₹90,000 (exceeds ₹50,000 cap)
    order = Order(
        order_id="ORD_BIG",
        symbol="TCS",
        side=OrderSide.BUY,
        quantity=30,
        price=3000.0,
        stop_loss=2900.0,
    )

    decision = engine.evaluate_order(order, sample_portfolio, skip_market_hours=True)
    assert decision.approved is False
    assert "exceeds max position size" in decision.reason


def test_circuit_limit_proximity_rejection(clean_kill_switch, sample_portfolio):
    engine = RiskEngine(kill_switch=clean_kill_switch)
    # Upper circuit is ₹1000. 1.5% buffer means prices >= ₹985 are rejected
    instrument = Instrument(symbol="XYZ", upper_circuit=1000.0, lower_circuit=800.0)

    order = Order(
        order_id="ORD_CIRCUIT",
        symbol="XYZ",
        side=OrderSide.BUY,
        quantity=10,
        price=990.0,  # Dangerously close to 1000
        stop_loss=950.0,
    )

    decision = engine.evaluate_order(order, sample_portfolio, instrument=instrument, skip_market_hours=True)
    assert decision.approved is False
    assert "Upper Circuit" in decision.reason
