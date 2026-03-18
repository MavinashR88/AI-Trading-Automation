"""
OptionsFlowMicro — finds tickers with unusual call/put volume (>3× average) via yfinance.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import yfinance as yf

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)

SCAN_TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "JPM",
    "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "MRK", "ABBV", "CVX",
    "ORCL", "COST", "BAC", "KO", "NFLX", "PEP", "TMO", "CSCO", "ADBE", "MCD",
    "WFC", "IBM", "AMD", "CRM", "INTC", "GS", "RTX", "TXN", "QCOM", "SPY",
    "QQQ", "IWM", "SMCI", "CRWD", "PLTR", "COIN", "MRNA", "SNOW", "DDOG", "PANW",
]

VOLUME_SPIKE_THRESHOLD = 3.0


def _scan_options(tickers: list[str]) -> list[dict]:
    results = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            exps = t.options
            if not exps:
                continue
            # Use nearest expiration
            chain = t.option_chain(exps[0])
            calls = chain.calls
            puts = chain.puts
            if calls.empty and puts.empty:
                continue

            total_call_vol = int(calls["volume"].fillna(0).sum()) if not calls.empty else 0
            total_put_vol = int(puts["volume"].fillna(0).sum()) if not puts.empty else 0
            total_call_oi = int(calls["openInterest"].fillna(0).sum()) if not calls.empty else 0
            total_put_oi = int(puts["openInterest"].fillna(0).sum()) if not puts.empty else 0

            total_vol = total_call_vol + total_put_vol
            total_oi = total_call_oi + total_put_oi
            if total_oi <= 0 or total_vol <= 0:
                continue

            # Volume-to-OI ratio as proxy for unusual activity (OI approximates "average" expectation)
            vol_oi_ratio = total_vol / total_oi
            if vol_oi_ratio < VOLUME_SPIKE_THRESHOLD:
                continue

            cp_ratio = (total_call_vol / total_put_vol) if total_put_vol > 0 else float(total_call_vol)
            results.append({
                "ticker": ticker,
                "call_put_ratio": round(cp_ratio, 2),
                "volume": total_vol,
                "reason": (
                    f"options_flow: vol/OI={vol_oi_ratio:.1f}x, "
                    f"C/P={cp_ratio:.1f}, total_vol={total_vol:,}"
                ),
            })
        except Exception as exc:
            logger.debug("[OptionsFlowMicro] %s skipped: %s", ticker, exc)
    return results


class OptionsFlowMicro(MicroAgent):
    name = "OptionsFlowMicro"
    timeout_seconds = 90.0

    async def execute(self, task: Any) -> list[dict]:
        tickers = (task or {}).get("tickers", SCAN_TICKERS)
        try:
            results = await asyncio.to_thread(_scan_options, tickers)
            logger.info("[OptionsFlowMicro] found %d unusual options tickers", len(results))
            return results
        except Exception as exc:
            logger.warning("[OptionsFlowMicro] scan failed: %s", exc)
            return []
