import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from indian_equity_agent.strategies.vwap_reversion import VWAPReversionStrategy
from indian_equity_agent.strategies.evolution import StrategyEvolutionEngine, TradeRecord
from indian_equity_agent.core.models import Position, ProductType


def test_vwap_reversion_strategy():
    strat = VWAPReversionStrategy()
    assert strat.name == "VWAP Institutional Reversion"

    # Synthetic price series with overextended low price below VWAP
    n = 30
    dates = pd.date_range("2026-01-01", periods=n, freq="15min")
    # Base prices
    prices = np.linspace(100, 105, n)
    # Stretch last price far below to trigger oversold reversion
    prices[-1] = 95.0
    
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices + 0.1,
        "high": prices + 0.5,
        "low": prices - 0.5,
        "close": prices,
        "volume": [1000] * (n - 1) + [5000],  # Volume surge on last candle
    })

    sig = strat.generate_signal("TESTSTOCK.NS", df)
    assert sig.symbol == "TESTSTOCK.NS"
    # Either BUY (reversion from stretched bottom) or HOLD depending on ATR threshold
    assert sig.action in ("BUY", "HOLD")
    if sig.action == "BUY":
        assert sig.suggested_stop_loss < sig.suggested_target


def test_strategy_evolution_engine(tmp_path):
    state_file = tmp_path / "test_evo_state.json"
    history_file = tmp_path / "test_journal.json"
    engine = StrategyEvolutionEngine(state_file=state_file, history_file=history_file)

    # Initial weights
    initial_trend_weight = engine.get_strategy_weight("trend_following")
    assert initial_trend_weight > 0

    # Record winning trades
    for i in range(5):
        engine.record_completed_trade(
            symbol="RELIANCE",
            strategy_key="trend_following",
            side="BUY",
            quantity=10,
            entry_price=1000.0,
            exit_price=1050.0,
            pnl=500.0,
            exit_reason="TARGET",
        )

    # After wins, weight should scale up
    boosted_weight = engine.get_strategy_weight("trend_following")
    assert boosted_weight >= initial_trend_weight

    # Risk multiplier should scale up on winning strategy
    mult = engine.get_risk_multiplier("trend_following", market_sentiment="BULLISH")
    assert mult >= 1.0

    # Record consecutive losses on another strategy
    for i in range(5):
        engine.record_completed_trade(
            symbol="INFY",
            strategy_key="mean_reversion",
            side="BUY",
            quantity=10,
            entry_price=1500.0,
            exit_price=1400.0,
            pnl=-1000.0,
            exit_reason="STOP_LOSS",
        )

    penalized_weight = engine.get_strategy_weight("mean_reversion")
    assert penalized_weight < 1.0
    reduced_mult = engine.get_risk_multiplier("mean_reversion")
    assert reduced_mult <= 0.80

    # Summary checks
    summary = engine.get_evolution_summary()
    assert summary["total_trades"] == 10
    assert summary["overall_win_rate"] == 50.0
    assert len(summary["recent_trades"]) == 10
