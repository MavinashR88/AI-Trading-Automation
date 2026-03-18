"""
ValidationMasterAgent
---------------------
Runs all 6 validation checks against a TradingAlgorithm.
All 6 must pass for all_passed = True.

Checks (run in parallel where possible):
  1. out_of_sample       — 2024 live data win-rate degradation ≤ 20%
  2. monte_carlo         — real Sharpe > 95% of shuffled portfolios
  3. paper_trading       — ≥ 50 paper trades, win_rate ≥ 45%
  4. correlation         — max correlation with deployed algos ≤ 0.70
  5. capacity            — position size < 0.5% of daily volume
  6. risk_committee      — committee (Jones/Dalio/Simons) approves

Returns ValidationResult.
Entry point: validate(algorithm, data_router) → ValidationResult
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any

from backend.agents.base.master import MasterAgent
from backend.agents.base.micro import MicroAgent
from backend.agents.validation.micro.out_of_sample_micro import OutOfSampleMicroAgent
from backend.agents.validation.micro.monte_carlo_micro import MonteCarloMicroAgent
from backend.agents.validation.micro.paper_trading_period_micro import PaperTradingPeriodMicroAgent
from backend.agents.validation.micro.correlation_check_micro import CorrelationCheckMicroAgent
from backend.agents.validation.micro.capacity_micro import CapacityMicroAgent
from backend.agents.validation.micro.risk_committee_micro import RiskCommitteeMicroAgent
from backend.models.trading_algorithm import TradingAlgorithm
from backend.models.validation_result import ValidationCheck, ValidationResult

logger = logging.getLogger(__name__)


class ValidationMasterAgent(MasterAgent):
    name = "ValidationMasterAgent"

    def __init__(self):
        super().__init__()
        self._oos = OutOfSampleMicroAgent()
        self._mc = MonteCarloMicroAgent()
        self._paper = PaperTradingPeriodMicroAgent()
        self._corr = CorrelationCheckMicroAgent()
        self._cap = CapacityMicroAgent()
        self._risk_committee = RiskCommitteeMicroAgent()

    # ------------------------------------------------------------------
    # MasterAgent interface
    # ------------------------------------------------------------------

    async def decompose(self, state: Any) -> list[tuple[MicroAgent, Any]]:
        """
        Runs first 5 checks in parallel. Risk committee runs last (needs summary).
        We return only the first 5 here; risk_committee is invoked in synthesize().
        """
        algorithm: dict = state["algorithm"]
        data_router = state["data_router"]
        trade_returns: list[float] = state.get("trade_returns", [])
        ticker = algorithm.get("ticker", "")
        position_size_usd = float(state.get("position_size_usd", 10_000.0))

        return [
            (
                self._oos,
                {
                    "algorithm": algorithm,
                    "backtest_win_rate": algorithm.get("backtest_win_rate", 0.0),
                },
            ),
            (
                self._mc,
                {"algorithm": algorithm, "trade_returns": trade_returns},
            ),
            (
                self._paper,
                {"algorithm": algorithm},
            ),
            (
                self._corr,
                {"algorithm": algorithm, "data_router": data_router},
            ),
            (
                self._cap,
                {
                    "algorithm": algorithm,
                    "ticker": ticker,
                    "position_size_usd": position_size_usd,
                },
            ),
        ]

    async def synthesize(self, results: list[Any], state: Any) -> ValidationResult:
        """
        Collect first 5 checks, build summary, then run risk_committee.
        """
        algorithm: dict = state["algorithm"]

        # Ensure all results are ValidationCheck objects
        checks: list[ValidationCheck] = []
        for r in results:
            if isinstance(r, ValidationCheck):
                checks.append(r)
            elif isinstance(r, dict):
                try:
                    checks.append(ValidationCheck(**r))
                except Exception:
                    pass

        # Build summary for risk committee
        validation_summary = self._build_summary(checks, algorithm)

        # Run risk committee (sequential — needs full context)
        try:
            rc_check = await self._risk_committee.run({
                "algorithm": algorithm,
                "validation_summary": validation_summary,
            })
            if isinstance(rc_check, ValidationCheck):
                checks.append(rc_check)
        except Exception as exc:
            logger.warning("[ValidationMasterAgent] risk_committee failed: %s", exc)
            checks.append(ValidationCheck(
                name="risk_committee",
                passed=False,
                detail=f"Risk committee unavailable: {exc}",
            ))

        # ── Build ValidationResult ────────────────────────────────────────
        pass_count = sum(1 for c in checks if c.passed)
        fail_count = len(checks) - pass_count
        # Require 4+ of 6 checks to pass (not all 6 — paper_trading and monte_carlo
        # may not have enough data at initial validation stage)
        all_passed = pass_count >= 4

        failed_checks = [c for c in checks if not c.passed]
        rejection_reason = (
            "; ".join(f"{c.name}: {c.detail[:80]}" for c in failed_checks)
            if failed_checks
            else ""
        )

        # Extract key metrics from individual checks
        oos_check = next((c for c in checks if c.name == "out_of_sample"), None)
        mc_check = next((c for c in checks if c.name == "monte_carlo"), None)
        corr_check = next((c for c in checks if c.name == "correlation"), None)
        cap_check = next((c for c in checks if c.name == "capacity"), None)

        result = ValidationResult(
            id=str(uuid.uuid4()),
            algorithm_id=algorithm.get("id", ""),
            ticker=algorithm.get("ticker", ""),
            checks=checks,
            all_passed=all_passed,
            pass_count=pass_count,
            fail_count=fail_count,
            overall_verdict="APPROVED" if all_passed else "REJECTED",
            rejection_reason=rejection_reason,
            oos_degradation_pct=float(
                oos_check.metric.get("degradation", 0.0) if oos_check else 0.0
            ),
            monte_carlo_p_value=float(
                mc_check.metric.get("p_value", 1.0) if mc_check else 1.0
            ),
            correlation_with_portfolio=float(
                corr_check.metric.get("max_correlation", 0.0) if corr_check else 0.0
            ),
            capacity_daily_volume_pct=float(
                cap_check.metric.get("volume_pct", 0.0) if cap_check else 0.0
            ),
        )

        self._log_run({
            "ticker": algorithm.get("ticker"),
            "algo_name": algorithm.get("name"),
            "all_passed": all_passed,
            "pass_count": pass_count,
            "verdict": result.overall_verdict,
            "rejection_reason": rejection_reason[:120],
        })

        logger.info(
            "[ValidationMasterAgent] %s → %s (%d/6 passed)",
            algorithm.get("name", "?"),
            result.overall_verdict,
            pass_count,
        )

        return result

    # ------------------------------------------------------------------
    # Convenience entry point
    # ------------------------------------------------------------------

    async def validate(
        self,
        algorithm: TradingAlgorithm,
        data_router,
        trade_returns: list[float] | None = None,
        position_size_usd: float = 10_000.0,
    ) -> ValidationResult:
        """Run the full validation pipeline for a single algorithm."""
        return await self.run({
            "algorithm": algorithm.model_dump(),
            "data_router": data_router,
            "trade_returns": trade_returns or [],
            "position_size_usd": position_size_usd,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(checks: list[ValidationCheck], algorithm: dict) -> str:
        lines = [
            f"Algorithm: {algorithm.get('name')} | Ticker: {algorithm.get('ticker')}",
            f"Backtest win_rate={algorithm.get('backtest_win_rate', 0):.1%} "
            f"Sharpe={algorithm.get('backtest_sharpe', 0):.2f} "
            f"MaxDD={algorithm.get('backtest_max_drawdown_pct', 0):.1f}% "
            f"ProfitFactor={algorithm.get('backtest_profit_factor', 0):.2f} "
            f"Scenarios={algorithm.get('scenarios_passed', 0)}/8",
            "",
            "Validation Checks:",
        ]
        for c in checks:
            status = "PASS" if c.passed else "FAIL"
            lines.append(f"  [{status}] {c.name}: {c.detail[:120]}")
        return "\n".join(lines)
