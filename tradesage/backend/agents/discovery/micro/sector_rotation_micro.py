"""
SectorRotationMicro — finds the sector ETF with strongest 5-day momentum using yfinance.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import yfinance as yf

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)

SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE"]

# Representative tickers per sector ETF
SECTOR_TICKERS: dict[str, list[str]] = {
    "XLK":  ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CSCO", "ADBE", "CRM", "AMD", "TXN"],
    "XLF":  ["JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "CB", "MMC", "AIG"],
    "XLE":  ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "HAL", "BKR"],
    "XLV":  ["LLY", "UNH", "JNJ", "MRK", "ABBV", "TMO", "ABT", "AMGN", "GILD", "ISRG"],
    "XLI":  ["RTX", "CAT", "UNP", "DE", "LMT", "BA", "HON", "GE", "ETN", "EMR"],
    "XLY":  ["AMZN", "TSLA", "MCD", "HD", "BKNG", "ABNB", "NKE", "LOW", "TGT", "SBUX"],
    "XLP":  ["PG", "KO", "PEP", "COST", "WMT", "CL", "MO", "PM", "GIS", "KMB"],
    "XLU":  ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PCG", "ED", "XEL"],
    "XLB":  ["LIN", "APD", "ECL", "SHW", "NEM", "FCX", "NUE", "VMC", "MLM", "DOW"],
    "XLRE": ["PLD", "AMT", "EQIX", "PSA", "O", "DLR", "WELL", "SPG", "EQR", "AVB"],
}


def _compute_momentum(etfs: list[str]) -> dict:
    """Return the leading sector ETF with its 5-day momentum."""
    best_etf = None
    best_momentum = float("-inf")
    all_momenta: dict[str, float] = {}

    for etf in etfs:
        try:
            t = yf.Ticker(etf)
            hist = t.history(period="10d")
            if hist.empty or len(hist) < 6:
                continue
            close_prices = hist["Close"]
            momentum = ((close_prices.iloc[-1] - close_prices.iloc[-6]) / close_prices.iloc[-6]) * 100
            all_momenta[etf] = round(float(momentum), 2)
            if momentum > best_momentum:
                best_momentum = momentum
                best_etf = etf
        except Exception as exc:
            logger.debug("[SectorRotationMicro] %s skipped: %s", etf, exc)

    if best_etf is None:
        return {}

    return {
        "leading_sector_etf": best_etf,
        "momentum_pct": round(best_momentum, 2),
        "tickers_in_sector": SECTOR_TICKERS.get(best_etf, []),
        "all_sector_momenta": all_momenta,
    }


class SectorRotationMicro(MicroAgent):
    name = "SectorRotationMicro"
    timeout_seconds = 60.0

    async def execute(self, task: Any) -> dict:
        etfs = (task or {}).get("etfs", SECTOR_ETFS)
        try:
            result = await asyncio.to_thread(_compute_momentum, etfs)
            if result:
                logger.info(
                    "[SectorRotationMicro] leading sector: %s at %.2f%%",
                    result.get("leading_sector_etf"),
                    result.get("momentum_pct", 0),
                )
            return result
        except Exception as exc:
            logger.warning("[SectorRotationMicro] scan failed: %s", exc)
            return {}
