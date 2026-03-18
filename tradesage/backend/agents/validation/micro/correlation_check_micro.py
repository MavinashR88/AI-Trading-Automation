"""
CorrelationCheckMicroAgent
--------------------------
Fetches return streams for all currently-deployed algorithms and computes
pairwise correlation with this algorithm's return stream.
Fails if any correlation exceeds 0.70 (portfolio over-concentration risk).

task: {"algorithm": dict, "data_router": DataRouter}
returns: ValidationCheck
"""
from __future__ import annotations

import asyncio
import logging

import numpy as np

from backend.agents.base.micro import MicroAgent
from backend.models.validation_result import ValidationCheck

logger = logging.getLogger(__name__)

MAX_CORRELATION = 0.70


class CorrelationCheckMicroAgent(MicroAgent):
    name = "CorrelationCheckMicroAgent"
    timeout_seconds = 90.0

    async def execute(self, task: dict) -> ValidationCheck:
        algorithm: dict = task["algorithm"]
        data_router = task["data_router"]
        return await asyncio.to_thread(self._run, algorithm, data_router)

    # ------------------------------------------------------------------
    def _run(self, algorithm: dict, data_router) -> ValidationCheck:
        try:
            import yfinance as yf

            ticker = algorithm.get("ticker", "")
            deployed = data_router.get_deployed_algorithms(active_only=True)

            if not deployed:
                return ValidationCheck(
                    name="correlation",
                    passed=True,
                    score=1.0,
                    detail="No deployed algorithms; correlation check trivially passes.",
                    metric={"max_correlation": 0.0, "n_deployed": 0},
                )

            # Fetch 1-year daily returns for candidate algorithm's ticker
            candidate_hist = yf.Ticker(ticker).history(period="1y", interval="1d")
            if candidate_hist.empty:
                return ValidationCheck(
                    name="correlation",
                    passed=True,
                    detail=f"Could not fetch price data for {ticker}; skipping correlation.",
                    metric={"max_correlation": 0.0},
                )

            candidate_returns = candidate_hist["Close"].pct_change().dropna().values

            correlations: list[float] = []
            names: list[str] = []

            for dep in deployed:
                dep_ticker = dep.get("ticker", "")
                if not dep_ticker or dep_ticker == ticker:
                    continue
                try:
                    dep_hist = yf.Ticker(dep_ticker).history(period="1y", interval="1d")
                    if dep_hist.empty:
                        continue
                    dep_returns = dep_hist["Close"].pct_change().dropna().values

                    # Align lengths
                    min_len = min(len(candidate_returns), len(dep_returns))
                    if min_len < 20:
                        continue

                    r = float(np.corrcoef(
                        candidate_returns[-min_len:], dep_returns[-min_len:]
                    )[0, 1])

                    if not np.isnan(r):
                        correlations.append(r)
                        names.append(dep_ticker)

                except Exception as inner_exc:
                    logger.debug(
                        "[CorrelationCheckMicroAgent] %s fetch failed: %s",
                        dep_ticker, inner_exc,
                    )

            if not correlations:
                return ValidationCheck(
                    name="correlation",
                    passed=True,
                    score=1.0,
                    detail="No comparable deployed tickers for correlation check.",
                    metric={"max_correlation": 0.0, "n_deployed": len(deployed)},
                )

            max_corr = float(max(correlations))
            max_idx = correlations.index(max_corr)
            most_correlated = names[max_idx] if max_idx < len(names) else "unknown"

            passed = max_corr <= MAX_CORRELATION

            detail = (
                f"Max correlation={max_corr:.3f} with {most_correlated} "
                f"(limit={MAX_CORRELATION}). Checked {len(correlations)} deployed tickers."
            )

            return ValidationCheck(
                name="correlation",
                passed=passed,
                score=float(1.0 - max_corr),
                detail=detail,
                metric={
                    "max_correlation": round(max_corr, 4),
                    "most_correlated_ticker": most_correlated,
                    "n_checked": len(correlations),
                    "correlations": {n: round(c, 4) for n, c in zip(names, correlations)},
                },
            )

        except Exception as exc:
            logger.warning("[CorrelationCheckMicroAgent] failed: %s", exc)
            return ValidationCheck(
                name="correlation",
                passed=False,
                detail=f"Correlation check failed: {exc}",
                metric={"error": str(exc)},
            )
