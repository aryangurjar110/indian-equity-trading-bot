"""Portfolio & Exposure Monitoring Module for Indian Equities."""

from __future__ import annotations

from typing import Dict, List
from ..core.models import PortfolioState, Position


class PortfolioTracker:
    """Monitors live MTM, daily performance, and risk exposure thresholds."""

    def __init__(self, initial_capital: float = 500000.0):
        self.initial_capital = initial_capital

    def summarize(self, state: PortfolioState) -> Dict[str, float]:
        """Calculates a clean dictionary of key portfolio metrics."""
        tot_pos_val = sum(pos.position_value for pos in state.positions.values())
        return {
            "cash": round(state.cash, 2),
            "invested_capital": round(tot_pos_val, 2),
            "total_portfolio_value": round(state.total_portfolio_value, 2),
            "daily_realized_pnl": round(state.daily_realized_pnl, 2),
            "total_unrealized_pnl": round(state.total_unrealized_pnl, 2),
            "daily_total_pnl": round(state.daily_total_pnl, 2),
            "daily_loss_pct": round(state.daily_loss_pct * 100, 2),
            "drawdown_pct": round(state.current_drawdown_pct * 100, 2),
            "exposure_pct": round(state.total_exposure_pct * 100, 2),
            "active_positions_count": len(state.positions),
        }
