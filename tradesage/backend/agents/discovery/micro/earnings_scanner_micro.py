"""
EarningsScannerMicro — finds stocks with recent earnings surprises >5% using yfinance.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import yfinance as yf

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)

SCAN_TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "JPM",
    "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "MRK", "ABBV", "CVX",
    "ORCL", "COST", "BAC", "KO", "NFLX", "PEP", "TMO", "CSCO", "ADBE", "MCD",
    "WFC", "IBM", "AMD", "CRM", "INTC", "GS", "NEE", "RTX", "TXN", "QCOM",
    "CAT", "SPGI", "AMGN", "UNP", "DE", "LMT", "BA", "BKNG", "SYK", "GILD",
]

SURPRISE_THRESHOLD = 5.0  # percent
LOOKBACK_DAYS = 90


def _scan_earnings(tickers: list[str]) -> list[dict]:
    results = []
    cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            cal = t.earnings_dates
            if cal is None or cal.empty:
                continue
            # earnings_dates index is datetime; filter to recent
            recent = cal[cal.index >= cutoff]
            if recent.empty:
                continue
            # Look for rows with both reported and estimate EPS
            for dt, row in recent.iterrows():
                reported = row.get("Reported EPS")
                estimate = row.get("EPS Estimate")
                if reported is None or estimate is None:
                    continue
                try:
                    reported = float(reported)
                    estimate = float(estimate)
                except (TypeError, ValueError):
                    continue
                if estimate == 0:
                    continue
                surprise_pct = ((reported - estimate) / abs(estimate)) * 100
                if abs(surprise_pct) >= SURPRISE_THRESHOLD:
                    results.append({
                        "ticker": ticker,
                        "surprise_pct": round(surprise_pct, 2),
                        "earnings_date": dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt),
                        "reason": f"earnings_surprise: {surprise_pct:+.1f}% vs estimate",
                    })
                    break  # one entry per ticker is enough
        except Exception as exc:
            logger.debug("[EarningsScannerMicro] %s skipped: %s", ticker, exc)
    return results


class EarningsScannerMicro(MicroAgent):
    name = "EarningsScannerMicro"
    timeout_seconds = 90.0

    async def execute(self, task: Any) -> list[dict]:
        tickers = (task or {}).get("tickers", SCAN_TICKERS)
        try:
            results = await asyncio.to_thread(_scan_earnings, tickers)
            logger.info("[EarningsScannerMicro] found %d earnings surprises", len(results))
            return results
        except Exception as exc:
            logger.warning("[EarningsScannerMicro] scan failed: %s", exc)
            return []
