"""
StatsMicroAgent
---------------
Aggregates a list of SimResult dicts into a StatsResult dict.
Computes: Sharpe ratio, Sortino ratio, profit factor, avg win/loss, expectancy.
Uses scipy.stats if available, falls back to manual math.

task: {"sim_results": list[dict]}
returns: StatsResult dict
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import numpy as np

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)


class StatsMicroAgent(MicroAgent):
    name = "StatsMicroAgent"
    timeout_seconds = 30.0

    async def execute(self, task: dict) -> dict:
        sim_results: list[dict] = task["sim_results"]
        return await asyncio.to_thread(self._compute, sim_results)

    # ------------------------------------------------------------------
    def _compute(self, sim_results: list[dict]) -> dict:
        # Collect all trade returns across all scenarios
        all_returns: list[float] = []
        for sr in sim_results:
            all_returns.extend(sr.get("trade_returns", []))

        if not all_returns:
            return self._zero_stats()

        returns = np.array(all_returns, dtype=float)
        n = len(returns)

        mean_ret = float(returns.mean())
        std_ret = float(returns.std(ddof=1)) if n > 1 else 0.0

        # ── Sharpe (annualised, assuming ~252 trades/year proxy) ────────
        annual_factor = math.sqrt(252)
        if std_ret > 0:
            sharpe = (mean_ret / std_ret) * annual_factor
        else:
            sharpe = 0.0

        # ── Sortino (downside deviation only) ───────────────────────────
        downside = returns[returns < 0]
        if len(downside) > 1:
            downside_std = float(np.std(downside, ddof=1))
        elif len(downside) == 1:
            downside_std = float(abs(downside[0]))
        else:
            downside_std = 0.0

        sortino = (mean_ret / downside_std * annual_factor) if downside_std > 0 else 0.0

        # ── Profit factor ────────────────────────────────────────────────
        gross_wins = float(returns[returns > 0].sum()) if (returns > 0).any() else 0.0
        gross_losses = float(abs(returns[returns < 0].sum())) if (returns < 0).any() else 0.0
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 0.0

        # ── Win/Loss averages ────────────────────────────────────────────
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
        win_rate = len(wins) / n

        # ── Expectancy = win_rate * avg_win + loss_rate * avg_loss ───────
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

        # ── Overall win_rate / drawdown from scenario results ────────────
        scenario_win_rates = [
            sr["win_rate"] for sr in sim_results if sr.get("n_trades", 0) > 0
        ]
        overall_win_rate = float(np.mean(scenario_win_rates)) if scenario_win_rates else 0.0

        drawdowns = [sr.get("max_drawdown_pct", 0.0) for sr in sim_results]
        max_drawdown = float(max(drawdowns)) if drawdowns else 0.0

        # ── p-value via scipy (optional) ─────────────────────────────────
        p_value = 1.0
        try:
            from scipy import stats as scipy_stats
            if n >= 5 and std_ret > 0:
                t_stat, p_value = scipy_stats.ttest_1samp(returns, 0.0)
                p_value = float(p_value)
        except ImportError:
            # Manual two-sided t-test p-value approximation
            if n >= 5 and std_ret > 0:
                t_stat = (mean_ret / (std_ret / math.sqrt(n)))
                # Approximate p-value using normal CDF for large n
                p_value = float(2.0 * (1.0 - self._norm_cdf(abs(t_stat))))

        return {
            "n_trades": n,
            "win_rate": round(overall_win_rate, 4),
            "mean_return": round(mean_ret, 6),
            "std_return": round(std_ret, 6),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "profit_factor": round(profit_factor, 4),
            "avg_win": round(avg_win, 6),
            "avg_loss": round(avg_loss, 6),
            "expectancy": round(expectancy, 6),
            "max_drawdown_pct": round(max_drawdown, 2),
            "p_value": round(p_value, 6),
            "gross_wins": round(gross_wins, 4),
            "gross_losses": round(gross_losses, 4),
        }

    def _zero_stats(self) -> dict:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "mean_return": 0.0,
            "std_return": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "max_drawdown_pct": 0.0,
            "p_value": 1.0,
            "gross_wins": 0.0,
            "gross_losses": 0.0,
        }

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """Standard normal CDF using the error function."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))
