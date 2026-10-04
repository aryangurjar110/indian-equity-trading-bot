"""Command Line Interface for Indian Equities Trading Agent."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from ..config import settings
from ..market_data.calendar import IndianMarketCalendar
from ..market_data.yfinance_source import YFinanceSource
from ..market_data.mock_live_source import MockMarketDataSource
from ..strategies.trend_following import TrendFollowingStrategy
from ..strategies.momentum_breakout import MomentumBreakoutStrategy
from ..strategies.mean_reversion import MeanReversionStrategy
from ..strategies.volatility_breakout import VolatilityBreakoutStrategy
from ..backtest.engine import BacktestEngine
from ..execution import create_broker, PaperBroker, GrowwBroker, KiteConnectBroker
from ..risk.kill_switch import KillSwitch
from ..risk.engine import RiskEngine
from ..ai_engine.gemini_analyst import GeminiMarketAnalyst
from ..confidence_filter.filter import ConfidenceFilter
from .dashboard import display_dashboard

app = typer.Typer(
    help="Production-Grade AI-Powered Algorithmic Trading Agent for Indian Equities",
    no_args_is_help=True,
)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(legacy_windows=False)


def _get_strategy(name: str):
    name = name.lower()
    if "trend" in name:
        return TrendFollowingStrategy()
    elif "momentum" in name or "donchian" in name:
        return MomentumBreakoutStrategy()
    elif "mean" in name or "reversion" in name:
        return MeanReversionStrategy()
    elif "volatility" in name or "squeeze" in name:
        return VolatilityBreakoutStrategy()
    else:
        console.print(f"[yellow]Unknown strategy '{name}', defaulting to TrendFollowingStrategy[/yellow]")
        return TrendFollowingStrategy()


@app.command()
def status():
    """Display system status, Indian market timings, and broker configuration."""
    ks = KillSwitch()
    broker_type = settings.broker.broker_type.upper()
    now_ist = IndianMarketCalendar.now_ist()

    table = Table(title="🛡️ System Status & Environment", border_style="cyan")
    table.add_column("Parameter", style="bold")
    table.add_column("Value", style="yellow")

    table.add_row("Indian Market Time (IST)", now_ist.strftime("%Y-%m-%d %H:%M:%S"))
    table.add_row("NSE Market Status", "OPEN" if IndianMarketCalendar.is_market_open(now_ist) else "CLOSED")
    table.add_row("Entry Permitted (09:20-15:05)", "YES" if IndianMarketCalendar.is_entry_allowed(now_ist) else "NO")
    table.add_row("Emergency Kill Switch", "ENGAGED" if ks.is_active else "ARMED (OK)")
    table.add_row("Configured Broker", broker_type)
    table.add_row("Initial Capital", f"₹{settings.broker.paper_initial_capital:,.2f}")
    table.add_row("Max Risk Per Trade", f"{settings.risk.max_risk_per_trade_pct*100:.1f}%")
    table.add_row("Max Daily Loss Limit", f"{settings.risk.max_daily_loss_pct*100:.1f}%")
    table.add_row("Max Portfolio Drawdown", f"{settings.risk.max_drawdown_pct*100:.1f}%")
    table.add_row("Gemini AI Analysis", "ENABLED" if settings.ai.enable_ai_analysis else "DISABLED")

    console.print(table)

    warnings = settings.validate_setup()
    if warnings:
        console.print("\n[bold yellow]System Configuration Warnings:[/bold yellow]")
        for w in warnings:
            console.print(f" - {w}")


@app.command()
def backtest(
    symbols: str = typer.Option("RELIANCE.NS,TCS.NS", help="Comma-separated NSE symbols"),
    strategy: str = typer.Option("trend_following", help="Strategy: trend_following, momentum_breakout, mean_reversion, volatility_breakout"),
    days: int = typer.Option(60, help="Lookback days for historical bars"),
    capital: float = typer.Option(500000.0, help="Starting capital in INR"),
):
    """Run an out-of-sample, walk-forward backtest with Indian statutory taxes and slippage."""
    strat_instance = _get_strategy(strategy)
    engine = BacktestEngine(strategy=strat_instance, initial_capital=capital)
    data_source = YFinanceSource()

    end_dt = IndianMarketCalendar.now_ist()
    start_dt = end_dt - timedelta(days=days)

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    console.print(f"\n[bold green]Initiating Walk-Forward Backtest on {len(symbol_list)} Indian Equities...[/bold green]")
    console.print(f"Strategy: [bold cyan]{strat_instance.name}[/bold cyan] | Capital: ₹{capital:,.2f} | Period: {days} days\n")

    for sym in symbol_list:
        console.print(f"📥 Downloading historical bars for [bold]{sym}[/bold]...")
        bars = data_source.get_historical_bars(sym, start_date=start_dt, end_date=end_dt, interval="1d")

        if not bars:
            console.print(f"[red]No data retrieved for {sym}. Skipping.[/red]")
            continue

        res = engine.run(symbol=sym, bars=bars)

        if "error" in res:
            console.print(f"[red]Backtest failed for {sym}: {res['error']}[/red]")
            continue

        table = Table(title=f"📊 Backtest Performance: {sym}", border_style="blue")
        table.add_column("Metric", style="bold")
        table.add_column("Value", style="green" if res["net_profit"] >= 0 else "red", justify="right")

        table.add_row("Initial Capital", f"₹{res['initial_capital']:,.2f}")
        table.add_row("Final Capital", f"₹{res['final_capital']:,.2f}")
        table.add_row("Net Profit (After All Taxes)", f"₹{res['net_profit']:,.2f} ({res['return_pct']}%)")
        table.add_row("Max Drawdown (MDD)", f"{res['max_drawdown_pct']}%")
        table.add_row("Sharpe Ratio", str(res["sharpe_ratio"]))
        table.add_row("Sortino Ratio", str(res["sortino_ratio"]))
        table.add_row("Calmar Ratio", str(res["calmar_ratio"]))
        table.add_row("Total Trades", str(res["total_trades"]))
        table.add_row("Win Rate", f"{res['win_rate_pct']}%")
        table.add_row("Profit Factor", str(res["profit_factor"]))
        table.add_row("Total Statutory Costs (STT/GST/NSE)", f"₹{res['total_statutory_costs']:,.2f}")

        console.print(table)


@app.command()
def paper_trade(
    symbol: str = typer.Option("RELIANCE", help="Symbol to simulate"),
    iterations: int = typer.Option(5, help="Number of simulation steps"),
    use_mock: bool = typer.Option(True, help="Use mock feed instead of live Yahoo Finance"),
):
    """Run paper trading simulation through the complete 10-layer pipeline."""
    ks = KillSwitch()
    broker = PaperBroker(initial_capital=settings.broker.paper_initial_capital)
    strategy = TrendFollowingStrategy()
    risk_engine = RiskEngine(kill_switch=ks)
    ai_analyst = GeminiMarketAnalyst()
    conf_filter = ConfidenceFilter()

    feed = MockMarketDataSource() if use_mock else YFinanceSource()

    console.print(f"[bold cyan]Starting Paper Trading Simulation on {symbol}...[/bold cyan]")

    end_dt = IndianMarketCalendar.now_ist()
    start_dt = end_dt - timedelta(days=30)
    bars = feed.get_historical_bars(symbol, start_date=start_dt, end_date=end_dt)

    if not bars:
        console.print("[red]Could not retrieve bars for simulation.[/red]")
        return

    import pandas as pd
    records = [b.model_dump() for b in bars]
    df = pd.DataFrame(records)
    df.set_index("timestamp", inplace=True)

    # 1. Strategy signal
    signal = strategy.generate_signal(symbol, df)
    console.print(f"1. Strategy Signal: [bold]{signal.action}[/bold] (Stop: ₹{signal.suggested_stop_loss}, Target: ₹{signal.suggested_target})")

    # 2. AI Analysis
    ai_decision = ai_analyst.analyze(symbol, signal.indicators, proposed_signal=signal)
    console.print(f"2. Gemini AI Analysis: [bold]{ai_decision.action}[/bold] (Confidence: {ai_decision.confidence:.2f}, Risk: {ai_decision.risk_level})")
    console.print(f"   Reason: {ai_decision.reason}")

    # 3. Confidence Filter
    consensus_ok, consensus_reason = conf_filter.evaluate(signal, ai_decision)
    console.print(f"3. Confidence Filter Consensus: {'[green]PASSED[/green]' if consensus_ok else '[yellow]REJECTED (NO TRADE)[/yellow]'}")
    console.print(f"   Explanation: {consensus_reason}")

    if consensus_ok and signal.action in ("BUY", "SELL"):
        # 4. Position Sizing
        portfolio_state = broker.get_portfolio_state()
        qty, risk_amount, size_reason = risk_engine.position_sizer.calculate_quantity(
            symbol=symbol,
            entry_price=bars[-1].close,
            stop_loss_price=signal.suggested_stop_loss,
            portfolio=portfolio_state,
            bar_volume=bars[-1].volume,
        )
        console.print(f"4. Position Sizing: {size_reason}")

        if qty > 0:
            from ..core.models import Order, OrderSide, OrderType, ProductType
            order = Order(
                order_id="SIM_ORDER_1",
                symbol=symbol,
                side=OrderSide.BUY if signal.action == "BUY" else OrderSide.SELL,
                order_type=OrderType.MARKET,
                product=ProductType.MIS,
                quantity=qty,
                price=bars[-1].close,
                stop_loss=signal.suggested_stop_loss,
                target_price=signal.suggested_target,
            )

            # 5. Independent Risk Engine
            risk_decision = risk_engine.evaluate_order(
                order=order,
                portfolio=portfolio_state,
                instrument=feed.get_instrument(symbol),
                skip_market_hours=True,
            )
            console.print(f"5. Independent Risk Engine: {'[green]APPROVED[/green]' if risk_decision.approved else '[red]REJECTED[/red]'}")
            console.print(f"   Reason: {risk_decision.reason}")

            if risk_decision.approved:
                # 6. Broker execution
                filled_order = broker.place_order(order)
                console.print(f"6. Paper Broker Fill: Order {filled_order.order_id} filled @ ₹{filled_order.average_fill_price:.2f}")

    # Display final dashboard
    display_dashboard(broker.get_portfolio_state(), ks)


@app.command()
def trade(
    symbol: str = typer.Option("RELIANCE", help="Symbol to analyze and execute"),
    strategy: str = typer.Option("trend_following", help="Strategy: trend_following, momentum_breakout, mean_reversion, volatility_breakout"),
    use_mock: bool = typer.Option(False, help="Use mock feed instead of live Yahoo Finance"),
    broker_override: Optional[str] = typer.Option(None, help="Override broker: paper, groww, kite"),
):
    """Execute autonomous trading cycle using the active broker (Groww, Kite, or Paper)."""
    ks = KillSwitch()
    b_type = (broker_override or settings.broker.broker_type).lower()
    broker = create_broker(broker_type=b_type, kill_switch=ks)
    strat_instance = _get_strategy(strategy)
    risk_engine = RiskEngine(kill_switch=ks)
    ai_analyst = GeminiMarketAnalyst()
    conf_filter = ConfidenceFilter()

    feed = MockMarketDataSource() if use_mock else YFinanceSource()

    console.print(f"\n[bold green]🚀 Initiating Live/Paper Trade Cycle on {symbol} via {b_type.upper()} broker...[/bold green]")
    console.print(f"Strategy: [bold cyan]{strat_instance.name}[/bold cyan] | AI: {'ENABLED' if settings.ai.enable_ai_analysis else 'DISABLED'}\n")

    end_dt = IndianMarketCalendar.now_ist()
    start_dt = end_dt - timedelta(days=60)
    bars = feed.get_historical_bars(symbol, start_date=start_dt, end_date=end_dt)

    if not bars:
        console.print(f"[red]Could not retrieve bars for {symbol}. Aborting.[/red]")
        return

    import pandas as pd
    records = [b.model_dump() for b in bars]
    df = pd.DataFrame(records)
    df.set_index("timestamp", inplace=True)

    # 1. Strategy signal
    signal = strat_instance.generate_signal(symbol, df)
    console.print(f"1. Strategy Signal: [bold]{signal.action}[/bold] (Stop: ₹{signal.suggested_stop_loss}, Target: ₹{signal.suggested_target})")

    # 2. AI Analysis
    ai_decision = ai_analyst.analyze(symbol, signal.indicators, proposed_signal=signal)
    console.print(f"2. Gemini AI Analysis: [bold]{ai_decision.action}[/bold] (Confidence: {ai_decision.confidence:.2f}, Risk: {ai_decision.risk_level})")
    console.print(f"   Reason: {ai_decision.reason}")

    # 3. Confidence Filter Consensus
    consensus_ok, consensus_reason = conf_filter.evaluate(signal, ai_decision)
    console.print(f"3. Consensus Gatekeeper: {'[green]APPROVED[/green]' if consensus_ok else '[yellow]REJECTED (NO TRADE)[/yellow]'}")
    console.print(f"   Explanation: {consensus_reason}")

    if not consensus_ok or signal.action not in ("BUY", "SELL"):
        console.print("[bold yellow]Safe default: NO TRADE executed. Capital preserved.[/bold yellow]")
        display_dashboard(broker.get_portfolio_state(), ks)
        return

    # 4. Position Sizing
    portfolio_state = broker.get_portfolio_state()
    qty, risk_amount, size_reason = risk_engine.position_sizer.calculate_quantity(
        symbol=symbol,
        entry_price=bars[-1].close,
        stop_loss_price=signal.suggested_stop_loss,
        portfolio=portfolio_state,
        bar_volume=bars[-1].volume,
    )
    console.print(f"4. Position Sizing: {size_reason}")

    if qty <= 0:
        console.print("[yellow]Calculated quantity is 0. No order submitted.[/yellow]")
        return

    from ..core.models import Order, OrderSide, OrderType, ProductType
    order = Order(
        order_id=f"ORD_{int(datetime.now().timestamp())}",
        symbol=symbol,
        side=OrderSide.BUY if signal.action == "BUY" else OrderSide.SELL,
        order_type=OrderType.MARKET,
        product=ProductType.CNC,
        quantity=qty,
        price=bars[-1].close,
        stop_loss=signal.suggested_stop_loss,
        target_price=signal.suggested_target,
    )

    # 5. Independent Risk Engine Veto Check
    risk_decision = risk_engine.evaluate_order(
        order=order,
        portfolio=portfolio_state,
        instrument=feed.get_instrument(symbol),
        skip_market_hours=False,
    )
    console.print(f"5. Independent Risk Engine: {'[green]APPROVED[/green]' if risk_decision.approved else '[red]VETOED / REJECTED[/red]'}")
    console.print(f"   Reason: {risk_decision.reason}")

    if not risk_decision.approved:
        console.print("[bold red]Order vetoed by deterministic risk engine. Execution halted.[/bold red]")
        return

    # 6. Broker Execution
    console.print(f"6. Routing order to {b_type.upper()} broker...")
    executed = broker.place_order(order)
    console.print(f"   Order ID: {executed.order_id} | Status: {executed.status.value} | Rejection Reason: {executed.rejection_reason or 'None'}")

    display_dashboard(broker.get_portfolio_state(), ks)


@app.command()
def kill_switch(
    action: str = typer.Option("status", help="status, trigger, reset"),
    reason: str = typer.Option("Manual emergency halt via CLI", help="Reason for emergency trigger"),
    token: str = typer.Option("", help="Confirmation token required to reset: AUTHORIZE_RESET_CONFIRMED"),
):
    """Emergency Circuit Breaker management."""
    ks = KillSwitch()
    action = action.lower()

    if action == "status":
        status_text = f"[red]ENGAGED ({ks.reason})[/red]" if ks.is_active else "[green]ARMED & NORMAL[/green]"
        console.print(f"Circuit Breaker Status: {status_text}")
    elif action == "trigger":
        ks.trigger(reason)
        console.print(f"[bold red]Emergency kill switch ENGAGED: {reason}[/bold red]")
    elif action == "reset":
        if ks.reset(token):
            console.print("[bold green]Kill switch successfully RESET. Order execution is re-armed.[/bold green]")
        else:
            console.print("[bold red]Reset failed. Must provide --token AUTHORIZE_RESET_CONFIRMED[/bold red]")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="Host interface to bind"),
    port: int = typer.Option(8000, help="Port to listen on"),
):
    """Launch interactive localhost Web Dashboard on http://localhost:<port>."""
    import uvicorn
    console.print(f"\n[bold green]🚀 Launching Indian Equities Trading Agent Web Dashboard on http://{host}:{port}[/bold green]")
    console.print(f"[cyan]Open your browser and navigate to [bold underline]http://{host}:{port}[/bold underline][/cyan]\n")
    uvicorn.run("indian_equity_agent.web.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
