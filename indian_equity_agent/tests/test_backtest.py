"""Unit tests for Walk-Forward Backtesting Engine and Metrics."""

from datetime import datetime, timedelta
import pytz
import pytest

from indian_equity_agent.backtest.engine import BacktestEngine
from indian_equity_agent.backtest.metrics import calculate_performance_metrics
from indian_equity_agent.market_data.calendar import IST
from indian_equity_agent.market_data.mock_live_source import MockMarketDataSource
from indian_equity_agent.strategies.trend_following import TrendFollowingStrategy


def test_calculate_performance_metrics():
    # Equity curve starting at ₹500,000, rising to ₹550,000
    curve = [500000.0, 510000.0, 520000.0, 515000.0, 530000.0, 550000.0]
    trades = [
        {"net_pnl": 10000.0, "total_charges": 150.0},
        {"net_pnl": 10000.0, "total_charges": 150.0},
        {"net_pnl": -5000.0, "total_charges": 150.0},
        {"net_pnl": 15000.0, "total_charges": 150.0},
        {"net_pnl": 20000.0, "total_charges": 150.0},
    ]
    metrics = calculate_performance_metrics(curve, trades)

    assert metrics["initial_capital"] == 500000.0
    assert metrics["final_capital"] == 550000.0
    assert metrics["net_profit"] == 50000.0
    assert metrics["total_trades"] == 5
    assert metrics["win_rate_pct"] == 80.0
    assert metrics["max_drawdown_pct"] > 0.0


def test_backtest_engine_run():
    feed = MockMarketDataSource(seed=123)
    start_dt = IST.localize(datetime(2026, 8, 20, 9, 15))
    end_dt = IST.localize(datetime(2026, 9, 1, 15, 30))
    bars = feed.get_historical_bars("RELIANCE", start_date=start_dt, end_date=end_dt, interval="15m")

    strategy = TrendFollowingStrategy()
    engine = BacktestEngine(strategy=strategy, initial_capital=500000.0)

    results = engine.run(symbol="RELIANCE", bars=bars)
    assert "error" not in results
    assert results["symbol"] == "RELIANCE"
    assert "sharpe_ratio" in results
    assert "max_drawdown_pct" in results
    assert results["initial_capital"] == 500000.0
