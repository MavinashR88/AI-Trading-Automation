"""
SecFilingMicroAgent
-------------------
Uses Tavily to search for recent SEC Form 4 insider-buying activity
for the last 90 days, then uses call_llm("research_classify") to
determine whether the signal is positive.

Returns:
    {insider_buying: bool, institutional_change_pct: float}

task: {"ticker": str}
"""
from __future__ import annotations

import asyncio
import json
import logging

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = (
    "You are a financial analyst specializing in SEC filings and institutional ownership. "
    "Return ONLY valid JSON — no markdown, no explanation."
)

_DEFAULT = {
    "insider_buying_last_90d": False,
    "institutional_ownership_change_pct": 0.0,
}


class SecFilingMicroAgent(MicroAgent):
    name = "SecFilingMicroAgent"
    timeout_seconds = 45.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        raw_text = await self._search(ticker)
        if not raw_text:
            return {"ticker": ticker, **_DEFAULT}
        return await self._classify(ticker, raw_text)

    # ------------------------------------------------------------------
    # Tavily search
    # ------------------------------------------------------------------
    async def _search(self, ticker: str) -> str:
        try:
            from backend.config import settings
            from tavily import TavilyClient

            if not settings.TAVILY_API_KEY:
                return ""

            client = TavilyClient(api_key=settings.TAVILY_API_KEY)
            query = f"SEC Form 4 {ticker} insider buying last 90 days institutional ownership"
            result = await asyncio.to_thread(
                client.search,
                query,
                max_results=5,
                search_depth="basic",
            )
            snippets = [r.get("content", "") for r in result.get("results", [])]
            return "\n\n".join(snippets)[:5000]
        except Exception as exc:
            logger.warning("[SecFilingMicroAgent] Tavily search failed for %s: %s", ticker, exc)
            return ""

    # ------------------------------------------------------------------
    # LLM classification
    # ------------------------------------------------------------------
    async def _classify(self, ticker: str, raw_text: str) -> dict:
        prompt = (
            f"Ticker: {ticker}\n\n"
            f"SEC/insider search results (last 90 days):\n{raw_text}\n\n"
            "Determine:\n"
            "1. Is there notable insider BUYING (Form 4 purchases by officers/directors)? "
            "Answer true only if there is clear evidence of meaningful purchases.\n"
            "2. What is the estimated net institutional ownership change in percentage points "
            "(positive = net buying, negative = net selling, 0 if unclear)?\n\n"
            "Return JSON:\n"
            '{"insider_buying": <bool>, "institutional_change_pct": <float>}'
        )
        try:
            raw = await call_llm("research_classify", prompt, _CLASSIFY_SYSTEM)
            cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(cleaned)

            insider_buying = bool(data.get("insider_buying", False))
            inst_change = float(data.get("institutional_change_pct", 0.0))

            return {
                "ticker": ticker,
                "insider_buying_last_90d": insider_buying,
                "institutional_ownership_change_pct": round(inst_change, 2),
            }
        except Exception as exc:
            logger.warning("[SecFilingMicroAgent] LLM classify failed for %s: %s", ticker, exc)
            return {"ticker": ticker, **_DEFAULT}
