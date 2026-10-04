from typing import Optional
from .base_broker import BaseBroker
from .cost_calculator import IndianCostCalculator
from .paper_broker import PaperBroker
from .kite_broker import KiteConnectBroker
from .groww_broker import GrowwBroker
from ..config import settings
from ..risk.kill_switch import KillSwitch


def create_broker(
    broker_type: Optional[str] = None,
    kill_switch: Optional[KillSwitch] = None,
    initial_capital: Optional[float] = None,
) -> BaseBroker:
    """Factory function to instantiate the configured broker adapter."""
    b_type = (broker_type or settings.broker.broker_type).lower()
    if b_type == "kite":
        return KiteConnectBroker(kill_switch=kill_switch)
    elif b_type == "groww":
        return GrowwBroker(kill_switch=kill_switch)
    else:
        return PaperBroker(initial_capital=initial_capital or settings.broker.paper_initial_capital)


__all__ = [
    "BaseBroker",
    "IndianCostCalculator",
    "PaperBroker",
    "KiteConnectBroker",
    "GrowwBroker",
    "create_broker",
]
