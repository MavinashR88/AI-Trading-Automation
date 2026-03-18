"""
CompetitorMicroAgent
--------------------
Uses yfinance to get the ticker's sector/industry, maps it to a
hardcoded list of well-known competitors, then computes a
relative-strength score by comparing 20-day returns of the ticker
vs. its peer group.

relative_strength ranges from -1.0 (far underperforming peers)
to +1.0 (far outperforming peers).

task: {"ticker": str}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded sector → competitor mapping
# Covers the 20 default TradeSage tickers plus common sectors.
# ---------------------------------------------------------------------------
SECTOR_COMPETITORS: dict[str, list[str]] = {
    # Technology – semiconductors
    "Semiconductors": ["NVDA", "AMD", "INTC", "QCOM", "AVGO", "MU", "AMAT", "SMCI"],
    "Semiconductor Equipment & Materials": ["NVDA", "AMD", "INTC", "QCOM", "AVGO", "MU"],
    # Technology – software / internet
    "Software—Infrastructure": ["MSFT", "GOOGL", "ORCL", "IBM", "CSCO"],
    "Software—Application": ["MSFT", "ORCL", "CRM", "SAP", "ADBE"],
    "Internet Content & Information": ["GOOGL", "META", "SNAP", "PINS", "TWTR"],
    "Consumer Electronics": ["AAPL", "MSFT", "SONY", "SSNLF"],
    # Communication services
    "Internet Retail": ["AMZN", "SHOP", "BABA", "JD", "EBAY"],
    # Automotive
    "Auto Manufacturers": ["TSLA", "GM", "F", "RIVN", "NIO", "LCID"],
    # Financials
    "Banks—Diversified": ["JPM", "BAC", "WFC", "C", "GS", "MS"],
    "Capital Markets": ["GS", "MS", "JPM", "SCHW", "BLK"],
    # Energy
    "Oil & Gas Integrated": ["XOM", "CVX", "BP", "SHEL", "TTE"],
    "Oil & Gas E&P": ["XOM", "CVX", "COP", "OXY", "EOG"],
    # Healthcare
    "Drug Manufacturers—General": ["LLY", "PFE", "MRK", "JNJ", "ABBV"],
    "Biotechnology": ["MRNA", "BIIB", "GILD", "REGN", "AMGN"],
    # ETFs / indexes — use sector ETFs as peers
    "Exchange Traded Fund": ["SPY", "QQQ", "IWM", "DIA", "VTI"],
    # Precious metals
    "Gold": ["GLD", "IAU", "GDX", "GDXJ", "NEM"],
    # Bonds
    "Government Bonds": ["TLT", "IEF", "SHY", "BND", "AGG"],
    # Catch-all
    "default": ["SPY", "QQQ", "IWM"],
}

# Map industry keywords → sector key (for fuzzy matching)
INDUSTRY_KEYWORD_MAP: dict[str, str] = {
    "semiconductor": "Semiconductors",
    "software": "Software—Infrastructure",
    "internet": "Internet Content & Information",
    "retail": "Internet Retail",
    "auto": "Auto Manufacturers",
    "bank": "Banks—Diversified",
    "oil": "Oil & Gas Integrated",
    "pharma": "Drug Manufacturers—General",
    "biotech": "Biotechnology",
    "gold": "Gold",
    "bond": "Government Bonds",
    "etf": "Exchange Traded Fund",
}


def _lookup_competitors(ticker: str, sector: str, industry: str) -> list[str]:
    """Return competitor list, excluding the ticker itself."""
    # Try exact sector match first
    for key, peers in SECTOR_COMPETITORS.items():
        if sector and sector.lower() in key.lower():
            return [p for p in peers if p.upper() != ticker.upper()]

    # Try industry keyword match
    industry_lower = (industry or "").lower()
    for kw, sector_key in INDUSTRY_KEYWORD_MAP.items():
        if kw in industry_lower:
            peers = SECTOR_COMPETITORS.get(sector_key, [])
            return [p for p in peers if p.upper() != ticker.upper()]

    return SECTOR_COMPETITORS["default"]


class CompetitorMicroAgent(MicroAgent):
    name = "CompetitorMicroAgent"
    timeout_seconds = 40.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        return await asyncio.to_thread(self._compute, ticker)

    # ------------------------------------------------------------------
    # Sync worker
    # ------------------------------------------------------------------
    def _compute(self, ticker: str) -> dict:
        try:
            import yfinance as yf

            info = yf.Ticker(ticker).info or {}
            sector = info.get("sector", "")
            industry = info.get("industry", "")

            competitors = _lookup_competitors(ticker, sector, industry)

            # Compute relative strength: ticker 20-day return vs peer avg
            rel_strength = self._relative_strength(ticker, competitors[:5])

            return {
                "ticker": ticker,
                "sector": sector,
                "industry": industry,
                "competitors": competitors[:8],
                "relative_strength": round(rel_strength, 4),
            }
        except Exception as exc:
            logger.warning("[CompetitorMicroAgent] %s failed: %s", ticker, exc)
            return {
                "ticker": ticker,
                "sector": "",
                "industry": "",
                "competitors": SECTOR_COMPETITORS["default"],
                "relative_strength": 0.0,
            }

    @staticmethod
    def _relative_strength(ticker: str, peers: list[str]) -> float:
        """
        Compare ticker's 20-day return against peers' average 20-day return.
        Returns a value in roughly [-1, +1].
        """
        try:
            import yfinance as yf

            all_tickers = [ticker] + peers
            data = yf.download(all_tickers, period="1mo", progress=False, auto_adjust=True)

            if data.empty:
                return 0.0

            close = data["Close"] if "Close" in data.columns else data

            def _ret(t: str) -> float:
                if t not in close.columns:
                    return 0.0
                series = close[t].dropna()
                if len(series) < 2:
                    return 0.0
                return float((series.iloc[-1] - series.iloc[0]) / series.iloc[0])

            ticker_ret = _ret(ticker)
            peer_rets = [_ret(p) for p in peers if p in close.columns]
            if not peer_rets:
                return 0.0
            peer_avg = sum(peer_rets) / len(peer_rets)

            # Normalize difference to [-1, 1] using a ±10% range
            diff = ticker_ret - peer_avg
            normalized = max(-1.0, min(1.0, diff / 0.10))
            return normalized
        except Exception as exc:
            logger.debug("[CompetitorMicroAgent] relative strength calc failed: %s", exc)
            return 0.0
