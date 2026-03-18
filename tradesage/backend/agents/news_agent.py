"""
News Agent — Claude + Tavily powered pipeline.
Replaces NewsAPI + Benzinga entirely.

Pipeline per ticker:
  1. Fire 5 parallel Tavily searches
  2. Claude synthesizes raw results → structured NewsEvent JSON
  3. MERGE NewsEvent into Neo4j, wire relationships
  4. Emit WebSocket event on breaking news
  5. Cache in SQLite (TTL 30 min) to preserve Tavily quota
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from backend.llm.router import call_llm
from backend.models.signal import NewsSignal
from backend.knowledge.graph_updater import GraphUpdater
from backend.db.sqlite_store import TradeStore

logger = logging.getLogger(__name__)

# ── Claude synthesis prompt ────────────────────────────────────────────────────

_SYNTHESIS_PROMPT = """You are a professional financial news analyst with 20 years experience.
Given raw web search results about a stock ticker, synthesize everything
into a structured JSON analysis. Extract:

1. most_important_headline: the single most market-moving headline (string)
2. source_url: URL of that headline (string)
3. sentiment_score: float from -1.0 (extremely bearish) to +1.0 (extremely bullish)
4. urgency: "immediate" (news < 30 min old) | "watch" (< 4 hrs) | "background" (older)
5. catalyst_type: "earnings" | "macro" | "analyst" | "insider" | "sector" | "geopolitical" | "none"
6. already_priced_in: bool — has price likely already moved on this news?
7. sector_ripple: list of other tickers/sectors likely affected
8. divergence_flag: bool — is price NOT reacting to this news? (bearish signal if true)
9. summary: 2-3 sentence plain English summary of the overall news picture
10. breaking_news: bool — is this a major macro event (Fed/CPI/earnings surprise > 5%)?

