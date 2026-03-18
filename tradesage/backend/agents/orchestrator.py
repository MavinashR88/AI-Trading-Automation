"""
Orchestrator Agent — LangGraph StateGraph that routes between all agents.
Implements the 3-Layer Gate System: News → Risk → Mentor.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, Literal, Optional, TypedDict

from langgraph.graph import StateGraph, END

from backend.models.trade import (
    RiskParams, Signal, TradeResult, ProbabilityScore, ReviewNote, TradeDetail
)
from backend.models.lesson import Lesson
from backend.models.signal import NewsSignal
from backend.agents.news_agent import NewsAgent
from backend.agents.risk_agent import RiskAgent
from backend.agents.mentor_agent import MentorAgent
from backend.agents.macro_check import macro_check_agent
from backend.agents.trade_executor import TradeExecutor
from backend.knowledge.graph_updater import GraphUpdater
from backend.knowledge.graph_reasoner import GraphReasoner
from backend.analytics.stats import compute_probability_score
from backend.db.sqlite_store import TradeStore
from backend.data.market_data import MarketDataFeed

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# LangGraph State
# ──────────────────────────────────────────────

class TradeSageState(TypedDict):
    # Input
    ticker: str
    market_type: str                    # "stock" | "crypto" | "forex" | "options"
    requested_action: str               # "buy" | "sell" | "hold"

    # Gate 0 — Macro check
    macro_gate: Optional[dict]          # result from macro_check_agent
    macro_size_multiplier: float        # 1.0 = full, 0.5 = CAUTION (half position)

    # Layer 1 — News gate
    news_summary: str
    sentiment_score: float              # -1.0 to 1.0
    news_urgency: str                   # "immediate" | "wait" | "override_cancel"
    news_catalyst: str
    news_age_minutes: int
    breaking_news_override: bool
    news_gate_passed: bool
    news_signal_id: str

    # Layer 2 — Risk gate
    entry_price: float
    risk_params: Optional[RiskParams]
    risk_gate_passed: bool
    risk_gate_reason: str
    portfolio_value: float
    daily_pnl: float

    # Layer 3 — Mentor review gate
    review_note: Optional[ReviewNote]
    mentor_gate_passed: bool
    probability_score: Optional[ProbabilityScore]

    # Layer 4 — Algorithm gate (graduated pipeline algorithms)
    algo_gate_passed: bool
    algo_gate_reason: str

    # Retry control
    retry_count: int                    # max 1 retry

    # Execution
    signal: Optional[Signal]
    trade_result: Optional[TradeResult]
    trade_blocked: bool
    block_reason: str

    # Post-trade
    mentor_lesson: Optional[Lesson]
    win_rate: float
    consecutive_wins: int
    mode: str                           # "paper" | "live"

    # Error
    error: Optional[str]


# ──────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────

class TradeSageOrchestrator:
    """
    LangGraph StateGraph orchestrator for TradeSage.
    Wires: News → Risk → Mentor → Execute → Post-trade.
    """

    def __init__(
        self,
        news_agent: NewsAgent,
        risk_agent: RiskAgent,
        mentor_agent: MentorAgent,
        executor: TradeExecutor,
        graph_updater: GraphUpdater,
        graph_reasoner: GraphReasoner,
        market_data: MarketDataFeed,
        store: TradeStore,
        llm_model: str = "",            # legacy, ignored — router handles model
        anthropic_api_key: str = "",    # legacy, ignored — router handles auth
        websocket_emitter: Optional[Callable] = None,
        data_router=None,               # DataRouter — for algo gate
    ):
        self._news = news_agent
        self._risk = risk_agent
        self._mentor = mentor_agent
        self._executor = executor
        self._graph_updater = graph_updater
        self._graph_reasoner = graph_reasoner
        self._market_data = market_data
        self._store = store
        self._emit = websocket_emitter
        self._data_router = data_router
        self._graph = self._build_graph()
        self._watch_list: list[str] = []

    # ──────────────────────────────────────────────
    # Graph Construction
    # ──────────────────────────────────────────────

    def _build_graph(self) -> Any:
        """Build and compile the LangGraph StateGraph."""
        builder = StateGraph(TradeSageState)

        # Add nodes (Gate 0 is new)
        builder.add_node("macro_layer", self._node_macro_layer)
        builder.add_node("news_layer", self._node_news_layer)
        builder.add_node("risk_layer", self._node_risk_layer)
        builder.add_node("mentor_layer", self._node_mentor_layer)
        builder.add_node("algo_gate_layer", self._node_algo_gate_layer)
        builder.add_node("execute_trade", self._node_execute_trade)
        builder.add_node("post_trade", self._node_post_trade)
        builder.add_node("block_trade", self._node_block_trade)
        builder.add_node("retry_analysis", self._node_retry_analysis)

        # Gate 0 is the new entry point
        builder.set_entry_point("macro_layer")

        # Macro → News (CAUTION allows through with half position)
        builder.add_conditional_edges(
            "macro_layer",
            self._route_after_macro,
            {
                "news_layer": "news_layer",
                "block_trade": "block_trade",
            },
        )

        # Conditional routing after news layer
        builder.add_conditional_edges(
            "news_layer",
            self._route_after_news,
            {
                "risk_layer": "risk_layer",
                "block_trade": "block_trade",
            },
        )

        # Conditional routing after risk layer
        builder.add_conditional_edges(
            "risk_layer",
            self._route_after_risk,
            {
                "mentor_layer": "mentor_layer",
                "retry": "retry_analysis",
                "block_trade": "block_trade",
            },
        )

        # Conditional routing after mentor layer → algo gate
        builder.add_conditional_edges(
            "mentor_layer",
            self._route_after_mentor,
            {
                "execute_trade": "algo_gate_layer",
                "retry": "retry_analysis",
                "block_trade": "block_trade",
            },
        )

        # Conditional routing after algo gate
        builder.add_conditional_edges(
            "algo_gate_layer",
            self._route_after_algo_gate,
            {
                "execute_trade": "execute_trade",
                "block_trade": "block_trade",
            },
        )

        # Retry goes back to news layer
        builder.add_conditional_edges(
            "retry_analysis",
            self._route_after_retry,
            {
                "news_layer": "news_layer",
                "block_trade": "block_trade",
            },
        )

        # Execute → post-trade
        builder.add_edge("execute_trade", "post_trade")
        builder.add_edge("post_trade", END)
        builder.add_edge("block_trade", END)

        return builder.compile()

    # ──────────────────────────────────────────────
    # Node Implementations
    # ──────────────────────────────────────────────

    async def _node_macro_layer(self, state: TradeSageState) -> dict:
        """Gate 0: Macro/sentiment check — runs before all other gates."""
        ticker = state["ticker"]
        action = state.get("requested_action", "buy")
        logger.info("[Orchestrator] Gate 0: Macro check for %s", ticker)

        try:
            # Determine sector from graph or default to Unknown
            sector = "Unknown"
            if self._graph_reasoner:
                try:
                    sector = self._graph_reasoner.get_sector(ticker) or "Unknown"
                except Exception:
                    pass

            macro_result = await macro_check_agent.check(
                ticker=ticker,
                sector=sector,
                action=action,
                sentiment_score=state.get("sentiment_score", 0.0),
            )
        except Exception as exc:
            logger.warning("Macro check failed (allowing trade): %s", exc)
            macro_result = {"verdict": "PASS", "passed": True, "size_multiplier": 1.0, "reason": "Macro check unavailable"}

        # Emit gate result to WebSocket
        if self._emit:
            try:
                await self._emit("gate_result", {
                    "gate": "macro",
                    "ticker": ticker,
                    "verdict": macro_result.get("verdict", "PASS"),
                    "reason": macro_result.get("reason", ""),
                    "risk_factors": macro_result.get("risk_factors", []),
                })
            except Exception:
                pass

        return {
            "macro_gate": macro_result,
            "macro_size_multiplier": macro_result.get("size_multiplier", 1.0),
        }

    async def _node_news_layer(self, state: TradeSageState) -> dict:
        """Layer 1: Fetch and score news."""
        ticker = state["ticker"]
        logger.info("[Orchestrator] Layer 1: News scan for %s", ticker)

        try:
            news_signal = await self._news.scan_ticker(ticker)
        except Exception as exc:
            logger.error("News layer failed: %s", exc)
            news_signal = None

        if news_signal:
            return {
                "news_summary": news_signal.raw_text or news_signal.headline,
                "sentiment_score": news_signal.sentiment_score,
                "news_urgency": news_signal.urgency,
                "news_catalyst": news_signal.catalyst,
                "news_age_minutes": news_signal.age_minutes,
                "breaking_news_override": news_signal.breaking_override,
                "news_gate_passed": news_signal.urgency != "override_cancel",
                "news_signal_id": news_signal.signal_id,
            }
        else:
            return {
                "news_summary": "No news data available",
                "sentiment_score": 0.0,
                "news_urgency": "wait",
                "news_catalyst": "No catalyst",
                "news_age_minutes": 999,
                "breaking_news_override": False,
                "news_gate_passed": True,   # No news = neutral, allow analysis to proceed
                "news_signal_id": "",
            }

    async def _node_risk_layer(self, state: TradeSageState) -> dict:
        """Layer 2: Risk assessment and position sizing."""
        ticker = state["ticker"]
        logger.info("[Orchestrator] Layer 2: Risk evaluation for %s", ticker)

        # Get current price — try last, ask, bid in order
        try:
            price_data = await self._market_data.get_price(ticker)
            entry_price = next(
                (v for v in [
                    price_data.get("last"),
                    price_data.get("ask"),
                    price_data.get("bid"),
                    price_data.get("close"),
                ] if v and v > 0),
                0.0,
            )
        except Exception as exc:
            logger.error("Price fetch failed: %s", exc)
            entry_price = 0.0

        if entry_price <= 0:
            return {
                "entry_price": 0.0,
                "risk_gate_passed": False,
                "risk_gate_reason": f"Could not fetch valid price for {ticker}",
                "risk_params": None,
            }

        # Get portfolio value
        portfolio_value = state.get("portfolio_value", 50_000.0)
        daily_pnl = state.get("daily_pnl", 0.0)
        win_rate = state.get("win_rate", 0.5)
        sentiment_confidence = max(0.1, abs(state.get("sentiment_score", 0.5)))

        passed, reason, risk_params = self._risk.evaluate(
            portfolio_value=portfolio_value,
            entry_price=entry_price,
            action=state.get("requested_action", "buy"),
            win_rate=win_rate,
            sentiment_confidence=sentiment_confidence,
            daily_pnl=daily_pnl,
        )

        # Apply macro CAUTION size multiplier (0.5 = half position on risky macro)
        size_mult = state.get("macro_size_multiplier", 1.0)
        if risk_params and size_mult < 1.0:
            risk_params.position_size = risk_params.position_size * size_mult
            est_qty = max(1, int(risk_params.position_size / max(entry_price, 0.01)))
            logger.info(
                "[Orchestrator] Macro CAUTION: position scaled to %.0f%% → $%.2f (~%d shares)",
                size_mult * 100,
                risk_params.position_size,
                est_qty,
            )

        return {
            "entry_price": entry_price,
            "risk_gate_passed": passed,
            "risk_gate_reason": reason,
            "risk_params": risk_params,
        }

    async def _node_mentor_layer(self, state: TradeSageState) -> dict:
        """Layer 3: Mentor pre-trade review gate."""
        ticker = state["ticker"]
        trade_id = str(uuid.uuid4())
        logger.info("[Orchestrator] Layer 3: Mentor review for %s (trade_id=%s)", ticker, trade_id)

        # Compute probability score
        sentiment = state.get("sentiment_score", 0.5)
        news_score = (sentiment + 1) / 2   # normalise -1..1 → 0..1
        risk_params = state.get("risk_params")
        risk_score = 0.8 if risk_params else 0.2

        # Historical win rate from graph
        ticker_stats = self._graph_reasoner.q11_ticker_win_rate(ticker)
        historical_wr = ticker_stats.get("win_rate", 0.5)

        # Mentor conviction starts at 0.5 — will be updated after review
        prob_score = compute_probability_score(
            trade_id=trade_id,
            news_score=news_score,
            risk_score=risk_score,
            mentor_score=0.5,
            historical_win_rate=historical_wr,
        )

        # Mentor review
        review = await self._mentor.pre_trade_review(
            trade_id=trade_id,
            ticker=ticker,
            market_type=state.get("market_type", "stock"),
            action=state.get("requested_action", "buy"),
            entry_price=state.get("entry_price", 0.0),
            signal_confidence=state.get("sentiment_score", 0.5),
            sentiment_score=state.get("sentiment_score", 0.0),
            news_summary=state.get("news_summary", ""),
            news_catalyst=state.get("news_catalyst", ""),
            news_urgency=state.get("news_urgency", "wait"),
            risk_params=risk_params.model_dump() if risk_params else {},
            probability_score=prob_score,
            macro_gate=state.get("macro_gate"),
        )

        # Recalculate prob score with actual mentor confidence
        prob_score = compute_probability_score(
            trade_id=trade_id,
            news_score=news_score,
            risk_score=risk_score,
            mentor_score=review.confidence_score,
            historical_win_rate=historical_wr,
        )
        review.probability_score = prob_score

        # Save review note
        await self._store.save_review_note(review)

        # Emit review note to WebSocket (fires BEFORE trade)
        if self._emit:
            try:
                await self._emit("review_note", review.model_dump(mode="json"))
            except Exception as exc:
                logger.warning("WebSocket emit (review_note) failed: %s", exc)

        mentor_passed = review.decision in ("APPROVED", "REDUCED")

        # Build signal if approved
        signal = None
        if mentor_passed:
            signal = Signal(
                trade_id=trade_id,
                ticker=ticker,
                market_type=state.get("market_type", "stock"),
                action=state.get("requested_action", "buy"),
                confidence=review.confidence_score,
                reasoning=review.reasoning,
                entry_price=state.get("entry_price", 0.0),
                timestamp=datetime.utcnow(),
            )

        return {
            "review_note": review,
            "mentor_gate_passed": mentor_passed,
            "signal": signal,
            "probability_score": prob_score,
            "trade_blocked": not mentor_passed,
            "block_reason": "" if mentor_passed else f"Mentor BLOCKED: {review.reasoning[:200]}",
        }

    async def _node_execute_trade(self, state: TradeSageState) -> dict:
        """Execute the trade via the executor."""
        signal = state.get("signal")
        risk_params = state.get("risk_params")

        if not signal or not risk_params:
            return {
                "trade_result": None,
                "trade_blocked": True,
                "block_reason": "No signal or risk params available for execution",
            }

        logger.info(
            "[Orchestrator] Executing %s trade: %s %s @ $%.4f",
            state.get("mode", "paper"), signal.action.upper(), signal.ticker, signal.entry_price
        )

        result = await self._executor.execute(signal, risk_params)

        # Save trade detail to SQLite
        review = state.get("review_note")
        prob = state.get("probability_score")
        if review and prob:
            detail = TradeDetail(
                trade_id=signal.trade_id,
                ticker=signal.ticker,
                market_type=signal.market_type,
                signal=signal,
                risk_params=risk_params,
                review_note=review,
                probability_score=prob,
                trade_result=result,
                mode=state.get("mode", "paper"),
            )
            await self._store.save_trade(detail)

        return {"trade_result": result}

    async def _node_post_trade(self, state: TradeSageState) -> dict:
        """Post-trade: generate lesson + update graph + update win rate."""
        result = state.get("trade_result")
        signal = state.get("signal")
        review = state.get("review_note")

        if not result or not signal or not review:
            return {}

        # Trade stays OPEN — bracket orders on Alpaca will auto-close at TP/SL.
        # The position monitor in main.py detects the close and calls generate_lesson.
        if result.outcome == "OPEN":
            return {"trade_result": result}

        if not result.outcome:
            return {"trade_result": result}

        # Win rate stats
        win_rate_data = await self._store.compute_win_rate()
        win_rate = win_rate_data.get("win_rate", 0.5)
        consecutive_wins = win_rate_data.get("consecutive_wins", 0)
        total_trades = win_rate_data.get("total_trades", 0)

        # Detect pattern
        pattern = self._graph_reasoner.detect_pattern(
            signal.ticker,
            state.get("sentiment_score", 0.0),
            state.get("news_urgency", "wait"),
        )

        # Generate mentor lesson
        lesson = await self._mentor.generate_lesson(
            trade_id=signal.trade_id,
            ticker=signal.ticker,
            action=signal.action,
            entry_price=result.entry_price,
            exit_price=result.exit_price or result.entry_price,
            pnl_pct=(result.pnl_pct or 0.0) / 100,
            pnl_dollars=result.pnl_dollars or 0.0,
            outcome=result.outcome,
            hold_minutes=result.hold_minutes or 0,
            news_summary=state.get("news_summary", ""),
            sentiment_score=state.get("sentiment_score", 0.0),
            pattern_name=pattern,
            review_note=review,
            win_rate=win_rate,
            consecutive_wins=consecutive_wins,
        )

        # Save lesson
        await self._store.save_lesson(lesson)

        # Update graph
        prob = state.get("probability_score")
        self._graph_updater.wire_post_trade(
            trade_id=signal.trade_id,
            ticker=signal.ticker,
            result=result,
            lesson=lesson,
            pattern_name=pattern,
            principle_name=lesson.trader_principle,
            probability_score=prob.composite_score if prob else 0.5,
            news_event_id=state.get("news_signal_id"),
            win_rate=win_rate,
            consecutive_wins=consecutive_wins,
            total_trades=total_trades,
        )

        # Emit lesson to WebSocket
        if self._emit:
            try:
                await self._emit("lesson", lesson.model_dump(mode="json"))
            except Exception as exc:
                logger.warning("WebSocket emit (lesson) failed: %s", exc)

        return {
            "mentor_lesson": lesson,
            "win_rate": win_rate,
            "consecutive_wins": consecutive_wins,
            "trade_result": result,
        }

    async def _node_block_trade(self, state: TradeSageState) -> dict:
        """Log a blocked trade."""
        reason = state.get("block_reason", "Unknown reason")
        ticker = state.get("ticker", "UNKNOWN")
        logger.warning("[Orchestrator] Trade BLOCKED for %s: %s", ticker, reason)
        return {"trade_blocked": True}

    async def _node_retry_analysis(self, state: TradeSageState) -> dict:
        """Increment retry counter."""
        retry_count = state.get("retry_count", 0) + 1
        logger.info("[Orchestrator] Retry #%d for %s", retry_count, state.get("ticker"))
        return {"retry_count": retry_count}

    # ──────────────────────────────────────────────
    # Routing Functions
    # ──────────────────────────────────────────────

    def _route_after_macro(self, state: TradeSageState) -> str:
        """BLOCK only if 2+ risk factors. CAUTION allows through with half position."""
        macro = state.get("macro_gate") or {}
        verdict = macro.get("verdict", "PASS")
        if verdict == "BLOCK":
            return "block_trade"
        return "news_layer"  # PASS or CAUTION → continue

    def _route_after_news(self, state: TradeSageState) -> str:
        # Only hard-block on override_cancel (e.g. Fed rate decision mid-trade)
        if state.get("news_urgency") == "override_cancel":
            return "block_trade"
        # Breaking bearish news + SELL = aligned → allow through to risk/mentor
        # Breaking bearish news + BUY = misaligned → block (buying into a crash)
        if state.get("breaking_news_override"):
            sentiment = state.get("sentiment_score", 0.0)
            action = state.get("requested_action", "buy")
            if action == "buy" and sentiment < -0.3:
                return "block_trade"  # Don't buy into panic
            # Otherwise let the mentor decide
        return "risk_layer"

    def _route_after_risk(self, state: TradeSageState) -> str:
        if state.get("risk_gate_passed"):
            return "mentor_layer"
        if state.get("retry_count", 0) < 1:
            return "retry"
        return "block_trade"

    def _route_after_mentor(self, state: TradeSageState) -> str:
        if state.get("mentor_gate_passed"):
            return "execute_trade"
        if state.get("retry_count", 0) < 1:
            return "retry"
        return "block_trade"

    def _route_after_retry(self, state: TradeSageState) -> str:
        if state.get("retry_count", 0) >= 1:
            return "block_trade"
        return "news_layer"

    def _route_after_algo_gate(self, state: TradeSageState) -> str:
        if state.get("algo_gate_passed", True):
            return "execute_trade"
        return "block_trade"

    # ── Layer 4: Algorithm Gate ────────────────────────────────────

    async def _node_algo_gate_layer(self, state: TradeSageState) -> dict:
        """
        Gate 4: Check if any pipeline-graduated algorithm agrees with this signal.

        Rules:
        - No algos for this ticker → PASS (no opinion yet)
        - LIVE algo (graduated) disagrees → BLOCK (hard gate, proven strategy)
        - PAPER_TRADING algo disagrees → WARN but allow (still being validated)
        - Any algo agrees → PASS with confidence boost
        """
        ticker = state["ticker"]
        action = state.get("requested_action", "buy")
        logger.info("[Orchestrator] Gate 4: Algorithm gate for %s", ticker)

        result = {"algo_gate_passed": True, "algo_gate_reason": "No algorithms for this ticker"}

        if not self._data_router:
            return result

        try:
            from backend.agents.paper_trading.paper_trader import PaperTradingRunner

            # Fetch current bar for this ticker
            runner = PaperTradingRunner(self._data_router, self._market_data)
            bar = await runner._get_bar(ticker)
            if not bar:
                return result

            # Check LIVE algos first (hard gate)
            live_algos = self._data_router.get_algorithms(status="LIVE", ticker=ticker)
            for algo in live_algos:
                entry_signal = runner._run_entry(algo, bar)
                agrees = entry_signal if action == "buy" else not entry_signal
                if not agrees:
                    reason = f"LIVE algo '{algo.get('name')}' disagrees — blocking"
                    logger.warning("[AlgoGate] %s: %s", ticker, reason)
                    if self._emit:
                        await self._emit("gate_result", {
                            "gate": "algo",
                            "ticker": ticker,
                            "verdict": "BLOCK",
                            "reason": reason,
                        })
                    return {"algo_gate_passed": False, "algo_gate_reason": reason}
                else:
                    result = {"algo_gate_passed": True, "algo_gate_reason": f"LIVE algo '{algo.get('name')}' confirms signal"}

            # Check PAPER_TRADING algos (advisory)
            paper_algos = self._data_router.get_algorithms(status="PAPER_TRADING", ticker=ticker)
            agrees_count = 0
            disagrees_count = 0
            for algo in paper_algos:
                entry_signal = runner._run_entry(algo, bar)
                if (entry_signal and action == "buy") or (not entry_signal and action == "sell"):
                    agrees_count += 1
                else:
                    disagrees_count += 1

            if paper_algos:
                if agrees_count > disagrees_count:
                    result = {
                        "algo_gate_passed": True,
                        "algo_gate_reason": f"{agrees_count}/{len(paper_algos)} paper algos confirm",
                    }
                else:
                    result = {
                        "algo_gate_passed": True,  # advisory only — don't block
                        "algo_gate_reason": f"WARNING: {disagrees_count}/{len(paper_algos)} paper algos disagree (advisory)",
                    }

        except Exception as exc:
            logger.warning("[AlgoGate] Error checking algorithms for %s: %s", ticker, exc)
            result = {"algo_gate_passed": True, "algo_gate_reason": "Algorithm gate error — allowing"}

        if self._emit:
            try:
                await self._emit("gate_result", {
                    "gate": "algo",
                    "ticker": ticker,
                    "verdict": "PASS" if result["algo_gate_passed"] else "BLOCK",
                    "reason": result["algo_gate_reason"],
                })
            except Exception:
                pass

        logger.info("[AlgoGate] %s: %s", ticker, result["algo_gate_reason"])
        return result

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    async def run(
        self,
        ticker: str,
        market_type: str = "stock",
        action: str = "buy",
        portfolio_value: float = 50_000.0,
        daily_pnl: float = 0.0,
        win_rate: float = 0.5,
        mode: str = "paper",
    ) -> TradeSageState:
        """
        Run the full trade analysis + execution pipeline.
        Returns the final state.
        """
        initial_state: TradeSageState = {
            "ticker": ticker,
            "market_type": market_type,
            "requested_action": action,
            "macro_gate": None,
            "macro_size_multiplier": 1.0,
            "news_summary": "",
            "sentiment_score": 0.0,
            "news_urgency": "wait",
            "news_catalyst": "",
            "news_age_minutes": 0,
            "breaking_news_override": False,
            "news_gate_passed": False,
            "news_signal_id": "",
            "entry_price": 0.0,
            "risk_params": None,
            "risk_gate_passed": False,
            "risk_gate_reason": "",
            "portfolio_value": portfolio_value,
            "daily_pnl": daily_pnl,
            "review_note": None,
            "mentor_gate_passed": False,
            "probability_score": None,
            "algo_gate_passed": True,
            "algo_gate_reason": "",
            "retry_count": 0,
            "signal": None,
            "trade_result": None,
            "trade_blocked": False,
            "block_reason": "",
            "mentor_lesson": None,
            "win_rate": win_rate,
            "consecutive_wins": 0,
            "mode": mode,
            "error": None,
        }

        try:
            final_state = await self._graph.ainvoke(initial_state)
            return final_state
        except Exception as exc:
            logger.error("Orchestrator run failed: %s", exc)
            initial_state["error"] = str(exc)
            initial_state["trade_blocked"] = True
            initial_state["block_reason"] = f"Orchestrator error: {exc}"
            return initial_state

    def set_websocket_emitter(self, emitter: Callable) -> None:
        self._emit = emitter
        self._executor._emit = emitter

    def update_watch_list(self, tickers: list[str]) -> None:
        self._watch_list = tickers

    def get_watch_list(self) -> list[str]:
        return self._watch_list
