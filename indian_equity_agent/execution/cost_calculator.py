"""Indian Equity Statutory Charges and Taxes Calculator (NSE).

Calculates exact STT, NSE Exchange Turnover Charges, SEBI Charges,
Stamp Duty, GST, and Brokerage for Intraday (MIS) and Delivery (CNC).
"""

from __future__ import annotations

from typing import Dict
from ..core.models import ProductType
from ..config import settings


class IndianCostCalculator:
    """Calculates all statutory taxes and broker charges for NSE trades."""

    def __init__(self):
        self.cfg = settings.costs

    def calculate_turnover(self, buy_qty: int, buy_price: float, sell_qty: int, sell_price: float) -> float:
        """Total turnover = (Buy Qty * Buy Price) + (Sell Qty * Sell Price)."""
        return (buy_qty * buy_price) + (sell_qty * sell_price)

    def calculate_roundtrip_costs(
        self,
        quantity: int,
        buy_price: float,
        sell_price: float,
        product: ProductType = ProductType.MIS,
    ) -> Dict[str, float]:
        """Computes all roundtrip costs for an executed trade."""
        buy_val = quantity * buy_price
        sell_val = quantity * sell_price
        total_turnover = buy_val + sell_val

        # 1. Brokerage (e.g. ₹20 flat or 0.03% per leg)
        buy_brokerage = min(self.cfg.brokerage_per_order_max, buy_val * self.cfg.brokerage_pct)
        sell_brokerage = min(self.cfg.brokerage_per_order_max, sell_val * self.cfg.brokerage_pct)
        total_brokerage = round(buy_brokerage + sell_brokerage, 2)

        # 2. STT (Securities Transaction Tax)
        if product == ProductType.MIS:
            # Intraday: 0.025% on sell turnover only
            stt = round(sell_val * self.cfg.stt_intraday_sell_pct, 2)
        else:
            # Delivery: 0.1% on both buy and sell turnover
            stt = round((buy_val * self.cfg.stt_delivery_buy_pct) + (sell_val * self.cfg.stt_delivery_sell_pct), 2)

        # 3. Exchange Turnover Fees (NSE: 0.00297%)
        exchange_charges = round(total_turnover * self.cfg.nse_turnover_fee_pct, 2)

        # 4. SEBI Turnover Charges (₹10 per crore = 0.000001)
        sebi_charges = round(total_turnover * self.cfg.sebi_turnover_fee_pct, 4)

        # 5. Stamp Duty (State Stamp Act) - Buy leg only
        if product == ProductType.MIS:
            stamp_duty = round(buy_val * self.cfg.stamp_duty_intraday_pct, 2)
        else:
            stamp_duty = round(buy_val * self.cfg.stamp_duty_delivery_pct, 2)

        # 6. GST (18% on Brokerage + Exchange charges + SEBI charges)
        taxable_services = total_brokerage + exchange_charges + sebi_charges
        gst = round(taxable_services * self.cfg.gst_pct, 2)

        total_charges = round(total_brokerage + stt + exchange_charges + sebi_charges + stamp_duty + gst, 2)
        gross_pnl = round(sell_val - buy_val, 2)
        net_pnl = round(gross_pnl - total_charges, 2)

        return {
            "buy_value": round(buy_val, 2),
            "sell_value": round(sell_val, 2),
            "turnover": round(total_turnover, 2),
            "brokerage": total_brokerage,
            "stt": stt,
            "exchange_charges": exchange_charges,
            "sebi_charges": sebi_charges,
            "stamp_duty": stamp_duty,
            "gst": gst,
            "total_charges": total_charges,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "breakeven_points_per_share": round(total_charges / quantity, 2) if quantity > 0 else 0.0,
        }
