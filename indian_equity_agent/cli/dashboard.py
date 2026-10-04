"""Rich Terminal Dashboard for Indian Equities Trading Agent."""

from __future__ import annotations

from typing import Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from ..core.models import PortfolioState, RiskDecision, StrategySignal, AIAnalysisOutput
from ..market_data.calendar import IndianMarketCalendar
from ..risk.kill_switch import KillSwitch
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(legacy_windows=False)


def render_portfolio_table(portfolio: PortfolioState) -> Table:
    """Renders high-level capital and risk limits summary."""
    table = Table(title="📊 Capital & Risk Overview", border_style="blue", show_header=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold yellow", justify="right")
    table.add_column("Limit / Target", style="dim", justify="right")

    pnl_style = "bold green" if portfolio.daily_total_pnl >= 0 else "bold red"
    sign = "+" if portfolio.daily_total_pnl >= 0 else ""

    table.add_row("Total Equity", f"₹{portfolio.total_portfolio_value:,.2f}", f"Peak: ₹{portfolio.peak_equity:,.2f}")
    table.add_row("Available Cash", f"₹{portfolio.cash:,.2f}", "Min 20% Reserve")
    table.add_row("Today's Total P&L", f"[{pnl_style}]{sign}₹{portfolio.daily_total_pnl:,.2f}[/{pnl_style}]", "Max Loss: 2.0%")
    table.add_row("Current Drawdown", f"{portfolio.current_drawdown_pct*100:.2f}%", "Max Ceiling: 6.0%")
    table.add_row("Portfolio Exposure", f"{portfolio.total_exposure_pct*100:.2f}%", "Max Ceiling: 80.0%")
    table.add_row("Open Positions", str(len(portfolio.positions)), "Max Limit: 5")

    return table


def render_positions_table(portfolio: PortfolioState) -> Table:
    """Renders active open positions."""
    table = Table(title="📈 Active Positions (NSE/BSE)", border_style="green", show_header=True)
    table.add_column("Symbol", style="bold")
    table.add_column("Product", style="cyan")
    table.add_column("Qty", justify="right")
    table.add_column("Entry Price", justify="right")
    table.add_column("Current LTP", justify="right")
    table.add_column("Stop Loss", justify="right", style="red")
    table.add_column("Target", justify="right", style="green")
    table.add_column("MTM P&L", justify="right")

    if not portfolio.positions:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "No active positions")
        return table

    for pos in portfolio.positions.values():
        if pos.quantity == 0:
            continue
        pnl = pos.unrealized_pnl
        pnl_color = "green" if pnl >= 0 else "red"
        pnl_str = f"[{pnl_color}]{'+' if pnl>=0 else ''}₹{pnl:,.2f}[/{pnl_color}]"

        table.add_row(
            pos.symbol,
            pos.product.value,
            str(pos.quantity),
            f"₹{pos.average_entry_price:.2f}",
            f"₹{pos.current_price:.2f}",
            f"₹{pos.stop_loss:.2f}",
            f"₹{pos.target_price:.2f}",
            pnl_str,
        )

    return table


def render_system_status_panel(kill_switch: KillSwitch, broker_name: str = "Paper Broker") -> Panel:
    """Renders system status, IST clock, and emergency state."""
    now_ist = IndianMarketCalendar.now_ist().strftime("%Y-%m-%d %H:%M:%S IST")
    market_open = IndianMarketCalendar.is_market_open()
    market_status = "[bold green]OPEN[/bold green]" if market_open else "[bold yellow]CLOSED[/bold yellow]"

    ks_status = (
        f"[bold red]🚨 ENGAGED: {kill_switch.reason}[/bold red]"
        if kill_switch.is_active
        else "[bold green]🟢 ARMED & NORMAL[/bold green]"
    )

    content = (
        f"⏰ [bold]Market Time:[/bold] {now_ist}  |  "
        f"🏛️ [bold]NSE Status:[/bold] {market_status}  |  "
        f"🔌 [bold]Broker:[/bold] {broker_name}\n"
        f"🛡️ [bold]Emergency Circuit Breaker:[/bold] {ks_status}"
    )
    return Panel(content, title="🛡️ Indian Equities AI Trading Agent", border_style="cyan")


def display_dashboard(portfolio: PortfolioState, kill_switch: KillSwitch, broker_name: str = "Paper Broker") -> None:
    """Prints a clean, styled dashboard snapshot to terminal."""
    console.print(render_system_status_panel(kill_switch, broker_name))
    console.print(render_portfolio_table(portfolio))
    console.print(render_positions_table(portfolio))
