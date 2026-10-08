"""Unit tests for Indian statutory costs and taxes calculator (Groww NSE tariff)."""

from indian_equity_agent.execution.cost_calculator import IndianCostCalculator
from indian_equity_agent.core.models import OrderSide, ProductType


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
    assert res["brokerage"] == 40.0  # ₹20 buy + ₹20 sell (capped at ₹20 each leg)
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


def test_cost_calculator_single_leg_mis():
    calc = IndianCostCalculator()
    # Buy leg (MIS): Stamp duty applies (0.003%), STT does NOT apply (₹0)
    buy_costs = calc.calculate_single_leg_costs(
        side=OrderSide.BUY,
        quantity=10,
        price=1000.0,
        product=ProductType.MIS,
    )
    assert buy_costs["turnover"] == 10000.0
    assert buy_costs["brokerage"] == 5.0  # 10,000 * 0.05% = ₹5.00 (< ₹20 cap)
    assert buy_costs["stt"] == 0.0  # No STT on MIS Buy
    assert buy_costs["stamp_duty"] == 0.30  # 10,000 * 0.003% = ₹0.30
    assert buy_costs["exchange_charges"] == 0.30  # 10,000 * 0.00297% = ~₹0.30
    assert buy_costs["total_charges"] > 5.0

    # Sell leg (MIS): STT applies (0.025%), Stamp duty does NOT apply (₹0)
    sell_costs = calc.calculate_single_leg_costs(
        side=OrderSide.SELL,
        quantity=10,
        price=1000.0,
        product=ProductType.MIS,
    )
    assert sell_costs["turnover"] == 10000.0
    assert sell_costs["brokerage"] == 5.0
    assert sell_costs["stt"] == 2.50  # 10,000 * 0.025% = ₹2.50
    assert sell_costs["stamp_duty"] == 0.0  # No stamp duty on Sell


def test_cost_calculator_small_order_groww_rates():
    calc = IndianCostCalculator()
    # 1 share of BEL @ ₹376.00 (Small order where 0.05% is far below ₹20 cap)
    costs = calc.calculate_single_leg_costs(
        side=OrderSide.SELL,
        quantity=1,
        price=376.00,
        product=ProductType.MIS,
    )
    # Brokerage: 376 * 0.0005 = 0.188 -> ₹0.19 (NOT ₹20 flat!)
    assert costs["brokerage"] == 0.19
    assert costs["stt"] == 0.09  # 376 * 0.00025 = 0.094 -> ₹0.09
    assert costs["total_charges"] < 1.0  # Total single leg cost is under ₹1!


def test_cost_calculator_short_roundtrip():
    calc = IndianCostCalculator()
    # Short: Entry Sell 10 shares @ ₹500, Exit Buy 10 shares @ ₹480
    res = calc.calculate_roundtrip_costs(
        quantity=10,
        entry_price=500.0,
        exit_price=480.0,
        is_short=True,
        product=ProductType.MIS,
    )
    # Gross P&L: (500 - 480) * 10 = +₹200.00
    assert res["gross_pnl"] == 200.0
    # Entry sell incurred STT; Exit buy incurred Stamp Duty
    assert res["stt"] > 0.0
    assert res["stamp_duty"] > 0.0
    assert res["net_pnl"] == round(200.0 - res["total_charges"], 2)
    assert res["entry_charges"] > 0
    assert res["exit_charges"] > 0
