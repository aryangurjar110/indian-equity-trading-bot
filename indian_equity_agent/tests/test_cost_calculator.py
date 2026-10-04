"""Unit tests for Indian statutory costs and taxes calculator."""

from indian_equity_agent.execution.cost_calculator import IndianCostCalculator
from indian_equity_agent.core.models import ProductType


def test_cost_calculator_intraday_mis():
    calc = IndianCostCalculator()
    # Buy 100 shares @ ₹1000, Sell 100 shares @ ₹1050 (MIS Intraday)
    # Buy value = ₹100,000, Sell value = ₹105,000, Turnover = ₹205,000
    res = calc.calculate_roundtrip_costs(
        quantity=100,
        buy_price=1000.0,
        sell_price=1050.0,
        product=ProductType.MIS,
    )

    assert res["turnover"] == 205000.0
    assert res["brokerage"] == 40.0  # ₹20 buy + ₹20 sell
    # STT: 0.025% on sell only = 105,000 * 0.00025 = ₹26.25
    assert res["stt"] == 26.25
    assert res["exchange_charges"] > 0
    assert res["gst"] > 0
    assert res["gross_pnl"] == 5000.0
    assert res["net_pnl"] < res["gross_pnl"]
    assert res["net_pnl"] == round(res["gross_pnl"] - res["total_charges"], 2)


def test_cost_calculator_delivery_cnc():
    calc = IndianCostCalculator()
    # CNC Delivery
    res = calc.calculate_roundtrip_costs(
        quantity=100,
        buy_price=1000.0,
        sell_price=1050.0,
        product=ProductType.CNC,
    )
    # STT: 0.1% on buy (₹100) + 0.1% on sell (₹105) = ₹205
    assert res["stt"] == 205.0
    assert res["total_charges"] > 205.0
