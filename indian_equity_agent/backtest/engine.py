"""Walk-Forward Event-Driven Backtesting Engine for Indian Equities.

Prevents look-ahead bias by strictly exposing past information at each step,
incorporating slippage, Indian statutory transaction taxes, and deterministic risk checks.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import pandas as pd
from ..core.models import (
    Bar,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    StrategySignal,
)
from ..execution.cost_calculator import IndianCostCalculator
from ..execution.paper_broker import PaperBroker
from ..monitoring.exit_manager import ExitManager
from ..risk.engine import RiskEngine
from ..strategies.base import BaseStrategy
from ..indicators.price_action import extract_features
from .metrics import calculate_performance_metrics

logger = logging.getLogger("indian_equity_agent.backtest")


class BacktestEngine:
    """Walk-forward backtest runner without look-ahead bias."""

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = 500000.0,
        slippage_pct: float = 0.0005,
        min_lookback: int = 50,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.slippage_pct = slippage_pct
        self.min_lookback = min_lookback
        self.cost_calculator = IndianCostCalculator()

    def run(
        self,
        symbol: str,
        bars: List[Bar],
        instrument: Optional[Instrument] = None,
    ) -> Dict[str, Any]:
        """Executes walk-forward backtest across the provided bar series."""
        if len(bars) < self.min_lookback + 5:
            return {"error": f"Insufficient bars ({len(bars)}) for backtesting."}

        broker = PaperBroker(initial_capital=self.initial_capital, slippage_pct=self.slippage_pct)
        risk_engine = RiskEngine()
        exit_manager = ExitManager()

        # Convert bars to pandas DataFrame
        records = [b.model_dump() for b in bars]
        full_df = pd.DataFrame(records)
        full_df.set_index("timestamp", inplace=True)
        full_feat_df = extract_features(full_df)

        equity_curve: List[float] = [self.initial_capital]
        trade_logs: List[Dict[str, Any]] = []

        # Walk forward through each bar strictly
        for i in range(self.min_lookback, len(bars)):
            curr_bar = bars[i]
            slice_df = full_feat_df.iloc[: i + 1]

            # 1. Update broker MTM and check exits on active positions
            portfolio_state = broker.get_portfolio_state()
            active_pos = portfolio_state.positions.get(symbol)

            if active_pos and active_pos.quantity != 0:
                broker.update_market_price(symbol, curr_bar.close)

                # Check if stop loss or target hit during this bar
                exit_order = exit_manager.evaluate_position_exits(
                    position=active_pos,
                    current_price=curr_bar.close,
                    current_time=curr_bar.timestamp,
                )

                if exit_order:
                    pos_qty = abs(active_pos.quantity)
                    pos_entry = active_pos.average_entry_price
                    is_long = active_pos.quantity > 0
                    prod = active_pos.product

                    # Execute exit on paper broker
                    filled_exit = broker.place_order(exit_order)

                    # Record completed trade
                    trade_info = self.cost_calculator.calculate_roundtrip_costs(
                        quantity=pos_qty,
                        buy_price=pos_entry if is_long else filled_exit.average_fill_price,
                        sell_price=filled_exit.average_fill_price if is_long else pos_entry,
                        product=prod,
                    )
                    trade_logs.append(trade_info)

            # 2. If no position is open, evaluate strategy signal
            portfolio_state = broker.get_portfolio_state()
            if symbol not in portfolio_state.positions or portfolio_state.positions[symbol].quantity == 0:
                signal: StrategySignal = self.strategy.generate_signal(symbol, slice_df)

                if signal.action in ("BUY", "SELL"):
                    # Calculate quantity via Risk Engine's Position Sizer
                    qty, risk_amount, rationale = risk_engine.position_sizer.calculate_quantity(
                        symbol=symbol,
                        entry_price=curr_bar.close,
                        stop_loss_price=signal.suggested_stop_loss,
                        portfolio=portfolio_state,
                        bar_volume=curr_bar.volume,
                    )

                    if qty > 0:
                        # Determine product: CNC for daily bar swing holding, MIS for intraday
                        product_type = ProductType.CNC if len(bars) > 1 and (bars[1].timestamp - bars[0].timestamp).total_seconds() >= 86400 else ProductType.MIS

                        order = Order(
                            order_id=f"BT_{i}",
                            symbol=symbol,
                            side=OrderSide.BUY if signal.action == "BUY" else OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            product=product_type,
                            quantity=qty,
                            price=curr_bar.close,
                            stop_loss=signal.suggested_stop_loss,
                            target_price=signal.suggested_target,
                        )

                        # Evaluate against independent deterministic risk engine
                        risk_decision = risk_engine.evaluate_order(
                            order=order,
                            portfolio=portfolio_state,
                            instrument=instrument,
                            skip_market_hours=True,  # Backtest bar sequence controls time
                        )

                        if risk_decision.approved:
                            broker.place_order(order)

            # Record updated portfolio equity
            current_portfolio = broker.get_portfolio_state()
            equity_curve.append(current_portfolio.total_portfolio_value)

        # Force close any remaining position at final bar close
        final_pos = broker.get_positions().get(symbol)
        if final_pos and final_pos.quantity != 0:
            final_qty = abs(final_pos.quantity)
            final_entry = final_pos.average_entry_price
            final_long = final_pos.quantity > 0
            final_prod = final_pos.product

            exit_order = Order(
                order_id="BT_FINAL_EXIT",
                symbol=symbol,
                side=OrderSide.SELL if final_long else OrderSide.BUY,
                quantity=final_qty,
                price=bars[-1].close,
            )
            filled_final = broker.place_order(exit_order)
            trade_logs.append(
                self.cost_calculator.calculate_roundtrip_costs(
                    quantity=final_qty,
                    buy_price=final_entry if final_long else filled_final.average_fill_price,
                    sell_price=filled_final.average_fill_price if final_long else final_entry,
                    product=final_prod,
                )
            )
            equity_curve.append(broker.get_portfolio_state().total_portfolio_value)

        metrics = calculate_performance_metrics(equity_curve, trade_logs)
        metrics["symbol"] = symbol
        metrics["strategy_name"] = self.strategy.name
        metrics["bars_tested"] = len(bars)
        metrics["trade_logs"] = trade_logs

        return metrics
