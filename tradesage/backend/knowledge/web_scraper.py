"""
Tavily-powered web scraper for mentor research.
Creates WebArticle nodes in the knowledge graph.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Optional

from neo4j import Driver

logger = logging.getLogger(__name__)


class WebScraper:
    """Fetches web articles via Tavily and ingests them as WebArticle nodes."""

    def __init__(self, driver: Driver, tavily_api_key: str):
        self._driver = driver
        self._api_key = tavily_api_key

    async def search_and_ingest(self, query: str, ticker: Optional[str] = None) -> list[dict]:
        """
        Run a Tavily search and ingest results as WebArticle nodes.
        Returns list of article dicts.
        """
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=self._api_key)
            response = await asyncio.to_thread(
                client.search,
                query=query,
                search_depth="advanced",
                max_results=5,
                include_raw_content=True,
            )
        except Exception as exc:
            logger.warning("Tavily search failed for '%s': %s", query, exc)
            return []

        articles = []
        for r in response.get("results", []):
            article = {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", "")[:2000],
                "raw_content": r.get("raw_content", "")[:4000],
                "score": r.get("score", 0.0),
                "ticker": ticker,
            }
            self._ingest_article(article)
            articles.append(article)

        logger.info("Tavily: ingested %d articles for query '%s'", len(articles), query)
        return articles

    def _ingest_article(self, article: dict) -> None:
        url = article.get("url", "")
        if not url:
            return

        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        try:
            with self._driver.session() as session:
                session.run(
                    """
                    MERGE (wa:WebArticle {url: $url})
                    SET wa.title = $title,
                        wa.content = $content,
                        wa.source = $source,
                        wa.ticker = $ticker,
                        wa.score = $score,
                        wa.ingested_at = datetime()
                    """,
                    {
                        "url": url,
                        "title": article.get("title", ""),
                        "content": article.get("content", ""),
                        "source": url.split("/")[2] if "/" in url else "unknown",
                        "ticker": article.get("ticker", ""),
                        "score": article.get("score", 0.0),
                    },
                )
        except Exception as exc:
            logger.warning("Failed to ingest WebArticle %s: %s", url, exc)

    async def research_ticker(self, ticker: str) -> list[dict]:
        """Deep research a specific ticker."""
        queries = [
            f"{ticker} stock analysis latest news",
            f"{ticker} earnings report revenue growth",
            f"{ticker} technical analysis price target",
        ]
        all_articles = []
        for q in queries:
            articles = await self.search_and_ingest(q, ticker=ticker)
            all_articles.extend(articles)
        return all_articles

    async def research_macro(self, topic: str) -> list[dict]:
        """Research a macro topic (Fed, CPI, GDP, etc.)."""
        return await self.search_and_ingest(
            f"{topic} market impact analysis",
            ticker=None
        )
