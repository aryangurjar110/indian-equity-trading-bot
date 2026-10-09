"""Position Sizing Module for Indian Equities.

Enforces strict Fixed-Fractional Risk sizing, mandatory stop-loss constraints,
cash adequacy, anti-martingale drawdown scaling, and NSE lot/tick size rounding.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple
from ..core.models import PortfolioState, ProductType
from ..config import settings


class PositionSizer:
    """Calculates safe, deterministic position sizes."""

    def __init__(
        self,
        max_risk_pct: float = 0.01,         # Max 1% equity at risk per trade
        max_position_pct: float = 0.10,     # Max 10% portfolio value in single stock
        max_volume_pct: float = 0.01,       # Max 1% of bar volume to limit market impact
        tick_size: float = 0.05,            # NSE equity tick size ₹0.05
    ):
        self.max_risk_pct = max_risk_pct
        self.max_position_pct = max_position_pct
        self.max_volume_pct = max_volume_pct
        self.tick_size = tick_size

    def round_to_tick(self, price: float) -> float:
        """Rounds price to NSE tick size (₹0.05)."""
        if math.isnan(price) or math.isinf(price) or price <= 0:
            return 0.0
        return round(round(price / self.tick_size) * self.tick_size, 2)

    def calculate_quantity(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        portfolio: PortfolioState,
        bar_volume: Optional[int] = None,
        product: Optional[ProductType] = None,
    ) -> Tuple[int, float, str]:
        """Calculates deterministic position quantity based on risk and constraints.

        Returns: (quantity, risk_amount_inr, sizing_rationale)
        """
        if math.isnan(entry_price) or math.isinf(entry_price) or entry_price <= 0:
            return 0, 0.0, "Invalid non-positive or NaN entry price"

        if math.isnan(stop_loss_price) or math.isinf(stop_loss_price) or stop_loss_price <= 0:
            return 0, 0.0, "Mandatory stop-loss is missing, non-positive, or NaN. Undefined risk prohibited."

        stop_distance = abs(entry_price - stop_loss_price)
        if math.isnan(stop_distance) or stop_distance < self.tick_size:
            return 0, 0.0, f"Stop loss distance ({stop_distance:.2f}) is smaller than tick size ({self.tick_size})"

        # Anti-martingale equity base: uses current portfolio equity, contracting automatically in drawdowns
        current_equity = portfolio.total_portfolio_value
        if current_equity <= 0:
            return 0, 0.0, "Non-positive portfolio equity"

        # Check if small retail account (e.g. balance < ₹10,000, like user's ₹300)
        is_small_account = current_equity < 10000.0

        # Leverage factor: 5x for intraday MIS, 1x for delivery CNC
        leverage = 5.0 if (product == ProductType.MIS or (product is None and is_small_account)) else 1.0

        # 1. Maximum monetary risk budget for this trade
        if is_small_account:
            # On small retail accounts, allow a sensible risk floor (min of ₹20 or 6% of equity) so 1 share can be entered
            risk_budget_inr = max(current_equity * self.max_risk_pct, min(20.0, current_equity * 0.06))
            raw_qty_by_risk = max(1, math.floor(risk_budget_inr / stop_distance)) if stop_distance <= entry_price * 0.10 else math.floor(risk_budget_inr / stop_distance)
        else:
            risk_budget_inr = current_equity * self.max_risk_pct
            raw_qty_by_risk = math.floor(risk_budget_inr / stop_distance)

        # Reserve a mandatory ₹25 charges buffer from available cash to guarantee brokerage/tax coverage
        fees_safety_buffer = 25.0
        usable_cash = max(0.0, portfolio.cash - fees_safety_buffer)
        if usable_cash <= 0:
            return 0, 0.0, f"Available cash (₹{portfolio.cash:.2f}) is below minimum ₹{fees_safety_buffer:.2f} safety buffer required for brokerage and exchange charges."

        # 2. Maximum capital allocation cap for a single position
        if is_small_account:
            # Allow allocating up to 95% of available buying power so small retail capital isn't locked out
            max_position_value = (usable_cash * leverage) * 0.95
            raw_qty_by_pos_cap = math.floor(max_position_value / entry_price)
        else:
            max_position_value = current_equity * self.max_position_pct
            raw_qty_by_pos_cap = math.floor(max_position_value / entry_price)

        # 3. Available cash / margin cap
        available_buying_power = usable_cash * leverage
        raw_qty_by_cash = math.floor(available_buying_power / entry_price)

        # 4. Liquidity / Market impact cap
        if bar_volume and bar_volume > 0:
            raw_qty_by_liquidity = math.floor(bar_volume * self.max_volume_pct)
        else:
            raw_qty_by_liquidity = raw_qty_by_risk  # No volume filter if unavailable

        # Final quantity is the most conservative of all limits
        final_qty = min(raw_qty_by_risk, raw_qty_by_pos_cap, raw_qty_by_cash, raw_qty_by_liquidity)

        # On small accounts, ensure minimum 1 share if affordable within available margin
        if is_small_account and final_qty <= 0:
            margin_per_share = entry_price / leverage
            if usable_cash >= margin_per_share and stop_distance <= current_equity * 0.10:
                final_qty = 1

        if final_qty <= 0:
            limiting_factor = (
                "Cash limit" if raw_qty_by_cash == 0
                else "Position cap" if raw_qty_by_pos_cap == 0
                else "Risk budget"
            )
            return 0, 0.0, f"Calculated quantity is 0 due to {limiting_factor} constraint."

        actual_risk_amount = round(final_qty * stop_distance, 2)
        rationale = (
            f"Qty {final_qty} calculated ({'Retail Adaptive' if is_small_account else 'Fixed-Risk'}): "
            f"Risk budget ₹{risk_budget_inr:.2f} with stop distance ₹{stop_distance:.2f}. "
            f"Total risk = ₹{actual_risk_amount:.2f} ({actual_risk_amount/current_equity*100:.2f}% of equity). "
            f"Position value = ₹{final_qty * entry_price:.2f} (Margin required: ₹{final_qty * entry_price / leverage:.2f})."
        )

        return final_qty, actual_risk_amount, rationale
