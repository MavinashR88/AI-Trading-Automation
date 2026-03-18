"""
ShortSqueezeMicro — finds tickers with high short interest + recent volume spike using yfinance.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import yfinance as yf

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)

# Tickers historically prone to short squeezes + high short interest candidates
SCAN_TICKERS = [
    "GME", "AMC", "BBBY", "WISH", "CLOV", "MVIS", "WKHS", "RIDE", "NKLA", "SPCE",
    "PLTR", "HOOD", "RIVN", "LCID", "FFIE", "MULN", "ATER", "BBIG", "PROG", "SNDL",
    "TSLA", "COIN", "MSTR", "BYND", "OPEN", "SOFI", "UWMC", "SKLZ", "DKNG", "PENN",
    "SMCI", "NVDA", "AMD", "CRWD", "SNOW", "DDOG", "UPST", "AFRM", "LMND", "RBLX",
    "PTON", "TDOC", "ZM", "ROKU", "SQ", "SHOP", "SE", "DOCU", "BILL", "NET",
]

SHORT_INTEREST_THRESHOLD = 10.0  # percent of float
VOLUME_SPIKE_THRESHOLD = 2.0


def _scan_short_squeeze(tickers: list[str]) -> list[dict]:
    results = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}

            short_pct = info.get("shortPercentOfFloat") or info.get("shortRatio")
            if short_pct is None:
                continue
            # shortPercentOfFloat is 0-1 float, shortRatio is days-to-cover
            # Normalize: if it looks like a 0-1 decimal, convert to percent
            if short_pct < 1.0:
                short_pct_display = short_pct * 100
            else:
                short_pct_display = float(short_pct)

            if short_pct_display < SHORT_INTEREST_THRESHOLD:
                continue

            # Check volume spike
            hist = t.history(period="20d")
            if hist.empty or len(hist) < 5:
                continue
            avg_vol = hist["Volume"].iloc[:-1].mean()
            if avg_vol <= 0:
                continue
            current_vol = hist["Volume"].iloc[-1]
            vol_ratio = current_vol / avg_vol

            if vol_ratio < VOLUME_SPIKE_THRESHOLD:
                continue

            results.append({
                "ticker": ticker,
                "short_interest_pct": round(short_pct_display, 2),
                "volume_ratio": round(vol_ratio, 2),
                "reason": (
                    f"short_squeeze: {short_pct_display:.1f}% short interest, "
                    f"{vol_ratio:.1f}x volume spike"
                ),
            })
        except Exception as exc:
            logger.debug("[ShortSqueezeMicro] %s skipped: %s", ticker, exc)
    return results


class ShortSqueezeMicro(MicroAgent):
    name = "ShortSqueezeMicro"
    timeout_seconds = 90.0

    async def execute(self, task: Any) -> list[dict]:
        tickers = (task or {}).get("tickers", SCAN_TICKERS)
        try:
            results = await asyncio.to_thread(_scan_short_squeeze, tickers)
            logger.info("[ShortSqueezeMicro] found %d short squeeze candidates", len(results))
            return results
        except Exception as exc:
            logger.warning("[ShortSqueezeMicro] scan failed: %s", exc)
            return []
