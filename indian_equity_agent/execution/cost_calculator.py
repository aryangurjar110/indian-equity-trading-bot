"""Indian Equity Statutory Charges and Taxes Calculator (NSE & Groww).

Calculates exact STT, NSE Exchange Turnover Charges, SEBI Charges, IPFT,
Stamp Duty, GST, and Brokerage for Intraday (MIS) and Delivery (CNC).
Supports both single-leg (entry or exit) and full roundtrip calculations
with exact statutory rules for Long (Buy->Sell) and Short (Sell->Buy) positions.
"""

from __future__ import annotations

from typing import Dict, Optional, Union
from ..core.models import OrderSide, ProductType
from ..config import settings


class IndianCostCalculator:
    """Calculates all statutory taxes and broker charges for NSE trades (Groww tariff)."""

    def __init__(self):
        self.cfg = settings.costs

    def calculate_turnover(self, buy_qty: int, buy_price: float, sell_qty: int, sell_price: float) -> float:
        """Total turnover = (Buy Qty * Buy Price) + (Sell Qty * Sell Price)."""
        return round((buy_qty * buy_price) + (sell_qty * sell_price), 2)

    def calculate_single_leg_costs(
        self,
        side: Union[OrderSide, str],
        quantity: int,
        price: float,
        product: Union[ProductType, str] = ProductType.MIS,
    ) -> Dict[str, float]:
        """Computes exact statutory charges and brokerage for a single executed order leg.

        Args:
            side: OrderSide.BUY / 'BUY' or OrderSide.SELL / 'SELL'
            quantity: Executed share quantity (positive integer)
            price: Executed price per share
            product: MIS (Intraday) or CNC (Delivery)
        """
        qty = abs(quantity)
        turnover = round(qty * price, 2)
        side_str = side.value if hasattr(side, "value") else str(side).upper()
        is_buy = side_str == "BUY"
        is_mis = (product == ProductType.MIS or str(product).upper() == "MIS")

        # 1. Brokerage: Groww charges min(₹20, 0.05% for MIS or 0.1% for CNC) per executed order
        rate = getattr(self.cfg, "brokerage_intraday_pct", 0.0005) if is_mis else getattr(self.cfg, "brokerage_delivery_pct", 0.0010)
        brokerage = min(self.cfg.brokerage_per_order_max, round(turnover * rate, 2))

        # 2. STT (Securities Transaction Tax):
        # MIS (Intraday): 0.025% on SELL only. BUY = ₹0.
        # CNC (Delivery): 0.1% on BUY and 0.1% on SELL.
        if is_mis:
            stt = round(turnover * self.cfg.stt_intraday_sell_pct, 2) if not is_buy else 0.0
        else:
            stt_rate = self.cfg.stt_delivery_buy_pct if is_buy else self.cfg.stt_delivery_sell_pct
            stt = round(turnover * stt_rate, 2)

        # 3. Exchange Turnover Fee (NSE: 0.00297%)
        exchange_charges = round(turnover * self.cfg.nse_turnover_fee_pct, 2)

        # 4. SEBI Turnover Fee (₹10 per crore = 0.0001%)
        sebi_charges = round(turnover * self.cfg.sebi_turnover_fee_pct, 4)

        # 5. IPFT Fee (NSE Investor Protection Fund: ₹10 per crore = 0.0001%)
        ipft_charges = round(turnover * getattr(self.cfg, "ipft_turnover_fee_pct", 0.000001), 4)

        # 6. Stamp Duty (State Stamp Act): Buy leg only (0.003% MIS, 0.015% CNC). Sell = ₹0.
        if is_buy:
            stamp_rate = self.cfg.stamp_duty_intraday_pct if is_mis else self.cfg.stamp_duty_delivery_pct
            stamp_duty = round(turnover * stamp_rate, 2)
        else:
            stamp_duty = 0.0

        # 7. GST: 18% on (Brokerage + Exchange Charges + SEBI Charges + IPFT)
        taxable_services = brokerage + exchange_charges + sebi_charges + ipft_charges
        gst = round(taxable_services * self.cfg.gst_pct, 2)

        total_taxes = round(stt + exchange_charges + sebi_charges + ipft_charges + stamp_duty + gst, 2)
        total_charges = round(brokerage + total_taxes, 2)

        return {
            "side": side_str,
            "quantity": qty,
            "price": price,
            "turnover": turnover,
            "brokerage": brokerage,
            "stt": stt,
            "exchange_charges": exchange_charges,
            "sebi_charges": sebi_charges,
            "ipft_charges": ipft_charges,
            "stamp_duty": stamp_duty,
            "gst": gst,
            "total_taxes": total_taxes,
            "total_charges": total_charges,
        }

    def calculate_roundtrip_costs(
        self,
        quantity: int,
        buy_price: float = 0.0,
        sell_price: float = 0.0,
        product: Union[ProductType, str] = ProductType.MIS,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        is_short: bool = False,
    ) -> Dict[str, float]:
        """Computes all roundtrip costs, gross P&L, and net P&L for an executed trade.

        Supports both Long (Buy -> Sell) and Short (Sell -> Buy) trades.
        """
        qty = abs(quantity)
        prod = ProductType.MIS if (product == ProductType.MIS or str(product).upper() == "MIS") else ProductType.CNC

        # Determine entry & exit prices and legs
        if entry_price is not None and exit_price is not None:
            if is_short:
                entry_leg = self.calculate_single_leg_costs(OrderSide.SELL, qty, entry_price, prod)
                exit_leg = self.calculate_single_leg_costs(OrderSide.BUY, qty, exit_price, prod)
                gross_pnl = round((entry_price - exit_price) * qty, 2)
                b_val = exit_leg["turnover"]
                s_val = entry_leg["turnover"]
            else:
                entry_leg = self.calculate_single_leg_costs(OrderSide.BUY, qty, entry_price, prod)
                exit_leg = self.calculate_single_leg_costs(OrderSide.SELL, qty, exit_price, prod)
                gross_pnl = round((exit_price - entry_price) * qty, 2)
                b_val = entry_leg["turnover"]
                s_val = exit_leg["turnover"]
        else:
            # Traditional call: buy_price and sell_price provided
            if is_short:
                # For short: entry was sell @ sell_price, exit was buy @ buy_price
                entry_leg = self.calculate_single_leg_costs(OrderSide.SELL, qty, sell_price, prod)
                exit_leg = self.calculate_single_leg_costs(OrderSide.BUY, qty, buy_price, prod)
                gross_pnl = round((sell_price - buy_price) * qty, 2)
            else:
                # For long: entry was buy @ buy_price, exit was sell @ sell_price
                entry_leg = self.calculate_single_leg_costs(OrderSide.BUY, qty, buy_price, prod)
                exit_leg = self.calculate_single_leg_costs(OrderSide.SELL, qty, sell_price, prod)
                gross_pnl = round((sell_price - buy_price) * qty, 2)
            b_val = round(qty * buy_price, 2)
            s_val = round(qty * sell_price, 2)

        total_turnover = round(b_val + s_val, 2)
        total_brokerage = round(entry_leg["brokerage"] + exit_leg["brokerage"], 2)
        stt = round(entry_leg["stt"] + exit_leg["stt"], 2)
        exchange_charges = round(entry_leg["exchange_charges"] + exit_leg["exchange_charges"], 2)
        sebi_charges = round(entry_leg["sebi_charges"] + exit_leg["sebi_charges"], 4)
        ipft_charges = round(entry_leg["ipft_charges"] + exit_leg["ipft_charges"], 4)
        stamp_duty = round(entry_leg["stamp_duty"] + exit_leg["stamp_duty"], 2)
        gst = round(entry_leg["gst"] + exit_leg["gst"], 2)

        total_taxes = round(stt + exchange_charges + sebi_charges + ipft_charges + stamp_duty + gst, 2)
        total_charges = round(total_brokerage + total_taxes, 2)
        net_pnl = round(gross_pnl - total_charges, 2)
        breakeven_pts = round(total_charges / qty, 2) if qty > 0 else 0.0

        return {
            "buy_value": b_val,
            "sell_value": s_val,
            "turnover": total_turnover,
            "brokerage": total_brokerage,
            "stt": stt,
            "exchange_charges": exchange_charges,
            "sebi_charges": sebi_charges,
            "ipft_charges": ipft_charges,
            "stamp_duty": stamp_duty,
            "gst": gst,
            "total_taxes": total_taxes,
            "total_charges": total_charges,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "breakeven_points_per_share": breakeven_pts,
            "entry_charges": entry_leg["total_charges"],
            "exit_charges": exit_leg["total_charges"],
        }
