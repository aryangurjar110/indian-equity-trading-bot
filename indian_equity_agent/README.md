# AI-Powered Algorithmic Trading Agent for Indian Equities

A production-grade, capital-preservation-first algorithmic trading framework specifically built for **Indian Equities (NSE/BSE)**.

---

## Non-Negotiable Safety Principles

1. **Capital Preservation Above All Else**: The primary directive of this system is to protect capital.
2. **Never Assume Profit Is Guaranteed**: All trading carries market risk. The system never claims or assumes guaranteed returns or "zero-loss" outcomes.
3. **No Trade Under Uncertainty**: The default and safest state when indicators or market conditions are ambiguous is **NO TRADE (HOLD)**.
4. **Independent Deterministic Risk Engine**: The AI analysis engine (Gemini) serves purely as an advisory signal generator. The Risk Engine has **absolute deterministic veto power** that no AI output can bypass.
5. **No Martingale / Revenge Trading**: The system never increases position size to recover losses. Position sizing dynamically contracts during drawdowns (anti-martingale).
6. **Strict Regulatory & Tax Friction Modeling**: Evaluates trades after real Indian statutory costs (STT, Stamp Duty, NSE turnover fees, SEBI charges, GST, and broker commissions).
7. **Credentials Outside Code**: All API keys, secrets, and session tokens are strictly loaded from environment variables.

---

## System Architecture Pipeline

```
Market Data Engine (NSE/BSE, IST Timezone)
         ↓
Data Validation Engine (Stale Quote, Missing Bar, Circuit Limits)
         ↓
Feature & Indicator Engine (EMA, ATR, RSI, VWAP, Supertrend, ADX)
         ↓
Strategy Engine (Trend Following, Momentum, Mean Reversion, Squeeze)
         ↓
AI Analysis Engine (Gemini Structured JSON: Action, Confidence, Regime, Thesis)
         ↓
Confidence Filter & Consensus Merger (Strategy == AI, Conf >= 0.70, Risk <= Med)
         ↓
Independent Deterministic Risk Engine (Position Size, Exposure, Daily Loss, Drawdown)
         ↓
Position Sizing Module (Fixed Fractional Risk, Cash Cap, Lot & Tick Rounding)
         ↓
Order Management System & Broker Adapter (PaperBroker / Kite Connect v3)
         ↓
Execution & Trailing Exit Monitoring (Breakeven Lock, 15:15 IST Auto Square-off)
         ↓
Audit Trail Logger (JSONL & SQLite Database)
```

---

## Indian Equity Market Timings (IST)

- **09:00 - 09:08 IST**: Pre-market discovery
- **09:15 - 15:30 IST**: Regular equity trading hours
- **09:20 - 15:05 IST**: Permissible new entry window (avoids first 5-minute volatility and closing volatility)
- **15:15 IST**: Mandatory intraday (MIS) auto-squareoff window
- **Tick Size**: ₹0.05
- **Circuit Breaker Buffer**: Rejects any entry within 1.5% of upper or lower circuit freeze.

---

## Indian Statutory Transaction Costs (NSE)

| Component | Intraday (MIS) | Delivery (CNC) |
| :--- | :--- | :--- |
| **Brokerage** | ₹20 flat or 0.03% (whichever lower) | ₹0 / ₹20 flat |
| **STT (Securities Transaction Tax)** | 0.025% on sell turnover | 0.1% on buy & sell turnover |
| **NSE Turnover Charges** | 0.00297% of turnover | 0.00297% of turnover |
| **SEBI Turnover Fee** | ₹10 per crore (0.000001) | ₹10 per crore (0.000001) |
| **Stamp Duty** | 0.003% on buy turnover | 0.015% on buy turnover |
| **GST** | 18% on (Brokerage + Exchange + SEBI) | 18% on (Brokerage + Exchange + SEBI) |

---

## Installation & Setup

1. Copy `.env.example` to `.env` and fill in your keys:
   ```bash
   cp .env.example .env
   ```

2. Run Backtesting on NSE Equities:
   ```bash
   python -m indian_equity_agent.cli.main backtest --symbols RELIANCE.NS,TCS.NS,INFY.NS --strategy trend_following --days 90
   ```

3. Run Paper Trading Simulation:
   ```bash
   python -m indian_equity_agent.cli.main paper-trade --symbol RELIANCE --iterations 5
   ```

4. Check System & Risk Engine Status:
   ```bash
   python -m indian_equity_agent.cli.main status
   ```

5. Emergency Kill Switch Controls:
   ```bash
   # Check status
   python -m indian_equity_agent.cli.main kill-switch --action status

   # Manually halt trading
   python -m indian_equity_agent.cli.main kill-switch --action trigger --reason "Manual intervention"

   # Reset with authorization token
   python -m indian_equity_agent.cli.main kill-switch --action reset --token AUTHORIZE_RESET_CONFIRMED
   ```

6. Launch Localhost Interactive Web Dashboard:
   ```bash
   python -m indian_equity_agent.cli.main web --host 127.0.0.1 --port 8000
   ```
   Open your browser at: **[http://localhost:8000](http://localhost:8000)** (or [http://127.0.0.1:8000](http://127.0.0.1:8000)).
   API Documentation (Swagger UI): **[http://localhost:8000/docs](http://localhost:8000/docs)**.

