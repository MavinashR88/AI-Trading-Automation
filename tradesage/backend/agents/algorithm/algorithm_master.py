"""
AlgorithmMaster
---------------
Orchestrates the algorithm generation pipeline for a single ticker.

The pipeline is SEQUENTIAL (each step depends on the previous):

  1. IndicatorSelectorMicro   — choose indicators from research data
  2. EntryRulesMicro          — generate entry Python code
  3. ExitRulesMicro           — generate exit Python code
  4. AlgorithmAssemblerMicro  — combine into 3 TradingAlgorithm variants

Note: Because steps are sequential (not parallel), decompose() returns
them as a single sequential chain rather than a parallel fan-out.
MasterAgent.run() is not used directly; the pipeline is managed by
run_for_ticker() which calls the micros in order.

Public API:
    master = AlgorithmMaster()
    algorithms = await master.run_for_ticker("NVDA", research_report)
    # returns list[TradingAlgorithm]  (3 variants, primary strategy first)
"""
from __future__ import annotations

import logging
from typing import Any

from backend.agents.base.master import MasterAgent
from backend.agents.base.micro import MicroAgent
from backend.agents.algorithm.micro.indicator_selector_micro import IndicatorSelectorMicroAgent
from backend.agents.algorithm.micro.entry_rules_micro import EntryRulesMicroAgent
from backend.agents.algorithm.micro.exit_rules_micro import ExitRulesMicroAgent
from backend.agents.algorithm.micro.algorithm_assembler_micro import AlgorithmAssemblerMicroAgent
from backend.models.research_report import ResearchReport
from backend.models.trading_algorithm import TradingAlgorithm

logger = logging.getLogger(__name__)


class AlgorithmMaster(MasterAgent):
    name = "AlgorithmMaster"
    improvement_interval = 10

    def __init__(self):
        super().__init__()
        self._indicator_selector = IndicatorSelectorMicroAgent()
        self._entry_rules = EntryRulesMicroAgent()
        self._exit_rules = ExitRulesMicroAgent()
        self._assembler = AlgorithmAssemblerMicroAgent()

    # ------------------------------------------------------------------
    # MasterAgent interface (not used in the sequential path, but
    # required by the abstract base class)
    # ------------------------------------------------------------------
    async def decompose(self, state: Any) -> list[tuple[MicroAgent, Any]]:
        """
        Returns all micro tasks for parallel execution.
        In practice run_for_ticker() calls them sequentially because
        each step depends on the previous output.
        """
        # Parallel decomposition is not useful here; return empty to
        # satisfy the interface. The real work is in synthesize().
        return []

    async def synthesize(self, results: list[Any], state: Any) -> list[TradingAlgorithm]:
        """
        Run the sequential pipeline and return assembled algorithms.
        `results` is ignored (decompose returns nothing); state is used directly.
        """
        return await self._run_pipeline(state)

    # ------------------------------------------------------------------
    # Sequential pipeline implementation
    # ------------------------------------------------------------------
    async def _run_pipeline(self, state: dict) -> list[TradingAlgorithm]:
        ticker: str = state["ticker"]
        research: dict = state.get("research", {})
        predecessor_context: dict | None = state.get("predecessor_context")

        # Strategy type: explicit override from state, then research, then default
        strategy_type: str = (
            state.get("strategy_type")
            or research.get("suggested_strategy")
            or "momentum"
        )

        # ── Step 1: Select indicators ────────────────────────────────
        try:
            indicator_result = await self._indicator_selector.run(
                {"ticker": ticker, "research": research, "strategy_type": strategy_type}
            )
        except Exception as exc:
            logger.warning("[AlgorithmMaster] indicator selector failed for %s: %s", ticker, exc)
            indicator_result = {
                "indicators": ["RSI", "ATR", "EMA_20", "EMA_50", "VOLUME"],
                "params": {"rsi_period": 14, "atr_period": 14},
            }

        indicators: list = indicator_result.get("indicators", [])
        params: dict = indicator_result.get("params", {})

        # ── Step 2: Entry rules ──────────────────────────────────────
        entry_task = {
            "ticker": ticker,
            "strategy_type": strategy_type,
            "indicators": indicators,
            "params": params,
            "predecessor_context": predecessor_context,  # LLM learns from previous algo
        }
        try:
            entry_result = await self._entry_rules.run(entry_task)
        except Exception as exc:
            logger.warning("[AlgorithmMaster] entry rules failed for %s: %s", ticker, exc)
            entry_result = {"entry_rules_code": ""}

        # ── Step 3: Exit rules ───────────────────────────────────────
        exit_task = {
            "ticker": ticker,
            "strategy_type": strategy_type,
            "indicators": indicators,
            "params": params,
            "predecessor_context": predecessor_context,
        }
        try:
            exit_result = await self._exit_rules.run(exit_task)
        except Exception as exc:
            logger.warning("[AlgorithmMaster] exit rules failed for %s: %s", ticker, exc)
            exit_result = {"exit_rules_code": ""}

        # ── Step 4: Assemble algorithms ──────────────────────────────
        assembler_task = {
            "ticker": ticker,
            "base_indicators": indicators,
            "base_params": params,
            "entry_rules_code": entry_result.get("entry_rules_code", ""),
            "exit_rules_code": exit_result.get("exit_rules_code", ""),
            "strategy_type": strategy_type,
        }
        try:
            algorithms: list[TradingAlgorithm] = await self._assembler.run(assembler_task)
        except Exception as exc:
            logger.error("[AlgorithmMaster] assembler failed for %s: %s", ticker, exc)
            algorithms = []

        # Feed self-improvement log
        self._log_run({
            "ticker": ticker,
            "strategy_type": strategy_type,
            "indicators": indicators,
            "algorithms_generated": len(algorithms),
        })

        logger.info(
            "[AlgorithmMaster] Generated %d algorithm variants for %s (primary: %s)",
            len(algorithms),
            ticker,
            strategy_type,
        )
        return algorithms

    # ------------------------------------------------------------------
    # Override run() to bypass the parallel decompose path
    # ------------------------------------------------------------------
    async def run(self, state: Any) -> list[TradingAlgorithm]:
        """Run sequential pipeline directly."""
        algorithms = await self._run_pipeline(state)

        self._completion_count += 1
        import asyncio
        if self._completion_count % self.improvement_interval == 0:
            asyncio.create_task(self._self_improve())

        return algorithms

    # ------------------------------------------------------------------
    # Convenience public method
    # ------------------------------------------------------------------
    async def run_for_ticker(
        self,
        ticker: str,
        research_report: ResearchReport | None = None,
    ) -> list[TradingAlgorithm]:
        """
        High-level entry point.

        Args:
            ticker: Stock ticker symbol (e.g. "NVDA")
            research_report: Optional ResearchReport from StockResearchMaster.
                             If None, sensible defaults are used.

        Returns:
            list[TradingAlgorithm] — 3 variants, recommended strategy first.
        """
        research: dict = {}
        if research_report is not None:
            # Convert Pydantic model to dict for downstream use
            research = research_report.model_dump()

        state = {"ticker": ticker.upper(), "research": research}
        return await self.run(state)
