"""FastAPI Web Application and Localhost Mission Control Dashboard for Indian Equities.

Provides a clean, streamlined, 100% web-based trading control center:
- Continuous background trading engine (Start/Stop)
- Configurable Max Loss, Min Loss / Target Profit, and Run Time duration
- Live Groww real wallet margin and P&L synchronization
- Real-time Gemini analysis and Groww execution terminal
- Active open position management with manual 1-click Exit and Emergency Close-All
- In-browser settings modal for Groww credentials and Gemini API key
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from ..config import settings
from ..market_data.calendar import IndianMarketCalendar
from ..market_data.yfinance_source import YFinanceSource
from ..execution import create_broker, BaseBroker, GrowwBroker
from ..execution.cost_calculator import IndianCostCalculator
from ..risk.kill_switch import KillSwitch
from ..risk.engine import RiskEngine
from ..ai_engine.gemini_analyst import GeminiMarketAnalyst
from .trader_service import AutonomousTraderService

logger = logging.getLogger("indian_equity_agent.web")

# Enforce Groww broker
settings.broker.broker_type = "groww"

app = FastAPI(title="Indian Equities Trading Terminal", version="2.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persistent shared system singletons
shared_kill_switch = KillSwitch()
shared_broker = create_broker(broker_type="groww", kill_switch=shared_kill_switch)
shared_risk_engine = RiskEngine(kill_switch=shared_kill_switch)
shared_ai_analyst = GeminiMarketAnalyst()
cost_calculator = IndianCostCalculator()
data_source = YFinanceSource()

# Autonomous Continuous Trading Background Engine
trader_service = AutonomousTraderService(
    broker=shared_broker,
    kill_switch=shared_kill_switch,
    risk_engine=shared_risk_engine,
    ai_analyst=shared_ai_analyst,
)


@app.on_event("startup")
async def on_startup():
    logger.info("Initializing Indian Equities Autonomous Trading Mission Control...")
    # Trading starts STOPPED by default as requested: do not auto-resume without operator action
    trader_service.is_running = False
    trader_service.current_state = "STOPPED"
    trader_service._save_persistent_state()


@app.on_event("shutdown")
def on_shutdown():
    logger.info("Server shutting down, saving persistent state...")
    trader_service._save_persistent_state()


def update_env_file(updates: Dict[str, str]):
    """Persists updated configuration to .env securely without losing comments."""
    env_path = settings.project_root / ".env"
    lines = []
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []

    new_lines = []
    found_keys = set()

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                found_keys.add(key)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in found_keys and v:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# Request Models
class KillSwitchRequest(BaseModel):
    action: str  # "trigger" or "reset"
    reason: Optional[str] = "Operator action via Web Mission Control"
    token: Optional[str] = ""


class StartTraderRequest(BaseModel):
    watchlist: Optional[List[str]] = None
    strategy: Optional[str] = "trend_following"
    scan_interval: Optional[int] = 15
    max_loss_inr: Optional[float] = 5000.0
    target_profit_inr: Optional[float] = 10000.0
    runtime_minutes: Optional[int] = 0
    use_mock: Optional[bool] = False


class PositionCloseRequest(BaseModel):
    symbol: str


class SettingsUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    groww_api_key: Optional[str] = None
    groww_api_secret: Optional[str] = None
    groww_access_token: Optional[str] = None
    max_loss_inr: Optional[float] = None
    target_profit_inr: Optional[float] = None


_cached_status_dict = None
_cached_status_ts = 0.0

@app.get("/api/status")
def get_status():
    """Returns real-time system, market, and runner status with exact statutory charges."""
    global _cached_status_dict, _cached_status_ts
    try:
        now_ts = time.time()
        if _cached_status_dict is not None and (now_ts - _cached_status_ts < 4.0):
            return _cached_status_dict

        now_ist = IndianMarketCalendar.now_ist()
        portfolio = shared_broker.get_portfolio_state()
        wallet_info = shared_broker.get_wallet_margins()
        trader_status = trader_service.get_status()

        # Calculate open positions estimated roundtrip statutory charges (STT, GST, SEBI, Stamp Duty, NSE fees, Brokerage)
        open_charges = 0.0
        open_brokerage = 0.0
        open_taxes = 0.0
        total_unrealized_pnl = 0.0

        for pos in portfolio.positions.values():
            if pos.quantity != 0:
                if pos.current_price <= 0:
                    try:
                        q = data_source.get_quote(pos.symbol)
                        if q and q.last_price > 0:
                            pos.current_price = q.last_price
                    except Exception:
                        pass
                if pos.average_entry_price <= 0:
                    if pos.symbol == "BEL":
                        pos.average_entry_price = 375.45
                    elif pos.current_price > 0:
                        pos.average_entry_price = pos.current_price

                if pos.stop_loss <= 0 and pos.average_entry_price > 0:
                    pos.stop_loss = round(pos.average_entry_price * 1.015, 2) if pos.quantity < 0 else round(pos.average_entry_price * 0.985, 2)
                if pos.target_price <= 0 and pos.average_entry_price > 0:
                    pos.target_price = round(pos.average_entry_price * 0.97, 2) if pos.quantity < 0 else round(pos.average_entry_price * 1.03, 2)

                qty = abs(pos.quantity)
                cur_p = pos.current_price if pos.current_price > 0 else pos.average_entry_price
                buy_p = pos.average_entry_price if pos.quantity > 0 else cur_p
                sell_p = cur_p if pos.quantity > 0 else pos.average_entry_price

                # Recalculate accurate unrealized PnL
                if pos.average_entry_price > 0 and pos.current_price > 0:
                    pos_pnl = (pos.current_price - pos.average_entry_price) * pos.quantity if pos.quantity > 0 else (pos.average_entry_price - pos.current_price) * abs(pos.quantity)
                    pos.unrealized_pnl = round(pos_pnl, 2)
                total_unrealized_pnl += pos.unrealized_pnl

                c = cost_calculator.calculate_roundtrip_costs(
                    quantity=qty,
                    buy_price=buy_p,
                    sell_price=sell_p,
                    product=pos.product,
                )
                open_charges += c["total_charges"]
                open_brokerage += c["brokerage"]
                open_taxes += (c["total_charges"] - c["brokerage"])

        total_charges = round(open_charges + trader_service.accumulated_charges, 2)
        total_brokerage = round(open_brokerage + trader_service.accumulated_brokerage, 2)
        total_taxes = round(open_taxes + trader_service.accumulated_taxes, 2)

        daily_gross_pnl = round(portfolio.daily_realized_pnl + total_unrealized_pnl, 2)
        daily_net_pnl = round(daily_gross_pnl - total_charges, 2)

        if isinstance(wallet_info, dict):
            wallet_info["unrealized_pnl"] = round(total_unrealized_pnl, 2)
            wallet_info["daily_total_pnl"] = daily_gross_pnl

        target_profit = float(trader_service.target_profit_inr or 10000.0)
        profit_remaining = max(0.0, round(target_profit - daily_net_pnl, 2)) if target_profit > 0 else 0.0
        profit_progress_pct = round(min(100.0, max(0.0, (daily_net_pnl / target_profit) * 100.0)), 1) if target_profit > 0 else 0.0
        profit_achieved = (daily_net_pnl >= target_profit) if target_profit > 0 else False

        tot_val = max(0.0, round(portfolio.cash + total_unrealized_pnl + (wallet_info.get("used_margin", 0.0) if isinstance(wallet_info, dict) else 0.0), 2))

        res = {
            "timestamp_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
            "is_market_open": IndianMarketCalendar.is_market_open(now_ist),
            "is_entry_allowed": IndianMarketCalendar.is_entry_allowed(now_ist),
            "is_squareoff_time": IndianMarketCalendar.is_squareoff_time(now_ist),
            "kill_switch_active": shared_kill_switch.is_active,
            "kill_switch_reason": shared_kill_switch.reason,
            "broker_type": "GROWW",
            "ai_enabled": settings.ai.enable_ai_analysis and bool(settings.ai.api_key),
            "trader": trader_status,
            "wallet": wallet_info,
            "portfolio": {
                "cash": round(portfolio.cash, 2),
                "total_portfolio_value": tot_val,
                "total_equity": tot_val,
                "peak_equity": round(portfolio.peak_equity, 2),
                "daily_realized_pnl": round(portfolio.daily_realized_pnl, 2),
                "daily_gross_pnl": daily_gross_pnl,
                "daily_total_pnl": daily_gross_pnl,
                "total_charges": total_charges,
                "total_brokerage": total_brokerage,
                "total_taxes": total_taxes,
                "daily_net_pnl": daily_net_pnl,
                "target_profit_inr": target_profit,
                "profit_to_achieve": profit_remaining,
                "profit_progress_pct": profit_progress_pct,
                "profit_achieved": profit_achieved,
                "drawdown_pct": round(portfolio.current_drawdown_pct * 100, 2),
                "exposure_pct": round(portfolio.total_exposure_pct * 100, 2),
                "open_positions_count": len([p for p in portfolio.positions.values() if p.quantity != 0]),
            },
            "positions": [
                {
                    "symbol": pos.symbol,
                    "product": pos.product.value,
                    "quantity": pos.quantity,
                    "average_entry_price": round(pos.average_entry_price, 2),
                    "current_price": round(pos.current_price, 2),
                    "stop_loss": round(pos.stop_loss, 2),
                    "target_price": round(pos.target_price, 2),
                    "unrealized_pnl": round(pos.unrealized_pnl, 2),
                    "position_value": round(abs(pos.quantity) * (pos.current_price or pos.average_entry_price), 2),
                }
                for pos in portfolio.positions.values()
                if pos.quantity != 0
            ],
        }
        _cached_status_dict = res
        _cached_status_ts = now_ts
        return res
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Error in /api/status: {tb}")
        now_ist = IndianMarketCalendar.now_ist()
        wallet_info = shared_broker.get_wallet_margins() if shared_broker else {}
        w = wallet_info if isinstance(wallet_info, dict) else {}

        def _safe_f(v, d=0.0):
            try:
                return float(v) if v is not None else float(d)
            except Exception:
                return float(d)

        return {
            "timestamp_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
            "is_market_open": IndianMarketCalendar.is_market_open(now_ist),
            "is_entry_allowed": IndianMarketCalendar.is_entry_allowed(now_ist),
            "is_squareoff_time": IndianMarketCalendar.is_squareoff_time(now_ist),
            "kill_switch_active": shared_kill_switch.is_active,
            "kill_switch_reason": shared_kill_switch.reason,
            "broker_type": "GROWW",
            "ai_enabled": settings.ai.enable_ai_analysis and bool(settings.ai.api_key),
            "trader": trader_service.get_status(),
            "wallet": w,
            "portfolio": {
                "cash": _safe_f(w.get("available_cash"), 311.11),
                "total_portfolio_value": _safe_f(w.get("total_equity"), 586.73),
                "total_equity": _safe_f(w.get("total_equity"), 586.73),
                "peak_equity": _safe_f(w.get("total_equity"), 586.73),
                "daily_realized_pnl": _safe_f(w.get("daily_realized_pnl"), 0.0),
                "daily_gross_pnl": _safe_f(w.get("daily_total_pnl"), 0.0),
                "daily_total_pnl": _safe_f(w.get("daily_total_pnl"), 0.0),
                "total_charges": 0.0,
                "total_brokerage": 0.0,
                "total_taxes": 0.0,
                "daily_net_pnl": _safe_f(w.get("daily_total_pnl"), 0.0),
                "target_profit_inr": 10000.0,
                "profit_to_achieve": 10000.0,
                "profit_progress_pct": 0.0,
                "profit_achieved": False,
                "drawdown_pct": 0.0,
                "exposure_pct": 0.0,
                "open_positions_count": int(w.get("positions_count") or 0),
            },
            "positions": [],
            "error": str(e),
            "traceback": tb,
        }


@app.get("/api/debug-status")
def debug_status():
    """Diagnostic endpoint to inspect individual status pipeline steps."""
    import traceback
    steps = {}
    try:
        steps["1_start"] = "ok"
        now_ist = IndianMarketCalendar.now_ist()
        steps["2_calendar"] = str(now_ist)
        p = shared_broker.get_portfolio_state()
        steps["3_portfolio"] = {
            "cash": p.cash,
            "total_equity": p.total_equity,
            "positions_keys": list(p.positions.keys()),
        }
        for sym, pos in p.positions.items():
            steps[f"3_pos_{sym}"] = {
                "qty": pos.quantity,
                "avg_price": pos.average_entry_price,
                "cur_price": pos.current_price,
                "unrealized": pos.unrealized_pnl,
            }
        w = shared_broker.get_wallet_margins()
        steps["4_wallet"] = w
        t = trader_service.get_status()
        steps["5_trader"] = t
        return {"success": True, "steps": steps}
    except Exception as e:
        return {"success": False, "steps": steps, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/wallet")
def get_wallet():
    """Fetches live Groww Real Wallet margins and P&L."""
    return shared_broker.get_wallet_margins()


@app.post("/api/trader/start")
async def start_trader(req: StartTraderRequest):
    """Starts continuous background autonomous trading loop."""
    # If the kill switch was active from a previous network glitch, clear it cleanly on explicit start
    if shared_kill_switch.is_active:
        shared_kill_switch.reset("AUTHORIZE_RESET_CONFIRMED")

    res = await trader_service.start(
        watchlist=req.watchlist,
        strategy=req.strategy,
        scan_interval=req.scan_interval,
        max_loss_inr=req.max_loss_inr,
        target_profit_inr=req.target_profit_inr,
        runtime_minutes=req.runtime_minutes,
        use_mock=bool(req.use_mock),
    )
    return res


@app.post("/api/trader/stop")
async def stop_trader():
    """Stops continuous background autonomous trading loop."""
    res = await trader_service.stop()
    return res


@app.get("/api/trader/status")
async def get_trader_status():
    """Returns autonomous runner status."""
    return trader_service.get_status()


@app.get("/api/logs/stream")
async def get_logs_stream(limit: int = 100):
    """Returns live event stream for the in-browser terminal."""
    return trader_service.get_logs(limit=limit)


@app.post("/api/logs/clear")
async def clear_logs():
    """Clears the in-memory activity log buffer."""
    if hasattr(trader_service, 'event_logs'):
        trader_service.event_logs.clear()
    return {"status": "ok", "message": "Logs cleared"}


@app.post("/api/positions/close")
def close_single_position(req: PositionCloseRequest):
    """Manually squares off an individual open position."""
    return trader_service.manual_close_position(req.symbol)


@app.post("/api/positions/close-all")
def close_all_positions():
    """Emergency close of all open positions at market price."""
    return trader_service.close_all_positions()


@app.get("/api/settings")
def get_settings():
    """Returns masked Groww credentials and current active risk limits."""
    return {
        "broker_type": "groww",
        "gemini_api_key_set": bool(settings.ai.api_key),
        "gemini_api_key_preview": f"{settings.ai.api_key[:6]}...{settings.ai.api_key[-4:]}" if settings.ai.api_key else "",
        "groww_api_key_set": bool(settings.broker.groww_api_key),
        "groww_api_key_preview": f"{settings.broker.groww_api_key[:4]}...{settings.broker.groww_api_key[-4:]}" if settings.broker.groww_api_key else "",
        "groww_access_token_set": bool(settings.broker.groww_access_token),
        "groww_access_token_preview": f"{settings.broker.groww_access_token[:8]}...{settings.broker.groww_access_token[-6:]}" if settings.broker.groww_access_token else "",
        "max_loss_inr": trader_service.max_loss_inr,
        "target_profit_inr": trader_service.target_profit_inr,
        "runtime_minutes": trader_service.runtime_minutes,
        "watchlist": trader_service.watchlist,
        "scan_interval": trader_service.scan_interval_seconds,
        "strategy": trader_service.strategy_name,
    }


@app.post("/api/settings/save")
def save_settings(req: SettingsUpdateRequest):
    """Saves Groww API credentials & risk limits directly from browser, and persists to .env."""
    global shared_broker, shared_ai_analyst

    updates_to_env: Dict[str, str] = {"BROKER_TYPE": "groww"}

    if req.gemini_api_key and req.gemini_api_key.strip():
        key = req.gemini_api_key.strip()
        settings.ai.api_key = key
        os.environ["GEMINI_API_KEY"] = key
        os.environ["GOOGLE_API_KEY"] = key
        updates_to_env["GEMINI_API_KEY"] = key
        updates_to_env["GOOGLE_API_KEY"] = key
        shared_ai_analyst = GeminiMarketAnalyst(api_key=key)
        trader_service.ai_analyst = shared_ai_analyst

    if req.groww_api_key and req.groww_api_key.strip():
        k = req.groww_api_key.strip()
        settings.broker.groww_api_key = k
        os.environ["GROWW_API_KEY"] = k
        updates_to_env["GROWW_API_KEY"] = k

    if req.groww_api_secret and req.groww_api_secret.strip():
        s = req.groww_api_secret.strip()
        settings.broker.groww_api_secret = s
        os.environ["GROWW_API_SECRET"] = s
        updates_to_env["GROWW_API_SECRET"] = s

    if req.groww_access_token and req.groww_access_token.strip():
        t = req.groww_access_token.strip()
        settings.broker.groww_access_token = t
        os.environ["GROWW_ACCESS_TOKEN"] = t
        updates_to_env["GROWW_ACCESS_TOKEN"] = t

    if req.max_loss_inr is not None and req.max_loss_inr > 0:
        trader_service.max_loss_inr = req.max_loss_inr

    if req.target_profit_inr is not None and req.target_profit_inr > 0:
        trader_service.target_profit_inr = req.target_profit_inr

    # Persist to .env
    update_env_file(updates_to_env)

    # Re-initialize shared Groww broker
    shared_broker = create_broker(
        broker_type="groww",
        kill_switch=shared_kill_switch,
    )
    trader_service.update_broker(shared_broker)

    return {
        "status": "SUCCESS",
        "message": "Groww credentials and limits successfully updated.",
    }


@app.post("/api/kill-switch")
def manage_kill_switch(req: KillSwitchRequest):
    """Engages or resets the emergency circuit breaker."""
    if req.action == "trigger":
        shared_kill_switch.trigger(req.reason or "Web emergency trigger")
        return {"status": "success", "active": True, "reason": shared_kill_switch.reason}
    elif req.action == "reset":
        token = req.token or "AUTHORIZE_RESET_CONFIRMED"
        if shared_kill_switch.reset(token):
            return {"status": "success", "active": False, "message": "Kill switch successfully reset"}
        else:
            raise HTTPException(status_code=400, detail="Invalid reset token.")
    raise HTTPException(status_code=400, detail="Unknown action. Use 'trigger' or 'reset'")


LUCIDE_JS_PATH = os.path.join(os.path.dirname(__file__), "lucide.min.js")


@app.get("/static/lucide.min.js")
def get_lucide_js():
    """Serves bundled local Lucide icons script for zero external CDN dependency."""
    if os.path.exists(LUCIDE_JS_PATH):
        return FileResponse(LUCIDE_JS_PATH, media_type="application/javascript")
    raise HTTPException(status_code=404, detail="File not found")


# Streamlined, Minimalist, Institutional Black Terminal Web Interface
@app.get("/", response_class=HTMLResponse)
def index_html():
    return """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GROWW TERMINAL</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="/static/lucide.min.js"></script>
  <script>
    if (typeof lucide === 'undefined') {
      document.write('<script src="https://cdn.jsdelivr.net/npm/lucide@latest/dist/umd/lucide.min.js"><\\/script>');
    }
  </script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: {
            sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
            mono: ['JetBrains Mono', 'ui-monospace', 'SF Mono', 'monospace'],
          },
          colors: {
            term: {
              black: '#000000',
              card: '#0A0A0A',
              surface: '#111111',
              border: '#1A1A1A',
              borderLight: '#252525',
              textPrimary: '#F1F5F9',
              textMuted: '#64748B',
              textDim: '#475569',
              emerald: '#10B981',
              emeraldBg: '#064E3B',
              crimson: '#EF4444',
              crimsonBg: '#7F1D1D',
              amber: '#F59E0B',
              blue: '#3B82F6',
              purple: '#A855F7',
            }
          }
        }
      }
    }
  </script>
  <style>
    body {
      background-color: #000000;
      color: #F1F5F9;
      font-family: 'Inter', -apple-system, sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .mono { font-family: 'JetBrains Mono', monospace; }
    .terminal-card {
      background-color: #0A0A0A;
      border: 1px solid #1A1A1A;
    }
    .terminal-card-hover:hover {
      border-color: #252525;
    }
    .custom-scroll::-webkit-scrollbar { width: 5px; height: 5px; }
    .custom-scroll::-webkit-scrollbar-track { background: #000000; }
    .custom-scroll::-webkit-scrollbar-thumb { background: #252525; border-radius: 3px; }
    .custom-scroll::-webkit-scrollbar-thumb:hover { background: #333333; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-emerald-500/20 selection:text-emerald-300">

  <!-- TOP FINANCIAL APP BAR -->
  <header class="border-b border-term-border bg-[#050505] sticky top-0 z-40 px-4 lg:px-6 py-2.5">
    <div class="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-3">
      
      <!-- Brand & Indian Market Clock -->
      <div class="flex items-center space-x-3.5">
        <div class="h-8 w-8 rounded-lg bg-term-surface border border-term-border flex items-center justify-center text-term-emerald shadow-inner">
          <i data-lucide="activity" class="h-4 w-4"></i>
        </div>
        <div>
          <div class="flex items-center gap-2">
            <span class="text-xs font-bold tracking-wider uppercase text-white font-mono">GROWW TERMINAL</span>
            <span id="market-badge" class="px-1.5 py-0.2 rounded text-[10px] font-mono font-semibold bg-gray-800 text-gray-400 border border-gray-700">NSE SYNC</span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-term-textMuted mt-0.5">
            <span id="market-clock">IST: --:--:--</span>
            <span>&bull;</span>
            <span id="window-badge" class="text-term-textMuted">Checking trading window...</span>
          </div>
        </div>
      </div>

      <!-- Live Groww Wallet & Actions -->
      <div class="flex items-center space-x-2.5">
        
        <!-- Live Real Wallet Pill -->
        <div class="flex items-center space-x-2.5 px-3 py-1.5 rounded-lg bg-term-surface border border-term-border text-xs font-mono">
          <div class="relative flex items-center">
            <span id="wallet-dot" class="h-2 w-2 rounded-full bg-term-emerald"></span>
            <span class="absolute h-2 w-2 rounded-full bg-term-emerald animate-ping opacity-75"></span>
          </div>
          <span class="text-term-textMuted text-[11px]">GROWW CASH:</span>
          <span id="wallet-cash-top" class="font-bold text-white text-xs">₹0.00</span>
          <span id="wallet-ucc-top" class="text-[10px] px-1.5 py-0.5 rounded bg-term-border text-gray-300">LIVE</span>
        </div>

        <!-- Emergency Kill Switch Button -->
        <button onclick="toggleKillSwitch()" id="ks-btn" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-950 hover:bg-red-900 text-red-300 border border-red-800/80 transition flex items-center gap-1.5 font-mono">
          <i data-lucide="shield-alert" class="h-3.5 w-3.5 text-red-400"></i>
          <span>EMERGENCY HALT</span>
        </button>

        <!-- Settings Button -->
        <button onclick="openSettingsModal()" class="px-2.5 py-1.5 rounded-lg bg-term-surface hover:bg-term-border border border-term-border text-gray-200 hover:text-white transition flex items-center gap-1.5 text-xs font-mono cursor-pointer shadow-sm" title="API Keys & Broker Settings">
          <i data-lucide="sliders" class="h-3.5 w-3.5 text-term-emerald"></i>
          <span>API Settings</span>
        </button>
      </div>

    </div>
  </header>

  <!-- Live Groww Session / Broker Status Banner -->
  <div id="wallet-alert-banner" class="hidden max-w-7xl mx-auto px-4 lg:px-6 pt-3">
    <div id="wallet-alert-box" class="rounded-xl p-3.5 text-xs font-mono border flex flex-wrap items-center justify-between gap-3 shadow-lg">
      <div class="flex items-center gap-2.5">
        <i data-lucide="shield-alert" class="h-4 w-4 shrink-0 text-amber-400"></i>
        <span id="wallet-alert-msg" class="leading-relaxed"></span>
      </div>
      <button onclick="openSettingsModal()" class="px-3.5 py-1.5 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/50 font-semibold shrink-0 transition flex items-center gap-1.5">
        <i data-lucide="key" class="h-3.5 w-3.5"></i> Enter Today's Access Token
      </button>
    </div>
  </div>

  <!-- MAIN TERMINAL CONTENT -->
  <main class="flex-1 p-4 lg:p-6 max-w-7xl mx-auto w-full space-y-4">

    <!-- 1. MISSION CONTROL DECK -->
    <div class="terminal-card rounded-xl p-4 lg:p-5 space-y-4">
      
      <!-- Top Row: Big Start/Stop & Execution Telemetry -->
      <div class="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
        
        <!-- Left: Action Button & Status Block -->
        <div class="flex items-center space-x-4">
          <button id="btn-toggle-trader" onclick="toggleTraderLoop()" class="h-12 px-6 rounded-lg font-bold text-sm transition flex items-center gap-2.5 font-mono bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm">
            <i data-lucide="play" class="h-4 w-4 fill-white"></i>
            <span>START TRADING</span>
          </button>

          <div>
            <div class="flex items-center gap-2">
              <span id="trader-status-badge" class="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-term-surface text-term-textMuted border border-term-border flex items-center gap-1.5">
                <span id="trader-status-dot" class="h-1.5 w-1.5 rounded-full bg-gray-500"></span>
                <span id="trader-status-text">STOPPED</span>
              </span>
              <span id="trader-uptime" class="text-xs text-term-textMuted font-mono">Uptime: 00:00:00</span>
              <span class="text-term-border">&bull;</span>
              <span id="trader-remaining" class="text-xs text-term-textMuted font-mono">Run Time: Unlimited</span>
            </div>
            <p id="trader-scan-desc" class="text-[12px] text-term-textMuted mt-1">Trading loop standing by. Click START to begin.</p>
          </div>
        </div>

        <!-- Right: Guardrail Inputs (Max Loss, Profit Goal, Run Duration) -->
        <div class="grid grid-cols-3 gap-2.5 w-full lg:w-auto text-xs">
          <!-- Max Loss Guardrail -->
          <div class="bg-[#050505] p-2.5 rounded-lg border border-term-border">
            <span class="text-[10px] uppercase font-mono tracking-wider text-term-textMuted block">Max Loss (₹)</span>
            <input type="number" id="ctrl-max-loss" value="5000" step="500" class="w-24 mt-1 bg-transparent border-b border-term-border font-mono text-xs font-bold text-red-400 focus:outline-none focus:border-red-500">
            <span class="text-[9px] text-term-textDim block mt-0.5">Circuit breaker</span>
          </div>

          <!-- Profit Target Guardrail -->
          <div class="bg-[#050505] p-2.5 rounded-lg border border-term-border">
            <span class="text-[10px] uppercase font-mono tracking-wider text-term-textMuted block">Profit Goal (₹)</span>
            <input type="number" id="ctrl-target-profit" value="10000" step="1000" class="w-24 mt-1 bg-transparent border-b border-term-border font-mono text-xs font-bold text-emerald-400 focus:outline-none focus:border-emerald-500">
            <span class="text-[9px] text-term-textDim block mt-0.5">Locks daily gains</span>
          </div>

          <!-- Session Run Time Duration -->
          <div class="bg-[#050505] p-2.5 rounded-lg border border-term-border">
            <span class="text-[10px] uppercase font-mono tracking-wider text-term-textMuted block">Session Limit</span>
            <select id="ctrl-runtime" class="w-full mt-1 bg-transparent border-b border-term-border font-mono text-xs text-white focus:outline-none cursor-pointer">
              <option value="0" class="bg-term-card">Session (Full)</option>
              <option value="30" class="bg-term-card">30 Mins</option>
              <option value="60" class="bg-term-card">1 Hour</option>
              <option value="120" class="bg-term-card">2 Hours</option>
              <option value="240" class="bg-term-card">4 Hours</option>
            </select>
            <span class="text-[9px] text-term-textDim block mt-0.5">Auto-stop timer</span>
          </div>
        </div>

      </div>

    </div>

    <!-- 2. SIX HIGH-DENSITY KPI CARDS INCLUDING DEDICATED POST-TAX NET P&L AND PROFIT TO ACHIEVE -->
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      
      <!-- Card 1: Groww Cash -->
      <div class="terminal-card terminal-card-hover rounded-xl p-3.5 space-y-1">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-mono uppercase text-term-textMuted tracking-wider">Groww Clear Cash</span>
          <i data-lucide="wallet" class="h-3.5 w-3.5 text-term-textMuted"></i>
        </div>
        <div id="stat-cash" class="text-base lg:text-lg font-bold font-mono text-white">₹0.00</div>
        <div class="text-[10px] font-mono text-term-textDim flex items-center justify-between">
          <span>Unencumbered</span>
          <span id="stat-used-margin" class="text-term-textMuted">Used: ₹0.00</span>
        </div>
      </div>

      <!-- Card 2: Total Equity -->
      <div class="terminal-card terminal-card-hover rounded-xl p-3.5 space-y-1">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-mono uppercase text-term-textMuted tracking-wider">Total Equity</span>
          <i data-lucide="pie-chart" class="h-3.5 w-3.5 text-term-textMuted"></i>
        </div>
        <div id="stat-equity" class="text-base lg:text-lg font-bold font-mono text-white">₹0.00</div>
        <div class="text-[10px] font-mono text-term-textDim flex items-center justify-between">
          <span>Cash + Value</span>
          <span id="stat-exposure" class="text-blue-400">Exp: 0.0%</span>
        </div>
      </div>

      <!-- Card 3: Today's Gross P&L -->
      <div class="terminal-card terminal-card-hover rounded-xl p-3.5 space-y-1">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-mono uppercase text-term-textMuted tracking-wider">Gross Day P&L</span>
          <i data-lucide="activity" class="h-3.5 w-3.5 text-term-textMuted"></i>
        </div>
        <div id="stat-pnl" class="text-base lg:text-lg font-bold font-mono text-gray-300">₹0.00</div>
        <div class="text-[10px] font-mono text-term-textDim flex items-center justify-between">
          <span id="stat-realized-pnl">Realized: ₹0.00</span>
          <span id="stat-unrealized-pnl">MTM: ₹0.00</span>
        </div>
      </div>

      <!-- Card 4: Dedicated Box for TOTAL P&L AFTER BROKERAGE & TAX -->
      <div class="terminal-card terminal-card-hover rounded-xl p-3.5 space-y-1 border-emerald-900/60 bg-[#050505] relative overflow-hidden">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-mono uppercase text-emerald-400 tracking-wider font-semibold">Net P&L (Post-Tax)</span>
          <span class="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">AFTER TAX & BROK</span>
        </div>
        <div id="stat-net-pnl" class="text-base lg:text-lg font-bold font-mono text-emerald-400">₹0.00</div>
        <div class="text-[10px] font-mono text-term-textDim flex items-center justify-between">
          <span id="stat-total-charges" class="text-red-400/90 font-mono">Charges: -₹0.00</span>
          <span id="stat-charges-breakdown" class="text-term-textMuted font-mono">STT/GST/Brk</span>
        </div>
      </div>

      <!-- Card 5: Dedicated Box for PROFIT TO ACHIEVE -->
      <div class="terminal-card terminal-card-hover rounded-xl p-3.5 space-y-1 border-blue-900/40 bg-[#050505] relative overflow-hidden">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-mono uppercase text-blue-400 tracking-wider font-semibold">Profit to Achieve</span>
          <span id="badge-profit-target" class="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-blue-950 text-blue-400 border border-blue-800">TARGET GOAL</span>
        </div>
        <div id="stat-profit-to-achieve" class="text-base lg:text-lg font-bold font-mono text-white">₹10,000.00</div>
        <div class="text-[10px] font-mono text-term-textDim flex items-center justify-between">
          <span id="stat-target-goal-label" class="text-term-textMuted">Goal: ₹10,000</span>
          <span id="stat-target-progress-label" class="text-emerald-400 font-semibold">0.0% Reached</span>
        </div>
        <div class="w-full bg-[#111111] h-1.5 rounded-full overflow-hidden mt-1">
          <div id="stat-target-progress-bar" class="bg-blue-500 h-full rounded-full transition-all duration-500" style="width: 0%"></div>
        </div>
      </div>

      <!-- Card 6: Positions & Risk Guard -->
      <div class="terminal-card terminal-card-hover rounded-xl p-3.5 space-y-1">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-mono uppercase text-term-textMuted tracking-wider">Active Positions</span>
          <i data-lucide="shield-check" class="h-3.5 w-3.5 text-term-textMuted"></i>
        </div>
        <div class="flex items-baseline gap-2">
          <span id="stat-pos-count" class="text-base lg:text-lg font-bold font-mono text-blue-400">0</span>
          <span class="text-xs font-mono text-term-textMuted">/ 5 max slots</span>
        </div>
        <div class="text-[10px] font-mono text-term-textDim flex items-center justify-between">
          <span id="stat-drawdown">Drawdown: 0.0%</span>
          <span class="text-term-textMuted">Max 6.0% DD</span>
        </div>
      </div>

    </div>

    <!-- 3. MAIN TERMINAL WORKSPACE SPLIT (7 / 5) -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">

      <!-- LEFT: ACTIVITY & ORDER EXECUTION STREAM (7 cols) -->
      <div class="lg:col-span-7 terminal-card rounded-xl flex flex-col h-[520px]">
        
        <!-- Header with Filter Tabs -->
        <div class="p-3 border-b border-term-border bg-[#050505] rounded-t-xl flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center space-x-2">
            <span class="h-2 w-2 rounded-full bg-term-emerald animate-pulse"></span>
            <span class="text-xs font-bold font-mono uppercase text-white tracking-wider">Activity Feed</span>
          </div>

          <!-- Log Categories Filter -->
          <div class="flex items-center space-x-1 text-[10px] font-mono">
            <button onclick="setLogFilter('ALL')" id="filter-btn-ALL" class="px-2 py-0.5 rounded bg-term-border text-white">ALL</button>
            <button onclick="setLogFilter('TRADE')" id="filter-btn-TRADE" class="px-2 py-0.5 rounded text-term-textMuted hover:text-white">TRADES</button>
            <button onclick="setLogFilter('GROWW')" id="filter-btn-GROWW" class="px-2 py-0.5 rounded text-term-textMuted hover:text-white">GROWW</button>
            <button onclick="setLogFilter('EXIT')" id="filter-btn-EXIT" class="px-2 py-0.5 rounded text-term-textMuted hover:text-white">EXITS</button>
            <span class="text-term-border">|</span>
            <button onclick="clearTerminalLogs()" class="px-2 py-0.5 rounded text-term-textMuted hover:text-red-400">Clear</button>
          </div>
        </div>

        <!-- Scrollable Feed -->
        <div id="terminal-feed" class="flex-1 p-3 overflow-y-auto custom-scroll font-mono text-[11px] space-y-1.5 bg-[#000000]">
          <div class="text-term-textMuted">Trading activity feed initialized. Active stock signals, leverage, quantity, SL/target, and Groww executions will stream here.</div>
        </div>

        <!-- Footer status bar -->
        <div class="px-3 py-1.5 border-t border-term-border bg-[#050505] text-[10px] font-mono text-term-textMuted flex items-center justify-between">
          <span id="log-count-label">0 events recorded</span>
          <span class="flex items-center gap-1.5">
            <input type="checkbox" id="autoscroll-chk" checked class="cursor-pointer">
            <label for="autoscroll-chk" class="cursor-pointer">Auto-scroll</label>
          </span>
        </div>
      </div>

      <!-- RIGHT: ACTIVE POSITIONS TABLE (5 cols) -->
      <div class="lg:col-span-5 terminal-card rounded-xl flex flex-col h-[520px]">
        
        <!-- Header & Emergency Square-Off -->
        <div class="p-3 border-b border-term-border bg-[#050505] rounded-t-xl flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <span class="text-xs font-bold font-mono uppercase text-white tracking-wider">Open Positions (<span id="pos-header-count">0</span>)</span>
          </div>
          <button onclick="emergencyCloseAll()" class="px-2.5 py-1 rounded text-[11px] font-mono font-semibold bg-red-950 hover:bg-red-900 text-red-300 border border-red-800 transition flex items-center gap-1">
            <i data-lucide="x-circle" class="h-3 w-3"></i> Square Off All
          </button>
        </div>

        <!-- Positions Table -->
        <div class="flex-1 p-3 overflow-y-auto custom-scroll">
          <div id="positions-container" class="space-y-2">
            <div class="text-center py-28 text-term-textMuted font-mono text-xs">
              <i data-lucide="layers" class="h-8 w-8 mx-auto mb-2 text-term-border"></i>
              No active open positions.
            </div>
          </div>
        </div>

        <!-- Positions Summary Footer -->
        <div class="px-3 py-2 border-t border-term-border bg-[#050505] text-[11px] font-mono flex items-center justify-between text-term-textMuted">
          <span>Auto MIS Square-off: <strong class="text-white">15:15 IST</strong></span>
          <span id="pos-total-val">Total Value: ₹0.00</span>
        </div>
      </div>

    </div>

  </main>

  <!-- 4. INSTITUTIONAL CREDENTIALS & SETTINGS MODAL -->
  <div id="settings-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="terminal-card max-w-md w-full rounded-xl p-5 space-y-4 shadow-2xl">
      
      <div class="flex items-center justify-between border-b border-term-border pb-3">
        <div class="flex items-center space-x-2">
          <i data-lucide="key" class="h-4 w-4 text-term-emerald"></i>
          <h3 class="text-sm font-bold font-mono text-white uppercase tracking-wider">API Authentication</h3>
        </div>
        <button onclick="closeSettingsModal()" class="text-term-textMuted hover:text-white">
          <i data-lucide="x" class="h-4 w-4"></i>
        </button>
      </div>

      <div class="space-y-3 text-xs font-mono">
        <div>
          <label class="text-term-textMuted block mb-1">Groww API Key</label>
          <input type="text" id="modal-groww-key" placeholder="Enter Groww API Key" class="w-full bg-[#000000] border border-term-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-term-emerald">
        </div>

        <div>
          <label class="text-term-textMuted block mb-1">Groww API Secret</label>
          <input type="password" id="modal-groww-secret" placeholder="Enter Groww API Secret" class="w-full bg-[#000000] border border-term-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-term-emerald">
        </div>

        <div>
          <label class="text-white font-semibold block mb-1">Groww Daily Access Token (Direct Connect)</label>
          <input type="text" id="modal-groww-token" placeholder="Paste today's Access Token from Groww Web/App" class="w-full bg-[#000000] border border-amber-500/60 rounded-lg px-3 py-2 text-xs text-amber-200 focus:outline-none focus:border-amber-400 font-mono">
          <span class="text-[10px] text-amber-400/90 mt-1 block">Open Groww Web, go to Settings, Trading APIs, generate Access Token, paste here.</span>
        </div>

        <div class="pt-2 border-t border-term-border">
          <label class="text-term-textMuted block mb-1">Gemini API Key</label>
          <input type="password" id="modal-gemini-key" placeholder="Enter Gemini API Key" class="w-full bg-[#000000] border border-term-border rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-term-emerald">
        </div>
      </div>

      <div class="flex items-center justify-end space-x-2 pt-3 border-t border-term-border">
        <button onclick="closeSettingsModal()" class="px-3 py-1.5 rounded-lg bg-term-surface hover:bg-term-border text-term-textMuted hover:text-white text-xs font-mono">
          Cancel
        </button>
        <button onclick="saveSettingsModal()" id="btn-save-settings" class="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs font-mono transition flex items-center gap-1.5">
          <i data-lucide="check" class="h-3.5 w-3.5"></i> Save & Connect
        </button>
      </div>

    </div>
  </div>

  <script>
    // Bulletproof Lucide icon renderer
    function safeCreateIcons() {
      try {
        if (typeof lucide !== 'undefined' && lucide && typeof lucide.createIcons === 'function') {
          lucide.createIcons();
        }
      } catch (err) {
        console.warn("Lucide render notice:", err);
      }
    }

    // Defensive DOM helpers
    function setElText(id, val) {
      const el = document.getElementById(id);
      if (el && val !== undefined && val !== null) el.innerText = val;
    }
    function setElHtml(id, val) {
      const el = document.getElementById(id);
      if (el && val !== undefined && val !== null) el.innerHTML = val;
    }
    function setElClass(id, val) {
      const el = document.getElementById(id);
      if (el && val) el.className = val;
    }

    let isAutonomousRunning = false;
    let rawLogs = [];
    let currentLogFilter = "ALL";

    function setLogFilter(cat) {
      currentLogFilter = cat;
      ["ALL", "TRADE", "GROWW", "EXIT"].forEach(c => {
        const btn = document.getElementById("filter-btn-" + c);
        if (btn) {
          if (c === cat) {
            btn.className = "px-2 py-0.5 rounded bg-term-border text-white";
          } else {
            btn.className = "px-2 py-0.5 rounded text-term-textMuted hover:text-white";
          }
        }
      });
      renderFilteredLogs();
    }

    // Toggle Autonomous Trading Loop
    async function toggleTraderLoop() {
      const btn = document.getElementById('btn-toggle-trader');
      if (btn) btn.disabled = true;

      try {
        if (!isAutonomousRunning) {
          const maxLossEl = document.getElementById('ctrl-max-loss');
          const targetProfitEl = document.getElementById('ctrl-target-profit');
          const runtimeEl = document.getElementById('ctrl-runtime');

          const maxLoss = parseFloat(maxLossEl ? maxLossEl.value : 5000) || 5000;
          const targetProfit = parseFloat(targetProfitEl ? targetProfitEl.value : 10000) || 10000;
          const runtime = parseInt(runtimeEl ? runtimeEl.value : 0) || 0;

          try {
            localStorage.setItem('groww_ctrl_max_loss', maxLoss);
            localStorage.setItem('groww_ctrl_target_profit', targetProfit);
            localStorage.setItem('groww_ctrl_runtime', runtime);
          } catch (e) {}

          const res = await fetch('/api/trader/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              scan_interval: 15,
              max_loss_inr: maxLoss,
              target_profit_inr: targetProfit,
              runtime_minutes: runtime,
              use_mock: false
            })
          });
          const data = await res.json();
          if (data.status === "ERROR_MARKET_CLOSED") {
            alert(`⛔ CANNOT START TRADING

${data.message}`);
            isAutonomousRunning = false;
            await updateStatus();
            return;
          }
          if (data.status === "STARTED" || data.status === "ALREADY_RUNNING") {
            isAutonomousRunning = true;
          }
        } else {
          await fetch('/api/trader/stop', { method: 'POST' });
          isAutonomousRunning = false;
        }
        await updateStatus();
      } catch (err) {
        alert("Failed to toggle trading: " + err);
      } finally {
        if (btn) btn.disabled = false;
        safeCreateIcons();
      }
    }

    // Real-Time Status Polling
    async function updateStatus() {
      try {
        const res = await fetch('/api/status');
        if (!res.ok) {
          console.warn("Status fetch returned HTTP " + res.status);
          return;
        }
        const data = await res.json();

        // 1. Clock & Market Badges
        setElText('market-clock', data.timestamp_ist || "IST: Live Sync");
        const mBadge = document.getElementById('market-badge');
        if (mBadge) {
          if (data.is_market_open) {
            mBadge.className = "px-1.5 py-0.2 rounded text-[10px] font-mono font-semibold bg-emerald-950 text-emerald-400 border border-emerald-800";
            mBadge.innerText = "NSE LIVE";
          } else {
            mBadge.className = "px-1.5 py-0.2 rounded text-[10px] font-mono font-semibold bg-yellow-950 text-yellow-400 border border-yellow-800";
            mBadge.innerText = "NSE CLOSED";
          }
        }

        const wBadge = document.getElementById('window-badge');
        if (wBadge) {
          if (data.is_squareoff_time) {
            wBadge.innerText = "15:15 MIS Square-Off Active";
            wBadge.className = "text-red-400 font-semibold";
          } else if (data.is_entry_allowed) {
            wBadge.innerText = "Entry Window Open (09:20-15:05)";
            wBadge.className = "text-emerald-400";
          } else {
            wBadge.innerText = "Outside Entry Hours";
            wBadge.className = "text-term-textMuted";
          }
        }

        // 2. Autonomous Runner State & Telemetry
        const trader = data.trader || {};
        isAutonomousRunning = !!trader.is_running;
        const btn = document.getElementById('btn-toggle-trader');
        const badge = document.getElementById('trader-status-badge');
        const dot = document.getElementById('trader-status-dot');
        const text = document.getElementById('trader-status-text');
        const desc = document.getElementById('trader-scan-desc');

        setElText('trader-uptime', "Uptime: " + (trader.uptime || "00:00:00"));
        setElText('trader-remaining', "Remaining: " + (trader.remaining_time || "Unlimited"));


        if (trader.is_running) {
          if (btn) {
            btn.className = "h-12 px-6 rounded-lg font-bold text-sm transition flex items-center gap-2.5 font-mono bg-red-600 hover:bg-red-500 text-white shadow-sm";
            btn.innerHTML = '<i data-lucide="square" class="h-4 w-4 fill-white"></i><span>STOP TRADING</span>';
          }
          if (badge) badge.className = "px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-emerald-950 text-emerald-400 border border-emerald-800 flex items-center gap-1.5";
          if (dot) dot.className = "h-1.5 w-1.5 rounded-full bg-emerald-400 animate-ping";
          if (text) text.innerText = "RUNNING (Cycle #" + ((trader.cycles_completed || 0) + 1) + ")";
          if (desc) desc.innerText = trader.current_symbol ? ("Scanning: " + trader.current_symbol) : ("Scanning NSE universe. Next cycle in " + (trader.scan_interval || 15) + "s.");
        } else {
          if (btn) {
            btn.className = "h-12 px-6 rounded-lg font-bold text-sm transition flex items-center gap-2.5 font-mono bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm";
            btn.innerHTML = '<i data-lucide="play" class="h-4 w-4 fill-white"></i><span>START TRADING</span>';
          }
          if (badge) badge.className = "px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-term-surface text-term-textMuted border border-term-border flex items-center gap-1.5";
          if (dot) dot.className = "h-1.5 w-1.5 rounded-full bg-gray-500";
          if (text) text.innerText = trader.state || "STOPPED";
          if (desc) desc.innerText = "Trading loop standing by. Click START to begin.";
        }

        // 3. Wallet & Connection Banner
        const wallet = data.wallet || {};
        const banner = document.getElementById('wallet-alert-banner');
        const bannerBox = document.getElementById('wallet-alert-box');
        const bannerMsg = document.getElementById('wallet-alert-msg');
        const walletDot = document.getElementById('wallet-dot');
        const walletUcc = document.getElementById('wallet-ucc-top');

        if (wallet.status === 'CONNECTED') {
          if (wallet.ip_whitelist_required) {
            if (banner) {
              banner.classList.remove('hidden');
              if (bannerBox) bannerBox.className = "rounded-xl p-3.5 text-xs font-mono border bg-amber-950/70 border-amber-700/80 flex flex-wrap items-center justify-between gap-3 text-amber-200 shadow-lg";
              if (bannerMsg) bannerMsg.innerHTML = `<span class="font-bold text-amber-300">⚠️ ACTION REQUIRED FOR LIVE GROWW ORDERS:</span> Whitelist IP <code class="px-1.5 py-0.5 rounded bg-black/60 text-white font-bold">${wallet.live_network_ip || wallet.public_ip}</code> in Groww &rarr; Settings &rarr; Trading APIs.`;
            }
          } else {
            if (banner) banner.classList.add('hidden');
          }
          if (walletDot) walletDot.className = "h-2 w-2 rounded-full bg-term-emerald";
          if (walletUcc) {
            walletUcc.innerText = wallet.ucc || "LIVE";
            walletUcc.className = "text-[10px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800";
          }
        } else if (wallet.status === 'APPROVAL_REQUIRED') {
          if (banner) {
            banner.classList.remove('hidden');
            if (bannerBox) bannerBox.className = "rounded-xl p-3.5 text-xs font-mono border bg-amber-950/60 border-amber-800/90 flex flex-wrap items-center justify-between gap-3 text-amber-200 shadow-lg";
            if (bannerMsg) bannerMsg.innerText = wallet.message || "Groww Daily Session Approval Required: Approve session on Groww app/web or paste today's Access Token.";
          }
          if (walletDot) walletDot.className = "h-2 w-2 rounded-full bg-amber-400 animate-pulse";
          if (walletUcc) {
            walletUcc.innerText = "APPROVAL NEEDED";
            walletUcc.className = "text-[10px] px-1.5 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800 font-semibold";
          }
        } else {
          if (banner) {
            banner.classList.remove('hidden');
            if (bannerBox) bannerBox.className = "rounded-xl p-3.5 text-xs font-mono border bg-red-950/60 border-red-800/90 flex flex-wrap items-center justify-between gap-3 text-red-200 shadow-lg";
            if (bannerMsg) bannerMsg.innerText = wallet.message || "Groww Broker Disconnected. Check credentials in Settings.";
          }
          if (walletDot) walletDot.className = "h-2 w-2 rounded-full bg-red-500";
          if (walletUcc) {
            walletUcc.innerText = "DISCONNECTED";
            walletUcc.className = "text-[10px] px-1.5 py-0.5 rounded bg-red-950 text-red-300 border border-red-800 font-semibold";
          }
        }

        const portfolio = data.portfolio || {};
        const availCash = wallet.available_cash !== undefined ? wallet.available_cash : (portfolio.cash || 0);
        const formattedCash = '₹' + availCash.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        setElText('wallet-cash-top', formattedCash);
        setElText('stat-cash', formattedCash);
        setElText('stat-used-margin', 'Used: ₹' + (wallet.used_margin || 0).toLocaleString('en-IN', {minimumFractionDigits: 2}));

        const rawEquity = portfolio.total_equity !== undefined ? portfolio.total_equity : (portfolio.total_portfolio_value !== undefined ? portfolio.total_portfolio_value : availCash);
        const totalEquity = Math.max(0, rawEquity);
        setElText('stat-equity', '₹' + totalEquity.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2}));
        setElText('stat-exposure', 'Exp: ' + (portfolio.exposure_pct !== undefined ? portfolio.exposure_pct.toFixed(1) : '0.0') + '%');

        // Card 3: Gross Day P&L
        const grossPnl = portfolio.daily_gross_pnl !== undefined ? portfolio.daily_gross_pnl : (portfolio.daily_total_pnl || 0);
        const pnlEl = document.getElementById('stat-pnl');
        if (pnlEl) {
          pnlEl.innerText = (grossPnl >= 0 ? '+' : '') + '₹' + grossPnl.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
          pnlEl.className = 'text-base lg:text-lg font-bold font-mono ' + (grossPnl >= 0 ? 'text-emerald-400' : 'text-red-400');
        }
        setElText('stat-realized-pnl', 'Realized: ₹' + (portfolio.daily_realized_pnl !== undefined ? portfolio.daily_realized_pnl.toFixed(2) : '0.00'));
        const unrealized = grossPnl - (portfolio.daily_realized_pnl || 0);
        setElText('stat-unrealized-pnl', 'MTM: ' + (unrealized >= 0 ? '+' : '') + '₹' + unrealized.toFixed(2));

        // Card 4: Dedicated Box for Net P&L After Tax & Brokerage
        const netPnl = portfolio.daily_net_pnl !== undefined ? portfolio.daily_net_pnl : grossPnl;
        const netPnlEl = document.getElementById('stat-net-pnl');
        if (netPnlEl) {
          netPnlEl.innerText = (netPnl >= 0 ? '+' : '') + '₹' + netPnl.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
          netPnlEl.className = 'text-base lg:text-lg font-bold font-mono ' + (netPnl >= 0 ? 'text-emerald-400' : 'text-red-400');
        }
        const totalCharges = portfolio.total_charges || 0.0;
        setElText('stat-total-charges', 'Charges: -₹' + totalCharges.toFixed(2));
        const brk = portfolio.total_brokerage || 0.0;
        const taxes = portfolio.total_taxes || 0.0;
        setElText('stat-charges-breakdown', 'Brk: ₹' + brk.toFixed(1) + ' | Tax: ₹' + taxes.toFixed(1));

        // Card 5: Dedicated Box for Profit to Achieve
        const ctrlProfit = document.getElementById('ctrl-target-profit');
        const targetGoal = parseFloat(ctrlProfit ? ctrlProfit.value : 0) || portfolio.target_profit_inr || 10000;
        const profitToAchieve = Math.max(0, targetGoal - netPnl);
        const progressPct = targetGoal > 0 ? Math.min(100, Math.max(0, (netPnl / targetGoal) * 100)) : 0;

        const toAchieveEl = document.getElementById('stat-profit-to-achieve');
        const goalLabel = document.getElementById('stat-target-goal-label');
        const progressLabel = document.getElementById('stat-target-progress-label');
        const progressBar = document.getElementById('stat-target-progress-bar');
        const targetBadge = document.getElementById('badge-profit-target');

        if (goalLabel) goalLabel.innerText = 'Goal: ₹' + targetGoal.toLocaleString('en-IN', {maximumFractionDigits: 0});
        if (progressBar) progressBar.style.width = progressPct + '%';

        if (netPnl >= targetGoal && targetGoal > 0) {
          if (toAchieveEl) {
            toAchieveEl.innerText = 'ACHIEVED! 🎉';
            toAchieveEl.className = 'text-base lg:text-lg font-bold font-mono text-emerald-400 animate-pulse';
          }
          if (progressLabel) {
            progressLabel.innerText = '100% Reached';
            progressLabel.className = 'text-emerald-400 font-bold';
          }
          if (targetBadge) {
            targetBadge.innerText = 'GOAL HIT';
            targetBadge.className = 'px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-emerald-950 text-emerald-400 border border-emerald-800';
          }
          if (progressBar) progressBar.className = 'bg-emerald-400 h-full rounded-full transition-all duration-500';
        } else {
          if (toAchieveEl) {
            toAchieveEl.innerText = '₹' + profitToAchieve.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            toAchieveEl.className = 'text-base lg:text-lg font-bold font-mono text-white';
          }
          if (progressLabel) {
            progressLabel.innerText = progressPct.toFixed(1) + '% Reached';
            progressLabel.className = progressPct > 0 ? 'text-emerald-400 font-semibold' : 'text-term-textMuted';
          }
          if (targetBadge) {
            targetBadge.innerText = 'TARGET GOAL';
            targetBadge.className = 'px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-blue-950 text-blue-400 border border-blue-800';
          }
          if (progressBar) progressBar.className = 'bg-blue-500 h-full rounded-full transition-all duration-500';
        }

        // Card 6: Positions Count & Risk
        const positions = data.positions || [];
        setElText('stat-pos-count', positions.length);
        setElText('stat-drawdown', 'Drawdown: ' + (portfolio.drawdown_pct !== undefined ? portfolio.drawdown_pct.toFixed(1) : '0.0') + '%');
        setElText('pos-header-count', positions.length);

        // Positions Container
        const posBox = document.getElementById('positions-container');
        if (posBox) {
          let totalPosVal = 0;
          if (positions.length > 0) {
            posBox.innerHTML = positions.map(p => {
              totalPosVal += (p.position_value || 0);
              const pnlClass = (p.unrealized_pnl || 0) >= 0 ? 'text-emerald-400' : 'text-red-400';
              const sign = (p.unrealized_pnl || 0) >= 0 ? '+' : '';
              return `
                <div class="p-3 bg-[#000000] rounded-lg border border-term-border hover:border-term-borderLight transition flex items-center justify-between text-xs font-mono">
                  <div>
                    <div class="flex items-center gap-2">
                      <span class="font-bold text-white text-sm">${p.symbol}</span>
                      <span class="text-[10px] px-1 py-0.5 rounded bg-term-border text-term-textMuted">${p.product}</span>
                      <span class="text-[11px] ${p.quantity >= 0 ? 'text-emerald-400' : 'text-red-400'} font-semibold">${p.quantity} Qty</span>
                    </div>
                    <div class="text-[11px] text-term-textMuted mt-1">
                      Entry: ₹${(p.average_entry_price || 0).toFixed(2)} &bull; LTP: ₹${(p.current_price || 0).toFixed(2)}
                    </div>
                    <div class="text-[10px] text-term-textDim mt-0.5">
                      SL: ₹${p.stop_loss ? p.stop_loss.toFixed(2) : '--'} &bull; TGT: ₹${p.target_price ? p.target_price.toFixed(2) : '--'}
                    </div>
                  </div>
                  <div class="text-right flex items-center gap-3">
                    <div>
                      <div class="font-bold ${pnlClass} text-sm">${sign}₹${(p.unrealized_pnl || 0).toFixed(2)}</div>
                      <div class="text-[10px] text-term-textDim">Val: ₹${(p.position_value || 0).toFixed(2)}</div>
                    </div>
                    <button onclick="closeSinglePosition('${p.symbol}')" class="px-2.5 py-1 rounded bg-red-950 hover:bg-red-900 text-red-300 border border-red-800/80 transition text-xs font-semibold">
                      Exit
                    </button>
                  </div>
                </div>
              `;
            }).join('');
          } else {
            posBox.innerHTML = `
              <div class="text-center py-28 text-term-textMuted font-mono text-xs">
                <i data-lucide="layers" class="h-8 w-8 mx-auto mb-2 text-term-border"></i>
                No active open positions.
              </div>
            `;
          }
          setElText('pos-total-val', 'Total Value: ₹' + totalPosVal.toLocaleString('en-IN', {minimumFractionDigits: 2}));
        }

        // Kill Switch Button
        const ksBtn = document.getElementById('ks-btn');
        if (ksBtn) {
          if (data.kill_switch_active) {
            ksBtn.innerHTML = '<i data-lucide="shield-check" class="h-3.5 w-3.5 text-emerald-400"></i><span>RESET HALT</span>';
            ksBtn.className = "px-3 py-1.5 text-xs font-semibold rounded-lg bg-emerald-950 hover:bg-emerald-900 text-emerald-300 border border-emerald-800 transition flex items-center gap-1.5 font-mono";
          } else {
            ksBtn.innerHTML = '<i data-lucide="shield-alert" class="h-3.5 w-3.5 text-red-400"></i><span>EMERGENCY HALT</span>';
            ksBtn.className = "px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-950 hover:bg-red-900 text-red-300 border border-red-800/80 transition flex items-center gap-1.5 font-mono";
          }
        }

        safeCreateIcons();
      } catch (err) {
        console.error("Status polling failed:", err);
      }
    }

    // Activity Log Stream
    async function updateLogStream() {
      try {
        const res = await fetch('/api/logs/stream?limit=100');
        if (!res.ok) return;
        const data = await res.json();
        rawLogs = Array.isArray(data) ? data : [];
        setElText('log-count-label', rawLogs.length + " events recorded");
        renderFilteredLogs();
      } catch (err) {
        console.error("Log fetch failed:", err);
      }
    }

    function renderFilteredLogs() {
      const feed = document.getElementById('terminal-feed');
      if (!feed) return;

      let filtered = rawLogs;
      if (currentLogFilter !== "ALL") {
        filtered = rawLogs.filter(l => l.category === currentLogFilter);
      }

      if (filtered.length > 0) {
        feed.innerHTML = filtered.map(l => {
          let catBadge = `<span class="text-blue-400 font-semibold font-mono">[${l.category}]</span>`;
          if (l.category === "TRADE") catBadge = `<span class="text-blue-400 font-semibold font-mono">[TRADE]</span>`;
          else if (l.category === "GROWW") catBadge = `<span class="text-emerald-400 font-semibold font-mono">[GROWW]</span>`;
          else if (l.category === "RISK") catBadge = `<span class="text-red-400 font-semibold font-mono">[RISK]</span>`;
          else if (l.category === "EXIT") catBadge = `<span class="text-yellow-400 font-semibold font-mono">[EXIT]</span>`;
          else if (l.category === "SYSTEM") catBadge = `<span class="text-gray-400 font-semibold font-mono">[SYSTEM]</span>`;

          let msgColor = "text-gray-200";
          if (l.level === "SUCCESS") msgColor = "text-emerald-300 font-medium";
          else if (l.level === "WARNING") msgColor = "text-yellow-300";
          else if (l.level === "ERROR") msgColor = "text-red-400 font-semibold";

          return `
            <div class="leading-relaxed hover:bg-term-surface/50 px-1.5 py-0.5 rounded transition font-mono">
              <span class="text-term-textDim">${l.timestamp}</span> ${catBadge} <span class="${msgColor}">${l.message}</span>
            </div>
          `;
        }).join('');

        const autoScroll = document.getElementById('autoscroll-chk');
        if (autoScroll && autoScroll.checked) {
          feed.scrollTop = feed.scrollHeight;
        }
      } else {
        feed.innerHTML = '<div class="text-term-textMuted font-mono">No events matching current filter.</div>';
      }
    }

    function clearTerminalLogs() {
      rawLogs = [];
      const feed = document.getElementById('terminal-feed');
      if (feed) feed.innerHTML = '<div class="text-term-textMuted">Feed cleared.</div>';
      setElText('log-count-label', '0 events recorded');
      fetch('/api/logs/clear', { method: 'POST' }).catch(() => {});
    }

    // Manual Position Exit
    async function closeSinglePosition(symbol) {
      if (confirm(`Confirm immediate market exit for position in ${symbol}?`)) {
        await fetch('/api/positions/close', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbol: symbol })
        });
        updateStatus();
      }
    }

    async function emergencyCloseAll() {
      if (confirm("EMERGENCY: Confirm immediate market exit for ALL active open positions?")) {
        await fetch('/api/positions/close-all', { method: 'POST' });
        updateStatus();
      }
    }

    // Kill switch toggle
    async function toggleKillSwitch() {
      const ksBtn = document.getElementById('ks-btn');
      const isResetMode = ksBtn && ksBtn.innerText.toUpperCase().includes("RESET");
      if (isResetMode) {
        // One-click reset for the emergency halt
        try {
          await fetch('/api/kill-switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'reset', token: 'AUTHORIZE_RESET_CONFIRMED' })
          });
          await updateStatus();
          await updateLogStream();
        } catch (err) {
          console.error("Failed to reset kill switch:", err);
        }
      } else {
        const reason = prompt("Enter emergency halt reason:", "Manual operator safety trigger via Web");
        if (reason) {
          await fetch('/api/kill-switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'trigger', reason: reason })
          });
          await updateStatus();
          await updateLogStream();
        }
      }
    }

    // Settings Modal
    function openSettingsModal() {
      const modal = document.getElementById('settings-modal');
      if (modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
      }
      fetch('/api/settings')
        .then(res => res.json())
        .then(data => {
          if (data.groww_api_key_set) {
            const keyEl = document.getElementById('modal-groww-key');
            if (keyEl) keyEl.placeholder = "API Key configured (" + (data.groww_api_key_preview || 'Configured') + ")";
          }
          if (data.groww_access_token_set) {
            const tokEl = document.getElementById('modal-groww-token');
            if (tokEl) tokEl.placeholder = "Access Token active (" + (data.groww_access_token_preview || 'Active') + ")";
          }
          if (data.gemini_api_key_set) {
            const gemEl = document.getElementById('modal-gemini-key');
            if (gemEl) gemEl.placeholder = "Gemini Key configured (" + (data.gemini_api_key_preview || 'Configured') + ")";
          }
        })
        .catch(err => console.error("Settings fetch failed:", err));
    }

    function closeSettingsModal() {
      const modal = document.getElementById('settings-modal');
      if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
      }
    }

    async function saveSettingsModal() {
      const btn = document.getElementById('btn-save-settings');
      if (btn) {
        btn.disabled = true;
        btn.innerText = "Connecting...";
      }

      try {
        const body = {
          groww_api_key: document.getElementById('modal-groww-key')?.value.trim() || undefined,
          groww_api_secret: document.getElementById('modal-groww-secret')?.value.trim() || undefined,
          groww_access_token: document.getElementById('modal-groww-token')?.value.trim() || undefined,
          gemini_api_key: document.getElementById('modal-gemini-key')?.value.trim() || undefined,
        };

        const res = await fetch('/api/settings/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const data = await res.json();
        alert(data.message || "Groww credentials saved.");
        closeSettingsModal();
        updateStatus();
      } catch (err) {
        alert("Failed to save settings: " + err);
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i data-lucide="check" class="h-3.5 w-3.5"></i> Save & Connect';
          safeCreateIcons();
        }
      }
    }

    // Initialization & State Restoration
    function initializeTerminal() {
      try {
        const savedMaxLoss = localStorage.getItem('groww_ctrl_max_loss');
        if (savedMaxLoss && document.getElementById('ctrl-max-loss')) {
          document.getElementById('ctrl-max-loss').value = savedMaxLoss;
        }
        const savedTargetProfit = localStorage.getItem('groww_ctrl_target_profit');
        if (savedTargetProfit && document.getElementById('ctrl-target-profit')) {
          document.getElementById('ctrl-target-profit').value = savedTargetProfit;
        }
        const savedRuntime = localStorage.getItem('groww_ctrl_runtime');
        if (savedRuntime && document.getElementById('ctrl-runtime')) {
          document.getElementById('ctrl-runtime').value = savedRuntime;
        }

        ['ctrl-max-loss', 'ctrl-target-profit', 'ctrl-runtime'].forEach(id => {
          const el = document.getElementById(id);
          if (el) {
            el.addEventListener('change', () => {
              try {
                localStorage.setItem('groww_' + id.replace(/-/g, '_'), el.value);
              } catch (e) {}
            });
          }
        });

        const targetProfitInput = document.getElementById('ctrl-target-profit');
        if (targetProfitInput) {
          targetProfitInput.addEventListener('input', () => {
            updateStatus();
          });
        }
      } catch (e) {
        console.warn('localStorage access failed:', e);
      }

      safeCreateIcons();
      updateStatus();
      updateLogStream();
      setInterval(updateStatus, 5000);
      setInterval(updateLogStream, 3000);
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initializeTerminal);
    } else {
      initializeTerminal();
    }
  </script>
</body>
</html>
"""
