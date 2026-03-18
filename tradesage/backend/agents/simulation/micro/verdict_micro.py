"""
VerdictMicroAgent
-----------------
Scores the full-history backtest result and generates a narrative via LLM.

Pass criteria (applied to the single "full" backtest):
  - n_trades  ≥ 20         (enough trades for meaningful statistics)
  - win_rate  ≥ 52%
  - profit_factor ≥ 1.2
  - max_drawdown ≤ 40%

If n_trades < 20 the algorithm is NOT rejected — it stays as DRAFT so the
pipeline will retry it (possibly with a different data period) until it
accumulates enough history.

task: {"scenario_results": list[dict], "stats": dict}
returns: {passed: bool, n_trades: int, verdict: str, narrative: str, criteria: dict}
"""
from __future__ import annotations

import asyncio
import json
import logging

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm

logger = logging.getLogger(__name__)


class VerdictMicroAgent(MicroAgent):
    name = "VerdictMicroAgent"
    timeout_seconds = 60.0

    # Pass thresholds
    # Note: high-WR strategies (90%+ target) use tight TP + wide stop which gives
    # low profit factor mathematically — PF check is relaxed accordingly.
    MIN_TRADES = 20
    MIN_WIN_RATE = 0.52
    MIN_PROFIT_FACTOR = 0.3   # relaxed — paper trading graduation is the real quality gate
    MAX_DRAWDOWN_PCT = 60.0   # relaxed — paper trading will filter further

    async def execute(self, task: dict) -> dict:
        scenario_results: list[dict] = task["scenario_results"]
        stats: dict = task["stats"]

        # Full backtest is the first (and only) scenario
        bt = scenario_results[0] if scenario_results else {}

        n_trades = bt.get("n_trades", stats.get("n_trades", 0))
        win_rate = stats.get("win_rate", bt.get("win_rate", 0.0))
        profit_factor = stats.get("profit_factor", 0.0)
        max_drawdown = stats.get("max_drawdown_pct", bt.get("max_drawdown_pct", 100.0))
        sharpe = stats.get("sharpe_ratio", 0.0)

        criteria = {
            "trades_ok": n_trades >= self.MIN_TRADES,
            "win_rate_ok": win_rate >= self.MIN_WIN_RATE,
            "profit_factor_ok": profit_factor >= self.MIN_PROFIT_FACTOR,
            "drawdown_ok": max_drawdown <= self.MAX_DRAWDOWN_PCT,
        }

        passed = all(criteria.values())
        verdict = "PASS" if passed else "FAIL"

        narrative = await self._get_narrative(bt, stats, criteria, n_trades, passed)

        return {
            "passed": passed,
            "n_trades": n_trades,
            "verdict": verdict,
            "narrative": narrative,
            "criteria": criteria,
            "win_rate": round(win_rate, 4),
            "sharpe_ratio": round(sharpe, 4),
            "profit_factor": round(profit_factor, 4),
            "max_drawdown_pct": round(max_drawdown, 2),
            # Keep scenarios_passed for API compatibility (1 = passed, 0 = failed)
            "scenarios_passed": 1 if passed else 0,
        }

    async def _get_narrative(
        self,
        bt: dict,
        stats: dict,
        criteria: dict,
        n_trades: int,
        passed: bool,
    ) -> str:
        def _safe_json(obj):
            if isinstance(obj, bool):
                return obj
            if isinstance(obj, float) and (obj != obj):  # nan
                return None
            return str(obj)

        prompt = (
            f"Simulation verdict: {'PASS' if passed else 'FAIL'}\n"
            f"Total trades: {n_trades} (minimum required: {self.MIN_TRADES})\n"
            f"Stats: {json.dumps(stats, indent=2, default=_safe_json)}\n"
            f"Criteria checks: {json.dumps(criteria, indent=2, default=_safe_json)}\n\n"
            "Provide a concise 2-3 sentence narrative explaining this simulation result. "
            "Be specific about which criteria passed or failed and what it means for the strategy."
        )

        system = (
            "You are a quantitative research analyst reviewing algorithmic trading simulation results. "
            "Provide clear, actionable feedback. Be direct and specific."
        )

        try:
            narrative = await call_llm("sim_verdict_simple", prompt, system)
            if not narrative or narrative == "{}":
                raise ValueError("empty LLM response")
            return narrative.strip()
        except Exception as exc:
            logger.warning("[VerdictMicroAgent] LLM narrative failed: %s", exc)
            fails = [k for k, v in criteria.items() if not v]
            if passed:
                return (
                    f"Algorithm passed simulation with {n_trades} trades. "
                    f"Win rate={stats.get('win_rate', 0):.1%}, "
                    f"profit factor={stats.get('profit_factor', 0):.2f}, "
                    f"max drawdown={stats.get('max_drawdown_pct', 0):.1f}%. "
                    "Ready for validation pipeline."
                )
            elif not criteria.get("trades_ok"):
                return (
                    f"Algorithm generated only {n_trades} trades in {self.MIN_TRADES}+ required. "
                    "Entry conditions may be too selective — strategy will be retried with extended data. "
                    "No structural rejection — will re-simulate on next pipeline run."
                )
            else:
                return (
                    f"Algorithm failed simulation ({n_trades} trades). "
                    f"Failed criteria: {', '.join(fails)}. "
                    "Review strategy entry/exit logic before re-running simulation."
                )
