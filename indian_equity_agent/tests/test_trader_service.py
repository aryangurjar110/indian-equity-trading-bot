"""Unit tests for AutonomousTraderService and Web Mission Control Endpoints."""

import pytest
import asyncio
import time
from fastapi.testclient import TestClient

from indian_equity_agent.web.trader_service import AutonomousTraderService
from indian_equity_agent.execution.paper_broker import PaperBroker
from indian_equity_agent.risk.kill_switch import KillSwitch
from indian_equity_agent.risk.engine import RiskEngine
from indian_equity_agent.ai_engine.gemini_analyst import GeminiMarketAnalyst
from indian_equity_agent.core.models import Order, OrderSide, OrderType, ProductType
from indian_equity_agent.market_data.calendar import IndianMarketCalendar
from indian_equity_agent.web.app import app


@pytest.mark.anyio
async def test_trader_service_start_stop(tmp_path):
    ks = KillSwitch()
    broker = PaperBroker(initial_capital=500000.0)
    service = AutonomousTraderService(broker=broker, kill_switch=ks, state_file=tmp_path / "trader_state.json")

    assert not service.is_running
    assert service.current_state == "STOPPED"

    # Start service with mock data
    res_start = await service.start(
        watchlist=["TCS.NS", "INFY.NS"],
        strategy="trend_following",
        scan_interval=5,
        use_mock=True,
    )
    assert res_start["status"] in ("STARTED", "ALREADY_RUNNING")
    assert service.is_running
    assert "TCS.NS" in service.watchlist

    # Short delay to allow task loop to run
    await asyncio.sleep(1.5)

    status = service.get_status()
    assert status["is_running"] is True
    assert status["scan_interval"] == 5

    # Stop service
    res_stop = await service.stop()
    assert res_stop["status"] == "STOPPED"
    assert not service.is_running


def test_trader_service_manual_close_position(tmp_path):
    ks = KillSwitch()
    broker = PaperBroker(initial_capital=500000.0)
    service = AutonomousTraderService(broker=broker, kill_switch=ks, state_file=tmp_path / "trader_state.json")

    # 1. Place an order to have an active position
    buy_order = Order(
        order_id="TEST_POS_1",
        symbol="RELIANCE.NS",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.CNC,
        quantity=10,
        price=2900.0,
    )
    broker.place_order(buy_order)

    positions = broker.get_positions()
    assert "RELIANCE.NS" in positions
    assert positions["RELIANCE.NS"].quantity == 10

    # 2. Test manual close position
    close_res = service.manual_close_position("RELIANCE.NS")
    assert close_res["status"] == "SUCCESS"

    positions_after = broker.get_positions()
    assert "RELIANCE.NS" not in positions_after


def test_trader_service_close_all_positions(tmp_path):
    ks = KillSwitch()
    broker = PaperBroker(initial_capital=500000.0)
    service = AutonomousTraderService(broker=broker, kill_switch=ks, state_file=tmp_path / "trader_state.json")

    broker.place_order(Order(
        order_id="TEST_1",
        symbol="TCS.NS",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.CNC,
        quantity=5,
        price=3900.0,
    ))
    broker.place_order(Order(
        order_id="TEST_2",
        symbol="INFY.NS",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.CNC,
        quantity=10,
        price=1800.0,
    ))

    assert len(broker.get_positions()) == 2
    res = service.close_all_positions()
    assert res["status"] == "SUCCESS"
    assert res["closed_count"] == 2
    assert len(broker.get_positions()) == 0


