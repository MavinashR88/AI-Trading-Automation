"""
VolumeScannerMicro — finds stocks with >3× average volume using yfinance.
Scans a predefined list of 100 liquid tickers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import yfinance as yf

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)

LIQUID_TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "JPM",
    "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "MRK", "ABBV", "CVX",
    "ORCL", "COST", "BAC", "KO", "NFLX", "PEP", "TMO", "CSCO", "ADBE", "MCD",
    "WFC", "IBM", "AMD", "CRM", "INTC", "GS", "NEE", "RTX", "TXN", "QCOM",
    "CAT", "SPGI", "AMGN", "UNP", "DE", "LMT", "BA", "BKNG", "SYK", "GILD",
    "SCHW", "MMC", "PLD", "ADP", "BLK", "CB", "ISRG", "MO", "DUK", "SO",
    "ZTS", "NOW", "REGN", "VRTX", "MRNA", "PANW", "SNPS", "KLAC", "MRVL", "LRCX",
    "AMAT", "MU", "NXPI", "CDNS", "FTNT", "MCHP", "ON", "SMCI", "CRWD", "DDOG",
    "SNOW", "PLTR", "COIN", "SQ", "SHOP", "UBER", "LYFT", "ABNB", "DASH", "RBLX",
    "HOOD", "RIVN", "LCID", "NIO", "XPEV", "LI", "F", "GM", "GLD", "TLT",
]

VOLUME_THRESHOLD = 3.0


def _scan_volume(tickers: list[str]) -> list[dict]:
    results = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="20d")
            if hist.empty or len(hist) < 5:
                continue
            avg_vol = hist["Volume"].iloc[:-1].mean()
            if avg_vol <= 0:
                continue
            current_vol = hist["Volume"].iloc[-1]
            ratio = current_vol / avg_vol
            if ratio >= VOLUME_THRESHOLD:
                price = float(hist["Close"].iloc[-1])
                results.append({
                    "ticker": ticker,
                    "volume_ratio": round(ratio, 2),
                    "price": round(price, 2),
                    "reason": f"volume_spike: {ratio:.1f}x avg volume",
                })
        except Exception as exc:
            logger.debug("[VolumeScannerMicro] %s skipped: %s", ticker, exc)
    return results


class VolumeScannerMicro(MicroAgent):
    name = "VolumeScannerMicro"
    timeout_seconds = 60.0

    async def execute(self, task: Any) -> list[dict]:
        tickers = (task or {}).get("tickers", LIQUID_TICKERS)
        try:
            results = await asyncio.to_thread(_scan_volume, tickers)
            logger.info("[VolumeScannerMicro] found %d volume spikes", len(results))
            return results
        except Exception as exc:
            logger.warning("[VolumeScannerMicro] scan failed: %s", exc)
            return []
