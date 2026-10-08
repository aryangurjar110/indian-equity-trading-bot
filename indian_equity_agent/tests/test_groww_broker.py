"""Unit tests for Groww Broker Adapter."""

from unittest.mock import MagicMock, patch
import pytest

from indian_equity_agent.execution.groww_broker import GrowwBroker
from indian_equity_agent.execution import create_broker
from indian_equity_agent.core.models import Order, OrderSide, OrderStatus, ProductType, OrderType
from indian_equity_agent.core.exceptions import BrokerConnectionError
from indian_equity_agent.risk.kill_switch import KillSwitch


def test_groww_broker_factory():
    broker = create_broker("groww")
    assert isinstance(broker, GrowwBroker)


def test_groww_broker_headers():
    broker = GrowwBroker(api_key="test_api_key", access_token="test_jwt_token")
    headers = broker._headers
    assert headers["Authorization"] == "Bearer test_jwt_token"
    assert headers["X-API-KEY"] == "test_api_key"
    assert headers["Content-Type"] == "application/json"


@patch("requests.request")
def test_groww_broker_place_order_success(mock_request):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": {
            "order_id": "GROWW_ORD_98765",
            "status": "OPEN",
        }
    }
    mock_request.return_value = mock_resp

    broker = GrowwBroker(api_key="key", access_token="token")
    mock_client = MagicMock()
    mock_client.place_order.return_value = {"order_id": "GROWW_ORD_98765", "status": "OPEN"}
    broker._client = mock_client
    order = Order(
        order_id="LOCAL_1",
        symbol="INFY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        product=ProductType.CNC,
        quantity=15,
        price=1850.0,
    )

    result = broker.place_order(order)
    assert result.status == OrderStatus.SUBMITTED
    assert result.order_id == "GROWW_ORD_98765"
    assert "order_reference_id" in mock_client.place_order.call_args[1]


@patch("requests.request")
def test_groww_broker_order_rejection_on_api_error(mock_request):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "error",
        "message": "Insufficient funds in Groww account",
    }
    mock_request.return_value = mock_resp

    ks = KillSwitch()
    broker = GrowwBroker(api_key="key", access_token="token", kill_switch=ks)
    mock_client = MagicMock()
    mock_client.place_order.side_effect = Exception("Insufficient funds in Groww account")
    broker._client = mock_client
    order = Order(
        order_id="LOCAL_2",
        symbol="TCS",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.CNC,
        quantity=100,
        price=3900.0,
    )

    result = broker.place_order(order)
    assert result.status == OrderStatus.REJECTED
    assert "Insufficient funds" in result.rejection_reason


@patch("requests.request")
def test_groww_broker_auth_failure_triggers_kill_switch(mock_request):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_request.return_value = mock_resp

    ks = KillSwitch()
    broker = GrowwBroker(api_key="key", access_token="expired_token", kill_switch=ks)

    with pytest.raises(BrokerConnectionError):
        broker.get_positions()

    assert ks.is_active
    assert "Groww authentication failure" in ks.reason
    # Clean up kill switch state
    ks.reset("AUTHORIZE_RESET_CONFIRMED")


def test_groww_broker_unregistered_ip_rejection():
    """Verify orders rejected due to unregistered IP are REJECTED, not simulated as filled."""
    broker = GrowwBroker(api_key="key", access_token="token")
    mock_client = MagicMock()
    mock_client.place_order.side_effect = Exception("Request from unregistered IP address.")
    broker._client = mock_client

    order = Order(
        order_id="TEST_ORD_1",
        symbol="TATASTEEL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.MIS,
        quantity=5,
        price=150.0,
    )

    res = broker.place_order(order)
    assert res.status == OrderStatus.REJECTED
    assert "Unregistered IP address" in res.rejection_reason
    assert broker._public_ip in res.rejection_reason
    assert broker._ip_unregistered is True
    # Ensure no phantom local positions are created
    assert len(broker.get_positions()) == 0


def test_groww_broker_equity_calculation_never_negative():
    """Verify total_equity properly accounts for cash, margin, and unrealized pnl without negative collapse."""
    broker = GrowwBroker(api_key="key", access_token="token")
    mock_client = MagicMock()
    mock_client.get_available_margin_details.return_value = {
        "clear_cash": 300.0,
        "net_margin_used": 50.0,
        "collateral_available": 0.0,
        "equity_margin_details": {"cnc_balance_available": 300.0},
    }
    mock_client.get_positions_for_user.return_value = {"positions": []}
    broker._client = mock_client

    state = broker.get_portfolio_state()
    assert state.cash == 300.0
    assert state.total_equity == 350.0
    assert state.total_portfolio_value == 350.0

    wallet = broker.get_wallet_margins()
    assert wallet["available_cash"] == 300.0
    assert wallet["used_margin"] == 50.0
    assert wallet["total_equity"] == 350.0

