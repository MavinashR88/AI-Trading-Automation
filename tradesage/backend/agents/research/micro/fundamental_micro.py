"""
FundamentalMicroAgent
---------------------
Fetches fundamental financial metrics for a ticker using yfinance.
Returns P/E ratio, revenue growth, gross margin, debt/equity, and
earnings surprise. All sync yfinance calls are wrapped in
asyncio.to_thread() so the agent stays fully async.

task: {"ticker": str}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)


class FundamentalMicroAgent(MicroAgent):
    name = "FundamentalMicroAgent"
    timeout_seconds = 30.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        return await asyncio.to_thread(self._fetch, ticker)

    # ------------------------------------------------------------------
    # Sync worker (runs in thread pool)
    # ------------------------------------------------------------------
    def _fetch(self, ticker: str) -> dict:
        try:
            import yfinance as yf

            info = yf.Ticker(ticker).info or {}

            pe_ratio: float | None = info.get("trailingPE") or info.get("forwardPE")

            # Revenue growth: yfinance returns as a decimal (e.g. 0.12 = 12%)
            revenue_growth_raw = info.get("revenueGrowth")
            revenue_growth_pct: float = (
                float(revenue_growth_raw) * 100.0
                if revenue_growth_raw is not None
                else 0.0
            )

            gross_margins_raw = info.get("grossMargins")
            gross_margin_pct: float = (
                float(gross_margins_raw) * 100.0
                if gross_margins_raw is not None
                else 0.0
            )

            debt_to_equity: float = float(info.get("debtToEquity") or 0.0)

            # Earnings surprise = (actual EPS - estimated EPS) / |estimated EPS| * 100
            eps_actual = info.get("trailingEps")
            eps_estimate = info.get("epsForward")
            earnings_surprise_pct: float = 0.0
            if eps_actual is not None and eps_estimate and abs(eps_estimate) > 0:
                earnings_surprise_pct = (
                    (float(eps_actual) - float(eps_estimate)) / abs(float(eps_estimate))
                ) * 100.0

            company_name: str = info.get("longName") or info.get("shortName") or ticker

            return {
                "ticker": ticker,
                "company_name": company_name,
                "pe_ratio": pe_ratio,
                "revenue_growth_pct": round(revenue_growth_pct, 2),
                "gross_margin_pct": round(gross_margin_pct, 2),
                "debt_to_equity": round(debt_to_equity, 2),
                "earnings_surprise_pct": round(earnings_surprise_pct, 2),
            }

        except Exception as exc:
            logger.warning("[FundamentalMicroAgent] %s fetch failed: %s", ticker, exc)
            # Return safe defaults so the pipeline keeps running
            return {
                "ticker": ticker,
                "company_name": ticker,
                "pe_ratio": None,
                "revenue_growth_pct": 0.0,
                "gross_margin_pct": 0.0,
                "debt_to_equity": 0.0,
                "earnings_surprise_pct": 0.0,
            }
