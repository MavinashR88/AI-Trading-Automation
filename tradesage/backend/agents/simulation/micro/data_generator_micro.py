"""
DataGeneratorMicroAgent
-----------------------
Downloads REAL historical daily bars for the ticker.
Uses the maximum available history (up to 5 years) so the backtest
has enough bars to generate at least 20–50+ trades regardless of
how selective the entry conditions are.

Single scenario: "full" — all available daily bars with 60-bar warmup
prepended so EMAs and RSI are stable from bar 1.

If yfinance fails, falls back to synthetic GBM for robustness.

task: {"ticker": str, "base_price": float}
returns: dict[scenario_name, pd.DataFrame] with columns [open, high, low, close, volume]
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)

WARMUP = 60   # extra bars prepended so indicators (EMA50) have warmup history
MIN_BARS = 120  # minimum usable bars after warmup


class DataGeneratorMicroAgent(MicroAgent):
    name = "DataGeneratorMicroAgent"
    timeout_seconds = 30.0

    async def execute(self, task: dict) -> dict[str, pd.DataFrame]:
        import asyncio
        ticker: str = task["ticker"]
        base_price: float = float(task.get("base_price", 100.0))
        try:
            result = await asyncio.to_thread(self._fetch_real, ticker)
            if result:
                total_bars = sum(len(v) for v in result.values())
                logger.info(
                    "[DataGenerator] %s: %d real bars fetched across %d scenario(s)",
                    ticker, total_bars, len(result),
                )
                return result
        except Exception as exc:
            logger.warning(
                "[DataGenerator] Real data fetch failed for %s: %s — using synthetic fallback",
                ticker, exc,
            )
        return self._synthetic_fallback(ticker, base_price)

    # ── Real data ──────────────────────────────────────────────────

    def _fetch_real(self, ticker: str) -> dict[str, pd.DataFrame]:
        import yfinance as yf

        # Try 5 years first, fall back to max available
        for period in ("5y", "3y", "2y", "1y"):
            df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
            if df is not None and not df.empty and len(df) >= MIN_BARS + WARMUP:
                break
        else:
            return {}

        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]

        df = df[["open", "high", "low", "close", "volume"]].dropna()
        df = df.sort_index()

        if len(df) < MIN_BARS:
            return {}

        logger.info(
            "[DataGenerator] %s: %d total bars downloaded (period spans %s → %s)",
            ticker, len(df),
            str(df.index[0])[:10], str(df.index[-1])[:10],
        )

        # Single "full" scenario — everything we have
        return {"full": df.copy()}

    # ── Synthetic fallback (GBM) ───────────────────────────────────

    def _synthetic_fallback(self, ticker: str, base_price: float) -> dict[str, pd.DataFrame]:
        logger.warning("[DataGenerator] Using synthetic GBM fallback for %s", ticker)
        rng = np.random.default_rng(seed=abs(hash(ticker)) % (2**31))
        BARS = 1260  # ~5 years of daily bars

        def gbm(s0, mu, sigma):
            r = rng.normal(mu, sigma, BARS)
            return np.exp(np.log(s0) + np.cumsum(r))

        def build(closes, avg_vol=1_000_000):
            n = len(closes)
            r = np.random.default_rng(42)
            hr = closes * r.uniform(0.005, 0.015, n)
            highs = closes + hr * r.uniform(0.3, 1.0, n)
            lows = np.maximum(closes - hr * r.uniform(0.3, 1.0, n), closes * 0.5)
            opens = np.roll(closes, 1); opens[0] = closes[0]
            vols = r.lognormal(np.log(avg_vol), 0.4, n).astype(int)
            dates = pd.bdate_range("2020-01-02", periods=n)
            return pd.DataFrame(
                {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
                index=dates,
            )

        return {"full": build(gbm(base_price, 0.0003, 0.015))}
