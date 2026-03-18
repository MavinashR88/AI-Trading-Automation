"""
StressTestMicroAgent
--------------------
Runs the algorithm specifically against the crash and volatile (high-volatility)
scenarios. Checks survival criteria:
  - max drawdown ≤ 20%
  - does not go bankrupt (equity never drops below 10% of starting value)

task: {"algorithm": dict, "crash_data": dict}
returns: {passed: bool, max_drawdown: float, crash_survival: bool, volatile_survival: bool, detail: str}
"""
from __future__ import annotations

import asyncio
import logging

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)


class StressTestMicroAgent(MicroAgent):
    name = "StressTestMicroAgent"
    timeout_seconds = 60.0

    async def execute(self, task: dict) -> dict:
        algorithm: dict = task["algorithm"]
        crash_data: dict = task["crash_data"]  # scenario_name → pd.DataFrame
        return await asyncio.to_thread(self._run_stress, algorithm, crash_data)

    # ------------------------------------------------------------------
    def _run_stress(self, algorithm: dict, crash_data: dict) -> dict:
        from backend.agents.simulation.micro.backtest_micro import BacktestMicroAgent

        bt = BacktestMicroAgent()

        crash_df = crash_data.get("crash")
        crash_result = bt._execute_rules(  # type: ignore[attr-defined]  # noqa: SLF001
            algorithm,
            crash_df,
            "crash",
        ) if crash_df is not None else bt._empty_result("crash", "missing")

        volatile_df = crash_data.get("volatile")
        volatile_result = bt._execute_rules(  # type: ignore[attr-defined]  # noqa: SLF001
            algorithm,
            volatile_df,
            "volatile",
        ) if volatile_df is not None else bt._empty_result("volatile", "missing")

        crash_equity = crash_result.get("equity_curve", [1.0])
        volatile_equity = volatile_result.get("equity_curve", [1.0])

        crash_min_equity = min(crash_equity)
        volatile_min_equity = min(volatile_equity)

        crash_survival = crash_min_equity > 0.10  # equity never drops below 10%
        volatile_survival = volatile_min_equity > 0.10

        crash_dd = crash_result.get("max_drawdown_pct", 100.0)
        volatile_dd = volatile_result.get("max_drawdown_pct", 100.0)
        max_drawdown = max(crash_dd, volatile_dd)

        passed = (
            max_drawdown <= 20.0
            and crash_survival
            and volatile_survival
        )

        detail_parts = []
        if not crash_survival:
            detail_parts.append(f"crash bankruptcy (min_equity={crash_min_equity:.3f})")
        if not volatile_survival:
            detail_parts.append(f"volatile bankruptcy (min_equity={volatile_min_equity:.3f})")
        if max_drawdown > 20.0:
            detail_parts.append(f"excessive drawdown {max_drawdown:.1f}%>20%")

        detail = "; ".join(detail_parts) if detail_parts else "All stress tests passed"

        return {
            "passed": passed,
            "max_drawdown": round(max_drawdown, 2),
            "crash_survival": crash_survival,
            "volatile_survival": volatile_survival,
            "crash_drawdown_pct": round(crash_dd, 2),
            "volatile_drawdown_pct": round(volatile_dd, 2),
            "crash_min_equity": round(crash_min_equity, 4),
            "volatile_min_equity": round(volatile_min_equity, 4),
            "detail": detail,
        }
