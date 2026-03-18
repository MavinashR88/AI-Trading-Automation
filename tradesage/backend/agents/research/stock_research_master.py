"""
StockResearchMaster
-------------------
Orchestrates the full stock research pipeline for a single ticker.

Pipeline (parallel micros, then verdict):
  FundamentalMicro  ─┐
  PriceHistoryMicro ─┤
  NewsHistoryMicro  ─┼─▶ aggregate ─▶ ResearchVerdictMicro ─▶ ResearchReport
  SecFilingMicro    ─┤
  CompetitorMicro   ─┘

Public API:
    master = StockResearchMaster()
    report = await master.run_for_ticker("NVDA")
    # returns ResearchReport
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.agents.base.master import MasterAgent
from backend.agents.base.micro import MicroAgent
from backend.agents.research.micro.fundamental_micro import FundamentalMicroAgent
from backend.agents.research.micro.price_history_micro import PriceHistoryMicroAgent
from backend.agents.research.micro.news_history_micro import NewsHistoryMicroAgent
from backend.agents.research.micro.sec_filing_micro import SecFilingMicroAgent
from backend.agents.research.micro.competitor_micro import CompetitorMicroAgent
from backend.agents.research.micro.research_verdict_micro import ResearchVerdictMicroAgent
from backend.models.research_report import ResearchReport

logger = logging.getLogger(__name__)


class StockResearchMaster(MasterAgent):
    name = "StockResearchMaster"
    improvement_interval = 10

    def __init__(self):
        super().__init__()
        # Instantiate micros once — they are stateless so reuse is fine
        self._fundamental = FundamentalMicroAgent()
        self._price_history = PriceHistoryMicroAgent()
        self._news_history = NewsHistoryMicroAgent()
        self._sec_filing = SecFilingMicroAgent()
        self._competitor = CompetitorMicroAgent()
        self._verdict = ResearchVerdictMicroAgent()

    # ------------------------------------------------------------------
    # MasterAgent interface
    # ------------------------------------------------------------------
    async def decompose(self, state: Any) -> list[tuple[MicroAgent, Any]]:
        """
        state is expected to be a dict with at least {"ticker": str}.
        Returns 5 parallel micro-agent tasks.
        """
        ticker: str = state["ticker"]
        tavily_client = state.get("tavily_client")

        return [
            (self._fundamental,  {"ticker": ticker}),
            (self._price_history, {"ticker": ticker}),
            (self._news_history,  {"ticker": ticker, "tavily_client": tavily_client}),
            (self._sec_filing,    {"ticker": ticker}),
            (self._competitor,    {"ticker": ticker}),
        ]

    async def synthesize(self, results: list[Any], state: Any) -> ResearchReport:
        """
        Merge all micro results into one flat dict, run the verdict micro,
        then build and return a ResearchReport.
        """
        ticker: str = state["ticker"]

        # Flatten all result dicts (later keys win on collision)
        merged: dict = {"ticker": ticker}
        for r in results:
            if isinstance(r, dict):
                merged.update(r)

        # Run verdict synchronously (needs combined data)
        try:
            verdict_result = await self._verdict.run(
                {"ticker": ticker, "research_data": merged}
            )
        except Exception as exc:
            logger.warning("[StockResearchMaster] verdict micro failed for %s: %s", ticker, exc)
            verdict_result = {
                "verdict": "NEUTRAL",
                "confidence": 0.0,
                "reasoning": "Verdict generation failed.",
                "suggested_strategy": "momentum",
            }

        # Build ResearchReport from merged + verdict
        report = ResearchReport(
            id=str(uuid.uuid4()),
            ticker=ticker,
            company_name=merged.get("company_name", ticker),
            # Fundamental
            pe_ratio=merged.get("pe_ratio"),
            revenue_growth_pct=merged.get("revenue_growth_pct", 0.0),
            gross_margin_pct=merged.get("gross_margin_pct", 0.0),
            debt_to_equity=merged.get("debt_to_equity", 0.0),
            earnings_surprise_pct=merged.get("earnings_surprise_pct", 0.0),
            # Technical
            atr_pct=merged.get("atr_pct", 0.0),
            trend_direction=merged.get("trend_direction", "sideways"),
            rsi_14=merged.get("rsi_14", 50.0),
            above_200ma=merged.get("above_200ma", False),
            support_level=merged.get("support_level", 0.0),
            resistance_level=merged.get("resistance_level", 0.0),
            # News history
            avg_move_on_catalyst_pct=merged.get("avg_move_on_catalyst_pct", 0.0),
            news_sensitivity=merged.get("news_sensitivity", "medium"),
            key_catalysts=merged.get("key_catalysts", []),
            # SEC / insider
            insider_buying_last_90d=merged.get("insider_buying_last_90d", False),
            institutional_ownership_change_pct=merged.get(
                "institutional_ownership_change_pct", 0.0
            ),
            # Competitors
            competitors=merged.get("competitors", []),
            relative_strength_vs_peers=merged.get("relative_strength", 0.0),
            # Verdict
            research_verdict=verdict_result.get("verdict", "NEUTRAL"),
            verdict_confidence=verdict_result.get("confidence", 0.0),
            verdict_reasoning=verdict_result.get("reasoning", ""),
            suggested_strategy=verdict_result.get("suggested_strategy", "momentum"),
        )

        # Feed self-improvement log
        self._log_run({
            "ticker": ticker,
            "verdict": report.research_verdict,
            "confidence": report.verdict_confidence,
            "micros_succeeded": len(results),
        })

        logger.info(
            "[StockResearchMaster] %s → %s (confidence=%.2f, strategy=%s)",
            ticker,
            report.research_verdict,
            report.verdict_confidence,
            report.suggested_strategy,
        )
        return report

    # ------------------------------------------------------------------
    # Convenience public method
    # ------------------------------------------------------------------
    async def run_for_ticker(
        self,
        ticker: str,
        tavily_client=None,
    ) -> ResearchReport:
        """
        High-level entry point. Builds state dict and calls self.run().

        Args:
            ticker: Stock ticker symbol (e.g. "NVDA")
            tavily_client: Optional pre-built TavilyClient instance.
                           If None, NewsHistoryMicro will try to build one
                           from settings.TAVILY_API_KEY.
        Returns:
            ResearchReport Pydantic model
        """
        state = {"ticker": ticker.upper(), "tavily_client": tavily_client}
        return await self.run(state)
