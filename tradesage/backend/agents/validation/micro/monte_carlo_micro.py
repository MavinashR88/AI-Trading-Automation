"""
MonteCarloMicroAgent
--------------------
Runs 1000 random shuffles of the algorithm's trade returns and checks whether
the real Sharpe ratio exceeds 95% of shuffled Sharpe ratios (p-value < 0.05).
Uses numpy for shuffles.

task: {"algorithm": dict, "trade_returns": list[float]}
returns: ValidationCheck
"""
from __future__ import annotations

import asyncio
import logging
import math

import numpy as np

from backend.agents.base.micro import MicroAgent
from backend.models.validation_result import ValidationCheck

logger = logging.getLogger(__name__)

N_SHUFFLES = 1000
P_VALUE_THRESHOLD = 0.05


class MonteCarloMicroAgent(MicroAgent):
    name = "MonteCarloMicroAgent"
    timeout_seconds = 60.0

    async def execute(self, task: dict) -> ValidationCheck:
        algorithm: dict = task["algorithm"]
        trade_returns: list[float] = task.get("trade_returns", [])
        return await asyncio.to_thread(self._run, algorithm, trade_returns)

    # ------------------------------------------------------------------
    def _run(self, algorithm: dict, trade_returns: list[float]) -> ValidationCheck:
        try:
            if len(trade_returns) < 10:
                return ValidationCheck(
                    name="monte_carlo",
                    passed=False,
                    detail=f"Insufficient trades for Monte Carlo ({len(trade_returns)} < 10)",
                    metric={"n_trades": len(trade_returns), "p_value": 1.0},
                )

            returns = np.array(trade_returns, dtype=float)
            real_sharpe = self._sharpe(returns)

            rng = np.random.default_rng(seed=42)
            shuffled_sharpes = np.empty(N_SHUFFLES)
            for i in range(N_SHUFFLES):
                shuffled = rng.permutation(returns)
                shuffled_sharpes[i] = self._sharpe(shuffled)

            # p-value = fraction of shuffles with Sharpe ≥ real Sharpe
            p_value = float((shuffled_sharpes >= real_sharpe).mean())
            percentile = float((shuffled_sharpes < real_sharpe).mean() * 100)

            passed = p_value < P_VALUE_THRESHOLD

            detail = (
                f"Real Sharpe={real_sharpe:.3f} exceeds {percentile:.1f}% of {N_SHUFFLES} "
                f"shuffled portfolios (p={p_value:.4f}, threshold={P_VALUE_THRESHOLD})"
            )

            return ValidationCheck(
                name="monte_carlo",
                passed=passed,
                score=float(1.0 - p_value),
                detail=detail,
                metric={
                    "real_sharpe": round(real_sharpe, 4),
                    "p_value": round(p_value, 6),
                    "percentile": round(percentile, 2),
                    "n_shuffles": N_SHUFFLES,
                    "n_trades": len(trade_returns),
                },
            )

        except Exception as exc:
            logger.warning("[MonteCarloMicroAgent] failed: %s", exc)
            return ValidationCheck(
                name="monte_carlo",
                passed=False,
                detail=f"Monte Carlo check failed: {exc}",
                metric={"error": str(exc)},
            )

    @staticmethod
    def _sharpe(returns: np.ndarray) -> float:
        """Annualised Sharpe ratio (assuming trade-level returns)."""
        if len(returns) < 2:
            return 0.0
        std = float(np.std(returns, ddof=1))
        if std == 0:
            return 0.0
        mean = float(np.mean(returns))
        return float((mean / std) * math.sqrt(252))
