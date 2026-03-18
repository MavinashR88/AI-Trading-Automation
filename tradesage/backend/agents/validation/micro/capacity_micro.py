"""
CapacityMicroAgent
------------------
Checks whether the algorithm's max position size is within market capacity limits.
Fetches average daily dollar volume via yfinance and verifies that the intended
position size does not exceed 0.5% of average daily volume.

task: {"algorithm": dict, "ticker": str, "position_size_usd": float}
returns: ValidationCheck
"""
from __future__ import annotations

import asyncio
import logging

import numpy as np

from backend.agents.base.micro import MicroAgent
from backend.models.validation_result import ValidationCheck

logger = logging.getLogger(__name__)

MAX_VOLUME_PCT = 0.005  # 0.5% of average daily dollar volume


class CapacityMicroAgent(MicroAgent):
    name = "CapacityMicroAgent"
    timeout_seconds = 30.0

    async def execute(self, task: dict) -> ValidationCheck:
        algorithm: dict = task["algorithm"]
        ticker: str = task.get("ticker") or algorithm.get("ticker", "")
        position_size_usd: float = float(task.get("position_size_usd", 10_000.0))
        return await asyncio.to_thread(self._run, algorithm, ticker, position_size_usd)

    # ------------------------------------------------------------------
    def _run(self, algorithm: dict, ticker: str, position_size_usd: float) -> ValidationCheck:
        try:
            import yfinance as yf

            if not ticker:
                return ValidationCheck(
                    name="capacity",
                    passed=False,
                    detail="No ticker provided for capacity check.",
                    metric={},
                )

            hist = yf.Ticker(ticker).history(period="3mo", interval="1d")
            if hist.empty:
                return ValidationCheck(
                    name="capacity",
                    passed=True,
                    detail=f"No volume data for {ticker}; capacity check skipped.",
                    metric={"position_size_usd": position_size_usd},
                )

            # Average daily dollar volume = close × volume
            dollar_volumes = hist["Close"] * hist["Volume"]
            avg_daily_volume_usd = float(dollar_volumes.mean())

            if avg_daily_volume_usd <= 0:
                return ValidationCheck(
                    name="capacity",
                    passed=True,
                    detail="Zero volume data; capacity check skipped.",
                    metric={"avg_daily_volume_usd": 0.0},
                )

            volume_pct = position_size_usd / avg_daily_volume_usd
            passed = volume_pct <= MAX_VOLUME_PCT

            detail = (
                f"Position ${position_size_usd:,.0f} = {volume_pct:.4%} of "
                f"avg daily volume ${avg_daily_volume_usd:,.0f} (limit={MAX_VOLUME_PCT:.1%})"
            )

            return ValidationCheck(
                name="capacity",
                passed=passed,
                score=float(min(1.0, MAX_VOLUME_PCT / volume_pct) if volume_pct > 0 else 1.0),
                detail=detail,
                metric={
                    "position_size_usd": round(position_size_usd, 2),
                    "avg_daily_volume_usd": round(avg_daily_volume_usd, 0),
                    "volume_pct": round(volume_pct, 6),
                    "limit_pct": MAX_VOLUME_PCT,
                },
            )

        except Exception as exc:
            logger.warning("[CapacityMicroAgent] failed: %s", exc)
            return ValidationCheck(
                name="capacity",
                passed=False,
                detail=f"Capacity check failed: {exc}",
                metric={"error": str(exc)},
            )
