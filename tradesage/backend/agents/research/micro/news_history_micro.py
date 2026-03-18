"""
NewsHistoryMicroAgent
---------------------
Uses Tavily to search historical news catalysts for a ticker, then
uses call_llm("research_classify") to extract 3-5 key catalyst types
and the average price move percentage they tend to produce.

If Tavily is unavailable (no client or API key), returns neutral
defaults so the pipeline is not blocked.

task: {"ticker": str, "tavily_client": optional}
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

_DEFAULT = {
    "key_catalysts": ["earnings", "product launch", "macro event"],
    "avg_move_on_catalyst_pct": 2.5,
    "news_sensitivity": "medium",
}

_CLASSIFY_SYSTEM = (
    "You are a financial research analyst specializing in stock catalysts. "
    "Return ONLY valid JSON — no markdown, no explanation."
)


class NewsHistoryMicroAgent(MicroAgent):
    name = "NewsHistoryMicroAgent"
    timeout_seconds = 45.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        tavily_client = task.get("tavily_client")

        raw_text = await self._search(ticker, tavily_client)
        if not raw_text:
            logger.info("[NewsHistoryMicroAgent] No Tavily results for %s, using defaults", ticker)
            return {"ticker": ticker, **_DEFAULT}

        return await self._classify(ticker, raw_text)

    # ------------------------------------------------------------------
    # Tavily search
    # ------------------------------------------------------------------
    async def _search(self, ticker: str, client) -> str:
        """Return combined text from Tavily search, or empty string."""
        if client is None:
            # Try to build one from settings
            try:
                from backend.config import settings
                from tavily import TavilyClient
                if settings.TAVILY_API_KEY:
                    client = TavilyClient(api_key=settings.TAVILY_API_KEY)
            except Exception:
                return ""

        if client is None:
            return ""

        query = f"{ticker} stock news catalyst historical price move earnings"
        try:
            import asyncio
            result = await asyncio.to_thread(
                client.search,
                query,
                max_results=5,
                search_depth="basic",
            )
            snippets = [r.get("content", "") for r in result.get("results", [])]
            return "\n\n".join(snippets)[:6000]  # cap context size
        except Exception as exc:
            logger.warning("[NewsHistoryMicroAgent] Tavily search failed for %s: %s", ticker, exc)
            return ""

    # ------------------------------------------------------------------
    # LLM classification
    # ------------------------------------------------------------------
    async def _classify(self, ticker: str, raw_text: str) -> dict:
        prompt = (
            f"Ticker: {ticker}\n\n"
            f"News search results:\n{raw_text}\n\n"
            "Based on this text, identify the 3-5 key catalysts that historically "
            "move this stock. For each catalyst estimate the average % price move it triggers.\n\n"
            "Return JSON:\n"
            '{"key_catalysts": ["<catalyst1>", ...], '
            '"avg_move_on_catalyst_pct": <float>, '
            '"news_sensitivity": "low|medium|high"}'
        )
        try:
            raw = await call_llm("research_classify", prompt, _CLASSIFY_SYSTEM)
            # Strip markdown code fences if present
            cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(cleaned)

            catalysts = data.get("key_catalysts", _DEFAULT["key_catalysts"])
            avg_move = float(data.get("avg_move_on_catalyst_pct", _DEFAULT["avg_move_on_catalyst_pct"]))
            sensitivity = data.get("news_sensitivity", "medium")
            if sensitivity not in ("low", "medium", "high"):
                sensitivity = "medium"

            return {
                "ticker": ticker,
                "key_catalysts": catalysts[:5],
                "avg_move_on_catalyst_pct": round(avg_move, 2),
                "news_sensitivity": sensitivity,
            }
        except Exception as exc:
            logger.warning("[NewsHistoryMicroAgent] LLM classify failed for %s: %s", ticker, exc)
            return {"ticker": ticker, **_DEFAULT}
