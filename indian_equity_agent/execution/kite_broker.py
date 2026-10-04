"""Zerodha Kite Connect v3 Broker Adapter.

Provides authorized execution against Zerodha Kite Connect APIs.
All secrets (API key, access token) are strictly loaded from environment variables.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional
import requests
from ..core.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioState,
    Position,
    ProductType,
)
from ..config import settings
from ..core.exceptions import BrokerConnectionError
from ..risk.kill_switch import KillSwitch
from .base_broker import BaseBroker

logger = logging.getLogger("indian_equity_agent.execution.kite")


class KiteConnectBroker(BaseBroker):
    """Zerodha Kite Connect v3 REST API adapter."""

    BASE_URL = "https://api.kite.trade"

    def __init__(
        self,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
        kill_switch: Optional[KillSwitch] = None,
    ):
        self.api_key = api_key or settings.broker.kite_api_key
        self.access_token = access_token or settings.broker.kite_access_token
        self.kill_switch = kill_switch or KillSwitch()

        if not self.api_key or not self.access_token:
            logger.warning(
                "KiteConnectBroker initialized without active credentials. "
                "Set KITE_API_KEY and KITE_ACCESS_TOKEN in .env for live authorized execution."
            )

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self.access_token}",
        }

    def _request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """Centralized authenticated HTTP dispatcher with failure tracking."""
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        try:
            resp = requests.request(method, url, headers=self._headers, data=data, timeout=7.0)
            if resp.status_code == 403 or resp.status_code == 401:
                self.kill_switch.trigger("Kite authentication failure: Invalid or expired access token.")
                raise BrokerConnectionError("Kite authentication failure.")

            resp.raise_for_status()
            payload = resp.json()

            if payload.get("status") != "success":
                err_msg = payload.get("message", "Unknown Kite error")
                self.kill_switch.record_api_failure(err_msg)
                raise BrokerConnectionError(f"Kite API error: {err_msg}")

            self.kill_switch.record_api_success()
            return payload.get("data", {})

        except requests.RequestException as e:
            self.kill_switch.record_api_failure(str(e))
            raise BrokerConnectionError(f"Network error connecting to Kite Connect: {e}") from e

    def get_portfolio_state(self) -> PortfolioState:
        """Fetches live margins and open positions."""
        margins = self._request("GET", "user/margins")
        equity_margins = margins.get("equity", {})
        available_cash = float(equity_margins.get("available", {}).get("cash", 0.0))
        net_equity = float(equity_margins.get("net", 0.0))

        positions_data = self.get_positions()
        total_pos_val = sum(p.position_value for p in positions_data.values())
        total_equity = available_cash + total_pos_val

        return PortfolioState(
            cash=available_cash,
            total_equity=total_equity,
            peak_equity=total_equity,
            daily_starting_equity=total_equity,
            daily_realized_pnl=0.0,
            positions=positions_data,
        )

    def place_order(self, order: Order) -> Order:
        """Submits regular order to NSE via Kite Connect."""
        kite_tx_type = "BUY" if order.side == OrderSide.BUY else "SELL"
        kite_order_type = "MARKET" if order.order_type == OrderType.MARKET else "LIMIT"
        kite_product = "MIS" if order.product == ProductType.MIS else "CNC"

        payload = {
            "tradingsymbol": order.symbol,
            "exchange": "NSE",
            "transaction_type": kite_tx_type,
            "order_type": kite_order_type,
            "quantity": order.quantity,
            "product": kite_product,
            "validity": "DAY",
        }

        if order.price > 0 and kite_order_type == "LIMIT":
            payload["price"] = order.price

        try:
            res = self._request("POST", "orders/regular", data=payload)
            order.order_id = res.get("order_id", order.order_id)
            order.status = OrderStatus.SUBMITTED
            logger.info(f"Order successfully submitted to Kite: ID {order.order_id} ({order.symbol})")
            return order
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(e)
            self.kill_switch.record_rejected_order(order.symbol, str(e))
            return order

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._request("DELETE", f"orders/regular/{order_id}")
            return True
        except Exception:
            return False

    def get_positions(self) -> Dict[str, Position]:
        """Fetches net positions from broker."""
        data = self._request("GET", "portfolio/positions")
        net_list = data.get("net", [])
        positions: Dict[str, Position] = {}

        for item in net_list:
            qty = int(item.get("quantity", 0))
            if qty == 0:
                continue
            sym = item.get("tradingsymbol", "")
            positions[sym] = Position(
                symbol=sym,
                product=ProductType.MIS if item.get("product") == "MIS" else ProductType.CNC,
                quantity=qty,
                average_entry_price=float(item.get("average_price", 0.0)),
                current_price=float(item.get("last_price", 0.0)),
                stop_loss=0.0,
                target_price=0.0,
                realized_pnl=float(item.get("pnl", 0.0)),
            )

        return positions

    def get_orders(self) -> List[Order]:
        orders_data = self._request("GET", "orders")
        results: List[Order] = []
        for o in orders_data:
            side = OrderSide.BUY if o.get("transaction_type") == "BUY" else OrderSide.SELL
            status_map = {
                "COMPLETE": OrderStatus.FILLED,
                "OPEN": OrderStatus.OPEN,
                "REJECTED": OrderStatus.REJECTED,
                "CANCELLED": OrderStatus.CANCELLED,
            }
            results.append(
                Order(
                    order_id=str(o.get("order_id")),
                    symbol=o.get("tradingsymbol", ""),
                    side=side,
                    quantity=int(o.get("quantity", 0)),
                    price=float(o.get("price", 0.0)),
                    status=status_map.get(o.get("status"), OrderStatus.SUBMITTED),
                    average_fill_price=float(o.get("average_price", 0.0)),
                )
            )
        return results
