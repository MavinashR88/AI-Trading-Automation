"""
IpoScannerMicro — finds recent IPOs (last 90 days) via Tavily search.
Uses call_llm("research_classify", ...) to parse and classify results.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a financial data parser. Extract IPO information from search results. "
    "Return ONLY a JSON array of objects with fields: ticker (string), ipo_date (YYYY-MM-DD), "
    "sector (string), company_name (string). If no valid IPOs found, return []."
)

LOOKBACK_DAYS = 90


async def _search_tavily(query: str) -> str:
    """Call Tavily search API. Returns raw text of results."""
    try:
        from backend.config import settings
        import httpx
        payload = {
            "api_key": settings.TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": 10,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post("https://api.tavily.com/search", json=payload)
            r.raise_for_status()
            data = r.json()
            # Concatenate result snippets
            snippets = [res.get("content", "") for res in data.get("results", [])]
            return "\n\n".join(snippets)
    except Exception as exc:
        logger.warning("[IpoScannerMicro] Tavily search failed: %s", exc)
        return ""


class IpoScannerMicro(MicroAgent):
    name = "IpoScannerMicro"
    timeout_seconds = 60.0

    async def execute(self, task: Any) -> list[dict]:
        cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        query = f"recent IPO initial public offering stocks listed after {cutoff_str} NYSE NASDAQ ticker symbol 2025 2026"

        raw_text = await _search_tavily(query)
        if not raw_text:
            return []

        prompt = (
            f"Extract all IPOs mentioned in the following search results that went public after {cutoff_str}.\n\n"
            f"{raw_text[:3000]}\n\n"
            "Return ONLY a JSON array. Example: "
            '[{"ticker": "NEWCO", "ipo_date": "2026-01-15", "sector": "Technology", "company_name": "New Co Inc"}]'
        )

        raw_llm = await call_llm("research_classify", prompt, SYSTEM_PROMPT)

        try:
            # Strip markdown fences if present
            cleaned = raw_llm.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            parsed: list[dict] = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[IpoScannerMicro] LLM parse failed: %s | raw=%s", exc, raw_llm[:200])
            return []

        results = []
        for item in parsed:
            ticker = item.get("ticker", "").upper().strip()
            if not ticker:
                continue
            results.append({
                "ticker": ticker,
                "ipo_date": item.get("ipo_date", ""),
                "sector": item.get("sector", "Unknown"),
                "reason": f"ipo: {item.get('company_name', ticker)} recent IPO",
            })

        logger.info("[IpoScannerMicro] found %d recent IPOs", len(results))
        return results