def test_web_api_endpoints():
    client = TestClient(app)

    # Status endpoint
    status_resp = client.get("/api/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert "portfolio" in data
    assert "wallet" in data
    assert "trader" in data

    # Wallet endpoint
    wallet_resp = client.get("/api/wallet")
    assert wallet_resp.status_code == 200
    w_data = wallet_resp.json()
    assert "available_cash" in w_data
    assert "total_equity" in w_data

    # Logs stream endpoint
    logs_resp = client.get("/api/logs/stream")
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert isinstance(logs, list)

    # Settings endpoint
    settings_resp = client.get("/api/settings")
    assert settings_resp.status_code == 200
    s_data = settings_resp.json()
    assert "broker_type" in s_data

    # Statutory charges, Net P&L & Profit to Achieve in status
    assert "daily_gross_pnl" in data["portfolio"]
    assert "total_charges" in data["portfolio"]
    assert "total_brokerage" in data["portfolio"]
    assert "total_taxes" in data["portfolio"]
    assert "daily_net_pnl" in data["portfolio"]
    assert "profit_to_achieve" in data["portfolio"]
    assert "target_profit_inr" in data["portfolio"]
    assert data["portfolio"]["daily_net_pnl"] == round(
        data["portfolio"]["daily_gross_pnl"] - data["portfolio"]["total_charges"], 2
    )

    # Test /api/status with active open position
    from indian_equity_agent.web.app import shared_broker
    from indian_equity_agent.core.models import Position
    if shared_broker:
        test_pos = Position(
            symbol="BEL",
            product=ProductType.MIS,
            quantity=-1,
            average_entry_price=376.0,
            current_price=375.0,
        )
        if hasattr(shared_broker, "positions"):
            shared_broker.positions["BEL"] = test_pos
        elif hasattr(shared_broker, "_cached_positions"):
            shared_broker._cached_positions = {"BEL": test_pos}
            shared_broker._cached_positions_ts = time.time()
        
        # Invalidate cache so get_status recalculates
        import indian_equity_agent.web.app as web_app
        web_app._cached_status_dict = None
        
        status_resp2 = client.get("/api/status")
        assert status_resp2.status_code == 200
        d2 = status_resp2.json()
        assert "error" not in d2
        if len(d2["positions"]) > 0:
            p0 = d2["positions"][0]
            assert "net_unrealized_pnl" in p0
            assert "est_charges" in p0
            assert "incurred_charges" in p0
            assert p0["est_charges"] > 0.0
            assert d2["portfolio"]["total_charges"] > 0.0


def test_intelligent_strategy_selection(tmp_path):
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta

    ks = KillSwitch()
    broker = PaperBroker(initial_capital=500000.0)
    service = AutonomousTraderService(broker=broker, kill_switch=ks, state_file=tmp_path / "test_trader.json")

    # 1. Base trend test dataframe
    dates = [datetime(2025, 1, 1) + timedelta(days=i) for i in range(60)]
    prices = [100.0 + i * 1.5 for i in range(60)]  # Uptrend
    df_trend = pd.DataFrame({
        "open": prices,
        "high": [p + 2.0 for p in prices],
        "low": [p - 1.0 for p in prices],
        "close": [p + 1.0 for p in prices],
        "volume": [100000] * 60,
    }, index=dates)

    strat, label, rationale = service.select_intelligent_strategy("TEST.NS", df_trend)
    assert strat is not None
    assert isinstance(label, str)
    assert isinstance(rationale, str)

    # 2. Test accumulated charges tracking on manual close
    service.broker.place_order(Order(
        order_id="TEST_CHARGE_POS",
        symbol="RELIANCE.NS",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.MIS,
        quantity=50,
        price=2500.0,
    ))
    assert service.accumulated_charges == 0.0
    close_res = service.manual_close_position("RELIANCE.NS")
    assert close_res["status"] == "SUCCESS"
    assert service.accumulated_charges > 0.0
    assert service.accumulated_brokerage > 0.0
    assert service.accumulated_taxes > 0.0


@pytest.mark.anyio
async def test_trader_state_persistence_and_market_timing_gate(tmp_path):
    ks = KillSwitch()
    broker = PaperBroker(initial_capital=500000.0)
    service = AutonomousTraderService(broker=broker, kill_switch=ks)
    service.state_file = tmp_path / "trader_state.json"

    # Start service with mock data
    await service.start(
        watchlist=["SBIN.NS", "ITC.NS"],
        strategy="trend_following",
        scan_interval=10,
        max_loss_inr=3000.0,
        target_profit_inr=8000.0,
        use_mock=True,
    )
    assert service.is_running
    assert service.state_file.exists()

    # Re-instantiate a new service with the same state file
    service2 = AutonomousTraderService(broker=broker, kill_switch=ks)
    service2.state_file = tmp_path / "trader_state.json"
    service2._load_persistent_state()

    assert service2.watchlist == ["SBIN.NS", "ITC.NS"]
    assert service2.max_loss_inr == 3000.0
    assert service2.target_profit_inr == 8000.0

    # Verify market timing gate blocks live trading if market is currently closed
    if not IndianMarketCalendar.is_market_open():
        res_blocked = await service2.start(use_mock=False)
        assert res_blocked["status"] == "ERROR_MARKET_CLOSED"
        assert not service2.is_running

    # Stop service
    await service.stop()


@pytest.mark.anyio
async def test_fast_position_monitor_and_fee_guaranteed_exit(tmp_path):
    ks = KillSwitch()
    broker = PaperBroker(initial_capital=500000.0)
    service = AutonomousTraderService(broker=broker, kill_switch=ks, state_file=tmp_path / "trader_state.json")

    # Place an open position
    broker.place_order(Order(
        order_id="BUY_EXIT_TEST",
        symbol="TCS.NS",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        product=ProductType.MIS,
        quantity=10,
        price=3500.0,
        stop_loss=3400.0,
        target_price=3520.0,
    ))

    pos = broker.get_positions()["TCS.NS"]
    assert pos.quantity == 10

    # Start service with mock data
    await service.start(
        watchlist=["TCS.NS"],
        strategy="trend_following",
        scan_interval=60,  # 60s main loop scan
        use_mock=True,
    )
    assert service._position_monitor_task is not None

    # Simulate price moving to target (3525.0) in mock data source
    service.mock_source.current_prices["TCS"] = 3525.0
    service.mock_source.current_prices["TCS.NS"] = 3525.0

    # Fast position monitor runs every 1.5s - wait 2.5s for fast monitor to execute exit
    await asyncio.sleep(2.5)

    # Position should have been automatically exited by fast position monitor loop without waiting for 60s
    positions_after = broker.get_positions()
    assert "TCS.NS" not in positions_after
    assert service.accumulated_charges > 0.0

    await service.stop()



