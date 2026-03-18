"""
PriceHistoryMicroAgent
----------------------
Computes technical metrics from price history using yfinance:
  - ATR (14-day) as a percentage of current price
  - RSI (14-period)
  - Trend direction (above / below 200-day MA)
  - Support level  (52-week low)
  - Resistance level (52-week high)

All sync yfinance calls are wrapped in asyncio.to_thread().

task: {"ticker": str}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)


class PriceHistoryMicroAgent(MicroAgent):
    name = "PriceHistoryMicroAgent"
    timeout_seconds = 30.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        return await asyncio.to_thread(self._compute, ticker)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _compute_rsi(closes, period: int = 14) -> float:
        """Wilder-smoothed RSI."""
        import statistics

        if len(closes) < period + 1:
            return 50.0

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0.0) for d in deltas]
        losses = [abs(min(d, 0.0)) for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1.0 + rs)), 2)

    @staticmethod
    def _compute_atr(highs, lows, closes, period: int = 14) -> float:
        """Average True Range over `period` days."""
        if len(closes) < 2:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        if not trs:
            return 0.0
        return sum(trs[-period:]) / min(len(trs), period)

    # ------------------------------------------------------------------
    # Sync worker (runs in thread pool)
    # ------------------------------------------------------------------
    def _compute(self, ticker: str) -> dict:
        try:
            import yfinance as yf

            # Fetch ~1 year of daily data for 200 MA + 52-week range
            hist = yf.Ticker(ticker).history(period="1y")
            if hist.empty or len(hist) < 15:
                raise ValueError("Insufficient price history")

            closes = hist["Close"].tolist()
            highs = hist["High"].tolist()
            lows = hist["Low"].tolist()

            current_price = closes[-1]

            # ATR as % of price
            atr = self._compute_atr(highs, lows, closes)
            atr_pct = round((atr / current_price) * 100.0, 3) if current_price else 0.0

            # RSI
            rsi_14 = self._compute_rsi(closes)

            # 200-day MA trend
            ma200 = sum(closes[-200:]) / min(len(closes), 200)
            above_200ma = current_price > ma200

            if above_200ma:
                # Short-term slope: compare last 20 days
                if len(closes) >= 20 and closes[-20] > 0:
                    slope_pct = (closes[-1] - closes[-20]) / closes[-20]
                    trend_direction = "uptrend" if slope_pct > 0.01 else "sideways"
                else:
                    trend_direction = "uptrend"
            else:
                if len(closes) >= 20 and closes[-20] > 0:
                    slope_pct = (closes[-1] - closes[-20]) / closes[-20]
                    trend_direction = "downtrend" if slope_pct < -0.01 else "sideways"
                else:
                    trend_direction = "downtrend"

            # 52-week support / resistance
            year_highs = highs[-252:] if len(highs) >= 252 else highs
            year_lows = lows[-252:] if len(lows) >= 252 else lows
            support_level = round(min(year_lows), 4)
            resistance_level = round(max(year_highs), 4)

            return {
                "ticker": ticker,
                "atr_pct": atr_pct,
                "rsi_14": rsi_14,
                "above_200ma": above_200ma,
                "trend_direction": trend_direction,
                "support_level": support_level,
                "resistance_level": resistance_level,
            }

        except Exception as exc:
            logger.warning("[PriceHistoryMicroAgent] %s failed: %s", ticker, exc)
            return {
                "ticker": ticker,
                "atr_pct": 0.0,
                "rsi_14": 50.0,
                "above_200ma": False,
                "trend_direction": "sideways",
                "support_level": 0.0,
                "resistance_level": 0.0,
            }
