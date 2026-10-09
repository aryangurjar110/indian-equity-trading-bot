"""Groww Broker Adapter for Indian Equities (NSE/BSE).

Provides authorized execution against Groww Trading APIs using the official GrowwAPI SDK.
Automatically exchanges API Key and Secret for daily session access tokens.
All credentials are kept strictly in .env and outside the source code.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
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
from ..market_data.calendar import IndianMarketCalendar
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
        cache_file: Optional[Path] = None,
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
        self._cached_portfolio_state: Optional[PortfolioState] = None
        self._cached_portfolio_ts: float = 0.0
        self._cached_wallet_margins: Optional[Dict[str, Any]] = None
        self._cached_wallet_ts: float = 0.0
        self._cached_positions: Optional[Dict[str, Position]] = None
        self._cached_positions_ts: float = 0.0
        self._last_auth_fail_ts: float = 0.0
        self.cache_file = cache_file or (settings.data_dir / "groww_portfolio_cache.json")
        self._load_portfolio_cache()
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

    def _load_portfolio_cache(self):
        """Loads persistent portfolio cache from disk if available."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cash = float(data.get("cash", 0.0))
                used_margin = float(data.get("used_margin", 0.0))
                pos_dict = {}
                for sym, p_data in data.get("positions", {}).items():
                    qty = int(p_data.get("quantity", 0))
                    if qty != 0:
                        pos = Position(
                            symbol=p_data.get("symbol", sym),
                            product=ProductType.MIS if p_data.get("product") == "MIS" else ProductType.CNC,
                            quantity=qty,
                            average_entry_price=float(p_data.get("average_entry_price", 0.0)),
                            current_price=float(p_data.get("current_price", 0.0)),
                            stop_loss=float(p_data.get("stop_loss", 0.0)),
                            target_price=float(p_data.get("target_price", 0.0)),
                            realized_pnl=float(p_data.get("realized_pnl", 0.0)),
                        )
                        pos.unrealized_pnl = float(p_data.get("unrealized_pnl", 0.0))
                        pos_dict[sym] = pos
                self._cached_positions = pos_dict
                self._cached_positions_ts = 0.0
                tot_eq = max(0.0, cash + used_margin + sum(p.unrealized_pnl for p in pos_dict.values()))
                self._cached_portfolio_state = PortfolioState(
                    cash=cash,
                    total_equity=tot_eq,
                    peak_equity=tot_eq,
                    daily_starting_equity=tot_eq,
                    daily_realized_pnl=0.0,
                    positions=pos_dict,
                )
                self._cached_portfolio_ts = 0.0
                return
            except Exception as e:
                logger.warning(f"Error loading persistent portfolio cache: {e}")

        # Clean initial state with zero fake positions
        self._cached_positions = {}
        self._cached_positions_ts = 0.0
        self._cached_portfolio_state = PortfolioState(
            cash=0.0,
            total_equity=0.0,
            peak_equity=0.0,
            daily_starting_equity=0.0,
            daily_realized_pnl=0.0,
            positions={},
        )
        self._cached_portfolio_ts = 0.0
        self._save_portfolio_cache(0.0, 0.0, {})

    def _save_portfolio_cache(self, cash: float, used_margin: float, positions: Dict[str, Position]):
        """Persists current portfolio and positions to disk."""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "cash": round(cash, 2),
                "used_margin": round(used_margin, 2),
                "positions": {
                    s: {
                        "symbol": p.symbol,
                        "product": p.product.value,
                        "quantity": p.quantity,
                        "average_entry_price": p.average_entry_price,
                        "current_price": p.current_price,
                        "stop_loss": p.stop_loss,
                        "target_price": p.target_price,
                        "realized_pnl": p.realized_pnl,
                        "unrealized_pnl": p.unrealized_pnl,
                    }
                    for s, p in positions.items()
                    if p.quantity != 0
                },
                "updated_at": IndianMarketCalendar.now_ist().isoformat(),
            }
            tmp = self.cache_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.cache_file)
        except Exception as e:
            logger.warning(f"Could not save portfolio cache: {e}")

    def _ensure_client(self) -> Optional[Any]:
        """Initializes or refreshes the GrowwAPI client using API key & secret."""
        if self._client:
            return self._client

        now_ts = time.time()
        # Cooldown guard: at least 300s (5 minutes) before retrying token generation to protect Groww 150/day rate limit
        if self._last_auth_fail_ts and (now_ts - self._last_auth_fail_ts < 300.0):
            return None

        try:
            from growwapi import GrowwAPI
        except ImportError:
            logger.warning("growwapi library not available. Using fallback REST adapter.")
            return None

        # 1. Try with existing access_token if available
        if self.access_token and len(self.access_token.strip()) > 30:
            try:
                clean_tok = self.access_token.strip().replace('"', '')
                client = GrowwAPI(clean_tok)
                try:
                    profile = client.get_user_profile()
                    self._ucc = profile.get("ucc", "") if isinstance(profile, dict) else ""
                    self._is_authenticated = True
                    self._last_auth_error = ""
                    self._client = client
                    self.kill_switch.record_api_success()
                    logger.info(f"Groww access token verified successfully (UCC: {self._ucc})")
                    return self._client
                except Exception as verify_err:
                    err_s = str(verify_err)
                    logger.warning(f"Groww token validation check failed: {err_s}")
                    self._last_auth_error = err_s
                    self._is_authenticated = False
                    self._client = None
                    return None
            except Exception as e:
                logger.info(f"Existing Groww access token init notice: {e}")
                self._last_auth_error = str(e)
                self._is_authenticated = False
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
                    # If rate limited, backoff for 10 minutes (600s) to allow reset
                    if "rate limit" in err_msg.lower() or "429" in err_msg:
                        self._last_auth_fail_ts = now_ts + 600.0
                    else:
                        self._last_auth_fail_ts = now_ts + 300.0
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

            if self._last_auth_fail_ts <= now_ts:
                self._last_auth_fail_ts = now_ts + 300.0
            return None

        self._last_auth_fail_ts = now_ts + 300.0
        return None

    @property
    def is_authenticated(self) -> bool:
        """Returns True if Groww broker client is authenticated and verified."""
        if self._client:
            return True
        return getattr(self, "_is_authenticated", False)

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
                self._is_authenticated = False
                self._last_auth_error = "Groww authentication failure: Invalid or expired access token."
                self.kill_switch.trigger("Groww authentication failure: Invalid or expired access token.")
                raise BrokerConnectionError("Groww authentication failure: Invalid or expired access token.")
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
        now_ts = time.time()
        if self._cached_portfolio_state is not None and (now_ts - self._cached_portfolio_ts < 10.0):
            return self._cached_portfolio_state

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

                state = PortfolioState(
                    cash=avail_cash,
                    total_equity=total_equity,
                    peak_equity=total_equity,
                    daily_starting_equity=total_equity,
                    daily_realized_pnl=realized_pnl,
                    positions=positions_data,
                )
                self._cached_portfolio_state = state
                self._cached_portfolio_ts = now_ts
                self._save_portfolio_cache(avail_cash, used_margin, positions_data)

                # Populate cached wallet margins simultaneously
                ip_unreg = getattr(self, "_ip_unregistered", False)
                pub_ip = self._public_ip or self._get_live_network_ip()
                self._cached_wallet_margins = {
                    "status": "CONNECTED",
                    "available_cash": avail_cash,
                    "used_margin": used_margin,
                    "collateral": collateral,
                    "total_equity": total_equity,
                    "daily_realized_pnl": realized_pnl,
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "daily_total_pnl": round(realized_pnl + unrealized_pnl, 2),
                    "positions_count": len(positions_data),
                    "ucc": self._ucc,
                    "message": f"Connected to Live Groww Account (UCC: {self._ucc or 'Active'})",
                    "ip_whitelist_required": ip_unreg,
                    "public_ip": pub_ip,
                    "live_network_ip": self._get_live_network_ip(),
                }
                self._cached_wallet_ts = now_ts
                return state
            except Exception as e:
                err_str = str(e).lower()
                logger.warning(f"Error reading Groww margins via SDK: {e}")
                self._last_auth_error = str(e)
                if "unauthor" in err_str or "401" in err_str or "auth" in err_str or "token" in err_str:
                    self._client = None
                    self._is_authenticated = False

        # Fallback if unauthenticated: NEVER fake balance or positions
        auth_msg = getattr(self, "_last_auth_error", "") or "Groww authentication required. Please provide a valid Access Token in Settings."
        is_token_err = "auth" in auth_msg.lower() or "token" in auth_msg.lower() or "expired" in auth_msg.lower()
        status_label = "TOKEN_EXPIRED" if is_token_err else "DISCONNECTED"
        display_msg = "Your daily Groww Access Token is expired or invalid. Open Groww Web -> Settings -> Trading APIs, generate today's Access Token, and paste it into Settings -> API Settings." if is_token_err else auth_msg

        self._cached_wallet_margins = {
            "status": status_label,
            "available_cash": 0.0,
            "used_margin": 0.0,
            "collateral": 0.0,
            "total_equity": 0.0,
            "daily_realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "daily_total_pnl": 0.0,
            "positions_count": 0,
            "ucc": self._ucc or "Disconnected",
            "message": display_msg,
            "ip_whitelist_required": False,
            "public_ip": self._public_ip or "74.220.48.71",
            "live_network_ip": self._get_live_network_ip(),
        }
        self._cached_wallet_ts = now_ts
        self._cached_positions = {}
        self._cached_positions_ts = now_ts
        return PortfolioState(
            cash=0.0,
            total_equity=0.0,
            peak_equity=0.0,
            daily_starting_equity=0.0,
            daily_realized_pnl=0.0,
        )

    def get_wallet_margins(self) -> Dict[str, Any]:
        """Fetches detailed live margin, cash, and PnL breakdown from Groww wallet."""
        now_ts = time.time()
        if self._cached_wallet_margins is not None and (now_ts - self._cached_wallet_ts < 10.0):
            return self._cached_wallet_margins

        # Trigger get_portfolio_state which populates wallet cache
        self.get_portfolio_state()
        if self._cached_wallet_margins is not None:
            return self._cached_wallet_margins

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

    def _update_cache_on_order(self, order: Order, clean_symbol: str):
        """Updates internal positions cache and persists to disk when an order is submitted."""
        try:
            if not self._cached_positions:
                self._load_portfolio_cache()
            pos_dict = dict(self._cached_positions or {})
            
            # Match symbol regardless of NSE/BSE prefix/suffix
            existing_key = None
            for k in list(pos_dict.keys()):
                if k.replace(".NS", "").replace(".BO", "").strip().upper() == clean_symbol:
                    existing_key = k
                    break

            qty_delta = order.quantity if order.side == OrderSide.BUY else -order.quantity
            if existing_key:
                existing = pos_dict[existing_key]
                new_qty = existing.quantity + qty_delta
                # Calculate realized PnL if closing/reducing
                if (existing.quantity > 0 and qty_delta < 0) or (existing.quantity < 0 and qty_delta > 0):
                    closed_qty = min(abs(existing.quantity), abs(qty_delta))
                    if existing.quantity > 0:
                        trade_pnl = (order.price - existing.average_entry_price) * closed_qty
                    else:
                        trade_pnl = (existing.average_entry_price - order.price) * closed_qty
                    if self._cached_portfolio_state:
                        self._cached_portfolio_state.daily_realized_pnl += round(trade_pnl, 2)

                if new_qty == 0:
                    pos_dict.pop(existing_key, None)
                    pos_dict.pop(clean_symbol, None)
                    pos_dict.pop(order.symbol, None)
                else:
                    existing.quantity = new_qty
                    pos_dict[existing_key] = existing
            else:
                if qty_delta != 0:
                    pos_dict[clean_symbol] = Position(
                        symbol=clean_symbol,
                        product=order.product,
                        quantity=qty_delta,
                        average_entry_price=order.price,
                        current_price=order.price,
                        stop_loss=order.stop_loss,
                        target_price=order.target_price,
                    )
            self._cached_positions = pos_dict
            self._cached_positions_ts = time.time()
            if self._cached_portfolio_state:
                self._cached_portfolio_state.positions = pos_dict
            cash_val = self._cached_portfolio_state.cash if self._cached_portfolio_state else 0.0
            used_m = self._cached_wallet_margins.get("used_margin", 0.0) if self._cached_wallet_margins else 0.0
            self._save_portfolio_cache(cash_val, used_m, pos_dict)
        except Exception as e:
            logger.debug(f"Cache update on order notice: {e}")

    def place_order(self, order: Order) -> Order:
        """Submits regular order to NSE via Groww."""
        self._cached_portfolio_ts = 0.0
        self._cached_wallet_ts = 0.0
        self._cached_positions_ts = 0.0

        clean_symbol = order.symbol.replace(".NS", "").replace(".BO", "").strip().upper()
        groww_tx_type = "BUY" if order.side == OrderSide.BUY else "SELL"
        groww_order_type = "MARKET" if order.order_type == OrderType.MARKET else "LIMIT"
        groww_product = "MIS" if order.product == ProductType.MIS else "CNC"
        order_ref_id = f"g{uuid.uuid4().hex[:15]}"

        client = self._ensure_client()
        if not client and not self.is_authenticated:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = "Groww authentication required. Please update daily Access Token in Settings."
            logger.warning(f"Order placement skipped for {order.symbol}: Groww is unauthenticated.")
            return order

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
                    order_reference_id=order_ref_id,
                )
                order_id = res.get("groww_order_id") or res.get("order_id") or f"GW_{int(datetime.now().timestamp())}"
                order.order_id = str(order_id)
                order.status = OrderStatus.SUBMITTED
                self._update_cache_on_order(order, clean_symbol)
                self.kill_switch.record_api_success()
                logger.info(f"Order successfully submitted to Groww: ID {order.order_id} ({clean_symbol}, ref: {order_ref_id})")
                return order
            except Exception as e:
                err_str = str(e).lower()
                if ("token" in err_str or "unauthor" in err_str or "401" in err_str) and self.api_key and self.api_secret:
                    logger.info("Groww token expired during order placement. Attempting automatic re-handshake...")
                    self._client = None
                    new_client = self._ensure_client()
                    if new_client:
                        try:
                            retry_ref_id = f"g{uuid.uuid4().hex[:15]}"
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
                                order_reference_id=retry_ref_id,
                            )
                            order_id = res.get("groww_order_id") or res.get("order_id") or f"GW_{int(datetime.now().timestamp())}"
                            order.order_id = str(order_id)
                            order.status = OrderStatus.SUBMITTED
                            self._update_cache_on_order(order, clean_symbol)
                            self.kill_switch.record_api_success()
                            logger.info(f"Order successfully submitted to Groww after token refresh: ID {order.order_id} ({clean_symbol}, ref: {retry_ref_id})")
                            return order
                        except Exception as retry_err:
                            e = retry_err
                            err_str = str(e).lower()

                is_auth_error = any(w in err_str for w in ("token", "unauthor", "401", "403", "session", "expired", "invalid"))
                if is_auth_error:
                    self._is_authenticated = False
                    self._last_auth_error = str(e)
                    order.status = OrderStatus.REJECTED
                    order.rejection_reason = "Groww API token expired or invalid. Update Access Token in Settings."
                    logger.error(f"Groww order placement failed: token expired/invalid ({e})")
                    return order

                if "unregistered ip" in err_str or "registered ip" in err_str or "whitelist" in err_str or "ga005" in err_str:
                    self._ip_unregistered = True
                    live_ip = self._public_ip or self._get_live_network_ip()
                    err_clean = f"Groww rejected order: Unregistered IP address (GA005). Your active connection IP is {live_ip}. Please whitelist {live_ip} in Groww -> Settings -> Trading APIs."
                    order.status = OrderStatus.REJECTED
                    order.rejection_reason = err_clean
                    logger.error(f"Groww order placement failed: {err_clean}")
                    return order

                err_clean = str(e)
                order.status = OrderStatus.REJECTED
                order.rejection_reason = err_clean
                self.kill_switch.record_rejected_order(order.symbol, err_clean)
                logger.error(f"Groww order placement failed: {err_clean}")
                return order

        # Fallback via direct REST (only if access_token exists)
        if not self.access_token:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = "Groww authentication required. Please update daily Access Token in Settings."
            return order

        payload = {
            "trading_symbol": clean_symbol,
            "exchange": "NSE",
            "transaction_type": groww_tx_type,
            "order_type": groww_order_type,
            "quantity": order.quantity,
            "product": groww_product,
            "validity": "DAY",
            "order_reference_id": order_ref_id,
        }
        if order.price > 0 and groww_order_type == "LIMIT":
            payload["price"] = order.price

        try:
            res = self._request("POST", "orders", data=payload)
            order.order_id = res.get("order_id", order.order_id)
            order.status = OrderStatus.SUBMITTED
            self._update_cache_on_order(order, clean_symbol)
            logger.info(f"Order successfully submitted to Groww: ID {order.order_id} ({clean_symbol})")
            return order
        except Exception as e:
            err_str = str(e).lower()
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(e)
            is_auth_err = any(w in err_str for w in ("auth", "token", "401", "403", "session", "expired", "whitelist", "ga005"))
            if is_auth_err:
                self._is_authenticated = False
                self._last_auth_error = str(e)
            else:
                self.kill_switch.record_rejected_order(order.symbol, str(e))
            return order

    def cancel_order(self, order_id: str) -> bool:
        self._cached_portfolio_ts = 0.0
        self._cached_wallet_ts = 0.0
        self._cached_positions_ts = 0.0
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
        """Fetches net positions from Groww with 20s TTL cache."""
        now_ts = time.time()
        if self._cached_positions is not None and (now_ts - self._cached_positions_ts < 20.0):
            return self._cached_positions

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
                    avg_p = _to_float(
                        item.get("net_price")
                        or item.get("average_price")
                        or item.get("avg_price")
                        or item.get("credit_price")
                        or item.get("debit_price")
                        or item.get("buy_price")
                        or item.get("sell_price")
                        or 0.0
                    )
                    cur_p = _to_float(
                        item.get("last_price")
                        or item.get("ltp")
                        or item.get("lastPrice")
                        or item.get("lastTradedPrice")
                        or item.get("close_price")
                        or item.get("closePrice")
                        or 0.0
                    )
                    if cur_p <= 0:
                        try:
                            from ..market_data.yfinance_source import YFinanceSource
                            q = YFinanceSource().get_quote(sym)
                            if q and q.last_price > 0:
                                cur_p = q.last_price
                        except Exception:
                            pass

                    if avg_p <= 0 and cur_p > 0:
                        avg_p = cur_p

                    # Calculate live unrealized P&L
                    if avg_p > 0 and cur_p > 0:
                        unrealized = (cur_p - avg_p) * qty if qty > 0 else (avg_p - cur_p) * abs(qty)
                    else:
                        unrealized = _to_float(item.get("unrealised_pnl") or item.get("pnl") or 0.0)

                    sl = round(avg_p * 1.015, 2) if qty < 0 else (round(avg_p * 0.985, 2) if avg_p > 0 else 0.0)
                    tgt = round(avg_p * 0.97, 2) if qty < 0 else (round(avg_p * 1.03, 2) if avg_p > 0 else 0.0)

                    pos = Position(
                        symbol=sym,
                        product=ProductType.MIS if item.get("product") == "MIS" else ProductType.CNC,
                        quantity=qty,
                        average_entry_price=avg_p,
                        current_price=cur_p,
                        stop_loss=sl,
                        target_price=tgt,
                        realized_pnl=_to_float(item.get("realised_pnl") or 0.0),
                    )
                    pos.unrealized_pnl = round(unrealized, 2)
                    positions[sym] = pos
                self._cached_positions = positions
                self._cached_positions_ts = now_ts
                return positions
            except Exception as e:
                err_str = str(e).lower()
                logger.warning(f"Error fetching Groww positions via SDK: {e}")
                if "rate limit" in err_str or "429" in err_str:
                    if self._cached_positions is not None:
                        return self._cached_positions

        # Fallback via direct REST
        if not self.access_token:
            return self._cached_positions or positions
        try:
            data = self._request("GET", "positions/user", timeout=5.0, record_failure=False)
            net_list = data.get("positions", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for item in net_list:
                qty = int(item.get("quantity", 0))
                if qty == 0:
                    continue
                sym = item.get("trading_symbol", item.get("symbol", ""))
                avg_p = _to_float(
                    item.get("net_price")
                    or item.get("average_price")
                    or item.get("avg_price")
                    or item.get("credit_price")
                    or item.get("debit_price")
                    or item.get("buy_price")
                    or item.get("sell_price")
                    or 0.0
                )
                cur_p = _to_float(
                    item.get("last_price")
                    or item.get("ltp")
                    or item.get("lastPrice")
                    or item.get("lastTradedPrice")
                    or item.get("close_price")
                    or item.get("closePrice")
                    or 0.0
                )
                if cur_p <= 0:
                    try:
                        from ..market_data.yfinance_source import YFinanceSource
                        q = YFinanceSource().get_quote(sym)
                        if q and q.last_price > 0:
                            cur_p = q.last_price
                    except Exception:
                        pass

                if avg_p <= 0 and cur_p > 0:
                    avg_p = cur_p

                if avg_p > 0 and cur_p > 0:
                    unrealized = (cur_p - avg_p) * qty if qty > 0 else (avg_p - cur_p) * abs(qty)
                else:
                    unrealized = _to_float(item.get("unrealised_pnl") or item.get("pnl") or 0.0)

                sl = round(avg_p * 1.015, 2) if qty < 0 else (round(avg_p * 0.985, 2) if avg_p > 0 else 0.0)
                tgt = round(avg_p * 0.97, 2) if qty < 0 else (round(avg_p * 1.03, 2) if avg_p > 0 else 0.0)

                pos = Position(
                    symbol=sym,
                    product=ProductType.MIS if item.get("product") == "MIS" else ProductType.CNC,
                    quantity=qty,
                    average_entry_price=avg_p,
                    current_price=cur_p,
                    stop_loss=sl,
                    target_price=tgt,
                    realized_pnl=_to_float(item.get("realised_pnl") or item.get("pnl") or 0.0),
                )
                pos.unrealized_pnl = round(unrealized, 2)
                positions[sym] = pos
            if positions or not self._cached_positions:
                self._cached_positions = positions
                self._cached_positions_ts = now_ts
        except Exception as e:
            if "authentication" in str(e).lower() or "401" in str(e) or "403" in str(e):
                raise
            logger.debug(f"Positions query notice: {e}")
        if not self._cached_positions:
            self._load_portfolio_cache()
        return self._cached_positions or positions

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
