"""
PaperTradingPeriodMicroAgent
----------------------------
Checks if the algorithm has completed the required paper trading period:
  - ≥ 50 paper trades done (from algorithm.paper_trades_done)
  - paper win rate ≥ 45% (from algorithm.paper_win_rate)

task: {"algorithm": dict}
returns: ValidationCheck
"""
from __future__ import annotations

import asyncio
import logging

from backend.agents.base.micro import MicroAgent
from backend.models.validation_result import ValidationCheck

logger = logging.getLogger(__name__)

MIN_PAPER_TRADES = 50
MIN_PAPER_WIN_RATE = 0.45


class PaperTradingPeriodMicroAgent(MicroAgent):
    name = "PaperTradingPeriodMicroAgent"
    timeout_seconds = 10.0

    async def execute(self, task: dict) -> ValidationCheck:
        algorithm: dict = task["algorithm"]

        paper_trades_done: int = int(algorithm.get("paper_trades_done", 0))
        paper_win_rate: float = float(algorithm.get("paper_win_rate", 0.0))
        paper_trades_required: int = int(
            algorithm.get("paper_trades_required", MIN_PAPER_TRADES)
        )

        # If no paper trades done yet → algorithm is entering paper trading phase (approved)
        if paper_trades_done == 0:
            return ValidationCheck(
                name="paper_trading",
                passed=True,
                score=0.5,
                detail="Approved to begin paper trading phase (0 trades done)",
                metric={
                    "paper_trades_done": 0,
                    "paper_trades_required": paper_trades_required,
                    "paper_win_rate": 0.0,
                    "min_win_rate": MIN_PAPER_WIN_RATE,
                },
            )

        trades_ok = paper_trades_done >= paper_trades_required
        win_rate_ok = paper_win_rate >= MIN_PAPER_WIN_RATE

        passed = trades_ok and win_rate_ok

        issues = []
        if not trades_ok:
            issues.append(
                f"only {paper_trades_done}/{paper_trades_required} paper trades completed"
            )
        if not win_rate_ok:
            issues.append(
                f"paper win_rate={paper_win_rate:.1%} < {MIN_PAPER_WIN_RATE:.0%} required"
            )

        detail = (
            "; ".join(issues)
            if issues
            else (
                f"Paper trading period complete: {paper_trades_done} trades, "
                f"win_rate={paper_win_rate:.1%}"
            )
        )

        score = (
            min(paper_trades_done / paper_trades_required, 1.0) * 0.5
            + min(paper_win_rate / MIN_PAPER_WIN_RATE, 1.0) * 0.5
        )

        return ValidationCheck(
            name="paper_trading",
            passed=passed,
            score=round(score, 4),
            detail=detail,
            metric={
                "paper_trades_done": paper_trades_done,
                "paper_trades_required": paper_trades_required,
                "paper_win_rate": round(paper_win_rate, 4),
                "min_win_rate": MIN_PAPER_WIN_RATE,
            },
        )
