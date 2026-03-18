"""
ResearchVerdictMicroAgent
--------------------------
Acts as a senior quantitative analyst. Takes all aggregated research
data and uses call_llm("research_verdict") to produce a final verdict
with confidence, reasoning, and suggested strategy.

task: {"ticker": str, "research_data": dict}

Returns:
    {
        "verdict": "STRONG_BUY" | "BUY" | "NEUTRAL" | "AVOID",
        "confidence": float (0–1),
        "reasoning": str,
        "suggested_strategy": "momentum" | "event_driven" | "breakout" | "mean_reversion"
    }
"""
from __future__ import annotations

import json
import logging

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

_VERDICT_SYSTEM = """You are a senior quantitative analyst with 20 years of experience.
You are evaluating a stock based on comprehensive fundamental, technical, news, SEC,
and competitive research data.

Analyse the data objectively and return a final trading verdict.

IMPORTANT: Return ONLY valid JSON — no markdown fences, no explanation outside JSON.
"""

_VALID_VERDICTS = {"STRONG_BUY", "BUY", "NEUTRAL", "AVOID"}
_VALID_STRATEGIES = {"momentum", "event_driven", "breakout", "mean_reversion"}


class ResearchVerdictMicroAgent(MicroAgent):
    name = "ResearchVerdictMicroAgent"
    timeout_seconds = 60.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        research_data: dict = task.get("research_data", {})

        prompt = self._build_prompt(ticker, research_data)
        raw = await call_llm("research_verdict", prompt, _VERDICT_SYSTEM)
        return self._parse(ticker, raw)

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------
    @staticmethod
    def _build_prompt(ticker: str, data: dict) -> str:
        return f"""Ticker: {ticker}

=== FUNDAMENTAL DATA ===
P/E Ratio: {data.get('pe_ratio', 'N/A')}
Revenue Growth: {data.get('revenue_growth_pct', 0):.1f}%
Gross Margin: {data.get('gross_margin_pct', 0):.1f}%
Debt/Equity: {data.get('debt_to_equity', 0):.2f}
Earnings Surprise: {data.get('earnings_surprise_pct', 0):.1f}%

=== TECHNICAL DATA ===
ATR%: {data.get('atr_pct', 0):.2f}%
RSI (14): {data.get('rsi_14', 50):.1f}
Above 200-day MA: {data.get('above_200ma', False)}
Trend: {data.get('trend_direction', 'sideways')}
Support: {data.get('support_level', 0):.2f}
Resistance: {data.get('resistance_level', 0):.2f}

=== NEWS & CATALYST HISTORY ===
News Sensitivity: {data.get('news_sensitivity', 'medium')}
Avg Move on Catalyst: {data.get('avg_move_on_catalyst_pct', 0):.1f}%
Key Catalysts: {', '.join(data.get('key_catalysts', []))}

=== INSIDER / INSTITUTIONAL ===
Insider Buying (90d): {data.get('insider_buying_last_90d', False)}
Institutional Ownership Change: {data.get('institutional_ownership_change_pct', 0):.1f}%

=== COMPETITIVE POSITION ===
Sector: {data.get('sector', 'Unknown')}
Relative Strength vs Peers: {data.get('relative_strength', 0):.2f} (range -1 to +1)
Main Competitors: {', '.join(data.get('competitors', [])[:5])}

Based on this comprehensive analysis, provide your verdict.

Return JSON:
{{
  "verdict": "STRONG_BUY|BUY|NEUTRAL|AVOID",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<2-3 sentence explanation of key drivers>",
  "suggested_strategy": "momentum|event_driven|breakout|mean_reversion"
}}"""

    # ------------------------------------------------------------------
    # Response parser with safe defaults
    # ------------------------------------------------------------------
    @staticmethod
    def _parse(ticker: str, raw: str) -> dict:
        default = {
            "ticker": ticker,
            "verdict": "NEUTRAL",
            "confidence": 0.0,
            "reasoning": "Unable to determine verdict — LLM response invalid.",
            "suggested_strategy": "momentum",
        }
        try:
            cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(cleaned)

            verdict = data.get("verdict", "NEUTRAL").upper()
            if verdict not in _VALID_VERDICTS:
                verdict = "NEUTRAL"

            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))

            strategy = data.get("suggested_strategy", "momentum").lower()
            if strategy not in _VALID_STRATEGIES:
                strategy = "momentum"

            return {
                "ticker": ticker,
                "verdict": verdict,
                "confidence": round(confidence, 4),
                "reasoning": str(data.get("reasoning", default["reasoning"]))[:1000],
                "suggested_strategy": strategy,
            }
        except Exception as exc:
            logger.warning("[ResearchVerdictMicroAgent] parse failed for %s: %s", ticker, exc)
            return default
