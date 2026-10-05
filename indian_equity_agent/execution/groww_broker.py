"""Groww Broker Adapter for Indian Equities (NSE/BSE).

Provides authorized execution against Groww Trading APIs using the official GrowwAPI SDK.
Automatically exchanges API Key and Secret for daily session access tokens.
All credentials are kept strictly in .env and outside the source code.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
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

logger = logging.getLogger("indian_equity_agent.execution.groww")


def _to_float(val: Any, default: float = 0.0) -> float:
    """Safely converts value to float, handling None, empty string, or invalid types."""
    try:
        if val is None:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


class GrowwBroker(BaseBroker):
    """Groww Trading API adapter powered by official GrowwAPI SDK."""

    BASE_URL = "https://api.groww.in/v1"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        kill_switch: Optional[KillSwitch] = None,
    ):
        self.api_key = api_key or settings.broker.groww_api_key
        self.api_secret = api_secret or settings.broker.groww_api_secret
        self.access_token = access_token or settings.broker.groww_access_token
        self.kill_switch = kill_switch or KillSwitch()

        self._client = None
        self._ucc = ""
        self._last_auth_error = ""
        self._whitelisted_ip = (
            settings.broker.groww_whitelisted_ip
            or os.getenv("GROWW_WHITELISTED_IP")
            or os.getenv("STATIC_IP")
            or ""
        )
        self._public_ip = self._whitelisted_ip or self._detect_public_ip()
        self._ip_unregistered = False
        self._local_positions: Dict[str, Position] = {}
        self._local_orders: List[Order] = []
        self._ensure_client()

    @staticmethod
    def _detect_public_ip() -> str:
        """Fetches the configured static IP or dynamically detects public IP."""
        override = os.getenv("GROWW_WHITELISTED_IP") or os.getenv("STATIC_IP")
        if override:
            return override.strip()
        for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://ifconfig.me/ip"):
            try:
                import urllib.request
                return urllib.request.urlopen(url, timeout=3).read().decode("utf-8").strip()
            except Exception:
                continue
        return "152.59.27.230"

    @staticmethod
    def _get_live_network_ip() -> str:
        """Always checks the real underlying external IP of this machine."""
        for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://ifconfig.me/ip"):
            try:
                import urllib.request
                return urllib.request.urlopen(url, timeout=3).read().decode("utf-8").strip()
            except Exception:
                continue
        return "152.56.177.0"

    def _ensure_client(self) -> Optional[Any]:
        """Initializes or refreshes the GrowwAPI client using API key & secret."""
        if self._client:
            return self._client

        try:
            from growwapi import GrowwAPI
        except ImportError:
            logger.warning("growwapi library not available. Using fallback REST adapter.")
            return None

        # 1. Try with existing access_token if available
        if self.access_token:
            try:
                client = GrowwAPI(self.access_token)
                try:
                    profile = client.get_user_profile()
                    self._ucc = profile.get("ucc", "") if isinstance(profile, dict) else ""
                except Exception:
                    pass
                # Verify access token works with margin check
                client.get_available_margin_details()
                self._client = client
                self._last_auth_error = ""
                self.kill_switch.record_api_success()
                logger.info(f"Groww client authenticated with access token (UCC: {self._ucc})")
                return self._client
            except Exception as e:
                logger.info(f"Existing Groww access token expired or rejected: {e}. Refreshing...")
                self._client = None

        # 2. Automatically generate fresh access token using api_key and secret
        if self.api_key:
            if self.api_secret:
                try:
                    token = GrowwAPI.get_access_token(api_key=self.api_key, secret=self.api_secret)
                    self.access_token = token
                    settings.broker.groww_access_token = token
                    client = GrowwAPI(token)
                    try:
                        profile = client.get_user_profile()
                        self._ucc = profile.get("ucc", "") if isinstance(profile, dict) else ""
                    except Exception:
                        pass
                    self._client = client
                    self._last_auth_error = ""
                    self.kill_switch.record_api_success()
                    logger.info(f"Groww access token generated successfully (UCC: {self._ucc})")
                    return self._client
                except Exception as e:
                    err_msg = str(e)
                    self._last_auth_error = err_msg
                    logger.warning(f"Groww approval token generation attempt: {err_msg}")

            # Try automated TOTP generation if pyotp is available and secret is TOTP secret
            try:
                import pyotp
                if self.api_secret:
                    clean_secret = self.api_secret.replace(" ", "").upper()
                    totp_code = pyotp.TOTP(clean_secret).now()
                    token = GrowwAPI.get_access_token(api_key=self.api_key, totp=totp_code)
                    self.access_token = token
                    settings.broker.groww_access_token = token
                    client = GrowwAPI(token)
                    try:
                        profile = client.get_user_profile()
                        self._ucc = profile.get("ucc", "") if isinstance(profile, dict) else ""
                    except Exception:
                        pass
                    self._client = client
                    self._last_auth_error = ""
                    self.kill_switch.record_api_success()
                    logger.info(f"Groww access token generated via automated TOTP (UCC: {self._ucc})")
                    return self._client
            except Exception:
                pass

            # Only record kill-switch API failure if it's NOT a normal daily approval wait
            last_err = self._last_auth_error or "Authentication failed"
            if "approval" not in last_err.lower() and "authorisation" not in last_err.lower() and "forbidden" not in last_err.lower():
                self.kill_switch.record_api_failure(last_err)
            return None

        return None

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token or self.api_key}",
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, endpoint: str, data: Optional[dict] = None, timeout: float = 15.0, record_failure: bool = True) -> dict:
        """Centralized authenticated HTTP dispatcher with failure tracking."""
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        try:
            resp = requests.request(method, url, headers=self._headers, json=data, timeout=timeout)
            if resp.status_code in (401, 403):
                self.kill_switch.trigger("Groww authentication failure: Invalid or expired access token.")
                raise BrokerConnectionError("Groww authentication failure.")
            if resp.status_code == 404 and endpoint in ("positions", "positions/user", "orders"):
                return {}

            resp.raise_for_status()
            payload = resp.json()

            if payload.get("status") == "error":
                err_msg = payload.get("message", "Unknown Groww error")
                if record_failure:
                    self.kill_switch.record_api_failure(err_msg)
                raise BrokerConnectionError(f"Groww API error: {err_msg}")

            self.kill_switch.record_api_success()
            return payload.get("data", payload)

        except requests.RequestException as e:
            if record_failure:
                self.kill_switch.record_api_failure(str(e))
            raise BrokerConnectionError(f"Network error connecting to Groww API: {e}") from e

    def get_portfolio_state(self) -> PortfolioState:
        """Fetches live margins and open positions from Groww."""
        client = self._ensure_client()
        if client:
            try:
                margin_data = client.get_available_margin_details() or {}
                clear_cash = _to_float(margin_data.get("clear_cash", 0.0))
                eq_details = margin_data.get("equity_margin_details") or {}
                cnc_avail = _to_float(eq_details.get("cnc_balance_available", clear_cash))
                avail_cash = max(clear_cash, cnc_avail)
                used_margin = _to_float(margin_data.get("net_margin_used", 0.0))
                collateral = _to_float(margin_data.get("collateral_available", 0.0))

                positions_data = self.get_positions()
                unrealized_pnl = sum(p.unrealized_pnl for p in positions_data.values())
                realized_pnl = sum(p.realized_pnl for p in positions_data.values())
                cnc_holdings_val = sum(p.position_value for p in positions_data.values() if p.product == ProductType.CNC and p.quantity > 0)

                # Total equity = Available cash + Blocked margin + Unrealized PnL + Collateral + Long CNC Delivery Value
                total_equity = max(0.0, round(avail_cash + used_margin + unrealized_pnl + collateral + cnc_holdings_val, 2))

                return PortfolioState(
                    cash=avail_cash,
                    total_equity=total_equity,
                    peak_equity=total_equity,
                    daily_starting_equity=total_equity,
                    daily_realized_pnl=realized_pnl,
                    positions=positions_data,
                )
            except Exception as e:
                logger.warning(f"Error reading Groww margins via SDK: {e}")

        # Fallback if client is unauthenticated or credentials missing
        return PortfolioState(
            cash=0.0,
            total_equity=0.0,
            peak_equity=0.0,
            daily_starting_equity=0.0,
            daily_realized_pnl=0.0,
            positions={},
        )

    def get_wallet_margins(self) -> Dict[str, Any]:
        """Fetches detailed live margin, cash, and PnL breakdown from Groww wallet."""
        client = self._ensure_client()
        if client:
            try:
                margin_data = client.get_available_margin_details() or {}
                clear_cash = _to_float(margin_data.get("clear_cash", 0.0))
                eq_details = margin_data.get("equity_margin_details") or {}
                cnc_avail = _to_float(eq_details.get("cnc_balance_available", clear_cash))
                avail_cash = max(clear_cash, cnc_avail)
                used_margin = _to_float(margin_data.get("net_margin_used", 0.0))
                collateral = _to_float(margin_data.get("collateral_available", 0.0))

                positions_data = self.get_positions()
                unrealized_pnl = sum(p.unrealized_pnl for p in positions_data.values())
                realized_pnl = sum(p.realized_pnl for p in positions_data.values())
                cnc_holdings_val = sum(p.position_value for p in positions_data.values() if p.product == ProductType.CNC and p.quantity > 0)
                total_equity = max(0.0, round(avail_cash + used_margin + unrealized_pnl + collateral + cnc_holdings_val, 2))

                ip_unreg = getattr(self, "_ip_unregistered", False)
                pub_ip = self._get_live_network_ip()
                user_msg = f"Connected to Live Groww Account (UCC: {self._ucc or 'Active'})"
                if ip_unreg:
                    user_msg += f" ⚠️ ACTION REQUIRED: Whitelist IP {pub_ip} in Groww Settings -> Trading APIs to execute live orders."

                return {
                    "status": "CONNECTED",
                    "available_cash": avail_cash,
                    "used_margin": used_margin,
                    "collateral": collateral,
                    "total_equity": total_equity,
                    "daily_realized_pnl": realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "daily_total_pnl": realized_pnl + unrealized_pnl,
                    "positions_count": len(positions_data),
                    "ucc": self._ucc,
                    "message": user_msg,
                    "ip_whitelist_required": ip_unreg,
                    "public_ip": pub_ip,
                    "live_network_ip": self._get_live_network_ip(),
                }
            except Exception as e:
                logger.warning(f"Error fetching Groww wallet margins: {e}")

        if not self.api_key:
            return {
                "status": "UNCONFIGURED",
                "available_cash": 0.0,
                "used_margin": 0.0,
                "collateral": 0.0,
                "total_equity": 0.0,
                "daily_realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "daily_total_pnl": 0.0,
                "positions_count": 0,
                "ucc": "",
                "message": "Groww API credentials missing. Provide GROWW_API_KEY and GROWW_API_SECRET in Settings.",
            }

        err_msg = getattr(self, "_last_auth_error", "") or ""
        is_approval = "approval" in err_msg.lower() or "authorisation" in err_msg.lower() or "session" in err_msg.lower() or "forbidden" in err_msg.lower()
        if is_approval:
            status = "APPROVAL_REQUIRED"
            user_msg = "Groww Daily Session Approval Required: Open Groww Web/App -> Settings -> Trading APIs and click 'Approve Session' for today (or copy your Daily Access Token into Settings)."
        else:
            status = "DISCONNECTED"
            user_msg = err_msg or "Groww client connection failed. Re-enter credentials or check API key/secret in Settings."

        return {
            "status": status,
            "available_cash": 0.0,
            "used_margin": 0.0,
            "collateral": 0.0,
            "total_equity": 0.0,
            "daily_realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "daily_total_pnl": 0.0,
            "positions_count": 0,
            "ucc": self._ucc,
            "message": user_msg,
            "public_ip": getattr(self, "_public_ip", None) or self._detect_public_ip(),
            "live_network_ip": self._get_live_network_ip(),
        }

    def place_order(self, order: Order) -> Order:
        """Submits regular order to NSE via Groww."""
        client = self._ensure_client()
        clean_symbol = order.symbol.replace(".NS", "").replace(".BO", "").strip().upper()
        groww_tx_type = "BUY" if order.side == OrderSide.BUY else "SELL"
        groww_order_type = "MARKET" if order.order_type == OrderType.MARKET else "LIMIT"
        groww_product = "MIS" if order.product == ProductType.MIS else "CNC"

        if client:
            try:
                res = client.place_order(
                    validity="DAY",
                    exchange="NSE",
                    order_type=groww_order_type,
                    product=groww_product,
                    quantity=order.quantity,
                    segment="CASH",
                    trading_symbol=clean_symbol,
                    transaction_type=groww_tx_type,
                    price=order.price if groww_order_type == "LIMIT" else 0.0,
                )
                order_id = res.get("groww_order_id") or res.get("order_id") or f"GW_{int(datetime.now().timestamp())}"
                order.order_id = str(order_id)
                order.status = OrderStatus.SUBMITTED
                self.kill_switch.record_api_success()
                logger.info(f"Order successfully submitted to Groww: ID {order.order_id} ({clean_symbol})")
                return order
            except Exception as e:
                err_str = str(e).lower()
                if ("token" in err_str or "unauthor" in err_str or "401" in err_str) and self.api_key and self.api_secret:
                    logger.info("Groww token expired during order placement. Attempting automatic re-handshake...")
                    self._client = None
                    self.access_token = None
                    new_client = self._ensure_client()
                    if new_client:
                        try:
                            res = new_client.place_order(
                                validity="DAY",
                                exchange="NSE",
                                order_type=groww_order_type,
                                product=groww_product,
                                quantity=order.quantity,
                                segment="CASH",
                                trading_symbol=clean_symbol,
                                transaction_type=groww_tx_type,
                                price=order.price if groww_order_type == "LIMIT" else 0.0,
                            )
                            order_id = res.get("groww_order_id") or res.get("order_id") or f"GW_{int(datetime.now().timestamp())}"
                            order.order_id = str(order_id)
                            order.status = OrderStatus.SUBMITTED
                            self.kill_switch.record_api_success()
                            logger.info(f"Order successfully submitted to Groww after token refresh: ID {order.order_id} ({clean_symbol})")
                            return order
                        except Exception as retry_err:
                            e = retry_err

                if "unregistered ip" in err_str or "registered ip" in err_str or "whitelist" in err_str or "ga005" in err_str:
                    self._ip_unregistered = True
                    live_ip = self._get_live_network_ip()
                    err_clean = f"Groww rejected order: Unregistered IP address (GA005). Your active connection IP is {live_ip}. Please whitelist {live_ip} in Groww -> Settings -> Trading APIs."
                else:
                    err_clean = str(e)

                order.status = OrderStatus.REJECTED
                order.rejection_reason = err_clean
                self.kill_switch.record_rejected_order(order.symbol, err_clean)
                logger.error(f"Groww order placement failed: {err_clean}")
                return order

        # Fallback via direct REST
        payload = {
            "trading_symbol": clean_symbol,
            "exchange": "NSE",
            "transaction_type": groww_tx_type,
            "order_type": groww_order_type,
            "quantity": order.quantity,
            "product": groww_product,
            "validity": "DAY",
        }
        if order.price > 0 and groww_order_type == "LIMIT":
            payload["price"] = order.price

        try:
            res = self._request("POST", "orders", data=payload)
            order.order_id = res.get("order_id", order.order_id)
            order.status = OrderStatus.SUBMITTED
            logger.info(f"Order successfully submitted to Groww: ID {order.order_id} ({clean_symbol})")
            return order
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(e)
            self.kill_switch.record_rejected_order(order.symbol, str(e))
            return order

    def cancel_order(self, order_id: str) -> bool:
        client = self._ensure_client()
        if client:
            try:
                client.cancel_order(groww_order_id=order_id, segment="CASH")
                return True
            except Exception:
                pass
        try:
            self._request("DELETE", f"orders/{order_id}")
            return True
        except Exception:
            return False

    def get_positions(self) -> Dict[str, Position]:
        """Fetches net positions from Groww."""
        positions: Dict[str, Position] = {}
        client = self._ensure_client()
        if client:
            try:
                data = client.get_positions_for_user(segment="CASH")
                net_list = data.get("positions", []) if isinstance(data, dict) else []
                for item in net_list:
                    qty = int(item.get("quantity", 0))
                    if qty == 0:
                        continue
                    sym = item.get("trading_symbol", item.get("symbol", ""))
                    positions[sym] = Position(
                        symbol=sym,
                        product=ProductType.MIS if item.get("product") == "MIS" else ProductType.CNC,
                        quantity=qty,
                        average_entry_price=float(item.get("average_price", item.get("avg_price", 0.0))),
                        current_price=float(item.get("last_price", item.get("ltp", 0.0))),
                        stop_loss=0.0,
                        target_price=0.0,
                        realized_pnl=float(item.get("realised_pnl", item.get("pnl", 0.0))),
                    )
                return positions
            except Exception as e:
                logger.warning(f"Error fetching Groww positions via SDK: {e}")

        # Fallback via direct REST
        try:
            data = self._request("GET", "positions/user", timeout=15.0, record_failure=False)
            net_list = data.get("positions", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for item in net_list:
                qty = int(item.get("quantity", 0))
                if qty == 0:
                    continue
                sym = item.get("trading_symbol", item.get("symbol", ""))
                positions[sym] = Position(
                    symbol=sym,
                    product=ProductType.MIS if item.get("product") == "MIS" else ProductType.CNC,
                    quantity=qty,
                    average_entry_price=float(item.get("average_price", item.get("avg_price", 0.0))),
                    current_price=float(item.get("last_price", item.get("ltp", 0.0))),
                    stop_loss=0.0,
                    target_price=0.0,
                    realized_pnl=float(item.get("realised_pnl", item.get("pnl", 0.0))),
                )
        except Exception as e:
            if "authentication" in str(e).lower() or "401" in str(e) or "403" in str(e):
                raise
            logger.debug(f"Positions query notice: {e}")

        return positions

    def get_orders(self) -> List[Order]:
        client = self._ensure_client()
        results: List[Order] = []
        if client:
            try:
                orders_data = client.get_order_list(segment="CASH")
                order_list = orders_data.get("orders", []) if isinstance(orders_data, dict) else []
                for o in order_list:
                    side = OrderSide.BUY if o.get("transaction_type") == "BUY" else OrderSide.SELL
                    st = o.get("order_status", o.get("status", "")).upper()
                    status = OrderStatus.FILLED if st in ("COMPLETE", "FILLED") else OrderStatus.SUBMITTED
                    results.append(
                        Order(
                            order_id=str(o.get("groww_order_id", o.get("order_id", ""))),
                            symbol=o.get("trading_symbol", o.get("symbol", "")),
                            side=side,
                            quantity=int(o.get("quantity", 0)),
                            price=float(o.get("price", 0.0)),
                            status=status,
                            average_fill_price=float(o.get("average_price", o.get("avg_price", 0.0))),
                        )
                    )
            except Exception:
                pass

        return results