Respond ONLY in valid JSON. No preamble, no explanation."""


class NewsAgent:
    """
    Hourly news reader powered entirely by Tavily search + Claude synthesis.

    Tavily fires 5 parallel searches per ticker; Claude turns the raw dump
    into a clean NewsEvent that flows into Neo4j and the SQLite cache.
    """

    # In-memory TTL cache: ticker -> (signal, fetched_at)
    _cache: dict[str, tuple[NewsSignal, datetime]] = {}
    CACHE_TTL_MINUTES: int = 10

    def __init__(
        self,
        tavily_api_key: str,
        anthropic_api_key: str = "",   # legacy — ignored, router handles auth
        llm_model: str = "",           # legacy — ignored, router handles model
        graph_updater: GraphUpdater = None,
        store: TradeStore = None,
        ws_broadcaster=None,
    ):
        self._tavily_key = tavily_api_key
        self._graph_updater = graph_updater
        self._store = store
        self._ws_broadcaster = ws_broadcaster
        self._last_scan: dict[str, datetime] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    async def scan_tickers(self, tickers: list[str]) -> dict[str, NewsSignal]:
        """Scan all tickers in parallel; returns ticker → NewsSignal map."""
        tasks = {ticker: asyncio.create_task(self.scan_ticker(ticker)) for ticker in tickers}
        results: dict[str, NewsSignal] = {}
        for ticker, task in tasks.items():
            try:
                results[ticker] = await task
            except Exception as exc:
                logger.error("News scan failed for %s: %s", ticker, exc)
        return results

    async def scan_ticker(self, ticker: str) -> NewsSignal:
        """Full pipeline for a single ticker — cache-aware."""
        cached = self._get_cached(ticker)
        if cached:
            logger.debug("Cache hit for %s (TTL %d min)", ticker, self.CACHE_TTL_MINUTES)
            return cached

        # Step 1 — Google News RSS (primary, free, no key) → Tavily → yfinance
        raw_results = await self._google_news_search(ticker)
        used_simple_fallback = False

        if not raw_results:
            raw_results = await self._tavily_multi_search(ticker)

        if not raw_results:
            raw_results = await self._yfinance_fallback(ticker)
            used_simple_fallback = True

        if not raw_results:
            signal = self._empty_signal(ticker)
        else:
            # Fast keyword sentiment — no Claude call needed for scan speed
            signal = self._keyword_sentiment_signal(ticker, raw_results)

        # Step 3 cont. — Persist to SQLite + Neo4j (non-fatal if unavailable)
        try:
            if self._store:
                await self._store.save_news_event(signal)
        except Exception as exc:
            logger.warning("Failed to save news event to SQLite: %s", exc)
        try:
            if self._graph_updater:
                self._graph_updater.upsert_news_event(signal)
        except Exception as exc:
            logger.warning("Failed to upsert news event to Neo4j: %s", exc)
        self._last_scan[ticker] = datetime.utcnow()

        # Step 4 — Breaking news WebSocket broadcast
        if signal.breaking_override:
            await self._broadcast_breaking_news(signal)

        # Step 5 — Cache
        self._cache[ticker] = (signal, datetime.utcnow())

        logger.info(
            "News [%s]: sentiment=%.2f urgency=%s breaking=%s catalyst=%s",
            ticker,
            signal.sentiment_score,
            signal.urgency,
            signal.breaking_override,
            signal.catalyst[:60],
        )
        return signal

    # ── Step 1 — Tavily multi-search ───────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15), reraise=False)
    async def _tavily_multi_search(self, ticker: str) -> list[dict]:
        """Fire 5 parallel Tavily searches; return merged result list."""
        if not self._tavily_key:
            logger.warning("TAVILY_API_KEY not set — skipping news fetch for %s", ticker)
            return []

        queries = [
            f"{ticker} stock news today",
            f"{ticker} earnings analyst forecast",
            f"{ticker} breaking news market impact",
            f"{ticker} SEC filing insider trading",
            f"{ticker} competitor news sector impact",
        ]

        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=self._tavily_key)

            search_tasks = [
                asyncio.to_thread(client.search, q, max_results=5)
                for q in queries
            ]
            raw_responses = await asyncio.gather(*search_tasks, return_exceptions=True)

            merged: list[dict] = []
            for resp in raw_responses:
                if isinstance(resp, Exception):
                    logger.warning("Tavily search error: %s", resp)
                    continue
                merged.extend(resp.get("results", []))

            # Deduplicate by URL
            seen_urls: set[str] = set()
            unique: list[dict] = []
            for item in merged:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique.append(item)

            logger.debug("Tavily returned %d unique results for %s", len(unique), ticker)
            return unique[:20]  # cap to avoid huge Claude prompts

        except Exception as exc:
            logger.error("Tavily multi-search failed for %s: %s", ticker, exc)
            return []

    async def _google_news_search(self, ticker: str) -> list[dict]:
        """Fetch news from Google News RSS — free, no API key, no rate limits."""
        try:
            import xml.etree.ElementTree as ET
            import urllib.request
            import urllib.parse

            queries = [
                f"{ticker} stock",
                f"{ticker} earnings",
                f"{ticker} market",
            ]
            results = []
            for q in queries:
                try:
                    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en"
                    response = await asyncio.to_thread(
                        lambda u=url: urllib.request.urlopen(u, timeout=8).read()
                    )
                    root = ET.fromstring(response)
                    for item in root.findall(".//item")[:5]:
                        title = item.findtext("title") or ""
                        desc = item.findtext("description") or ""
                        link = item.findtext("link") or ""
                        # Strip HTML tags from description
                        import re
                        desc_clean = re.sub(r"<[^>]+>", "", desc)
                        results.append({
                            "url": link,
                            "title": title,
                            "content": f"{title}. {desc_clean}".strip(),
                            "score": 0.8,
                        })
                except Exception:
                    continue

            # Deduplicate by title
            seen = set()
            unique = []
            for r in results:
                t = r.get("title", "")
                if t and t not in seen:
                    seen.add(t)
                    unique.append(r)

            logger.info("Google News RSS: %d articles for %s", len(unique), ticker)
            return unique[:15]
        except Exception as exc:
            logger.warning("Google News RSS failed for %s: %s", ticker, exc)
            return []

    def _keyword_sentiment_signal(self, ticker: str, articles: list[dict]) -> "NewsSignal":
        """Fast keyword-based sentiment when Tavily is unavailable (no Claude call)."""
        BULLISH = {"beat", "beats", "record", "growth", "surge", "rally", "upgrade",
                   "raises", "strong", "buy", "bullish", "profit", "positive", "gain",
                   "outperform", "revenue", "earnings beat", "raised guidance", "buyback"}
        BEARISH = {"miss", "misses", "decline", "fall", "drop", "downgrade", "cut",
                   "weak", "loss", "bearish", "sell", "warning", "lawsuit", "recall",
                   "layoff", "layoffs", "investigation", "negative", "concern", "risk",
                   "disappoints", "below", "missed"}

        score = 0.0
        headline = articles[0].get("title", f"{ticker} market update") if articles else f"{ticker} market update"
        for art in articles[:8]:
            text = (art.get("title", "") + " " + art.get("content", "")).lower()
            for word in BULLISH:
                if word in text:
                    score += 0.15
            for word in BEARISH:
                if word in text:
                    score -= 0.15

        score = max(-1.0, min(1.0, score))
        urgency = "wait" if abs(score) > 0.3 else "background"

        logger.info("News [%s] (keyword): sentiment=%.2f urgency=%s", ticker, score, urgency)
        return NewsSignal(
            signal_id=str(uuid.uuid4()),
            ticker=ticker,
            headline=headline,
            source="yfinance",
            url="",
            sentiment_score=score,
            urgency=urgency,
            catalyst="earnings" if any("earn" in (a.get("title","")).lower() for a in articles) else "news",
            age_minutes=60,
            breaking_override=False,
            timestamp=datetime.utcnow(),
            raw_text=" | ".join(a.get("title", "") for a in articles[:3]),
        )

    async def _yfinance_fallback(self, ticker: str) -> list[dict]:
        """Fallback: fetch news headlines from yfinance when Tavily is unavailable."""
        try:
            import yfinance as yf
            t = await asyncio.to_thread(yf.Ticker, ticker)
            news_items = await asyncio.to_thread(lambda: t.news)
            if not news_items:
                return []
            results = []
            for item in (news_items or [])[:10]:
                content = item.get("title", "")
                summary = item.get("summary", "") or item.get("description", "")
                if summary:
                    content = f"{content}. {summary}"
                results.append({
                    "url": item.get("link", ""),
                    "title": item.get("title", ""),
                    "content": content,
                    "score": 0.5,
                })
            logger.info("yfinance fallback: %d articles for %s", len(results), ticker)
            return results
        except Exception as exc:
            logger.warning("yfinance fallback failed for %s: %s", ticker, exc)
            return []

    # ── Step 2 — Claude synthesis ──────────────────────────────────────────────

    async def _synthesize_with_claude(self, ticker: str, raw_results: list[dict]) -> dict:
        """Send raw results to Claude via router; get structured JSON back."""
        snippets = []
        for i, r in enumerate(raw_results[:15], 1):
            title = r.get("title", "")
            content = (r.get("content") or r.get("snippet") or "")[:400]
            url = r.get("url", "")
            snippets.append(f"[{i}] {title}\n    URL: {url}\n    {content}")

        user_content = (
            f"Ticker: {ticker}\n\nRaw web search results:\n\n"
            + "\n\n".join(snippets)
        )

        try:
            raw = await call_llm("news_scoring", user_content, _SYNTHESIS_PROMPT)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                parts = cleaned.split("```")
                cleaned = parts[1] if len(parts) > 1 else cleaned
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            return json.loads(cleaned.strip())
        except json.JSONDecodeError as exc:
            logger.warning("LLM returned invalid JSON for %s: %s", ticker, exc)
        except Exception as exc:
            logger.error("LLM synthesis failed for %s: %s", ticker, exc)

        return {
            "most_important_headline": f"News available but analysis failed for {ticker}",
            "source_url": "", "sentiment_score": 0.0, "urgency": "background",
            "catalyst_type": "none", "already_priced_in": False,
            "sector_ripple": [], "divergence_flag": False,
            "summary": "LLM synthesis unavailable.", "breaking_news": False,
        }

    # ── Step 4 — Breaking news broadcast ──────────────────────────────────────

    async def _broadcast_breaking_news(self, signal: NewsSignal) -> None:
        """Emit a WebSocket event for breaking news."""
        if not self._ws_broadcaster:
            return
        try:
            event = {
                "type": "breaking_news",
                "ticker": signal.ticker,
                "headline": signal.headline,
                "sentiment_score": signal.sentiment_score,
                "timestamp": signal.timestamp.isoformat(),
            }
            await self._ws_broadcaster(event)
            logger.warning("BREAKING NEWS broadcast: %s — %s", signal.ticker, signal.headline[:80])
        except Exception as exc:
            logger.error("WebSocket broadcast failed: %s", exc)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_cached(self, ticker: str) -> Optional[NewsSignal]:
        if ticker not in self._cache:
            return None
        signal, fetched_at = self._cache[ticker]
        age = (datetime.utcnow() - fetched_at).total_seconds() / 60
        if age > self.CACHE_TTL_MINUTES:
            del self._cache[ticker]
            return None
        return signal

    def _empty_signal(self, ticker: str) -> NewsSignal:
        return NewsSignal(
            signal_id=str(uuid.uuid4()),
            ticker=ticker,
            headline=f"No recent news found for {ticker}",
            source="none",
            url="",
            sentiment_score=0.0,
            urgency="wait",
            catalyst="none",
            age_minutes=999,
            breaking_override=False,
            timestamp=datetime.utcnow(),
            raw_text="",
        )

    def _map_urgency(self, tavily_urgency: str) -> str:
        """Map Claude urgency values to the system's 3-level scale."""
        mapping = {
            "immediate": "immediate",
            "watch": "wait",
            "background": "wait",
        }
        return mapping.get(tavily_urgency, "wait")

    def _urgency_to_age(self, urgency: str) -> int:
        """Approximate age in minutes from urgency label."""
        return {"immediate": 15, "watch": 120, "background": 300}.get(urgency, 60)

    def _extract_source(self, url: str) -> str:
        """Extract domain name as source label from URL."""
        if not url:
            return "web"
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            return domain.replace("www.", "") or "web"
        except Exception:
            return "web"

    def get_last_scan_time(self, ticker: str) -> Optional[datetime]:
        return self._last_scan.get(ticker)
