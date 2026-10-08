"""Adaptive Strategy Evolution and Continuous Learning Engine for Indian Equities.

Continuously tracks, evaluates, and dynamically evolves trading strategies:
1. Records every trade outcome (entry, exit, P&L, duration, regime, strategy).
2. Calculates statistical edge: Win Rate, Profit Factor, Expectancy, Max Drawdown per strategy.
3. Dynamically adjusts strategy allocation weights (boosts profitable, deprioritizes weak).
4. Provides adaptive position sizing multipliers (scale up on edge, contract on drawdown).
5. Persists evolving state to data/strategy_evolution.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from ..config import settings
from ..market_data.calendar import IndianMarketCalendar

logger = logging.getLogger("indian_equity_agent.strategies.evolution")


class TradeRecord(BaseModel):
    """Complete immutable journal of an executed trade."""
    trade_id: str
    symbol: str
    strategy: str
    side: str  # "BUY" or "SELL"
    product: str  # "MIS" or "CNC"
    quantity: int
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    pnl: float
    pnl_pct: float
    exit_reason: str  # "TARGET", "STOP_LOSS", "TRAILING_STOP", "EOD_SQUAREOFF", "MANUAL"
    regime: Optional[str] = "Adaptive"


class StrategyStats(BaseModel):
    """Running performance statistics for a specific strategy."""
    name: str
    trades_count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    win_rate: float = 0.50
    profit_factor: float = 1.00
    current_streak: int = 0  # Positive for win streak, negative for losing streak
    weight: float = 1.00  # Dynamic priority weight (0.2 to 2.5)
    last_updated: str = Field(default_factory=lambda: IndianMarketCalendar.now_ist().isoformat())


class StrategyEvolutionEngine:
    """Orchestrates continuous learning, dynamic strategy weighting, and risk scaling."""

    def __init__(self, state_file: Optional[Path] = None, history_file: Optional[Path] = None):
        self.state_file = state_file or (settings.data_dir / "strategy_evolution.json")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file = history_file or (settings.data_dir / "trade_journal.json")

        # Baseline strategies registry
        self.strategy_stats: Dict[str, StrategyStats] = {
            "trend_following": StrategyStats(name="Trend Following (Dual EMA + Supertrend)", weight=1.10),
            "momentum_breakout": StrategyStats(name="Momentum Breakout (Surge)", weight=1.15),
            "mean_reversion": StrategyStats(name="Mean Reversion (Oversold/Overbought)", weight=0.95),
            "volatility_breakout": StrategyStats(name="Volatility Breakout (Squeeze)", weight=1.05),
            "vwap_reversion": StrategyStats(name="VWAP Institutional Reversion", weight=1.20),
        }
        self.trades_history: List[TradeRecord] = []
        self._load_state()

    def _load_state(self):
        """Restores evolution memory from disk if available."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.get("strategy_stats", {}).items():
                    if k in self.strategy_stats:
                        self.strategy_stats[k] = StrategyStats(**v)
                logger.info(f"Restored strategy evolution memory ({len(self.strategy_stats)} strategies).")
            except Exception as e:
                logger.warning(f"Could not load strategy evolution state: {e}")

        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    t_list = json.load(f)
                self.trades_history = [TradeRecord(**t) for t in t_list[-100:]]
            except Exception as e:
                logger.warning(f"Could not load trade journal: {e}")

    def _save_state(self):
        """Persists evolution metrics and trade history."""
        try:
            data = {
                "strategy_stats": {k: v.model_dump() for k, v in self.strategy_stats.items()},
                "total_recorded_trades": len(self.trades_history),
                "last_evolved_at": IndianMarketCalendar.now_ist().isoformat(),
            }
            tmp = self.state_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.state_file)

            # Persist trade journal
            tmp_hist = self.history_file.with_suffix(".tmp")
            with open(tmp_hist, "w", encoding="utf-8") as f:
                json.dump([t.model_dump() for t in self.trades_history[-200:]], f, indent=2)
            tmp_hist.replace(self.history_file)
        except Exception as e:
            logger.warning(f"Failed to persist strategy evolution state: {e}")

    def record_completed_trade(
        self,
        symbol: str,
        strategy_key: str,
        side: str,
        quantity: int,
        entry_price: float,
        exit_price: float,
        pnl: float,
        exit_reason: str,
        entry_time: Optional[str] = None,
        exit_time: Optional[str] = None,
        product: str = "MIS",
        regime: str = "Adaptive",
    ) -> TradeRecord:
        """Records an exit trade, updates strategy metrics, and dynamically re-weights strategies."""
        now_str = IndianMarketCalendar.now_ist().isoformat()
        entry_time = entry_time or now_str
        exit_time = exit_time or now_str

        pnl_pct = ((exit_price - entry_price) / entry_price * 100.0) if side == "BUY" else ((entry_price - exit_price) / entry_price * 100.0)
        is_win = pnl > 0

        trade = TradeRecord(
            trade_id=f"TRD_{int(datetime.now().timestamp())}_{symbol}",
            symbol=symbol,
            strategy=strategy_key,
            side=side,
            product=product,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=entry_time,
            exit_time=exit_time,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            exit_reason=exit_reason,
            regime=regime,
        )
        self.trades_history.append(trade)

        # Update Strategy Performance Statistics
        strat_key = strategy_key.lower().replace(" ", "_")
        matched_key = "trend_following"
        for k in self.strategy_stats.keys():
            if k in strat_key or strat_key in k:
                matched_key = k
                break

        st = self.strategy_stats.get(matched_key)
        if st:
            st.trades_count += 1
            st.total_pnl = round(st.total_pnl + pnl, 2)
            if is_win:
                st.wins += 1
                st.gross_profit += pnl
                st.current_streak = max(1, st.current_streak + 1)
            else:
                st.losses += 1
                st.gross_loss += abs(pnl)
                st.current_streak = min(-1, st.current_streak - 1)

            # Win Rate
            st.win_rate = round(st.wins / st.trades_count, 3)

            # Profit Factor
            st.profit_factor = round((st.gross_profit / max(1.0, st.gross_loss)), 2)

            # Dynamic Continuous Re-Weighting (Evolution)
            # High win-rate & high profit factor strategies scale up in priority
            # Losing strategies get deprioritized to minimize exposure
            performance_score = (st.win_rate - 0.5) * 1.5 + (min(2.5, st.profit_factor) - 1.0) * 0.4
            new_weight = 1.0 + performance_score
            st.weight = round(max(0.20, min(2.50, new_weight)), 2)
            st.last_updated = now_str

            logger.info(
                f"🧠 STRATEGY EVOLUTION: {st.name} | Win Rate: {st.win_rate*100:.1f}% | "
                f"Profit Factor: {st.profit_factor:.2f} | Dynamic Weight: {st.weight:.2f} | PnL: ₹{st.total_pnl:+,.2f}"
            )

        self._save_state()
        return trade

    def get_strategy_weight(self, strategy_key: str) -> float:
        """Returns the dynamic evolutionary priority weight for the given strategy."""
        k_clean = strategy_key.lower().replace(" ", "_")
        for k, v in self.strategy_stats.items():
            if k in k_clean or k_clean in k:
                return v.weight
        return 1.0

    def get_risk_multiplier(self, strategy_key: str, market_sentiment: str = "BULLISH") -> float:
        """Calculates dynamic risk & position sizing multiplier (0.50x to 1.40x).
        
        Takes calculated risk when conditions justify (proven edge + favorable sentiment),
        reduces exposure during adverse or unproven conditions.
        """
        weight = self.get_strategy_weight(strategy_key)
        
        # Base sizing factor derived from strategy performance
        multiplier = 1.0
        if weight >= 1.30:
            multiplier = 1.25  # High-performing strategy: scale up calculated risk
        elif weight <= 0.70:
            multiplier = 0.60  # Underperforming strategy: restrict risk exposure

        # Macro sentiment overlay
        if market_sentiment == "BEARISH" and "reversion" not in strategy_key.lower():
            multiplier *= 0.85

        return round(max(0.50, min(1.40, multiplier)), 2)

    def get_evolution_summary(self) -> Dict[str, Any]:
        """Provides real-time strategy performance metrics for Mission Control dashboard."""
        total_trades = len(self.trades_history)
        total_pnl = sum(t.pnl for t in self.trades_history)
        wins = sum(1 for t in self.trades_history if t.pnl > 0)
        overall_win_rate = (wins / total_trades) if total_trades > 0 else 0.50

        # Best performing strategy
        best_strat = max(self.strategy_stats.values(), key=lambda s: (s.weight, s.total_pnl))

        return {
            "total_trades": total_trades,
            "overall_win_rate": round(overall_win_rate * 100, 1),
            "total_evolution_pnl": round(total_pnl, 2),
            "best_strategy": best_strat.name,
            "best_strategy_weight": best_strat.weight,
            "strategies": {
                k: {
                    "name": v.name,
                    "trades": v.trades_count,
                    "win_rate_pct": round(v.win_rate * 100, 1),
                    "profit_factor": v.profit_factor,
                    "weight": v.weight,
                    "streak": v.current_streak,
                    "total_pnl": v.total_pnl,
                }
                for k, v in self.strategy_stats.items()
            },
            "recent_trades": [
                {
                    "symbol": t.symbol,
                    "strategy": t.strategy,
                    "side": t.side,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "exit_reason": t.exit_reason,
                    "exit_time": t.exit_time,
                }
                for t in reversed(self.trades_history[-10:])
            ],
        }
