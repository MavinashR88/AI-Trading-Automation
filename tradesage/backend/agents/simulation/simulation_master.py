"""
SimulationMasterAgent
---------------------
Orchestrates the full simulation pipeline for a TradingAlgorithm.

Uses a single "full" scenario (up to 5 years of real daily bars) so
the backtest accumulates as many trades as possible before scoring.
The algorithm is NOT discarded if it fails — the pipeline retries it
on the next run with the same code.

Pipeline:
  1. DataGeneratorMicroAgent  → single large historical dataset
  2. BacktestMicroAgent       → run entry/exit on full history
  3. StatsMicroAgent          → aggregate statistics
  4. VerdictMicroAgent        → pass/fail with narrative

Entry point: run_for_algorithm(algo: TradingAlgorithm) → TradingAlgorithm (updated)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.agents.base.master import MasterAgent
from backend.agents.base.micro import MicroAgent
from backend.agents.simulation.micro.data_generator_micro import DataGeneratorMicroAgent
from backend.agents.simulation.micro.backtest_micro import BacktestMicroAgent
from backend.agents.simulation.micro.stats_micro import StatsMicroAgent
from backend.agents.simulation.micro.verdict_micro import VerdictMicroAgent
from backend.models.trading_algorithm import TradingAlgorithm

logger = logging.getLogger(__name__)


class SimulationMasterAgent(MasterAgent):
    name = "SimulationMasterAgent"

    def __init__(self):
        super().__init__()
        self._data_gen = DataGeneratorMicroAgent()
        self._stats = StatsMicroAgent()
        self._verdict = VerdictMicroAgent()

    # ------------------------------------------------------------------
    # MasterAgent interface
    # ------------------------------------------------------------------

    async def decompose(self, state: Any) -> list[tuple[MicroAgent, Any]]:
        algo: TradingAlgorithm = state["algorithm"]
        ticker = algo.ticker
        base_price = float(algo.params.get("base_price", 100.0))
        return [(self._data_gen, {"ticker": ticker, "base_price": base_price})]

    async def synthesize(self, results: list[Any], state: Any) -> TradingAlgorithm:
        algo: TradingAlgorithm = state["algorithm"]

        if not results:
            logger.error("[SimulationMasterAgent] data generation failed for %s", algo.ticker)
            return algo

        scenario_data: dict = results[0]

        # ── Step 1: Single full backtest ─────────────────────────────────
        bt_result = await BacktestMicroAgent().run({
            "algorithm": algo.model_dump(),
            "scenario_data": scenario_data,
            "scenario_name": "full",
        })

        if isinstance(bt_result, Exception):
            logger.warning("[SimulationMasterAgent] backtest failed for %s: %s", algo.ticker, bt_result)
            bt_result = {
                "scenario_name": "full",
                "n_trades": 0,
                "win_rate": 0.0,
                "total_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "trade_returns": [],
                "equity_curve": [1.0],
                "trade_by_trade": [],
                "passed": False,
                "error": str(bt_result),
            }

        scenario_results = [bt_result]

        # ── Step 2: Stats ────────────────────────────────────────────────
        stats: dict = await self._stats.run({"sim_results": scenario_results})

        # ── Step 3: Verdict ──────────────────────────────────────────────
        verdict: dict = await self._verdict.run({
            "scenario_results": scenario_results,
            "stats": stats,
        })

        # ── Update TradingAlgorithm ──────────────────────────────────────
        updated = algo.model_copy(update={
            "backtest_win_rate": stats.get("win_rate", 0.0),
            "backtest_sharpe": stats.get("sharpe_ratio", 0.0),
            "backtest_max_drawdown_pct": stats.get("max_drawdown_pct", 0.0),
            "backtest_profit_factor": stats.get("profit_factor", 0.0),
            "scenarios_passed": bt_result.get("n_trades", 0),   # store total trade count
            "status": "SIMULATED" if verdict.get("passed") else "DRAFT",
        })

        self._log_run({
            "ticker": algo.ticker,
            "algo_name": algo.name,
            "passed": verdict.get("passed"),
            "n_trades": bt_result.get("n_trades", 0),
            "win_rate": stats.get("win_rate"),
            "sharpe": stats.get("sharpe_ratio"),
            "narrative": verdict.get("narrative", "")[:120],
        })

        logger.info(
            "[SimulationMasterAgent] %s → verdict=%s n_trades=%d win_rate=%.0f%% sharpe=%.2f",
            algo.name,
            verdict.get("verdict"),
            bt_result.get("n_trades", 0),
            (stats.get("win_rate", 0) * 100),
            stats.get("sharpe_ratio", 0),
        )

        return updated

    # ------------------------------------------------------------------
    # Convenience entry point
    # ------------------------------------------------------------------

    async def run_for_algorithm(self, algo: TradingAlgorithm) -> TradingAlgorithm:
        """Run the full simulation pipeline for a single algorithm."""
        return await self.run({"algorithm": algo})
