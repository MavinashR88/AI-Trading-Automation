"""
OutOfSampleMicroAgent
---------------------
Validates that the algorithm performs acceptably on 2024 out-of-sample price data
fetched via yfinance. Win-rate degradation from backtest must not exceed 20 percentage
points.

task: {"algorithm": dict, "backtest_win_rate": float}
returns: ValidationCheck
"""
from __future__ import annotations

import asyncio
import logging

from backend.agents.base.micro import MicroAgent
from backend.models.validation_result import ValidationCheck

logger = logging.getLogger(__name__)


class OutOfSampleMicroAgent(MicroAgent):
    name = "OutOfSampleMicroAgent"
    timeout_seconds = 90.0

    MAX_DEGRADATION = 0.20  # 20 percentage-point drop allowed

    async def execute(self, task: dict) -> ValidationCheck:
        algorithm: dict = task["algorithm"]
        backtest_win_rate: float = float(task.get("backtest_win_rate", 0.0))
        return await asyncio.to_thread(self._run, algorithm, backtest_win_rate)

    # ------------------------------------------------------------------
    def _run(self, algorithm: dict, backtest_win_rate: float) -> ValidationCheck:
        try:
            import yfinance as yf
            import numpy as np

            ticker = algorithm.get("ticker", "SPY")

            # Fetch 2024 daily OHLCV
            hist = yf.Ticker(ticker).history(start="2024-01-01", end="2024-12-31", interval="1d")
            if hist.empty:
                return ValidationCheck(
                    name="out_of_sample",
                    passed=False,
                    detail=f"No 2024 data available for {ticker}",
                    metric={"oos_win_rate": 0.0, "degradation": 1.0},
                )

            # Rename columns to lowercase to match backtest expectations
            hist = hist.rename(columns={
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            hist.index.name = "date"

            # Run backtest on OOS data
            from backend.agents.simulation.micro.backtest_micro import BacktestMicroAgent
            bt = BacktestMicroAgent()
            result = bt._execute_rules(algorithm, hist, "oos_2024")  # noqa: SLF001

            oos_win_rate = result.get("win_rate", 0.0)
            degradation = backtest_win_rate - oos_win_rate

            passed = degradation <= self.MAX_DEGRADATION

            detail = (
                f"OOS win_rate={oos_win_rate:.1%} vs backtest={backtest_win_rate:.1%} "
                f"(degradation={degradation:.1%}, limit={self.MAX_DEGRADATION:.0%})"
            )

            return ValidationCheck(
                name="out_of_sample",
                passed=passed,
                score=float(oos_win_rate),
                detail=detail,
                metric={
                    "oos_win_rate": round(oos_win_rate, 4),
                    "backtest_win_rate": round(backtest_win_rate, 4),
                    "degradation": round(degradation, 4),
                    "n_trades": result.get("n_trades", 0),
                },
            )

        except Exception as exc:
            logger.warning("[OutOfSampleMicroAgent] failed: %s", exc)
            return ValidationCheck(
                name="out_of_sample",
                passed=False,
                detail=f"Out-of-sample check failed: {exc}",
                metric={"error": str(exc)},
            )
