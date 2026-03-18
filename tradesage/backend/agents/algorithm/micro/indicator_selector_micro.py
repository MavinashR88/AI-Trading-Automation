"""
IndicatorSelectorMicroAgent
---------------------------
Given research data for a ticker, uses call_llm("pattern_analysis") to
select the best technical indicators and parameter values suited to that
stock's volatility, trend behaviour, and news sensitivity.

task: {"ticker": str, "research": dict}

Returns:
    {
        "indicators": ["RSI", "MACD", "ATR", ...],
        "params": {"rsi_period": 14, "macd_fast": 12, ...}
    }
"""
from __future__ import annotations

import json
import logging

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a quantitative technical analyst. "
    "Select the most effective technical indicators for the given stock "
    "based on its characteristics. Return ONLY valid JSON — no markdown."
)

_DEFAULT_INDICATORS = ["RSI", "ATR", "EMA_20", "EMA_50", "VOLUME"]
_DEFAULT_PARAMS = {
    "rsi_period": 14,
    "atr_period": 14,
    "ema_fast": 20,
    "ema_slow": 50,
    "volume_ma_period": 20,
}


class IndicatorSelectorMicroAgent(MicroAgent):
    name = "IndicatorSelectorMicroAgent"
    timeout_seconds = 45.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        research: dict = task.get("research", {})

        prompt = self._build_prompt(ticker, research)
        raw = await call_llm("pattern_analysis", prompt, _SYSTEM)
        return self._parse(ticker, raw)

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------
    @staticmethod
    def _build_prompt(ticker: str, r: dict) -> str:
        return f"""Stock: {ticker}
Strategy type suggested: {r.get('suggested_strategy', 'momentum')}
Trend: {r.get('trend_direction', 'sideways')}
ATR%: {r.get('atr_pct', 0):.2f}%
RSI (current): {r.get('rsi_14', 50):.1f}
News sensitivity: {r.get('news_sensitivity', 'medium')}
Avg catalyst move: {r.get('avg_move_on_catalyst_pct', 2):.1f}%
Above 200MA: {r.get('above_200ma', False)}

Select 4-6 technical indicators best suited to trade this stock algorithmically.
Consider: trend vs oscillator mix, volatility for stop-loss, volume confirmation.

Return JSON:
{{
  "indicators": ["<INDICATOR_NAME>", ...],
  "params": {{
    "<param_name>": <value>,
    ...
  }}
}}

Allowed indicator names (use exact strings):
RSI, MACD, ATR, BBANDS, EMA_20, EMA_50, EMA_200,
SMA_50, SMA_200, VWAP, VOLUME, OBV, STOCH, ADX, CCI, MFI"""

    # ------------------------------------------------------------------
    # Parser
    # ------------------------------------------------------------------
    @staticmethod
    def _parse(ticker: str, raw: str) -> dict:
        try:
            cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(cleaned)
            indicators = data.get("indicators", _DEFAULT_INDICATORS)
            params = data.get("params", _DEFAULT_PARAMS)
            # Validate types
            if not isinstance(indicators, list):
                indicators = _DEFAULT_INDICATORS
            if not isinstance(params, dict):
                params = _DEFAULT_PARAMS
            return {
                "ticker": ticker,
                "indicators": indicators[:8],  # cap at 8
                "params": params,
            }
        except Exception as exc:
            logger.warning("[IndicatorSelectorMicroAgent] parse failed for %s: %s", ticker, exc)
            return {
                "ticker": ticker,
                "indicators": _DEFAULT_INDICATORS,
                "params": _DEFAULT_PARAMS,
            }
