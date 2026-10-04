"""Abstract Broker Adapter Interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from ..core.models import Order, Position, PortfolioState


class BaseBroker(ABC):
    """Unified Broker Interface for paper simulation and authorized production brokers."""

    @abstractmethod
    def get_portfolio_state(self) -> PortfolioState:
        """Fetch current portfolio cash, equity, and positions."""
        pass

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit order for execution."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        pass

    @abstractmethod
    def get_positions(self) -> Dict[str, Position]:
        """Fetch all currently open positions."""
        pass

    @abstractmethod
    def get_orders(self) -> List[Order]:
        """Fetch orders placed during current session."""
        pass

    def get_wallet_margins(self) -> Dict[str, Any]:
        """Fetch detailed live wallet margin and PnL metrics."""
        port = self.get_portfolio_state()
        unrealized = sum(p.unrealized_pnl for p in port.positions.values())
        return {
            "status": "CONNECTED",
            "available_cash": port.cash,
            "used_margin": sum(p.position_value for p in port.positions.values()),
            "collateral": 0.0,
            "total_equity": port.total_portfolio_value,
            "daily_realized_pnl": port.daily_realized_pnl,
            "unrealized_pnl": unrealized,
            "daily_total_pnl": port.daily_realized_pnl + unrealized,
            "positions_count": len(port.positions),
            "message": "Broker Active",
        }
