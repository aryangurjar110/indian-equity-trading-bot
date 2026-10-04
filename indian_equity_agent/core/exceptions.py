"""Domain exceptions for Indian Equities Trading Agent."""

class TradingAgentError(Exception):
    """Base exception for all trading agent errors."""
    pass


class MarketClosedError(TradingAgentError):
    """Raised when an operation requires an active market session."""
    pass


class StaleDataError(TradingAgentError):
    """Raised when market data is older than the allowed latency tolerance."""
    pass


class DataValidationError(TradingAgentError):
    """Raised when market data fails structural integrity checks."""
    pass


class RiskViolationError(TradingAgentError):
    """Raised when a proposed trade or portfolio state violates hard risk limits."""
    pass


class KillSwitchActiveError(TradingAgentError):
    """Raised when attempting an order execution while emergency kill switch is engaged."""
    pass


class InsufficientFundsError(TradingAgentError):
    """Raised when capital is insufficient to meet margin and risk limits."""
    pass


class BrokerConnectionError(TradingAgentError):
    """Raised when broker API communication fails repeatedly."""
    pass
