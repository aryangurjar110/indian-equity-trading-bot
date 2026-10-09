"""Gemini AI Market Analyst for Indian Equities.

Uses Gemini as an explainable analysis component while enforcing strict
structured JSON schemas and a deterministic safe-state fallback (HOLD).
Free-form text is completely prevented from placing orders.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional
from ..core.models import AIAnalysisOutput, StrategySignal
from ..config import settings
from .prompts import ANALYSIS_PROMPT_TEMPLATE, SYSTEM_PROMPT
from .schema import GeminiAnalysisResponse

logger = logging.getLogger("indian_equity_agent.ai_engine")


class GeminiMarketAnalyst:
    """Institutional-grade AI market analyst powered by Gemini."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.api_key = api_key if api_key is not None else settings.ai.api_key
        self.model_name = model_name or settings.ai.model_name
        self.timeout = timeout
        self._client = None

        if self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Could not initialize Google GenAI client: {e}. Running in fallback mode.")
                self._client = None
        else:
            logger.info("GEMINI_API_KEY not configured. Running AI engine in deterministic fallback mode.")

    def _fallback_decision(self, symbol: str, reason: str) -> AIAnalysisOutput:
        """Returns safe default 'HOLD' state when AI is unavailable, times out, or fails."""
        return AIAnalysisOutput(
            symbol=symbol,
            action="HOLD",
            confidence=0.0,
            reason=f"AI Safe Fallback: {reason}",
            market_regime="UNCERTAIN",
            risk_level="HIGH",
            invalidating_conditions=["AI analysis offline or failed"],
        )

    def analyze(
        self,
        symbol: str,
        technical_context: Dict[str, Any],
        proposed_signal: Optional[StrategySignal] = None,
    ) -> AIAnalysisOutput:
        """Analyzes technical snapshot using Gemini with structured output validation."""
        if not self._client or not self.api_key:
            return self._fallback_decision(symbol, "API key not configured or client initialization failed")

        proposed_action = proposed_signal.action if proposed_signal else "NONE"
        stop_loss = proposed_signal.suggested_stop_loss if proposed_signal else 0.0
        target = proposed_signal.suggested_target if proposed_signal else 0.0

        st_dir = technical_context.get("supertrend_dir", 1)
        st_label = "BULLISH" if st_dir == 1 else "BEARISH"

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            symbol=symbol,
            close=technical_context.get("close", 0.0),
            low=technical_context.get("low", 0.0),
            high=technical_context.get("high", 0.0),
            volume=technical_context.get("volume", 0),
            vol_surge=technical_context.get("vol_surge", 1.0),
            ema_9=technical_context.get("ema_9", 0.0),
            ema_20=technical_context.get("ema_20", 0.0),
            ema_50=technical_context.get("ema_50", 0.0),
            atr_14=technical_context.get("atr_14", 0.0),
            rsi_14=technical_context.get("rsi_14", 50.0),
            macd=technical_context.get("macd", 0.0),
            macd_signal=technical_context.get("macd_signal", 0.0),
            adx=technical_context.get("adx", 0.0),
            supertrend=technical_context.get("supertrend", 0.0),
            supertrend_dir_label=st_label,
            bb_upper=technical_context.get("bb_upper", 0.0),
            bb_lower=technical_context.get("bb_lower", 0.0),
            vwap=technical_context.get("vwap", 0.0),
            proposed_action=proposed_action,
            stop_loss=stop_loss,
            target=target,
        )

        for attempt in range(2):
            try:
                from google.genai import types

                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=GeminiAnalysisResponse,
                        temperature=0.1,
                    ),
                )

                if not response or not response.text:
                    return self._fallback_decision(symbol, "Empty response from Gemini")

                # Parse JSON strictly into Pydantic model
                data = json.loads(response.text)
                parsed = GeminiAnalysisResponse(**data)

                return AIAnalysisOutput(
                    symbol=parsed.symbol,
                    action=parsed.action,
                    confidence=parsed.confidence,
                    reason=parsed.reason,
                    market_regime=parsed.market_regime,
                    risk_level=parsed.risk_level,
                    invalidating_conditions=parsed.invalidating_conditions,
                )

            except Exception as e:
                err_str = str(e)
                if attempt == 0 and ("503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str):
                    logger.warning(f"Gemini 503 spike for {symbol}, retrying once in 1s...")
                    time.sleep(1.0)
                    continue
                logger.error(f"Gemini AI analysis error for {symbol}: {e}")
                return self._fallback_decision(symbol, f"Exception during AI inference: {str(e)}")
