"""Performance and Risk Metrics for Indian Equities Backtesting.

Calculates Sharpe ratio, Sortino ratio, Calmar ratio, Maximum Drawdown,
Win/Loss distributions, profit factor, and cost friction impacts.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List
import numpy as np
import pandas as pd


def calculate_performance_metrics(
    equity_curve: List[float],
    trades: List[Dict[str, Any]],
    risk_free_rate_annual: float = 0.065,  # Indian 10Y G-Sec bond yield ~6.5%
) -> Dict[str, Any]:
    """Calculates comprehensive quantitative performance metrics."""
    if not equity_curve or len(equity_curve) < 2:
        return {"error": "Insufficient equity data for metrics calculation"}

    eq_series = pd.Series(equity_curve)
    returns = eq_series.pct_change().dropna()

    initial_capital = equity_curve[0]
    final_capital = equity_curve[-1]
    net_profit = final_capital - initial_capital
    return_pct = (net_profit / initial_capital) * 100.0 if initial_capital > 0 else 0.0

    # Drawdown calculations
    running_max = eq_series.cummax()
    drawdown = (eq_series - running_max) / running_max
    max_drawdown_pct = abs(float(drawdown.min())) * 100.0

    # Returns statistics
    trading_days_approx = len(returns)
    annualization_factor = math.sqrt(252) if trading_days_approx > 10 else 1.0
    rf_daily = (1.0 + risk_free_rate_annual) ** (1.0 / 252.0) - 1.0

    mean_ret = float(returns.mean()) if not returns.empty else 0.0
    std_ret = float(returns.std()) if not returns.empty else 0.0
    annualized_volatility = std_ret * annualization_factor * 100.0

    # Sharpe Ratio
    excess_ret = mean_ret - rf_daily
    sharpe_ratio = (excess_ret / std_ret * annualization_factor) if std_ret > 1e-8 else 0.0

    # Sortino Ratio (Downside deviation)
    negative_returns = returns[returns < 0]
    downside_std = float(negative_returns.std()) if not negative_returns.empty else 0.0
    sortino_ratio = (excess_ret / downside_std * annualization_factor) if downside_std > 1e-8 else 0.0

    # Calmar Ratio
    calmar_ratio = (return_pct / max_drawdown_pct) if max_drawdown_pct > 0.01 else 0.0

    # Trade statistics
    total_trades = len(trades)
    if total_trades > 0:
        pnl_list = [t.get("net_pnl", 0.0) for t in trades]
        winning_trades = [p for p in pnl_list if p > 0]
        losing_trades = [p for p in pnl_list if p < 0]

        win_rate = (len(winning_trades) / total_trades) * 100.0
        avg_win = float(np.mean(winning_trades)) if winning_trades else 0.0
        avg_loss = float(abs(np.mean(losing_trades))) if losing_trades else 0.0
        win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else avg_win

        gross_profits = sum(winning_trades)
        gross_losses = abs(sum(losing_trades))
        profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (gross_profits if gross_profits > 0 else 0.0)

        total_costs_paid = sum(t.get("total_charges", 0.0) for t in trades)
        expectancy = float(np.mean(pnl_list)) if pnl_list else 0.0
    else:
        win_rate = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        win_loss_ratio = 0.0
        profit_factor = 0.0
        total_costs_paid = 0.0
        expectancy = 0.0

    return {
        "initial_capital": round(initial_capital, 2),
        "final_capital": round(final_capital, 2),
        "net_profit": round(net_profit, 2),
        "return_pct": round(return_pct, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "annualized_volatility_pct": round(annualized_volatility, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "sortino_ratio": round(sortino_ratio, 2),
        "calmar_ratio": round(calmar_ratio, 2),
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_inr": round(expectancy, 2),
        "total_statutory_costs": round(total_costs_paid, 2),
    }
