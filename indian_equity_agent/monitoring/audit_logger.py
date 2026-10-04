"""Audit logger with JSONL and SQLite persistence for Indian Equities."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from ..config import settings
from ..market_data.calendar import IndianMarketCalendar

logger = logging.getLogger("indian_equity_agent.monitoring.audit")


class AuditLogger:
    """Structured audit trail recorder."""

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = log_dir or settings.logs_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.log_dir / "audit_events.jsonl"
        self.db_path = self.log_dir / "trading_audit.db"
        self._init_db()

    def _init_db(self) -> None:
        """Initializes SQLite tables for structured queryable audit history."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy TEXT,
                    strategy_action TEXT,
                    ai_action TEXT,
                    ai_confidence REAL,
                    ai_reason TEXT,
                    risk_approved INTEGER,
                    risk_reason TEXT,
                    order_id TEXT
                )
                """
            )
            conn.commit()

    def log_event(self, event_type: str, payload: Dict[str, Any], symbol: Optional[str] = None) -> None:
        """Appends event to JSONL and SQLite."""
        ts = IndianMarketCalendar.now_ist().isoformat()
        record = {
            "timestamp": ts,
            "event_type": event_type,
            "symbol": symbol,
            "payload": payload,
        }

        # 1. Write to JSONL
        try:
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Failed to append to audit JSONL: {e}")

        # 2. Write to SQLite
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO audit_events (timestamp, event_type, symbol, payload) VALUES (?, ?, ?, ?)",
                    (ts, event_type, symbol, json.dumps(payload)),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to write audit event to SQLite: {e}")

    def log_trade_decision(
        self,
        symbol: str,
        strategy: str,
        strategy_action: str,
        ai_action: str,
        ai_confidence: float,
        ai_reason: str,
        risk_approved: bool,
        risk_reason: str,
        order_id: Optional[str] = None,
    ) -> None:
        ts = IndianMarketCalendar.now_ist().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO trade_decisions
                    (timestamp, symbol, strategy, strategy_action, ai_action, ai_confidence, ai_reason, risk_approved, risk_reason, order_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        symbol,
                        strategy,
                        strategy_action,
                        ai_action,
                        ai_confidence,
                        ai_reason,
                        1 if risk_approved else 0,
                        risk_reason,
                        order_id or "",
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to record trade decision: {e}")
