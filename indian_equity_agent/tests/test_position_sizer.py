"""Unit tests for Position Sizing and Anti-Martingale drawdown scaling."""

from indian_equity_agent.risk.position_sizer import PositionSizer
from indian_equity_agent.core.models import PortfolioState, ProductType


def test_position_sizer_basic_risk():
    sizer = PositionSizer(max_risk_pct=0.01, max_position_pct=0.10)
    portfolio = PortfolioState(
        cash=500000.0,
        total_equity=500000.0,
        peak_equity=500000.0,
        daily_starting_equity=500000.0,
    )
    # Entry = ₹1000, Stop Loss = ₹950 (Stop distance = ₹50)
    # Equity = ₹500,000. 1% Risk budget = ₹5,000
    # Qty by risk = floor(5000 / 50) = 100 shares
    # Max position value cap = 10% of 500,000 = ₹50,000 -> 50 shares
    # Conservative limit = 50 shares
    qty, risk_amount, rationale = sizer.calculate_quantity(
        symbol="TEST",
        entry_price=1000.0,
        stop_loss_price=950.0,
        portfolio=portfolio,
    )
    assert qty == 50
    assert risk_amount == 50 * 50.0  # ₹2500 risk


def test_position_sizer_drawdown_scaling():
    sizer = PositionSizer(max_risk_pct=0.01, max_position_pct=0.10)
    # Portfolio after drawdown: equity dropped from ₹500k to ₹400k
    portfolio_dd = PortfolioState(
        cash=400000.0,
        total_equity=400000.0,
        peak_equity=500000.0,
        daily_starting_equity=400000.0,
    )
    # 10% position cap is now ₹40,000 (40 shares vs 50 shares previously)
    qty, risk_amount, rationale = sizer.calculate_quantity(
        symbol="TEST",
        entry_price=1000.0,
        stop_loss_price=950.0,
        portfolio=portfolio_dd,
    )
    # Automatically contracted!
    assert qty == 40
    assert risk_amount == 40 * 50.0


def test_missing_stop_loss_rejection():
    sizer = PositionSizer()
    portfolio = PortfolioState(
        cash=500000.0,
        total_equity=500000.0,
        peak_equity=500000.0,
        daily_starting_equity=500000.0,
    )
    # Stop loss is 0 (undefined risk)
    qty, risk, rationale = sizer.calculate_quantity(
        symbol="TEST",
        entry_price=1000.0,
        stop_loss_price=0.0,
        portfolio=portfolio,
    )
    assert qty == 0
    assert "Mandatory stop-loss is missing" in rationale


def test_position_sizer_small_account_adaptation():
    """Verifies that retail accounts with small balances (e.g. ₹300) can size and enter affordable stocks."""
    sizer = PositionSizer()
    small_portfolio = PortfolioState(
        cash=300.0,
        total_equity=300.0,
        peak_equity=300.0,
        daily_starting_equity=300.0,
    )
    # TATASTEEL at ₹155 with SL at ₹151 (Stop distance = ₹4)
    # With 5x MIS leverage: margin per share is ₹31.0
    qty, risk, rationale = sizer.calculate_quantity(
        symbol="TATASTEEL.NS",
        entry_price=155.0,
        stop_loss_price=151.0,
        portfolio=small_portfolio,
        product=ProductType.MIS,
    )
    assert qty >= 1
    assert qty <= 10
    assert risk > 0
    assert "Retail Adaptive" in rationale

