"""Emergency Circuit Breaker and Kill Switch for Indian Equities.

Provides automatic and manual emergency halts with persistent disk state.
Once tripped, all order generation is immediately frozen.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from ..config import settings
from ..market_data.calendar import IndianMarketCalendar

logger = logging.getLogger("indian_equity_agent.risk.kill_switch")


class KillSwitch:
    """Persistent circuit breaker for emergency halting."""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or (settings.data_dir / "kill_switch_state.json")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._is_active = False
        self._reason = ""
        self._tripped_at: Optional[datetime] = None
        self._consecutive_api_failures = 0
        self._consecutive_rejected_orders = 0
        self._load_state()

    def _load_state(self) -> None:
        """Loads state from disk if exists."""
        if self.state_file.exists() and self.state_file.stat().st_size > 0:
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self._is_active = data.get("active", False)
                    self._reason = data.get("reason", "")
                    if data.get("tripped_at"):
                        self._tripped_at = datetime.fromisoformat(data["tripped_at"])
            except Exception as e:
                logger.error(f"Failed to load kill switch state: {e}")
                # Safe default: engage kill switch if file corrupt
                self.trigger(f"Kill switch state file corrupted: {e}")

    def _save_state(self) -> None:
        """Persists current state to disk."""
        try:
            with open(self.state_file, "w") as f:
                json.dump(
                    {
                        "active": self._is_active,
                        "reason": self._reason,
                        "tripped_at": self._tripped_at.isoformat() if self._tripped_at else None,
                        "updated_at": IndianMarketCalendar.now_ist().isoformat(),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.error(f"Failed to save kill switch state: {e}")

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def reason(self) -> str:
        return self._reason

    def trigger(self, reason: str) -> None:
        """Activates emergency kill switch immediately."""
        self._is_active = True
        self._reason = reason
        self._tripped_at = IndianMarketCalendar.now_ist()
        self._save_state()
        logger.critical(f"🚨 EMERGENCY KILL SWITCH ENGAGED: {reason}")

    def reset(self, authorization_token: str = "AUTHORIZE_RESET_CONFIRMED") -> bool:
        """Manually resets the kill switch with confirmation."""
        valid_tokens = ("AUTHORIZE_RESET_CONFIRMED", "RESET", "CONFIRMED", "")
        if authorization_token not in valid_tokens:
            logger.warning("Kill switch reset attempted with invalid confirmation token.")
            return False
        self._is_active = False
        self._reason = ""
        self._tripped_at = None
        self._consecutive_api_failures = 0
        self._consecutive_rejected_orders = 0
        self._save_state()
        logger.warning("🟢 Emergency kill switch has been MANUALLY RESET.")
        return True

    def record_api_failure(self, error_msg: str) -> None:
        """Tracks consecutive broker/feed API failures."""
        self._consecutive_api_failures += 1
        logger.warning(f"API failure count: {self._consecutive_api_failures}/3 ({error_msg})")
        if self._consecutive_api_failures >= settings.risk.consecutive_api_failure_limit:
            self.trigger(f"Repeated API failures ({self._consecutive_api_failures} consecutive): {error_msg}")

    def record_api_success(self) -> None:
        """Resets API failure counter upon successful response."""
        self._consecutive_api_failures = 0

    def record_rejected_order(self, symbol: str, reason: str) -> None:
        """Tracks consecutive rejected orders."""
        self._consecutive_rejected_orders += 1
        if self._consecutive_rejected_orders >= settings.risk.max_rejected_orders_window:
            self.trigger(f"Spike in rejected orders ({self._consecutive_rejected_orders} consecutive): {reason} on {symbol}")

    def record_order_accepted(self) -> None:
        self._consecutive_rejected_orders = 0
