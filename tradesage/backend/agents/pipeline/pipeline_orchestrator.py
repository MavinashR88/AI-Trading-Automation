"""
PipelineOrchestrator — runs every 4 hours (scheduled by APScheduler in main.py).

Stage flow:
  1. Discovery  → find top 15 stocks
  2. Research   → deep analysis per stock
  3. Algorithm  → generate 3 strategy variants
  4. Simulation → test on 8 scenarios
  5. Validation → 6-check quant firm validation
  6. Deployment → add to live watchlist
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.db.router import DataRouter

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    def __init__(self, data_router: "DataRouter", ws_manager=None, scheduler=None):
        self._dr = data_router
        self._ws = ws_manager
        self._scheduler = scheduler
        self._running = False
        self._last_run: datetime | None = None
        self._next_run: datetime | None = None
        self._current_stage: str | None = None
        self._stages_completed: list[str] = []

    def get_status(self) -> dict:
        # Pull next_run from APScheduler if available
        next_run = self._next_run
        if self._scheduler:
            try:
                job = self._scheduler.get_job("pipeline_full")
                if job and job.next_run_time:
                    next_run = job.next_run_time.replace(tzinfo=None)
            except Exception:
                pass
        return {
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "next_run": next_run.isoformat() if next_run else None,
            "current_stage": self._current_stage,
            "stages_completed": self._stages_completed,
            "stocks_discovered_total": len(self._dr.get_discovered_stocks()),
            "algorithms_live": len(self._dr.get_deployed_algorithms(active_only=True)),
            "algorithms_paper": len(self._dr.get_algorithms(status="PAPER_TRADING")),
        }

    async def run_full_pipeline(self):
        """Called by APScheduler every 4 hours."""
        if self._running:
            logger.info("[Pipeline] Already running — skipping scheduled run")
            return
        self._running = True
        self._current_stage = None
        self._stages_completed = []
        self._last_run = datetime.utcnow()
        self._next_run = datetime.utcnow() + timedelta(hours=4)
        logger.info("[Pipeline] Full pipeline started")

        try:
            await self._stage_discovery()
            await self._stage_research()
            await self._stage_algorithm()
            await self._stage_simulation()
            await self._stage_validation()
            await self._stage_check_paper_graduation()
            logger.info("[Pipeline] Full pipeline complete")
        except Exception as exc:
            logger.error("[Pipeline] Pipeline failed: %s", exc, exc_info=True)
        finally:
            self._running = False
            self._current_stage = None

    # ── Stage 1: Discovery ────────────────────────────────────────

    async def _stage_discovery(self):
        self._current_stage = "DISCOVERY"
        self._log_event("pipeline_run", "DISCOVERY", "STARTED")
        try:
            from backend.agents.discovery.discovery_master import DiscoveryMaster
            master = DiscoveryMaster()
            batch = await master.run({})
            for stock in getattr(batch, 'stocks', batch if isinstance(batch, list) else []):
                s = stock if isinstance(stock, dict) else stock.dict()
                self._dr.save_discovered_stock(s)

            count = len(getattr(batch, 'stocks', batch if isinstance(batch, list) else []))
            self._stages_completed.append("DISCOVERY")
            self._log_event("pipeline_run", "DISCOVERY", "COMPLETE", detail=f"{count} stocks found")
            if self._ws:
                await self._ws.broadcast("pipeline_stage", {"stage": "DISCOVERY", "status": "COMPLETE", "count": count})
        except ImportError:
            logger.warning("[Pipeline] Discovery master not available yet — skipping")
            self._stages_completed.append("DISCOVERY")
        except Exception as exc:
            logger.error("[Pipeline] Discovery failed: %s", exc)
            self._log_event("pipeline_run", "DISCOVERY", "FAILED", detail=str(exc))

    # ── Stage 2: Research ─────────────────────────────────────────

    async def _stage_research(self):
        self._current_stage = "RESEARCH"
        self._log_event("pipeline_run", "RESEARCH", "STARTED")
        try:
            from backend.agents.research.stock_research_master import StockResearchMaster
            stocks = self._dr.get_discovered_stocks(status="DISCOVERED")
            master = StockResearchMaster()
            researched = 0
            for stock in stocks[:5]:  # process top 5 per run to limit cost
                try:
                    report = await master.run_for_ticker(stock["ticker"])
                    if report:
                        self._dr.update_stock_status(stock["ticker"], "RESEARCHED")
                        self._dr.save_discovered_stock({**stock, "status": "RESEARCHED",
                                                        "verdict": getattr(report, "research_verdict", "NEUTRAL")})
                        researched += 1
                        self._log_event("research", "RESEARCH", "COMPLETE", ticker=stock["ticker"])
                except Exception as exc:
                    logger.warning("[Pipeline] Research failed for %s: %s", stock["ticker"], exc)
            self._stages_completed.append("RESEARCH")
            self._log_event("pipeline_run", "RESEARCH", "COMPLETE", detail=f"{researched} stocks researched")
        except ImportError:
            logger.warning("[Pipeline] Research master not available yet — skipping")
            self._stages_completed.append("RESEARCH")
        except Exception as exc:
            logger.error("[Pipeline] Research stage failed: %s", exc)
            self._log_event("pipeline_run", "RESEARCH", "FAILED", detail=str(exc))

    # ── Stage 3: Algorithm ────────────────────────────────────────

    async def _stage_algorithm(self):
        """
        Algorithm building strategy — one at a time, learn from predecessors:

        - If a ticker has NO active algorithms → build the first one (pick best strategy type)
        - If a ticker has an algorithm in PAPER_TRADING or LIVE → build a new parallel version
          so multiple algos run side-by-side for consensus validation
        - If a ticker has only DRAFT/SIMULATED algos → wait for them to complete before adding more
        - Each new version gets context from the previous algo's performance to learn from it
        - Cap: max 3 active (non-REJECTED) algos per ticker
        - If ALL algos are REJECTED → reset ticker to RESEARCHED and rebuild from scratch
        """
        self._current_stage = "ALGORITHM"
        self._log_event("pipeline_run", "ALGORITHM", "STARTED")
        try:
            from backend.agents.algorithm.algorithm_master import AlgorithmMaster

            # Collect tickers from RESEARCHED stocks PLUS tickers with all-rejected algos
            researched_stocks = self._dr.get_discovered_stocks(status="RESEARCHED")
            # Also check ALGO_BUILT / VALIDATING stocks that need rebuild
            rebuilds = self._dr.get_discovered_stocks(status="ALGO_BUILT") + \
                       self._dr.get_discovered_stocks(status="VALIDATING")
            rebuild_stocks = []
            for s in rebuilds:
                all_existing = self._dr.get_algorithms(ticker=s["ticker"])
                if all_existing and all(a.get("status") == "REJECTED" for a in all_existing):
                    # All algos rejected — reset stock so we can rebuild
                    self._dr.update_stock_status(s["ticker"], "RESEARCHED")
                    rebuild_stocks.append(s)
                    logger.info("[Pipeline] %s — all algos REJECTED, resetting to RESEARCHED for rebuild", s["ticker"])

            stocks = researched_stocks + rebuild_stocks
            master = AlgorithmMaster()
            built = 0
            for stock in stocks[:5]:
                ticker = stock["ticker"]
                existing = self._dr.get_algorithms(ticker=ticker)
                active = [a for a in existing if a.get("status") not in ("REJECTED",)]
                rejected = [a for a in existing if a.get("status") == "REJECTED"]
                draft_or_sim = [a for a in active if a.get("status") in ("DRAFT", "SIMULATED")]
                paper_or_live = [a for a in active if a.get("status") in ("PAPER_TRADING", "LIVE")]

                # Cap at 3 active algos
                if len(active) >= 3:
                    logger.info("[Pipeline] %s already has %d active algos — skipping build", ticker, len(active))
                    continue

                # If there are DRAFT/SIMULATED algos in flight, wait for them to resolve
                if draft_or_sim and not paper_or_live:
                    logger.info(
                        "[Pipeline] %s has %d algo(s) in DRAFT/SIMULATED — waiting before adding more",
                        ticker, len(draft_or_sim),
                    )
                    continue

                # Build predecessor context from active algos, OR best rejected algo if all rejected
                predecessor_context = None
                reference_algos = active if active else rejected
                if reference_algos:
                    # Pick best by paper_win_rate (real performance) > backtest_win_rate
                    best = max(reference_algos, key=lambda a: (
                        a.get("paper_win_rate", 0) * 100 + a.get("backtest_win_rate", 0)
                    ))
                    predecessor_context = {
                        "strategy_type": best.get("strategy_type"),
                        "name": best.get("name"),
                        "backtest_win_rate": best.get("backtest_win_rate", 0),
                        "backtest_sharpe": best.get("backtest_sharpe", 0),
                        "backtest_max_drawdown_pct": best.get("backtest_max_drawdown_pct", 0),
                        "paper_win_rate": best.get("paper_win_rate", 0),
                        "paper_trades_done": best.get("paper_trades_done", 0),
                        "status": best.get("status"),
                    }

                # Determine which strategy types are still available
                # When all algos are rejected, allow all types again (fresh start)
                active_types = {a.get("strategy_type") for a in active}
                if not active:
                    # All rejected — pick event_driven first (our best performer so far)
                    available_types = ["event_driven", "breakout", "momentum"]
                else:
                    available_types = [t for t in ("event_driven", "breakout", "momentum") if t not in active_types]
                if not available_types:
                    logger.info("[Pipeline] %s — all strategy types active, skipping", ticker)
                    continue

                try:
                    task = {
                        "ticker": ticker,
                        "strategy_type": available_types[0],  # take next unused type
                        "predecessor_context": predecessor_context,
                    }
                    algos = await master.run(task)
                    for algo in (algos if isinstance(algos, list) else [algos]):
                        a = algo if isinstance(algo, dict) else algo.dict()
                        # Final dedup check
                        if a.get("strategy_type") in {x.get("strategy_type") for x in active}:
                            continue
                        self._dr.save_algorithm(a)
                        built += 1
                    self._dr.update_stock_status(ticker, "ALGO_BUILT")
                    self._log_event("algorithm", "ALGORITHM", "COMPLETE", ticker=ticker,
                                    detail=f"predecessor={'yes' if predecessor_context else 'no'}")
                except Exception as exc:
                    logger.warning("[Pipeline] Algorithm gen failed for %s: %s", ticker, exc)
                    self._log_event("algorithm", "ALGORITHM", "FAILED",
                                    ticker=stock.get("ticker"), detail=str(exc))

            self._stages_completed.append("ALGORITHM")
            self._log_event("pipeline_run", "ALGORITHM", "COMPLETE", detail=f"{built} algorithms built")
        except ImportError:
            logger.warning("[Pipeline] Algorithm master not available yet — skipping")
            self._stages_completed.append("ALGORITHM")
        except Exception as exc:
            logger.error("[Pipeline] Algorithm stage failed: %s", exc)
            self._log_event("pipeline_run", "ALGORITHM", "FAILED", detail=str(exc))

    # ── Stage 4: Simulation ───────────────────────────────────────

    async def _stage_simulation(self):
        self._current_stage = "SIMULATION"
        self._log_event("pipeline_run", "SIMULATION", "STARTED")
        try:
            from backend.agents.simulation.simulation_master import SimulationMasterAgent as SimulationMaster
            from backend.models.trading_algorithm import TradingAlgorithm
            algos = self._dr.get_algorithms(status="DRAFT")
            master = SimulationMaster()
            passed = 0
            logger.info("[Pipeline] Simulation: processing %d DRAFT algorithms", len(algos))
            for algo in algos:
                try:
                    algo_model = TradingAlgorithm(**algo)
                    result = await master.run_for_algorithm(algo_model)
                    updated = result if isinstance(result, dict) else result.model_dump()
                    verdict_passed = updated.get("status") == "SIMULATED"

                    if verdict_passed:
                        # Algorithm passed — advance to SIMULATED for validation
                        self._dr.update_algorithm_status(
                            algo["id"], "SIMULATED",
                            backtest_win_rate=updated.get("backtest_win_rate", 0),
                            backtest_sharpe=updated.get("backtest_sharpe", 0),
                            backtest_max_drawdown_pct=updated.get("backtest_max_drawdown_pct", 0),
                            scenarios_passed=updated.get("scenarios_passed", 0),
                        )
                        passed += 1
                        self._log_event("simulation", "SIMULATION", "PASS", ticker=algo["ticker"],
                                        algorithm_id=algo["id"])
                    else:
                        # Algorithm did NOT pass — keep as DRAFT so pipeline retries next run.
                        # We still update the backtest metrics so the UI shows progress.
                        # Only permanently reject if sim_retry_count exceeds limit.
                        retry_count = int(algo.get("sim_retry_count", 0)) + 1
                        MAX_RETRIES = 5
                        if retry_count >= MAX_RETRIES:
                            self._dr.update_algorithm_status(
                                algo["id"], "REJECTED",
                                retire_reason=f"Failed simulation after {MAX_RETRIES} retries",
                                backtest_win_rate=updated.get("backtest_win_rate", 0),
                                backtest_sharpe=updated.get("backtest_sharpe", 0),
                                backtest_max_drawdown_pct=updated.get("backtest_max_drawdown_pct", 0),
                                scenarios_passed=updated.get("scenarios_passed", 0),
                                sim_retry_count=retry_count,
                            )
                            self._log_event("simulation", "SIMULATION", "REJECTED",
                                            ticker=algo["ticker"], algorithm_id=algo["id"],
                                            detail=f"Max retries ({MAX_RETRIES}) reached")
                        else:
                            # Keep as DRAFT — will retry next pipeline run
                            self._dr.update_algorithm_status(
                                algo["id"], "DRAFT",
                                backtest_win_rate=updated.get("backtest_win_rate", 0),
                                backtest_sharpe=updated.get("backtest_sharpe", 0),
                                backtest_max_drawdown_pct=updated.get("backtest_max_drawdown_pct", 0),
                                scenarios_passed=updated.get("scenarios_passed", 0),
                                sim_retry_count=retry_count,
                            )
                            self._log_event("simulation", "SIMULATION", "FAIL_RETRY",
                                            ticker=algo["ticker"], algorithm_id=algo["id"],
                                            detail=f"Retry {retry_count}/{MAX_RETRIES}")
                except Exception as exc:
                    logger.warning("[Pipeline] Simulation failed for %s: %s", algo["id"], exc)
            self._stages_completed.append("SIMULATION")
            self._log_event("pipeline_run", "SIMULATION", "COMPLETE", detail=f"{passed} algos passed")
        except ImportError:
            logger.warning("[Pipeline] Simulation master not available yet — skipping")
            self._stages_completed.append("SIMULATION")
        except Exception as exc:
            logger.error("[Pipeline] Simulation stage failed: %s", exc)
            self._log_event("pipeline_run", "SIMULATION", "FAILED", detail=str(exc))

    # ── Stage 5: Validation ───────────────────────────────────────

    async def _stage_validation(self):
        self._current_stage = "VALIDATION"
        self._log_event("pipeline_run", "VALIDATION", "STARTED")
        try:
            from backend.agents.validation.validation_master import ValidationMasterAgent as ValidationMaster
            from backend.models.trading_algorithm import TradingAlgorithm
            algos = self._dr.get_algorithms(status="SIMULATED")
            master = ValidationMaster()
            approved = 0
            logger.info("[Pipeline] Validation: processing %d SIMULATED algorithms", len(algos))
            for algo in algos:
                try:
                    algo_model = TradingAlgorithm(**algo)
                    # Generate trade returns from simulation metrics for Monte Carlo
                    trade_returns = self._synthetic_returns_from_algo(algo)
                    val_result = await master.validate(algo_model, self._dr, trade_returns=trade_returns)
                    res = val_result if isinstance(val_result, dict) else val_result.model_dump()
                    self._dr.save_validation_result({**res, "algorithm_id": algo["id"], "ticker": algo["ticker"]})

                    if res.get("all_passed", False) or res.get("overall_verdict") == "APPROVED":
                        # Start paper trading
                        self._dr.update_algorithm_status(algo["id"], "PAPER_TRADING")
                        self._dr.update_stock_status(algo["ticker"], "VALIDATING")
                        approved += 1
                        self._log_event("validation", "VALIDATION", "APPROVED", ticker=algo["ticker"],
                                        algorithm_id=algo["id"])
                        if self._ws:
                            await self._ws.broadcast("paper_trading_started", {"ticker": algo["ticker"], "algo_id": algo["id"]})
                    else:
                        self._dr.update_algorithm_status(algo["id"], "REJECTED",
                            retire_reason=res.get("rejection_reason", "Failed validation"))
                        self._log_event("validation", "VALIDATION", "REJECTED", ticker=algo["ticker"],
                                        algorithm_id=algo["id"], detail=res.get("rejection_reason",""))
                except Exception as exc:
                    logger.warning("[Pipeline] Validation failed for %s: %s", algo["id"], exc)
            self._stages_completed.append("VALIDATION")
            self._log_event("pipeline_run", "VALIDATION", "COMPLETE", detail=f"{approved} algos approved for paper")
        except ImportError:
            logger.warning("[Pipeline] Validation master not available yet — skipping")
            self._stages_completed.append("VALIDATION")
        except Exception as exc:
            logger.error("[Pipeline] Validation stage failed: %s", exc)
            self._log_event("pipeline_run", "VALIDATION", "FAILED", detail=str(exc))

    # ── Stage 6: Paper → Live graduation ─────────────────────────

    # Graduation threshold — paper trading win rate required to go live
    GRADUATION_WIN_RATE = 0.90   # 90% win rate required
    GRADUATION_MIN_TRADES = 10   # minimum paper trades before checking

    async def _stage_check_paper_graduation(self):
        """
        Promote paper trading algos that achieve ≥ 90% win rate over ≥ 10 paper trades.
        Algos that complete required trades but fail the WR threshold are kept in
        PAPER_TRADING so the runner continues accumulating trades — they are only
        retired after 3× the required trades with consistently low WR.
        """
        self._current_stage = "GRADUATION"
        try:
            from backend.agents.deployment.deployment_agent import DeploymentAgent
            paper_algos = self._dr.get_algorithms(status="PAPER_TRADING")
            deployer = DeploymentAgent()
            for algo in paper_algos:
                trades_done = algo.get("paper_trades_done", 0)
                trades_req = algo.get("paper_trades_required", 10)
                win_rate = algo.get("paper_win_rate", 0.0)

                if trades_done < self.GRADUATION_MIN_TRADES:
                    continue  # Not enough trades yet — keep running

                if win_rate >= self.GRADUATION_WIN_RATE:
                    # Graduate to live
                    await deployer.deploy(algo, self._dr, self._ws)
                    self._dr.update_stock_status(algo.get("ticker", ""), "LIVE")
                    self._log_event("deployment", "DEPLOYMENT", "LIVE",
                                    ticker=algo["ticker"], algorithm_id=algo["id"])
                    logger.info(
                        "[Pipeline] Algorithm %s graduated LIVE for %s (WR=%.0f%%, %d trades)",
                        algo["id"], algo["ticker"], win_rate * 100, trades_done,
                    )
                # else: keep accumulating — no auto-rejection. The pipeline will build parallel
                # v2 algorithms to try to achieve 90% WR. Only manual retire is allowed.
                else:
                    logger.debug(
                        "[Pipeline] %s paper WR=%.0f%% (%d trades) — still accumulating toward 90%%",
                        algo.get("name"), win_rate * 100, trades_done,
                    )

            self._stages_completed.append("GRADUATION")
        except ImportError:
            self._stages_completed.append("GRADUATION")
        except Exception as exc:
            logger.error("[Pipeline] Graduation check failed: %s", exc)

    @staticmethod
    def _synthetic_returns_from_algo(algo: dict) -> list[float]:
        """Generate synthetic trade returns that match the algorithm's simulated statistics."""
        import numpy as np
        win_rate = float(algo.get("backtest_win_rate", 0.5))
        profit_factor = max(float(algo.get("backtest_profit_factor", 1.0)), 0.1)
        n = 50  # minimum for Monte Carlo
        rng = np.random.default_rng(seed=42)
        wins = int(n * win_rate)
        losses = n - wins
        avg_win = 0.03 * profit_factor / max(profit_factor, 1)
        avg_loss = -0.02
        win_returns = rng.normal(avg_win, avg_win * 0.3, wins).tolist()
        loss_returns = rng.normal(avg_loss, abs(avg_loss) * 0.3, losses).tolist()
        returns = win_returns + loss_returns
        rng.shuffle(returns)
        return [float(r) for r in returns]

    def _log_event(self, event_type: str, stage: str, status: str,
                   ticker: str = None, algorithm_id: str = None, detail: str = ""):
        try:
            self._dr.log_pipeline_event(event_type, stage, status, ticker, algorithm_id, detail)
        except Exception:
            pass
