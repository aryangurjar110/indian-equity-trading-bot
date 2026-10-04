"""Configuration management for Indian Equities Algorithmic Trading Agent.

All sensitive credentials and secrets are loaded strictly from environment variables
or .env file. No secrets are ever hardcoded in the source code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load .env from current directory or project root
load_dotenv()


class MarketConfig(BaseModel):
    """Indian equity market parameters."""
    timezone: str = "Asia/Kolkata"
    exchange: str = "NSE"
    market_open_time: str = "09:15"
    market_close_time: str = "15:30"
    intraday_squareoff_time: str = "15:15"
    pre_market_open_time: str = "09:00"
    trading_start_buffer_minutes: int = 5  # No entries in first 5 mins (09:15-09:20)
    tick_size: float = 0.05  # NSE equity tick size in INR
    circuit_limit_buffer_pct: float = 1.5  # Reject trade if price is within 1.5% of upper/lower circuit


class RiskConfig(BaseModel):
    """Independent deterministic risk management parameters."""
    max_position_size_pct: float = Field(default=0.10, description="Max 10% of portfolio in single stock")
    max_risk_per_trade_pct: float = Field(default=0.01, description="Max 1% of total portfolio equity at risk")
    max_portfolio_exposure_pct: float = Field(default=0.80, description="Max 80% capital invested; 20% cash reserve")
    max_daily_loss_pct: float = Field(default=0.02, description="Max 2% loss per day; triggers hard halt")
    max_drawdown_pct: float = Field(default=0.06, description="Max 6% total drawdown; halts all trading")
    max_open_positions: int = Field(default=5, description="Max 5 simultaneous open positions")
    max_spread_pct: float = Field(default=0.0025, description="Max 0.25% bid-ask spread allowed")
    max_volume_pct_per_bar: float = Field(default=0.01, description="Max 1% of 15m bar volume to prevent market impact")
    consecutive_api_failure_limit: int = Field(default=3, description="Triggers kill switch after 3 API failures")
    max_rejected_orders_window: int = Field(default=3, description="Max rejected orders in 5m before halt")


class AIConfig(BaseModel):
    """Gemini AI Analysis configuration."""
    api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", ""))
    model_name: str = Field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    confidence_threshold: float = Field(default=0.70, description="Min AI confidence to permit trade (0-1)")
    enable_ai_analysis: bool = Field(default=True, description="Toggle AI analysis component")
    request_timeout_seconds: float = 15.0
    max_retries: int = 2


class BrokerConfig(BaseModel):
    """Broker execution credentials and parameters."""
    broker_type: Literal["paper", "kite", "groww"] = Field(
        default_factory=lambda: os.getenv("BROKER_TYPE", "paper").lower()  # type: ignore
    )
    kite_api_key: str = Field(default_factory=lambda: os.getenv("KITE_API_KEY", ""))
    kite_api_secret: str = Field(default_factory=lambda: os.getenv("KITE_API_SECRET", ""))
    kite_access_token: str = Field(default_factory=lambda: os.getenv("KITE_ACCESS_TOKEN", ""))
    groww_api_key: str = Field(default_factory=lambda: os.getenv("GROWW_API_KEY", ""))
    groww_api_secret: str = Field(default_factory=lambda: os.getenv("GROWW_API_SECRET", ""))
    groww_access_token: str = Field(default_factory=lambda: os.getenv("GROWW_ACCESS_TOKEN", ""))
    groww_whitelisted_ip: str = Field(
        default_factory=lambda: os.getenv("GROWW_WHITELISTED_IP", os.getenv("STATIC_IP", ""))
    )
    paper_initial_capital: float = Field(
        default_factory=lambda: float(os.getenv("INITIAL_CAPITAL", "500000.0"))
    )
    paper_slippage_pct: float = Field(default=0.0005, description="0.05% realistic execution slippage")


class StatutoryCostConfig(BaseModel):
    """Indian statutory charges & taxes for equity trading (NSE)."""
    brokerage_per_order_max: float = 20.0  # Discount broker e.g., Zerodha ₹20 flat
    brokerage_pct: float = 0.0003  # 0.03% or ₹20 whichever lower
    stt_delivery_buy_pct: float = 0.001  # 0.1% on buy
    stt_delivery_sell_pct: float = 0.001  # 0.1% on sell
    stt_intraday_sell_pct: float = 0.00025  # 0.025% on sell only
    nse_turnover_fee_pct: float = 0.0000297  # 0.00297%
    sebi_turnover_fee_pct: float = 0.000001  # ₹10 per crore
    stamp_duty_delivery_pct: float = 0.00015  # 0.015% on buy
    stamp_duty_intraday_pct: float = 0.00003  # 0.003% on buy
    gst_pct: float = 0.18  # 18% on (brokerage + turnover fee + sebi fee)


class SystemConfig(BaseModel):
    """Master configuration aggregate."""
    project_root: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = project_root / "data"
    logs_dir: Path = project_root / "logs"
    market: MarketConfig = Field(default_factory=MarketConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    costs: StatutoryCostConfig = Field(default_factory=StatutoryCostConfig)

    def validate_setup(self) -> List[str]:
        """Validates configuration sanity and returns a list of warnings or errors."""
        issues = []
        if self.broker.broker_type == "kite":
            if not self.broker.kite_api_key or not self.broker.kite_access_token:
                issues.append("Kite broker chosen but KITE_API_KEY or KITE_ACCESS_TOKEN is not set.")
        elif self.broker.broker_type == "groww":
            if not self.broker.groww_api_key or not self.broker.groww_access_token:
                issues.append("Groww broker chosen but GROWW_API_KEY or GROWW_ACCESS_TOKEN is not set.")
        if self.ai.enable_ai_analysis and not self.ai.api_key:
            issues.append("AI analysis enabled but GEMINI_API_KEY is not set. AI will run in fallback safe mode.")
        return issues


# Default global settings instance
settings = SystemConfig()
