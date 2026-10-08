"""Autonomous Continuous Trading Engine Service for Indian Equities.

Runs an asynchronous non-blocking background loop:
- Scans user-configured watchlist
- Enforces NSE/BSE market timings & 15:15 IST square-off
- Runs rule-based strategies + Gemini 2.5 Flash AI consensus
- Validates every order with deterministic Risk Engine
- Executes via active Broker (Groww or Paper)
- Streams real-time event logs to the web interface
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional
from pathlib import Path
import pandas as pd

from ..config import settings
from ..core.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioState,
    Position,
    ProductType,
    StrategySignal,
)
from ..execution.base_broker import BaseBroker
from ..execution import create_broker
from ..market_data.calendar import IndianMarketCalendar, IST
from ..market_data.yfinance_source import YFinanceSource
from ..market_data.mock_live_source import MockMarketDataSource
from ..market_data.market_scanner import MarketScanner
from ..market_data.market_news import MarketNewsEngine
from ..monitoring.exit_manager import ExitManager
from ..risk.kill_switch import KillSwitch
from ..risk.engine import RiskEngine
from ..ai_engine.gemini_analyst import GeminiMarketAnalyst
from ..confidence_filter.filter import ConfidenceFilter
from ..execution.cost_calculator import IndianCostCalculator
from ..indicators.price_action import extract_features, get_latest_feature_snapshot
from ..strategies.trend_following import TrendFollowingStrategy
from ..strategies.momentum_breakout import MomentumBreakoutStrategy
from ..strategies.mean_reversion import MeanReversionStrategy
from ..strategies.volatility_breakout import VolatilityBreakoutStrategy
from ..strategies.vwap_reversion import VWAPReversionStrategy
from ..strategies.evolution import StrategyEvolutionEngine

logger = logging.getLogger("indian_equity_agent.trader_service")


class LogEvent:
    def __init__(self, category: str, message: str, level: str = "INFO"):
        self.timestamp = IndianMarketCalendar.now_ist().strftime("%H:%M:%S")
        self.category = category.upper()  # SCAN, AI, RISK, EXECUTION, EXIT, SYSTEM
        self.message = message
        self.level = level.upper()  # INFO, SUCCESS, WARNING, ERROR

    def to_dict(self) -> Dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "category": self.category,
            "message": self.message,
            "level": self.level,
        }


def _normalize_symbol(sym: str) -> str:
    """Standardizes symbols by stripping NSE/BSE exchange suffixes."""
    s = sym.strip().upper()
    return s.replace(".NS", "").replace(".BO", "")


class AutonomousTraderService:
    """Manages continuous autonomous live/paper trading cycle in the background."""

    DEFAULT_WATCHLIST = list(MarketScanner.BROAD_NSE_UNIVERSE)

    def __init__(
        self,
        broker: Optional[BaseBroker] = None,
        kill_switch: Optional[KillSwitch] = None,
        risk_engine: Optional[RiskEngine] = None,
        ai_analyst: Optional[GeminiMarketAnalyst] = None,
        state_file: Optional[Path] = None,
    ):
        self.kill_switch = kill_switch or KillSwitch()
        self.broker = broker or create_broker(kill_switch=self.kill_switch)
        self.risk_engine = risk_engine or RiskEngine(kill_switch=self.kill_switch)
        self.ai_analyst = ai_analyst or GeminiMarketAnalyst()
        self.confidence_filter = ConfidenceFilter()
        self.exit_manager = ExitManager()
        self.cost_calculator = IndianCostCalculator()

        self.data_source = YFinanceSource()
        self.mock_source = MockMarketDataSource()
        self.market_scanner = MarketScanner()
        self.market_news = MarketNewsEngine()

        # Multi-regime strategies orchestrated by Autonomous Intelligence
        self.strategies = {
            "trend_following": TrendFollowingStrategy(),
            "momentum_breakout": MomentumBreakoutStrategy(),
            "mean_reversion": MeanReversionStrategy(),
            "volatility_breakout": VolatilityBreakoutStrategy(),
            "vwap_reversion": VWAPReversionStrategy(),
        }
        self.evolution_engine = StrategyEvolutionEngine()

        # Runner state
        self.state_file = state_file if state_file is not None else (settings.project_root / "data" / "trader_state.json")
        self.is_running = False
        self.was_running = False
        self._task: Optional[asyncio.Task] = None
        self.watchlist: List[str] = list(self.DEFAULT_WATCHLIST)
        self.strategy_name = "Autonomous Regime-Adaptive Intelligence"
        self.current_strategy_label: Optional[str] = None
        self.scan_interval_seconds = 15
        self.use_mock_data = False

        # Concurrency & In-Flight Tracking
        self._execution_lock: Optional[asyncio.Lock] = None
        self._in_flight_symbols: set[str] = set()
        self._symbol_cooldown: Dict[str, float] = {}

        # Statutory Taxes & Brokerage Accumulator (STT, GST, SEBI, IPFT, Stamp Duty, NSE Fees, Brokerage)
        self.accumulated_charges = 0.0
        self.accumulated_brokerage = 0.0
        self.accumulated_taxes = 0.0
        self.accumulated_stt = 0.0
        self.accumulated_exchange_charges = 0.0
        self.accumulated_gst = 0.0
        self.accumulated_stamp_duty = 0.0
        self.accumulated_sebi = 0.0

        # Capital & Risk Controls
        self.max_loss_inr = 5000.0  # Max daily loss threshold (₹)
        self.target_profit_inr = 10000.0  # Min/Target daily profit goal (₹)
        self.runtime_minutes = 0  # 0 = continuous until stopped

        self.cycles_completed = 0
        self.started_at: Optional[datetime] = None
        self.last_scan_at: Optional[datetime] = None
        self.current_symbol: Optional[str] = None
        self.current_state = "STOPPED"

        # Load persisted state if exists
        self._load_persistent_state()

        # Thread-safe ring buffer for web terminal events (last 150 events)
        self.event_logs: Deque[LogEvent] = deque(maxlen=150)
        self._log("SYSTEM", "Autonomous Trader Service initialized.", "INFO")

    @property
    def lock(self) -> asyncio.Lock:
        """Lazy thread-safe asyncio lock."""
        if self._execution_lock is None:
            self._execution_lock = asyncio.Lock()
        return self._execution_lock

    def _log(self, category: str, message: str, level: str = "INFO"):
        evt = LogEvent(category, message, level)
        self.event_logs.append(evt)
        logger.info(f"[{evt.category}] {evt.message}")

    def update_broker(self, broker: BaseBroker):
        """Allows dynamic broker updating without server restart."""
        self.broker = broker
        b_name = "GROWW REAL" if "Groww" in broker.__class__.__name__ else "GROWW"
        self._log("SYSTEM", f"Broker connected to {b_name}.", "SUCCESS")

    def _load_persistent_state(self):
        """Restores runner state and limits from data/trader_state.json if available."""
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            legacy_10 = {
                "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
                "SBIN.NS", "BHARTIARTL.NS", "LT.NS", "KOTAKBANK.NS", "ITC.NS", "TATAMOTORS.NS"
            }
            if "watchlist" in data and isinstance(data["watchlist"], list) and data["watchlist"]:
                cleaned = [s.strip().upper() for s in data["watchlist"] if s.strip()]
                if len(cleaned) == 10 and set(cleaned).issubset(legacy_10):
                    self.watchlist = list(self.DEFAULT_WATCHLIST)
                else:
                    self.watchlist = cleaned
            else:
                self.watchlist = list(self.DEFAULT_WATCHLIST)
            if "strategy_name" in data and data["strategy_name"]:
                self.strategy_name = data["strategy_name"]
            if "scan_interval_seconds" in data and data["scan_interval_seconds"] >= 5:
                self.scan_interval_seconds = int(data["scan_interval_seconds"])
            if "max_loss_inr" in data and data["max_loss_inr"] > 0:
                self.max_loss_inr = float(data["max_loss_inr"])
            if "target_profit_inr" in data and data["target_profit_inr"] > 0:
                self.target_profit_inr = float(data["target_profit_inr"])
            if "runtime_minutes" in data:
                self.runtime_minutes = max(0, int(data["runtime_minutes"]))

            now_ist = IndianMarketCalendar.now_ist()
            started_at_str = data.get("started_at")
            if started_at_str:
                try:
                    prev_start = datetime.fromisoformat(started_at_str)
                    if prev_start.date() == now_ist.date():
                        self.started_at = prev_start
                        self.accumulated_charges = float(data.get("accumulated_charges", 0.0))
                        self.accumulated_brokerage = float(data.get("accumulated_brokerage", 0.0))
                        self.accumulated_taxes = float(data.get("accumulated_taxes", 0.0))
                        self.accumulated_stt = float(data.get("accumulated_stt", 0.0))
                        self.accumulated_exchange_charges = float(data.get("accumulated_exchange_charges", 0.0))
                        self.accumulated_gst = float(data.get("accumulated_gst", 0.0))
                        self.accumulated_stamp_duty = float(data.get("accumulated_stamp_duty", 0.0))
                        self.accumulated_sebi = float(data.get("accumulated_sebi", 0.0))
                        self.cycles_completed = int(data.get("cycles_completed", 0))
                except Exception:
                    pass
            logger.info(f"Loaded persistent trader state: was_running={self.was_running}, watchlist={len(self.watchlist)}")
        except Exception as e:
            logger.warning(f"Could not load persistent trader state: {e}")

    def _save_persistent_state(self):
        """Atomically persists runner state, limits, and accumulated charges to disk."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "is_running": self.is_running,
                "current_state": self.current_state,
                "watchlist": self.watchlist,
                "strategy_name": self.strategy_name,
                "scan_interval_seconds": self.scan_interval_seconds,
                "max_loss_inr": self.max_loss_inr,
                "target_profit_inr": self.target_profit_inr,
                "runtime_minutes": self.runtime_minutes,
                "accumulated_charges": round(self.accumulated_charges, 2),
                "accumulated_brokerage": round(self.accumulated_brokerage, 2),
                "accumulated_taxes": round(self.accumulated_taxes, 2),
                "accumulated_stt": round(self.accumulated_stt, 2),
                "accumulated_exchange_charges": round(self.accumulated_exchange_charges, 2),
                "accumulated_gst": round(self.accumulated_gst, 2),
                "accumulated_stamp_duty": round(self.accumulated_stamp_duty, 2),
                "accumulated_sebi": round(self.accumulated_sebi, 4),
                "cycles_completed": self.cycles_completed,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "last_updated": IndianMarketCalendar.now_ist().isoformat(),
            }
            tmp_file = self.state_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp_file.replace(self.state_file)
        except Exception as e:
            logger.warning(f"Could not save persistent trader state: {e}")

    async def auto_resume_if_previously_running(self):
        """Automatically resumes trading loop on server start if it was left running."""
        if not self.was_running:
            return
        if self.kill_switch.is_active:
            self._log("SYSTEM", f"Auto-resume skipped: Kill switch is currently active ({self.kill_switch.reason}).", "WARNING")
            return

        now_ist = IndianMarketCalendar.now_ist()
        if self.runtime_minutes > 0 and self.started_at:
            elapsed_mins = (now_ist - self.started_at).total_seconds() / 60.0
            if elapsed_mins >= self.runtime_minutes:
                self._log("SYSTEM", f"Configured Run Time limit of {self.runtime_minutes} mins has elapsed. Auto-resume not needed.", "INFO")
                self.was_running = False
                self._save_persistent_state()
                return

        self._log("SYSTEM", "🔄 Auto-resuming autonomous trading session from persistent state...", "SUCCESS")
        await self.start(
            watchlist=self.watchlist,
            strategy=self.strategy_name,
            scan_interval=self.scan_interval_seconds,
            max_loss_inr=self.max_loss_inr,
            target_profit_inr=self.target_profit_inr,
            runtime_minutes=self.runtime_minutes,
            use_mock=self.use_mock_data,
        )

    def select_intelligent_strategy(self, symbol: str, df: pd.DataFrame) -> tuple[Any, str, str]:
        """Dynamically identifies market regime and selects the optimal quantitative strategy."""
        try:
            enriched = extract_features(df)
            snap = get_latest_feature_snapshot(enriched)

            adx = snap.get("adx", 20.0)
            vol_surge = snap.get("vol_surge", 1.0)
            rsi = snap.get("rsi_14", 50.0)
            bb_upper = snap.get("bb_upper", 0.0)
            bb_lower = snap.get("bb_lower", 0.0)
            close = snap.get("close", 0.0)
            bb_mid = snap.get("ema_20", close)
            bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0.1

            # 1. Volatility Squeeze / Expansion Regime
            if bb_width <= 0.045:
                return (
                    self.strategies["volatility_breakout"],
                    "Volatility Breakout (Squeeze)",
                    f"Volatility Compression Squeeze (BB Width: {bb_width:.3f} <= 0.045)",
                )

            # 2. VWAP Institutional Overextension Mean Reversion Regime
            vwap = snap.get("vwap", close)
            vwap_dev = abs(close - vwap) / vwap if vwap > 0 else 0.0
            if vwap_dev >= 0.012 and (rsi <= 35 or rsi >= 65) and vol_surge >= 1.15:
                return (
                    self.strategies["vwap_reversion"],
                    "VWAP Institutional Reversion",
                    f"Institutional Overextension from VWAP ({vwap_dev*100:.1f}%, RSI: {rsi:.1f}, Vol: {vol_surge:.1f}x)",
                )

            # 3. Momentum & High Volume Breakout Regime
            if vol_surge >= 1.5:
                return (
                    self.strategies["momentum_breakout"],
                    "Momentum Breakout (Surge)",
                    f"Volume Breakout Surge ({vol_surge:.2f}x 20-SMA Volume)",
                )

            # 4. Range-bound Mean Reversion Regime
            if adx < 22 and (rsi <= 32 or rsi >= 68):
                return (
                    self.strategies["mean_reversion"],
                    "Mean Reversion (Oversold/Overbought)",
                    f"Mean Reversion Regime (ADX {adx:.1f} < 22, RSI {rsi:.1f})",
                )

            # 5. Directional Trend Following Regime
            return (
                self.strategies["trend_following"],
                "Trend Following (Dual EMA + Supertrend)",
                f"Directional Trend Following (Dual EMA 20/50 + Supertrend, ADX: {adx:.1f})",
            )
        except Exception as e:
            logger.warning(f"Error in intelligent strategy selection for {symbol}: {e}")
            return (
                self.strategies["trend_following"],
                "Trend Following (Dual EMA + Supertrend)",
                "Default Trend Following fallback",
            )

    def _get_strategy(self, name: str):
        name = name.lower()
        if "vwap" in name:
            return self.strategies["vwap_reversion"]
        elif "momentum" in name:
            return self.strategies["momentum_breakout"]
        elif "mean" in name:
            return self.strategies["mean_reversion"]
        elif "volatility" in name or "squeeze" in name:
            return self.strategies["volatility_breakout"]
        return self.strategies["trend_following"]

    async def start(
        self,
        watchlist: Optional[List[str]] = None,
        strategy: Optional[str] = None,
        scan_interval: Optional[int] = None,
        max_loss_inr: Optional[float] = None,
        target_profit_inr: Optional[float] = None,
        runtime_minutes: Optional[int] = None,
        use_mock: bool = False,
    ) -> Dict[str, Any]:
        """Starts continuous background trading loop with risk and duration limits."""
        if self.is_running:
            return {"status": "ALREADY_RUNNING", "message": "Autonomous trading loop is already running."}

        if watchlist:
            self.watchlist = [s.strip().upper() for s in watchlist if s.strip()]
            if not self.watchlist:
                self.watchlist = list(self.DEFAULT_WATCHLIST)

        if strategy:
            self.strategy_name = strategy
        else:
            self.strategy_name = "Autonomous Regime-Adaptive Intelligence"

        if scan_interval and scan_interval >= 5:
            self.scan_interval_seconds = scan_interval

        if max_loss_inr is not None and max_loss_inr > 0:
            self.max_loss_inr = max_loss_inr
        if target_profit_inr is not None and target_profit_inr > 0:
            self.target_profit_inr = target_profit_inr
        if runtime_minutes is not None:
            self.runtime_minutes = max(0, runtime_minutes)

        # Check Indian Market Timings
        now_ist = IndianMarketCalendar.now_ist()
        is_market_open = IndianMarketCalendar.is_market_open(now_ist)
        if not is_market_open and not use_mock:
            curr_str = now_ist.strftime("%H:%M:%S IST")
            day_str = now_ist.strftime("%A")
            if not IndianMarketCalendar.is_trading_day(now_ist):
                err_msg = f"Cannot start trading: Today is {day_str} (NSE Closed / Weekend / Holiday). NSE trading hours are 09:15 to 15:30 IST, Monday to Friday."
            else:
                err_msg = f"Cannot start trading: Market is CLOSED right now ({curr_str}). Regular NSE trading hours are 09:15 to 15:30 IST."

            self._log("SYSTEM", f"⛔ {err_msg}", "ERROR")
            return {
                "status": "ERROR_MARKET_CLOSED",
                "message": err_msg,
                "is_market_open": False,
            }

        self.use_mock_data = use_mock
        self.is_running = True
        self.was_running = True
        self.started_at = now_ist
        self.current_state = "RUNNING"
        self._save_persistent_state()

        dur_text = f"{self.runtime_minutes} mins" if self.runtime_minutes > 0 else "Full Session"
        self._log(
            "SYSTEM",
            f"🟢 Trading Started | 10 NSE Stocks | Max Loss: ₹{self.max_loss_inr:,.0f} | Profit Goal: ₹{self.target_profit_inr:,.0f} | Duration: {dur_text}",
            "SUCCESS",
        )

        self._task = asyncio.create_task(self._main_loop())
        return {"status": "STARTED", "message": "Autonomous trading started successfully."}

    async def stop(self) -> Dict[str, Any]:
        """Gracefully stops the continuous background trading loop."""
        if not self.is_running:
            return {"status": "ALREADY_STOPPED", "message": "Autonomous trading is not running."}

        self.is_running = False
        self.was_running = False
        self.current_state = "STOPPED"
        self._save_persistent_state()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self._log("SYSTEM", "🛑 Autonomous Trading gracefully stopped by operator.", "WARNING")
        return {"status": "STOPPED", "message": "Autonomous trading stopped."}

    async def _main_loop(self):
        """Continuous execution loop running indefinitely until cancelled."""
        while self.is_running:
            try:
                now_ist = IndianMarketCalendar.now_ist()
                self.last_scan_at = now_ist

                # 1. Kill Switch Check
                if self.kill_switch.is_active:
                    self.current_state = "HALTED_KILL_SWITCH"
                    self._log("RISK", f"🚨 Emergency Kill Switch is ACTIVE ({self.kill_switch.reason}). Skipping scan cycle.", "ERROR")
                    await self._sleep_interruptible(10)
                    continue

                # 2. Check Run Time Duration Limit
                if self.runtime_minutes > 0 and self.started_at:
                    elapsed_mins = (now_ist - self.started_at).total_seconds() / 60.0
                    if elapsed_mins >= self.runtime_minutes:
                        self._log("SYSTEM", f"⏰ Configured Run Time limit of {self.runtime_minutes} minutes reached. Stopping autonomous trading.", "SUCCESS")
                        await self.stop()
                        break

                # 3. Check Max Loss & Target Profit Limits against Groww Wallet P&L
                port = self.broker.get_portfolio_state()
                if self.max_loss_inr > 0 and port.daily_total_pnl <= -self.max_loss_inr:
                    self._log("RISK", f"🚨 Max Loss Limit reached (₹{port.daily_total_pnl:,.2f} <= -₹{self.max_loss_inr:,.2f})! Halting to preserve capital.", "ERROR")
                    self.kill_switch.trigger(f"Max Loss Limit reached: ₹{port.daily_total_pnl:,.2f}")
                    await self.stop()
                    break

                if self.target_profit_inr > 0 and port.daily_total_pnl >= self.target_profit_inr:
                    self._log("RISK", f"🎯 Target Profit Goal reached (₹{port.daily_total_pnl:,.2f} >= ₹{self.target_profit_inr:,.2f})! Halting to lock in gains.", "SUCCESS")
                    await self.stop()
                    break

                # 4. Position Management & Exit Checks (SL, Target, Trailing Stop, 15:15 IST Square-off)
                await self._manage_active_positions(now_ist)

                # 3. Market Timing Gate
                is_market_open = IndianMarketCalendar.is_market_open(now_ist)
                is_entry_allowed = IndianMarketCalendar.is_entry_allowed(now_ist)

                if not is_market_open and not self.use_mock_data:
                    self.current_state = "STANDBY_MARKET_CLOSED"
                    await self._sleep_interruptible(min(self.scan_interval_seconds, 60))
                    continue

                self.current_state = "RUNNING"

                # 4. Sub-Second Whole-Market News & Macro Sentiment Analysis (< 1 sec)
                news = await asyncio.to_thread(self.market_news.fetch_market_news)
                if news and news.get("top_headlines"):
                    top_h = news["top_headlines"][0]
                    time_desc = f"{news.get('elapsed_ms', 0):.0f}ms" if not news.get("cached") else "< 1ms (live cache)"
                    self._log("NEWS", f"📰 WHOLE-MARKET NEWS ({time_desc} | Sentiment: {news.get('sentiment')} {news.get('score'):+.2f}): {top_h}", "INFO")

                # 5. Whole-Market Intelligent Screening:
                # Calculate available buying power (with 5x MIS intraday leverage)
                port_state = self.broker.get_portfolio_state()
                avail_cash = max(0.0, port_state.cash)
                buying_power = avail_cash * 5.0  # 5x intraday MIS leverage

                # Determine universe to scan:
                # If operator provided a small custom watchlist (<15 stocks), respect it; otherwise screen whole 50+ broad NSE market
                use_custom_watchlist = bool(self.watchlist) and len(self.watchlist) < 15 and (set(self.watchlist) != set(self.DEFAULT_WATCHLIST))
                universe_pool = self.watchlist if use_custom_watchlist else self.market_scanner.BROAD_NSE_UNIVERSE

                # Fast batch screen: filter by buying power and rank by momentum score in ~2 seconds
                max_price = buying_power if buying_power > 0 else 5000.0
                candidates = await asyncio.to_thread(
                    self.market_scanner.scan_market,
                    max_price_inr=max_price,
                    top_n=8,
                    universe=universe_pool,
                    use_mock=self.use_mock_data,
                )

                if candidates:
                    top_names = ", ".join([f"{c['symbol'].replace('.NS','')}(₹{c['price']:.0f})" for c in candidates[:4]])
                    self._log("GROWW", f"⚡ WHOLE-MARKET SCREEN: Scanned {len(universe_pool)} NSE stocks. Top setups: {top_names}", "INFO")
                    symbols_to_evaluate = [c["symbol"] for c in candidates]
                else:
                    symbols_to_evaluate = self.watchlist[:8]

                # Evaluate selected top opportunities in parallel for sub-2-second execution
                eval_tasks = [
                    self._evaluate_symbol_safe(sym, now_ist, is_entry_allowed)
                    for sym in symbols_to_evaluate[:6]
                ]
                await asyncio.gather(*eval_tasks, return_exceptions=True)

                self.current_symbol = None
                self.cycles_completed += 1
                self._save_persistent_state()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log("SYSTEM", f"Error in autonomous loop cycle: {e}", "ERROR")

            # Sleep until next scan cycle
            await self._sleep_interruptible(self.scan_interval_seconds)

    async def _manage_active_positions(self, now_ist: datetime):
        """Monitors and enforces exits (Stop-Loss, Target, Trailing Exit, 15:15 IST MIS Square-off)."""
        portfolio = self.broker.get_portfolio_state()
        if not portfolio.positions:
            return

        if self.cycles_completed % 4 == 0:
            active_list = []
            for s, p in portfolio.positions.items():
                if p.quantity != 0:
                    c_p = p.current_price if p.current_price > 0 else p.average_entry_price
                    p_pnl = (c_p - p.average_entry_price) * p.quantity if p.quantity > 0 else (p.average_entry_price - c_p) * abs(p.quantity)
                    active_list.append(f"{s} ({p.quantity} @ ₹{p.average_entry_price:,.2f} | LTP ₹{c_p:,.2f} | P&L: {'+' if p_pnl>=0 else ''}₹{p_pnl:,.2f})")
            if active_list:
                self._log("POSITIONS", f"📊 TRACKING POSITIONS: {', '.join(active_list)}", "INFO")

        for sym, pos in list(portfolio.positions.items()):
            if pos.quantity == 0:
                continue

            norm_sym = _normalize_symbol(sym)
            if norm_sym in self._in_flight_symbols:
                continue

            # Fetch latest price
            current_price = pos.current_price
            if current_price <= 0:
                try:
                    quote = (self.mock_source if self.use_mock_data else self.data_source).get_quote(sym)
                    if quote and quote.last_price > 0:
                        current_price = quote.last_price
                except Exception:
                    pass

            if current_price <= 0:
                try:
                    bars = (self.mock_source if self.use_mock_data else self.data_source).get_historical_bars(sym, limit=1)
                    if bars:
                        current_price = bars[-1].close
                except Exception:
                    pass

            if current_price <= 0:
                current_price = pos.average_entry_price

            is_sqoff = IndianMarketCalendar.is_squareoff_time(now_ist)
            exit_order = self.exit_manager.evaluate_position_exits(
                position=pos,
                current_price=current_price,
                force_eod_squareoff=is_sqoff,
                current_time=now_ist,
            )

            if exit_order:
                async with self.lock:
                    if norm_sym in self._in_flight_symbols:
                        continue
                    self._in_flight_symbols.add(norm_sym)
                    try:
                        clean_sym = _normalize_symbol(sym)
                        filled = await asyncio.to_thread(self.broker.place_order, exit_order)
                        exit_p = current_price if current_price > 0 else pos.average_entry_price
                        pnl = (exit_p - pos.average_entry_price) * pos.quantity if pos.quantity > 0 else (pos.average_entry_price - exit_p) * abs(pos.quantity)
                        pnl_str = f"+₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
                        exit_side = "SELL" if pos.quantity > 0 else "BUY"
                        self._log(
                            "EXIT",
                            f"🔔 EXIT TRIGGERED: {clean_sym} | {exit_side} {abs(pos.quantity)} shares @ ₹{exit_p:,.2f} | P&L: {pnl_str} (SL/Target/15:15 MIS)",
                            "SUCCESS" if pnl >= 0 else "WARNING",
                        )

                        # Track statutory taxes & brokerage for realized closed trade
                        if filled.status in (OrderStatus.SUBMITTED, OrderStatus.FILLED):
                            exit_p = current_price if current_price > 0 else pos.average_entry_price
                            qty = abs(pos.quantity)
                            is_short = pos.quantity < 0
                            costs = self.cost_calculator.calculate_roundtrip_costs(
                                quantity=qty,
                                entry_price=pos.average_entry_price,
                                exit_price=exit_p,
                                is_short=is_short,
                                product=pos.product,
                            )
                            self.accumulated_charges += costs["total_charges"]
                            self.accumulated_brokerage += costs["brokerage"]
                            self.accumulated_taxes += (costs["total_charges"] - costs["brokerage"])
                            self.accumulated_stt += costs["stt"]
                            self.accumulated_exchange_charges += costs["exchange_charges"]
                            self.accumulated_gst += costs["gst"]
                            self.accumulated_stamp_duty += costs["stamp_duty"]
                            self.accumulated_sebi += (costs["sebi_charges"] + costs.get("ipft_charges", 0.0))
                            self._save_persistent_state()

                            # Evolve strategy metrics on trade completion
                            side_str = "BUY" if pos.quantity > 0 else "SELL"
                            exit_reason = "15:15_MIS_SQUAREOFF" if is_sqoff else "STOP_OR_TARGET"
                            self.evolution_engine.record_completed_trade(
                                symbol=clean_sym,
                                strategy_key=self.current_strategy_label or "trend_following",
                                side=side_str,
                                quantity=qty,
                                entry_price=pos.average_entry_price,
                                exit_price=exit_p,
                                pnl=pnl,
                                exit_reason=exit_reason,
                                product=pos.product.value if hasattr(pos.product, "value") else str(pos.product),
                            )
                    finally:
                        self._in_flight_symbols.discard(norm_sym)

    async def _evaluate_symbol_safe(self, symbol: str, now_ist: datetime, is_entry_allowed: bool):
        """Safely evaluates a single symbol candidate without breaking concurrent batch execution."""
        if not self.is_running:
            return
        norm_sym = _normalize_symbol(symbol)
        now_ts = time.time()
        cooldown_until = self._symbol_cooldown.get(norm_sym, 0.0)
        if now_ts < cooldown_until:
            logger.debug(f"Skipping {norm_sym}: on cooldown for another {int(cooldown_until - now_ts)}s")
            return
        self.current_symbol = symbol
        try:
            await self._evaluate_symbol(symbol, now_ist, is_entry_allowed)
        except Exception as e_sym:
            logger.warning(f"Skipping symbol {symbol} due to evaluation error: {e_sym}")

    async def _evaluate_symbol(
        self,
        symbol: str,
        arg2: Any = None,
        arg3: Any = None,
        arg4: Any = None,
    ):
        """Evaluates single symbol through the complete 10-layer pipeline.
        
        Supports both signatures:
        _evaluate_symbol(symbol, now_ist, is_entry_allowed)
        _evaluate_symbol(symbol, strategy, now_ist, is_entry_allowed)
        """
        if isinstance(arg2, datetime):
            now_ist = arg2
            is_entry_allowed = bool(arg3)
            strategy = None
        else:
            strategy = arg2
            now_ist = arg3 if isinstance(arg3, datetime) else IndianMarketCalendar.now_ist()
            is_entry_allowed = bool(arg4) if arg4 is not None else IndianMarketCalendar.is_entry_allowed(now_ist)

        norm_sym = _normalize_symbol(symbol)
        if norm_sym in self._in_flight_symbols:
            return

        end_dt = now_ist
        start_dt = end_dt - timedelta(days=120)

        # 1. Market Data Fetch
        if self.use_mock_data:
            bars = await asyncio.to_thread(self.mock_source.get_historical_bars, symbol.split(".")[0], start_date=start_dt, end_date=end_dt)
        else:
            bars = await asyncio.to_thread(self.data_source.get_historical_bars, symbol, start_date=start_dt, end_date=end_dt, interval="1d")

        if not bars or len(bars) < 20:
            return

        records = [b.model_dump() for b in bars]
        df = pd.DataFrame(records)
        df.set_index("timestamp", inplace=True)

        # 2. Autonomous Regime Identification & Strategy Selection
        if strategy is None:
            strat, strat_label, rationale = self.select_intelligent_strategy(symbol, df)
            self.current_strategy_label = strat_label
        else:
            strat = strategy
            strat_label = getattr(strat, "name", "Configured Strategy")
            rationale = "Strategy assigned by parameter"

        # Generate Strategy Signal
        signal: StrategySignal = strat.generate_signal(symbol, df)
        last_price = bars[-1].close
        if signal.action == "HOLD":
            self._log("STRATEGY", f"🔍 {norm_sym} (₹{last_price:.2f}) [{strat_label}]: Signal HOLD (Awaiting breakout confirmation)", "INFO")
            return

        # Check entry window
        if not is_entry_allowed and not self.use_mock_data:
            self._log("TIMING", f"⏳ {norm_sym}: Signal {signal.action} generated but outside entry window (09:15 - 15:15 IST)", "WARNING")
            return

        # Cash segment: only allow naked short sell during market hours as MIS
        is_market_open = IndianMarketCalendar.is_market_open(now_ist)
        if signal.action == "SELL" and not is_market_open and not self.use_mock_data:
            self._log("TIMING", f"⏳ {norm_sym}: Intraday Short SELL requires active market hours", "WARNING")
            return

        # Real-time symbol news catalyst check (< 1ms)
        sym_news = self.market_news.get_symbol_news(symbol)
        signal.indicators["macro_sentiment"] = sym_news.get("market_sentiment", "NEUTRAL")
        signal.indicators["has_news_catalyst"] = sym_news.get("has_breaking_news", False)

        # 3. Gemini AI Analysis
        ai_decision = await asyncio.to_thread(self.ai_analyst.analyze, symbol, signal.indicators, proposed_signal=signal)

        # 4. Confidence Consensus Gatekeeper
        consensus_ok, consensus_reason = self.confidence_filter.evaluate(signal, ai_decision)
        if not consensus_ok:
            self._log("AI", f"🛡️ AI CONSENSUS: {norm_sym} ({consensus_reason})", "INFO")
            return

        # 5. Position Sizing
        portfolio = self.broker.get_portfolio_state()
        order_product = ProductType.MIS if is_market_open else ProductType.CNC
        qty, risk_amt, size_reason = self.risk_engine.position_sizer.calculate_quantity(
            symbol=symbol,
            entry_price=last_price,
            stop_loss_price=signal.suggested_stop_loss,
            portfolio=portfolio,
            bar_volume=bars[-1].volume,
            product=order_product,
        )

        if qty > 0:
            strat_key = "vwap_reversion" if "vwap" in strat_label.lower() else (
                "momentum_breakout" if "momentum" in strat_label.lower() else (
                    "mean_reversion" if "mean" in strat_label.lower() else (
                        "volatility_breakout" if "volatility" in strat_label.lower() else "trend_following"
                    )
                )
            )
            macro_sent = sym_news.get("market_sentiment", "BULLISH")
            risk_mult = self.evolution_engine.get_risk_multiplier(strat_key, market_sentiment=macro_sent)
            if risk_mult != 1.0:
                adj_qty = max(1, int(round(qty * risk_mult)))
                logger.info(f"Evolution risk multiplier ({risk_mult:.2f}x) scaled qty {qty} -> {adj_qty} for {norm_sym}")
                qty = adj_qty

        if qty <= 0:
            self._log("RISK", f"⚠️ Capital sizing for {norm_sym} (₹{last_price:.2f}): 0 shares ({size_reason} | Avail Cash: ₹{portfolio.cash:,.2f})", "WARNING")
            logger.info(f"Position sizing for {symbol} returned qty=0: {size_reason}")
            return

        order = Order(
            order_id=f"AUTO_{int(datetime.now().timestamp())}_{norm_sym}",
            symbol=symbol,
            side=OrderSide.BUY if signal.action == "BUY" else OrderSide.SELL,
            order_type=OrderType.MARKET,
            product=order_product,
            quantity=qty,
            price=last_price,
            stop_loss=signal.suggested_stop_loss,
            target_price=signal.suggested_target,
        )

        # 6. Independent Risk Engine Hard Veto
        instrument = self.mock_source.get_instrument(symbol) if self.use_mock_data else self.data_source.get_instrument(symbol, price=last_price)
        risk_decision = self.risk_engine.evaluate_order(
            order=order,
            portfolio=portfolio,
            instrument=instrument,
            skip_market_hours=self.use_mock_data,
        )

        if not risk_decision.approved:
            self._log("RISK", f"🛡️ Order for {norm_sym} rejected by risk engine: {risk_decision.rejection_reason}", "WARNING")
            return

        # 7. Broker Execution (Groww or Paper) with Mutex & In-Flight Tracking
        clean_name = _normalize_symbol(order.symbol)
        leverage_str = "5x Intraday (MIS)" if order.product == ProductType.MIS else "1x Delivery (CNC)"
        val_inr = order.quantity * order.price
        self._log(
            "TRADE",
            f"🎯 TRADE SIGNAL: {order.side.value} {clean_name} | Qty: {order.quantity} | Price: ₹{order.price:,.2f} (Val: ₹{val_inr:,.2f}) | Leverage: {leverage_str} | SL: ₹{order.stop_loss:,.2f} | Target: ₹{order.target_price:,.2f}",
            "SUCCESS",
        )

        async with self.lock:
            if norm_sym in self._in_flight_symbols:
                return
            self._in_flight_symbols.add(norm_sym)
            try:
                executed = await asyncio.to_thread(self.broker.place_order, order)
                if executed.status in (OrderStatus.SUBMITTED, OrderStatus.FILLED):
                    self._symbol_cooldown.pop(norm_sym, None)
                    self._log("GROWW", f"🚀 REAL TRADE PLACED TO GROWW: {executed.side.value} {qty} {clean_name} @ ₹{order.price:,.2f} | Leverage: {leverage_str} | Groww ID: {executed.order_id}", "SUCCESS")
                else:
                    self._symbol_cooldown[norm_sym] = time.time() + 300.0
                    self._log("GROWW", f"❌ GROWW REJECTION for {clean_name} (Cooldown 5m): {executed.rejection_reason}", "ERROR")
                    if "unregistered ip" in (executed.rejection_reason or "").lower() or "whitelist" in (executed.rejection_reason or "").lower():
                        pub_ip = getattr(self.broker, "_public_ip", None) or "active IP"
                        self._log("GROWW", f"⚠️ ACTION REQUIRED: Add IP {pub_ip} to Groww Web -> Settings -> Trading APIs -> Whitelist IP to trade live.", "WARNING")
            finally:
                self._in_flight_symbols.discard(norm_sym)

    async def _sleep_interruptible(self, seconds: int):
        """Sleeps in 1-second chunks so operator stop is instant."""
        for _ in range(int(seconds)):
            if not self.is_running:
                break
            await asyncio.sleep(1)

    def manual_close_position(self, symbol: str) -> Dict[str, Any]:
        """Manually closes an individual open position from the web UI."""
        portfolio = self.broker.get_portfolio_state()
        norm_target = _normalize_symbol(symbol)

        # Canonical lookup across positions
        pos = None
        matched_key = symbol
        for k, p in portfolio.positions.items():
            if _normalize_symbol(k) == norm_target:
                pos = p
                matched_key = k
                break

        if not pos or pos.quantity == 0:
            return {"status": "ERROR", "message": f"No open position found for {symbol}"}

        if norm_target in self._in_flight_symbols:
            return {"status": "ERROR", "message": f"Order already in flight for {symbol}"}

        self._in_flight_symbols.add(norm_target)
        try:
            exit_side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
            order = Order(
                order_id=f"MANUAL_EXIT_{int(datetime.now().timestamp())}_{norm_target}",
                symbol=matched_key,
                side=exit_side,
                order_type=OrderType.MARKET,
                product=pos.product,
                quantity=abs(pos.quantity),
                price=pos.current_price,
            )
            filled = self.broker.place_order(order)
            clean_name = _normalize_symbol(matched_key)
            exit_p = pos.current_price if pos.current_price > 0 else pos.average_entry_price
            pnl = (exit_p - pos.average_entry_price) * pos.quantity if pos.quantity > 0 else (pos.average_entry_price - exit_p) * abs(pos.quantity)
            pnl_str = f"+₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
            self._log("EXIT", f"👤 MANUAL EXIT: {clean_name} | {abs(pos.quantity)} shares @ ₹{exit_p:,.2f} | P&L: {pnl_str}", "INFO")

            if filled.status in (OrderStatus.SUBMITTED, OrderStatus.FILLED):
                exit_p = pos.current_price if pos.current_price > 0 else pos.average_entry_price
                qty = abs(pos.quantity)
                is_short = pos.quantity < 0
                costs = self.cost_calculator.calculate_roundtrip_costs(
                    quantity=qty,
                    entry_price=pos.average_entry_price,
                    exit_price=exit_p,
                    is_short=is_short,
                    product=pos.product,
                )
                self.accumulated_charges += costs["total_charges"]
                self.accumulated_brokerage += costs["brokerage"]
                self.accumulated_taxes += (costs["total_charges"] - costs["brokerage"])
                self.accumulated_stt += costs["stt"]
                self.accumulated_exchange_charges += costs["exchange_charges"]
                self.accumulated_gst += costs["gst"]
                self.accumulated_stamp_duty += costs["stamp_duty"]
                self.accumulated_sebi += (costs["sebi_charges"] + costs.get("ipft_charges", 0.0))
                self._save_persistent_state()

                # Evolve strategy metrics on manual exit
                side_str = "BUY" if pos.quantity > 0 else "SELL"
                self.evolution_engine.record_completed_trade(
                    symbol=clean_name,
                    strategy_key="manual_exit",
                    side=side_str,
                    quantity=qty,
                    entry_price=pos.average_entry_price,
                    exit_price=exit_p,
                    pnl=pnl,
                    exit_reason="MANUAL",
                    product=pos.product.value if hasattr(pos.product, "value") else str(pos.product),
                )
                return {"status": "SUCCESS", "order_id": filled.order_id, "message": f"Successfully closed position for {clean_name}"}
            else:
                self._log("EXIT", f"❌ MANUAL EXIT FAILED: {clean_name} | {filled.rejection_reason}", "ERROR")
                return {"status": "ERROR", "order_id": filled.order_id, "message": filled.rejection_reason or "Order rejected by broker"}
        finally:
            self._in_flight_symbols.discard(norm_target)

    def close_all_positions(self) -> Dict[str, Any]:
        """Emergency close of all active open positions from the web UI."""
        portfolio = self.broker.get_portfolio_state()
        closed_count = 0
        for sym, pos in list(portfolio.positions.items()):
            if pos.quantity != 0:
                self.manual_close_position(sym)
                closed_count += 1
        self._log("EXIT", f"Emergency Close All: liquidating {closed_count} open positions.", "WARNING")
        return {"status": "SUCCESS", "closed_count": closed_count}

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive status for web UI polling."""
        uptime_seconds = (IndianMarketCalendar.now_ist() - self.started_at).total_seconds() if self.started_at and self.is_running else 0
        hours, rem = divmod(int(uptime_seconds), 3600)
        minutes, seconds = divmod(rem, 60)
        uptime_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        remaining_seconds = max(0, int(self.runtime_minutes * 60 - uptime_seconds)) if self.runtime_minutes > 0 and self.is_running else None
        if remaining_seconds is not None:
            r_hrs, r_rem = divmod(remaining_seconds, 3600)
            r_mins, r_secs = divmod(r_rem, 60)
            remaining_formatted = f"{r_hrs:02d}:{r_mins:02d}:{r_secs:02d}"
        else:
            remaining_formatted = "Unlimited"

        return {
            "is_running": self.is_running,
            "state": self.current_state,
            "cycles_completed": self.cycles_completed,
            "current_symbol": self.current_symbol,
            "uptime": uptime_formatted,
            "remaining_time": remaining_formatted,
            "runtime_minutes": self.runtime_minutes,
            "max_loss_inr": self.max_loss_inr,
            "target_profit_inr": self.target_profit_inr,
            "watchlist": self.watchlist,
            "strategy": self.current_strategy_label or self.strategy_name,
            "scan_interval": self.scan_interval_seconds,
            "broker_type": "GROWW",
            "accumulated_charges": round(self.accumulated_charges, 2),
            "accumulated_brokerage": round(self.accumulated_brokerage, 2),
            "accumulated_taxes": round(self.accumulated_taxes, 2),
            "accumulated_stt": round(self.accumulated_stt, 2),
            "accumulated_exchange_charges": round(self.accumulated_exchange_charges, 2),
            "accumulated_gst": round(self.accumulated_gst, 2),
            "accumulated_stamp_duty": round(self.accumulated_stamp_duty, 2),
            "accumulated_sebi": round(self.accumulated_sebi, 4),
            "last_scan_at": self.last_scan_at.strftime("%H:%M:%S IST") if self.last_scan_at else "Never",
            "evolution": self.evolution_engine.get_evolution_summary(),
        }

    def get_logs(self, limit: int = 100) -> List[Dict[str, str]]:
        """Returns latest event stream for web terminal."""
        events = list(self.event_logs)
        return [e.to_dict() for e in events[-limit:]]
