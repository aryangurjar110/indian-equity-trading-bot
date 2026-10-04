"""Unit tests for Paper Broker simulation."""

from indian_equity_agent.execution.paper_broker import PaperBroker
from indian_equity_agent.core.models import Order, OrderSide, OrderStatus, ProductType


def test_paper_broker_order_lifecycle():
    broker = PaperBroker(initial_capital=500000.0, slippage_pct=0.0005)

    # 1. Buy Order
    buy_order = Order(
        order_id="PB_1",
        symbol="TCS",
        side=OrderSide.BUY,
        quantity=10,
        price=3900.0,
        stop_loss=3850.0,
        target_price=4000.0,
    )
    filled_buy = broker.place_order(buy_order)

    assert filled_buy.status == OrderStatus.FILLED
    # Fill price includes slippage: 3900 * 1.0005 = 3901.95
    assert filled_buy.average_fill_price >= 3900.0

    positions = broker.get_positions()
    assert "TCS" in positions
    assert positions["TCS"].quantity == 10

    # 2. Sell Order (Closing position with profit)
    sell_order = Order(
        order_id="PB_2",
        symbol="TCS",
        side=OrderSide.SELL,
        quantity=10,
        price=4000.0,
    )
    filled_sell = broker.place_order(sell_order)

    assert filled_sell.status == OrderStatus.FILLED
    positions_after = broker.get_positions()
    assert "TCS" not in positions_after
    assert broker.daily_realized_pnl > 0.0  # Profitable net PnL after charges
