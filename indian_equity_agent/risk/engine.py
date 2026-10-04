"""Master Independent Deterministic Risk Engine.

HIGHEST PRIORITY COMPONENT:
The AI and Strategy engines have ZERO authority to override this engine.
Every trade must pass every deterministic rule check.
If ANY check fails: Returns 'TRADE REJECTED'.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from ..core.models import Instrument, Order, PortfolioState, RiskDecision, Tick
from ..config import settings
from .kill_switch import KillSwitch
from .position_sizer import PositionSizer
from .rules import (
    CashSufficiencyRule,
    CircuitLimitRule,
    DuplicateOrderRule,
    KillSwitchRule,
    MarketSessionRule,
    MaxDailyLossRule,
    MaxDrawdownRule,
    MaxOpenPositionsRule,
    MaxPortfolioExposureRule,
    MaxPositionSizeRule,
    RiskRule,
)

logger = logging.getLogger("indian_equity_agent.risk.engine")


class RiskEngine:
    """Deterministic, independent pre-trade and portfolio risk engine."""

    def __init__(
        self,
        kill_switch: Optional[KillSwitch] = None,
        position_sizer: Optional[PositionSizer] = None,
    ):
        self.kill_switch = kill_switch or KillSwitch()
        self.position_sizer = position_sizer or PositionSizer(
            max_risk_pct=settings.risk.max_risk_per_trade_pct,
            max_position_pct=settings.risk.max_position_size_pct,
            max_volume_pct=settings.risk.max_volume_pct_per_bar,
            tick_size=settings.market.tick_size,
        )

        # Register deterministic rules
        self.rules: List[RiskRule] = [
            KillSwitchRule(self.kill_switch),
            MarketSessionRule(),
            MaxDailyLossRule(settings.risk.max_daily_loss_pct),
            MaxDrawdownRule(settings.risk.max_drawdown_pct),
            MaxOpenPositionsRule(settings.risk.max_open_positions),
            DuplicateOrderRule(),
            MaxPositionSizeRule(settings.risk.max_position_size_pct),
            MaxPortfolioExposureRule(settings.risk.max_portfolio_exposure_pct),
            CircuitLimitRule(settings.market.circuit_limit_buffer_pct / 100.0),
            CashSufficiencyRule(),
        ]

    def evaluate_order(
        self,
        order: Order,
        portfolio: PortfolioState,
        instrument: Optional[Instrument] = None,
        quote: Optional[Tick] = None,
        context: Optional[Dict[str, Any]] = None,
        skip_market_hours: bool = False,
    ) -> RiskDecision:
        """Evaluates an order against all deterministic risk rules.

        Returns RiskDecision(approved=True) or RiskDecision(approved=False, reason="TRADE REJECTED: ...").
        """
        checked_rules: List[str] = []

        # Emergency auto-monitoring check
        if portfolio.daily_loss_pct >= settings.risk.max_daily_loss_pct:
            self.kill_switch.trigger(f"Daily loss limit breached: {portfolio.daily_loss_pct*100:.2f}% >= {settings.risk.max_daily_loss_pct*100:.2f}%")

        if portfolio.current_drawdown_pct >= settings.risk.max_drawdown_pct:
            self.kill_switch.trigger(f"Max drawdown breached: {portfolio.current_drawdown_pct*100:.2f}% >= {settings.risk.max_drawdown_pct*100:.2f}%")

        for rule in self.rules:
            # Allow skipping market hours check during backtesting or simulation
            if skip_market_hours and isinstance(rule, MarketSessionRule):
                continue

            passed, failure_reason = rule.evaluate(
                order=order,
                portfolio=portfolio,
                instrument=instrument,
                quote=quote,
                context=context,
            )
            checked_rules.append(rule.name)

            if not passed:
                logger.warning(f"❌ TRADE REJECTED by {rule.name}: {failure_reason}")
                return RiskDecision(
                    approved=False,
                    action="REJECT",
                    symbol=order.symbol,
                    reason=f"TRADE REJECTED: {rule.name} - {failure_reason}",
                    checked_rules=checked_rules,
                    adjusted_quantity=0,
                    stop_loss=order.stop_loss or 0.0,
                    risk_amount_inr=0.0,
                )

        # Stop loss distance and risk amount calculation
        stop_dist = abs(order.price - (order.stop_loss or order.price))
        risk_amount = round(order.quantity * stop_dist, 2)

        logger.info(f"✅ TRADE APPROVED for {order.symbol}: Qty {order.quantity} @ ₹{order.price:.2f}")
        return RiskDecision(
            approved=True,
            action="PROCEED",
            symbol=order.symbol,
            reason="All deterministic risk checks satisfied.",
            checked_rules=checked_rules,
            adjusted_quantity=order.quantity,
            stop_loss=order.stop_loss or 0.0,
            risk_amount_inr=risk_amount,
        )
