"""
RiskCommitteeMicroAgent
-----------------------
Convenes a simulated risk committee of legendary traders (Paul Tudor Jones,
Ray Dalio, Jim Simons) via LLM. Presents full algorithm details and all
prior validation results. Returns a committee verdict as a ValidationCheck.

task: {"algorithm": dict, "validation_summary": str}
returns: ValidationCheck
"""
from __future__ import annotations

import asyncio
import json
import logging

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm
from backend.models.validation_result import ValidationCheck

logger = logging.getLogger(__name__)

COMMITTEE_SYSTEM = """\
You are a senior risk committee composed of:
- Paul Tudor Jones (macro discretionary, risk management, never lose money)
- Ray Dalio (all-weather diversification, correlation awareness, systematic)
- Jim Simons (statistical edge, Sharpe ratio, regime awareness, p-value rigor)

Your task: Review this algorithmic trading strategy and its validation results.
Apply your combined wisdom to decide: APPROVED or REJECTED.

Return a JSON object with these fields:
{
  "verdict": "APPROVED" or "REJECTED",
  "score": 0.0-1.0 (overall conviction),
  "concerns": ["list of specific risk concerns"],
  "strengths": ["list of identified strengths"],
  "committee_note": "2-3 sentence synthesis of the committee decision"
}

Be rigorous. Only APPROVE strategies with genuine statistical edge and sound risk management.
"""


class RiskCommitteeMicroAgent(MicroAgent):
    name = "RiskCommitteeMicroAgent"
    timeout_seconds = 120.0

    async def execute(self, task: dict) -> ValidationCheck:
        algorithm: dict = task["algorithm"]
        validation_summary: str = task.get("validation_summary", "No prior validation data.")

        try:
            return await self._run(algorithm, validation_summary)
        except Exception as exc:
            logger.warning("[RiskCommitteeMicroAgent] failed: %s", exc)
            return ValidationCheck(
                name="risk_committee",
                passed=False,
                detail=f"Risk committee review failed: {exc}",
                metric={"error": str(exc)},
            )

    async def _run(self, algorithm: dict, validation_summary: str) -> ValidationCheck:
        # Prepare algorithm summary (avoid sending full code if very long)
        algo_summary = {
            "ticker": algorithm.get("ticker"),
            "name": algorithm.get("name"),
            "strategy_type": algorithm.get("strategy_type"),
            "indicators": algorithm.get("indicators", []),
            "params": algorithm.get("params", {}),
            "backtest_win_rate": algorithm.get("backtest_win_rate"),
            "backtest_sharpe": algorithm.get("backtest_sharpe"),
            "backtest_max_drawdown_pct": algorithm.get("backtest_max_drawdown_pct"),
            "backtest_profit_factor": algorithm.get("backtest_profit_factor"),
            "scenarios_passed": algorithm.get("scenarios_passed"),
            "paper_trades_done": algorithm.get("paper_trades_done"),
            "paper_win_rate": algorithm.get("paper_win_rate"),
            "entry_rules_code": (algorithm.get("entry_rules_code", "")[:500] + "...")
            if len(algorithm.get("entry_rules_code", "")) > 500
            else algorithm.get("entry_rules_code", ""),
            "exit_rules_code": (algorithm.get("exit_rules_code", "")[:500] + "...")
            if len(algorithm.get("exit_rules_code", "")) > 500
            else algorithm.get("exit_rules_code", ""),
        }

        prompt = (
            f"Algorithm Details:\n{json.dumps(algo_summary, indent=2)}\n\n"
            f"Prior Validation Results:\n{validation_summary}\n\n"
            "Please provide your risk committee verdict as the JSON format specified."
        )

        raw = await call_llm("risk_committee", prompt, COMMITTEE_SYSTEM)

        # Parse LLM response
        verdict_data = self._parse_response(raw)

        approved = verdict_data.get("verdict", "REJECTED") == "APPROVED"
        score = float(verdict_data.get("score", 0.5))
        committee_note = verdict_data.get("committee_note", raw[:300] if raw else "No response")
        concerns = verdict_data.get("concerns", [])
        strengths = verdict_data.get("strengths", [])

        detail = committee_note
        if concerns:
            detail += f" | Concerns: {'; '.join(concerns[:3])}"

        return ValidationCheck(
            name="risk_committee",
            passed=approved,
            score=score,
            detail=detail,
            metric={
                "verdict": verdict_data.get("verdict", "REJECTED"),
                "score": score,
                "concerns": concerns,
                "strengths": strengths,
                "committee_note": committee_note,
            },
        )

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """Extract JSON from LLM response, with fallback."""
        if not raw or raw == "{}":
            return {"verdict": "REJECTED", "score": 0.0, "committee_note": "No LLM response"}

        # Try direct JSON parse
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "verdict" in data:
                return data
        except json.JSONDecodeError:
            pass

        # Try to find JSON block in text
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        # Text-based fallback
        raw_upper = raw.upper()
        verdict = "APPROVED" if "APPROVED" in raw_upper else "REJECTED"
        return {
            "verdict": verdict,
            "score": 0.7 if verdict == "APPROVED" else 0.3,
            "committee_note": raw[:300],
            "concerns": [],
            "strengths": [],
        }
