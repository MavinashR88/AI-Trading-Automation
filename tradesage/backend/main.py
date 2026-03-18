"""
TradeSage — FastAPI entrypoint
Startup sequence, WebSocket live feed, REST API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks,
    HTTPException, UploadFile, File, Body
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Config (must be first — crashes if keys missing) ────────────────────────
from backend.config import settings

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tradesage")

# ── Internal imports ────────────────────────────────────────────────────────
from backend.db.sqlite_store import init_db, get_store
from backend.knowledge.graph_schema import setup_graph, get_graph_stats
from backend.knowledge.ingest import ingest_all, ingest_pdf
from backend.knowledge.graph_reasoner import GraphReasoner
from backend.knowledge.graph_updater import GraphUpdater
from backend.data.market_data import MarketDataFeed
from backend.agents.news_agent import NewsAgent
from backend.agents.risk_agent import RiskAgent
from backend.agents.mentor_agent import MentorAgent
from backend.agents.trade_executor import TradeExecutor
from backend.agents.orchestrator import TradeSageOrchestrator
from backend.analytics.stats import compute_probability_score
from backend.models.model_registry import save_version, list_versions, load_best, rollback_to
from backend.llm.cost_tracker import cost_tracker
from backend.mentor.book_suggester import get_reading_list, mark_book_uploaded, mark_book_learned, suggest_book_for_loss
from backend.mentor.pattern_analyzer import run_weekly_analysis, get_weekly_reports
from backend.db.router import get_data_router, DataRouter
from backend.agents.pipeline.pipeline_orchestrator import PipelineOrchestrator
from backend.agents.paper_trading.paper_trader import PaperTradingRunner


# ══════════════════════════════════════════════
# Application State (singletons)
# ══════════════════════════════════════════════

class AppState:
    neo4j_driver = None
    graph_reasoner: Optional[GraphReasoner] = None
    graph_updater: Optional[GraphUpdater] = None
    market_data: Optional[MarketDataFeed] = None
    news_agent: Optional[NewsAgent] = None
    risk_agent: Optional[RiskAgent] = None
    mentor_agent: Optional[MentorAgent] = None
    executor: Optional[TradeExecutor] = None
    orchestrator: Optional[TradeSageOrchestrator] = None
    scheduler: Optional[AsyncIOScheduler] = None
    watch_list: list[str] = list(settings.DEFAULT_TICKERS)
    portfolio_value: float = settings.STARTING_CAPITAL
    daily_pnl: float = 0.0
    win_rate: float = 0.5
    # Pending signals awaiting user approval: signal_id → signal dict
    pending_signals: dict[str, dict] = {}
    data_router: Optional[DataRouter] = None
    pipeline_orchestrator: Optional[PipelineOrchestrator] = None
    paper_trading_runner: Optional[PaperTradingRunner] = None


app_state = AppState()

# ──────────────────────────────────────────────
# WebSocket connection manager
# ──────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        logger.info("WebSocket connected. Total connections: %d", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, event_type: str, data: Any) -> None:
        message = json.dumps({"type": event_type, "data": data, "timestamp": datetime.utcnow().isoformat()})
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = ConnectionManager()


async def ws_emit(event_type: str, data: Any) -> None:
    """WebSocket emitter passed to agents."""
    await ws_manager.broadcast(event_type, data)


# ══════════════════════════════════════════════
# Startup / Shutdown
# ══════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Full startup sequence and graceful shutdown."""
    logger.info("=" * 60)
    logger.info("  TradeSage — Multi-Agent AI Trading System")
    logger.info("  Starting up...")
    logger.info("=" * 60)

    # 1. SQLite
    await init_db()
    store = get_store()

    # 1b. Data router (pipeline persistence)
    try:
        app_state.data_router = await get_data_router()
    except Exception as exc:
        logger.warning("[1b] Data router init failed: %s", exc)
        app_state.data_router = None

    # 2. Neo4j
    logger.info("[2/8] Connecting to Neo4j...")
    try:
        driver = setup_graph(settings.NEO4J_URI, settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        if driver is not None:
            app_state.neo4j_driver = driver
            app_state.graph_reasoner = GraphReasoner(driver)
            app_state.graph_updater = GraphUpdater(driver)
            logger.info("[2/8] Neo4j connected.")
        else:
            logger.warning("[2/8] Neo4j unavailable — graph features disabled.")
            app_state.graph_reasoner = _StubReasoner()
            app_state.graph_updater = _StubUpdater()
    except Exception as exc:
        logger.error("[2/8] Neo4j connection failed: %s. Running without graph.", exc)
        app_state.graph_reasoner = _StubReasoner()
        app_state.graph_updater = _StubUpdater()

    # 3. Seed graph in background
    logger.info("[3/8] Seeding knowledge graph in background...")

    async def _seed():
        try:
            if app_state.neo4j_driver:
                await asyncio.to_thread(ingest_all, app_state.neo4j_driver)
        except Exception as exc:
            logger.error("Graph seeding failed: %s", exc)

    asyncio.create_task(_seed())

    # 4. Market data feed
    logger.info("[4/8] Initialising market data feed...")
    app_state.market_data = MarketDataFeed(
        alpaca_api_key=settings.ALPACA_API_KEY,
        alpaca_secret=settings.ALPACA_SECRET_KEY,
        alpaca_base_url=settings.ALPACA_BASE_URL,
        exchange_id=settings.EXCHANGE_ID,
        exchange_api_key=settings.EXCHANGE_API_KEY,
        exchange_secret=settings.EXCHANGE_SECRET,
    )

    # 5. Agents
    logger.info("[5/8] Initialising agents...")
    app_state.news_agent = NewsAgent(
        tavily_api_key=settings.TAVILY_API_KEY,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        llm_model=settings.LLM_MODEL,
        graph_updater=app_state.graph_updater,
        store=store,
    )
    app_state.risk_agent = RiskAgent(
        starting_capital=settings.STARTING_CAPITAL,
        max_position_pct=settings.MAX_POSITION_PCT,
        risk_per_trade=settings.RISK_PER_TRADE,
        reward_risk_ratio=settings.REWARD_RISK_RATIO,
        max_drawdown_pct=settings.MAX_DRAWDOWN_PCT,
        max_daily_loss_pct=settings.MAX_DAILY_LOSS_PCT,
    )
    app_state.mentor_agent = MentorAgent(
        llm_model=settings.LLM_MODEL,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        graph_reasoner=app_state.graph_reasoner,
    )
    app_state.executor = TradeExecutor(
        alpaca_api_key=settings.ALPACA_API_KEY,
        alpaca_secret=settings.ALPACA_SECRET_KEY,
        alpaca_paper_url=settings.ALPACA_BASE_URL,
        alpaca_live_url=settings.ALPACA_LIVE_URL,
        exchange_id=settings.EXCHANGE_ID,
        exchange_api_key=settings.EXCHANGE_API_KEY,
        exchange_secret=settings.EXCHANGE_SECRET,
        store=store,
        mode=settings.TRADING_MODE,
        websocket_emitter=ws_emit,
    )
    app_state.orchestrator = TradeSageOrchestrator(
        news_agent=app_state.news_agent,
        risk_agent=app_state.risk_agent,
        mentor_agent=app_state.mentor_agent,
        executor=app_state.executor,
        graph_updater=app_state.graph_updater,
        graph_reasoner=app_state.graph_reasoner,
        market_data=app_state.market_data,
        store=store,
        llm_model=settings.LLM_MODEL,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        websocket_emitter=ws_emit,
        data_router=app_state.data_router,
    )

    # Pipeline orchestrator (auto-runs every 4 hours)
    if app_state.data_router:
        app_state.pipeline_orchestrator = PipelineOrchestrator(app_state.data_router, ws_manager)
        app_state.paper_trading_runner = PaperTradingRunner(app_state.data_router, app_state.market_data)

    # 6. APScheduler — hourly news scan + position monitor
    logger.info("[6/8] Starting APScheduler (hourly news scan + position monitor)...")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _scheduled_news_scan,
        trigger="interval",
        minutes=settings.NEWS_SCAN_INTERVAL_MINUTES,
        id="hourly_news_scan",
        replace_existing=True,
    )
    scheduler.add_job(
        _position_monitor,
        trigger="interval",
        seconds=5,
        id="position_monitor",
        replace_existing=True,
    )
    scheduler.add_job(
        _weekly_analysis_job,
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=59,
        id="weekly_analysis",
        replace_existing=True,
    )
    if app_state.pipeline_orchestrator:
        scheduler.add_job(
            app_state.pipeline_orchestrator.run_full_pipeline,
            trigger="interval",
            hours=4,
            id="pipeline_full",
            replace_existing=True,
        )
    if app_state.paper_trading_runner:
        scheduler.add_job(
            app_state.paper_trading_runner.run_cycle,
            trigger="interval",
            minutes=5,
            id="paper_trading_cycle",
            replace_existing=True,
        )
    scheduler.start()
    app_state.scheduler = scheduler
    if app_state.pipeline_orchestrator:
        app_state.pipeline_orchestrator._scheduler = scheduler

    # 7. Alpaca paper account check
    logger.info("[7/8] Checking Alpaca paper account...")
    try:
        portfolio = await app_state.executor.get_portfolio()
        pv = portfolio.get("portfolio_value", settings.STARTING_CAPITAL)
        if pv > 0:
            app_state.portfolio_value = pv
            logger.info("[7/8] Alpaca portfolio value: $%.2f", pv)
    except Exception as exc:
        logger.warning("[7/8] Could not fetch Alpaca portfolio: %s", exc)

    # 8. Startup summary
    try:
        if app_state.neo4j_driver:
            stats = get_graph_stats(app_state.neo4j_driver)
        else:
            stats = {"nodes": 0, "relationships": 0}
    except Exception:
        stats = {"nodes": 0, "relationships": 0}

    logger.info("=" * 60)
    logger.info("  TradeSage READY")
    logger.info("  Mode:            %s", settings.TRADING_MODE.upper())
    logger.info("  LLM Mode:        %s  (budget $%.2f/day)", settings.LLM_MODE.upper(), settings.LLM_DAILY_BUDGET_USD)
    logger.info("  Portfolio:       $%.2f", app_state.portfolio_value)
    logger.info("  Watch list:      %s", ", ".join(app_state.watch_list))
    logger.info("  Graph nodes:     %d", stats["nodes"])
    logger.info("  Graph rels:      %d", stats["relationships"])
    logger.info("  Server:          http://%s:%d", settings.APP_HOST, settings.APP_PORT)
    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("Shutting down TradeSage...")
    if app_state.scheduler:
        app_state.scheduler.shutdown(wait=False)
    if app_state.neo4j_driver:
        app_state.neo4j_driver.close()


async def _position_monitor() -> None:
    """
    Every 2 minutes:
    - Real Alpaca trades: if position gone from Alpaca → bracket TP/SL triggered → record close
    - Simulated trades (paper_ order_id): check live price vs TP/SL → close if hit
    """
    if not app_state.executor:
        return
    import datetime as dt_mod
    store = get_store()
    try:
        open_trades = await store.get_open_trades()
        if not open_trades:
            return

        alpaca_positions = await app_state.executor.get_all_positions()
        logger.info("[Monitor] Open trades: %d  |  Alpaca live positions: %s",
                    len(open_trades), list(alpaca_positions.keys()))

        # Safety: if Alpaca returned 0 positions but we have open trades,
        # this is likely an API error — do NOT close anything falsely
        real_trades = [t for t in open_trades if not str(t.get("order_id", "")).startswith("paper_")]
        if real_trades and len(alpaca_positions) == 0:
            logger.warning("[Monitor] Alpaca returned 0 positions but %d real trades are open — skipping to avoid false close", len(real_trades))
            return

        for trade in open_trades:
            ticker = trade.get("ticker", "")
            trade_id = trade.get("trade_id", "")
            side = trade.get("side", "buy")
            entry_price = float(trade.get("entry_price") or 0)
            quantity = float(trade.get("quantity") or 0)
            stop_loss = float(trade.get("stop_loss") or 0)
            take_profit = float(trade.get("take_profit") or 0)
            order_id = trade.get("order_id", "")

            if not ticker or not entry_price or not quantity:
                continue

            filled_at_str = trade.get("created_at")
            filled_at = None
            if filled_at_str:
                try:
                    filled_at = dt_mod.datetime.fromisoformat(str(filled_at_str))
                except Exception:
                    pass

            # None order_id means execution fallback used simulate — treat as simulated
            is_simulated = not order_id or str(order_id).startswith("paper_")

            # Minimum hold time: 3 minutes before any simulated TP/SL can trigger
            # (prevents false closes right after entry due to timing glitches)
            MIN_HOLD_SECONDS = 180
            age_seconds = 0
            if filled_at:
                age_seconds = (dt_mod.datetime.utcnow() - filled_at.replace(tzinfo=None)).total_seconds()
            if is_simulated and age_seconds < MIN_HOLD_SECONDS:
                logger.debug("[Monitor][SIM] %s too young (%.0fs < %ds) — skipping TP/SL check", ticker, age_seconds, MIN_HOLD_SECONDS)
                continue

            if is_simulated:
                # Simulated trade — Alpaca never opened it, check current price vs TP/SL
                if not app_state.market_data:
                    continue
                try:
                    price_data = await app_state.market_data.get_price(ticker)
                    current_price = float(price_data.get("last") or price_data.get("ask") or 0) if isinstance(price_data, dict) else float(price_data or 0)
                except Exception:
                    continue
                if not current_price or current_price <= 0:
                    continue

                hit_tp = hit_sl = False
                if side == "buy":
                    hit_tp = take_profit > 0 and current_price >= take_profit
                    hit_sl = stop_loss > 0 and current_price <= stop_loss
                else:  # short
                    hit_tp = take_profit > 0 and current_price <= take_profit
                    hit_sl = stop_loss > 0 and current_price >= stop_loss

                if not hit_tp and not hit_sl:
                    continue  # Still open, no exit yet

                close_price = take_profit if hit_tp else stop_loss
                pattern = "simulated_tp" if hit_tp else "simulated_sl"
                logger.info("[Monitor][SIM] %s hit %s: price=%.2f close=%.2f",
                            ticker, "TP" if hit_tp else "SL", current_price, close_price)
            else:
                # Real Alpaca trade — check if position still exists
                if ticker in alpaca_positions:
                    continue  # Still open, bracket hasn't triggered

                # Position gone from Alpaca — find close fill price from order history
                close_price = None
                try:
                    from alpaca.trading.requests import GetOrdersRequest
                    from alpaca.trading.enums import QueryOrderStatus
                    client = await asyncio.to_thread(app_state.executor._get_alpaca_client)
                    req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=50, symbols=[ticker])
                    orders = await asyncio.to_thread(client.get_orders, req)
                    close_side = "sell" if side == "buy" else "buy"
                    for o in (orders or []):
                        o_side = str(o.side).replace("OrderSide.", "").lower()
                        if o_side == close_side and o.filled_avg_price:
                            close_price = float(o.filled_avg_price)
                            break
                except Exception as exc:
                    logger.warning("[Monitor] Could not fetch close orders for %s: %s", ticker, exc)

                if not close_price:
                    close_price = take_profit if take_profit else (entry_price * 1.02)
                pattern = "bracket_exit"

            # Record the close
            result = await app_state.executor.close_position_for_monitor(
                trade_id=trade_id,
                ticker=ticker,
                side=side,
                entry_price=entry_price,
                quantity=quantity,
                current_price=close_price,
                filled_at=filled_at,
            )

            # Generate lesson
            if app_state.mentor_agent:
                try:
                    wr_data = await store.compute_win_rate()
                    lesson = await app_state.mentor_agent.generate_lesson(
                        trade_id=trade_id,
                        ticker=ticker,
                        action=side,
                        entry_price=entry_price,
                        exit_price=close_price,
                        pnl_pct=(result.pnl_pct or 0.0) / 100,
                        pnl_dollars=result.pnl_dollars or 0.0,
                        outcome=result.outcome,
                        hold_minutes=result.hold_minutes or 0,
                        news_summary=f"{ticker} closed via {pattern}",
                        sentiment_score=0.0,
                        pattern_name=pattern,
                        review_note=None,
                        win_rate=wr_data.get("win_rate", 0.5),
                        consecutive_wins=wr_data.get("consecutive_wins", 0),
                    )
                    await store.save_lesson(lesson)
                    await ws_manager.broadcast("lesson", lesson.model_dump(mode="json"))

                    # Suggest a book after losses to address the knowledge gap
                    if result.outcome == "LOSS":
                        try:
                            suggestion = await suggest_book_for_loss(
                                trade_id=trade_id,
                                ticker=ticker,
                                outcome=result.outcome,
                                pnl_pct=(result.pnl_pct or 0.0) / 100,
                                what_happened=lesson.what_happened,
                                correction=lesson.correction,
                                knowledge_gap=getattr(lesson, "knowledge_gap", ""),
                            )
                            if suggestion:
                                await ws_manager.broadcast("book_suggestion", suggestion)
                        except Exception as book_exc:
                            logger.debug("[Monitor] Book suggestion failed for %s: %s", ticker, book_exc)
                except Exception as exc:
                    logger.warning("[Monitor] Lesson generation failed for %s: %s", ticker, exc)

            logger.info("[Monitor] Closed %s %s: %s @ $%.2f  P&L=$%.2f",
                        ticker, pattern, result.outcome, close_price, result.pnl_dollars or 0)

    except Exception as exc:
        logger.error("[Monitor] Position monitor error: %s", exc)


async def _scheduled_news_scan() -> None:
    """Background task: hourly news scan for all watched tickers."""
    if app_state.news_agent:
        logger.info("Running scheduled news scan for: %s", app_state.watch_list)
        try:
            results = await app_state.news_agent.scan_tickers(app_state.watch_list)
            for ticker, signal in results.items():
                await ws_manager.broadcast("news_update", {
                    "ticker": ticker,
                    "sentiment": signal.sentiment_score,
                    "urgency": signal.urgency,
                    "catalyst": signal.catalyst,
                    "headline": signal.headline,
                })
        except Exception as exc:
            logger.error("Scheduled news scan failed: %s", exc)


async def _weekly_analysis_job() -> None:
    """Sunday midnight: run weekly self-analysis."""
    logger.info("Running weekly pattern analysis...")
    try:
        result = await run_weekly_analysis()
        await ws_manager.broadcast("weekly_analysis", result)
        logger.info("Weekly analysis complete: grade=%s pnl=$%.2f",
                    result.get("grade", "?"), result.get("total_pnl", 0))
    except Exception as exc:
        logger.error("Weekly analysis failed: %s", exc)


# ══════════════════════════════════════════════
# FastAPI App
# ══════════════════════════════════════════════

app = FastAPI(
    title="TradeSage API",
    description="Multi-Agent AI Trading System",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════
# REST Endpoints
# ══════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok", "mode": settings.TRADING_MODE, "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/market-status")
async def get_market_status():
    """Returns whether the US stock market is currently open."""
    if not app_state.executor:
        return {"is_open": False, "next_open": None, "next_close": None}
    try:
        client = await asyncio.to_thread(app_state.executor._get_alpaca_client)
        clock = await asyncio.to_thread(client.get_clock)
        return {
            "is_open": bool(clock.is_open),
            "next_open": clock.next_open.isoformat() if clock.next_open else None,
            "next_close": clock.next_close.isoformat() if clock.next_close else None,
        }
    except Exception as exc:
        logger.warning("Market status fetch failed: %s", exc)
        return {"is_open": False, "next_open": None, "next_close": None}


@app.get("/api/portfolio")
async def get_portfolio():
    """Current portfolio state, P&L, positions."""
    store = get_store()
    try:
        alpaca_portfolio = await app_state.executor.get_portfolio() if app_state.executor else {}
    except Exception as exc:
        logger.warning("Portfolio fetch failed: %s", exc)
        alpaca_portfolio = {"error": str(exc)}

    db_summary = await store.get_portfolio_summary(settings.STARTING_CAPITAL)
    risk_summary = app_state.risk_agent.get_risk_summary(
        alpaca_portfolio.get("portfolio_value") or db_summary.get("portfolio_value", settings.STARTING_CAPITAL)
    ) if app_state.risk_agent else {}

    return {
        "alpaca": alpaca_portfolio,
        "db_summary": db_summary,
        "risk": risk_summary,
        "mode": settings.TRADING_MODE,
        "watch_list": app_state.watch_list,
    }


@app.post("/api/trades/reconcile")
async def reconcile_trades():
    """Sync open trade quantities/prices with actual Alpaca fill data."""
    if not app_state.executor:
        return {"updated": 0, "message": "Executor not available"}
    store = get_store()
    open_trades = await store.get_open_trades()
    alpaca_positions = await app_state.executor.get_all_positions()
    updated = 0
    for trade in open_trades:
        ticker = trade.get("ticker", "")
        pos = alpaca_positions.get(ticker)
        if pos and ticker:
            db_qty = float(trade.get("quantity") or 0)
            actual_qty = pos["qty"]
            actual_price = pos["avg_entry"]
            if abs(db_qty - actual_qty) > 0.01:  # meaningful difference
                await store.update_trade_fill(
                    trade_id=trade["trade_id"],
                    quantity=actual_qty,
                    entry_price=actual_price,
                )
                logger.info("[Reconcile] %s: qty %.2f→%.2f  price %.4f→%.4f",
                            ticker, db_qty, actual_qty, float(trade.get("entry_price") or 0), actual_price)
                updated += 1
    return {"updated": updated, "checked": len(open_trades)}


@app.get("/api/trades")
async def get_trades(limit: int = 50, offset: int = 0, ticker: Optional[str] = None):
    """Paginated trade history."""
    store = get_store()
    trades = await store.get_trades(limit=limit, offset=offset, ticker=ticker)
    return {"trades": trades, "count": len(trades), "limit": limit, "offset": offset}


@app.get("/api/news/{ticker}")
async def get_news(ticker: str):
    """Latest news + sentiment for a ticker."""
    store = get_store()
    news = await store.get_news_for_ticker(ticker, limit=20)

    # Also query graph
    graph_news = []
    if app_state.graph_reasoner:
        try:
            graph_news = app_state.graph_reasoner.q12_recent_news(ticker, hours=24)
        except Exception as exc:
            logger.warning("Graph news query failed: %s", exc)

    return {"ticker": ticker, "news": news, "graph_news": graph_news}


@app.get("/api/lessons")
async def get_lessons(limit: int = 20, offset: int = 0):
    """Mentor lessons feed."""
    store = get_store()
    lessons = await store.get_lessons(limit=limit, offset=offset)
    return {"lessons": lessons, "count": len(lessons)}


@app.get("/api/reviews")
async def get_reviews(limit: int = 20, offset: int = 0):
    """Mentor pre-trade ReviewNotes feed."""
    store = get_store()
    reviews = await store.get_review_notes(limit=limit, offset=offset)
    return {"reviews": reviews, "count": len(reviews)}


@app.get("/api/win-rate")
async def get_win_rate():
    """Rolling win rate data for chart."""
    store = get_store()
    current = await store.compute_win_rate()
    history = await store.get_win_rate_history(limit=100)
    return {"current": current, "history": history}


@app.get("/api/graph/subgraph/{ticker}")
async def get_subgraph(ticker: str):
    """D3-compatible subgraph for the GraphExplorer component."""
    if not app_state.graph_reasoner:
        return {"nodes": [], "links": []}
    return app_state.graph_reasoner.get_subgraph_for_explorer(ticker)


@app.get("/api/graph/stats")
async def get_graph_stats_endpoint():
    """Graph node + relationship counts."""
    if not app_state.neo4j_driver:
        return {"nodes": 0, "relationships": 0, "error": "Neo4j not connected"}
    try:
        return get_graph_stats(app_state.neo4j_driver)
    except Exception as exc:
        return {"nodes": 0, "relationships": 0, "error": str(exc)}


@app.get("/api/graph/win-rate-by-pattern")
async def get_win_rate_by_pattern():
    if not app_state.graph_reasoner:
        return {"patterns": []}
    return {"patterns": app_state.graph_reasoner.q7_win_rate_by_pattern()}


class TradeRequest(BaseModel):
    ticker: str
    market_type: str = "stock"
    action: str = "buy"


@app.post("/api/trade")
async def trigger_trade(req: TradeRequest, background_tasks: BackgroundTasks):
    """Manually trigger a trade signal through the full orchestrator pipeline."""
    if not app_state.orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialised")

    background_tasks.add_task(
        _run_trade_pipeline,
        req.ticker,
        req.market_type,
        req.action,
    )
    return {"status": "queued", "ticker": req.ticker, "action": req.action}


async def _run_trade_pipeline(ticker: str, market_type: str, action: str, signal_id: str = "") -> None:
    """Run trade pipeline in background and broadcast results."""
    try:
        result = await app_state.orchestrator.run(
            ticker=ticker,
            market_type=market_type,
            action=action,
            portfolio_value=app_state.portfolio_value,
            daily_pnl=app_state.daily_pnl,
            win_rate=app_state.win_rate,
            mode=settings.TRADING_MODE,
        )
        app_state.win_rate = result.get("win_rate", app_state.win_rate)
        trade_result = result.get("trade_result")
        outcome = None
        if trade_result:
            # TradeResult is a Pydantic model — use attribute access
            outcome = getattr(trade_result, "outcome", None)

        blocked = result.get("trade_blocked", False)
        reason = result.get("block_reason", "")

        # Persist result back into pending_signals so page refresh restores it
        if signal_id and signal_id in app_state.pending_signals:
            app_state.pending_signals[signal_id]["result"] = {
                "blocked": blocked,
                "reason": reason,
                "outcome": outcome,
            }

        # Auto-save model version every 5 completed trades
        if not blocked and outcome in ("WIN", "LOSS", "BREAKEVEN"):
            try:
                store = get_store()
                wr_data = await store.compute_win_rate()
                total = wr_data.get("total_trades", 0)
                if total > 0 and total % 5 == 0:
                    save_version(
                        model_name=settings.LLM_MODEL,
                        win_rate=wr_data.get("win_rate", 0.5),
                        total_trades=total,
                        total_pnl=wr_data.get("total_pnl_dollars", 0.0),
                        trigger="auto",
                        notes=f"Auto-snapshot at {total} trades",
                    )
            except Exception as exc:
                logger.warning("Model registry snapshot failed: %s", exc)

        await ws_manager.broadcast("pipeline_complete", {
            "ticker": ticker,
            "signal_id": signal_id,
            "blocked": blocked,
            "reason": reason,
            "outcome": outcome,
        })
    except Exception as exc:
        logger.error("Trade pipeline failed: %s", exc)
        await ws_manager.broadcast("pipeline_error", {"ticker": ticker, "error": str(exc)})


class ModeToggleRequest(BaseModel):
    confirm: str     # Must be "SWITCH_TO_LIVE" or "SWITCH_TO_PAPER"
    reason: str


@app.post("/api/toggle-mode")
async def toggle_mode(req: ModeToggleRequest):
    """Switch paper ↔ live. Requires confirmation body."""
    current = settings.TRADING_MODE

    if current == "paper":
        if req.confirm != "SWITCH_TO_LIVE":
            raise HTTPException(
                status_code=400,
                detail='To switch to live trading, send {"confirm": "SWITCH_TO_LIVE", "reason": "..."}'
            )
        if not req.reason or len(req.reason) < 10:
            raise HTTPException(status_code=400, detail="Please provide a meaningful reason (min 10 chars)")

        settings.switch_mode("live")
        if app_state.executor:
            app_state.executor.switch_mode("live")
        await ws_manager.broadcast("mode_changed", {"mode": "live", "reason": req.reason})
        return {"mode": "live", "message": "Switched to LIVE trading. Real money at risk!"}
    else:
        if req.confirm not in ("SWITCH_TO_PAPER", "SWITCH_TO_LIVE"):
            raise HTTPException(status_code=400, detail='Send {"confirm": "SWITCH_TO_PAPER", "reason": "..."}')
        settings.switch_mode("paper")
        if app_state.executor:
            app_state.executor.switch_mode("paper")
        await ws_manager.broadcast("mode_changed", {"mode": "paper", "reason": req.reason})
        return {"mode": "paper", "message": "Switched to PAPER trading mode."}


@app.post("/api/upload-book")
async def upload_book(file: UploadFile = File(...)):
    """Upload a PDF for the mentor to study."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    uploads_dir = Path(settings.UPLOADS_DIR)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / file.filename

    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    # Ingest in background
    chunks = 0
    if app_state.neo4j_driver:
        chunks = await asyncio.to_thread(ingest_pdf, app_state.neo4j_driver, str(dest))

    return {
        "status": "uploaded",
        "filename": file.filename,
        "chunks_ingested": chunks,
        "path": str(dest),
    }


class AddTickerRequest(BaseModel):
    ticker: str


@app.post("/api/add-ticker")
async def add_ticker(req: AddTickerRequest):
    """Add a ticker to the watch list."""
    ticker = req.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Invalid ticker")
    if ticker not in app_state.watch_list:
        app_state.watch_list.append(ticker)
        if app_state.orchestrator:
            app_state.orchestrator.update_watch_list(app_state.watch_list)
    return {"watch_list": app_state.watch_list}


@app.delete("/api/remove-ticker/{ticker}")
async def remove_ticker(ticker: str):
    ticker = ticker.upper()
    if ticker in app_state.watch_list:
        app_state.watch_list.remove(ticker)
    return {"watch_list": app_state.watch_list}


@app.get("/api/alpaca/orders")
async def get_alpaca_orders(limit: int = 20):
    """Recent Alpaca orders (filled, pending, cancelled)."""
    if not app_state.executor:
        return {"orders": []}
    orders = await app_state.executor.get_orders(limit=limit)
    return {"orders": orders}


@app.delete("/api/alpaca/orders/cancel-all")
async def cancel_all_pending_orders():
    """Cancel all open/pending Alpaca orders to free up buying power."""
    if not app_state.executor:
        return {"cancelled": 0, "error": "Executor not ready"}
    try:
        client = await asyncio.to_thread(app_state.executor._get_alpaca_client)
        await asyncio.to_thread(client.cancel_orders)
        logger.warning("All pending Alpaca orders cancelled by user")
        return {"cancelled": True, "message": "All pending orders cancelled — buying power will be restored shortly"}
    except Exception as exc:
        logger.error("Cancel all orders failed: %s", exc)
        return {"cancelled": False, "error": str(exc)}


@app.get("/api/prices")
async def get_prices():
    """Get current prices for all watched tickers."""
    if not app_state.market_data:
        return {"prices": {}}
    try:
        prices = await app_state.market_data.get_prices_batch(app_state.watch_list)
        return {"prices": prices}
    except Exception as exc:
        return {"prices": {}, "error": str(exc)}


@app.get("/api/risk")
async def get_risk():
    """Current risk metrics."""
    if not app_state.risk_agent:
        return {}
    return app_state.risk_agent.get_risk_summary(app_state.portfolio_value)


# ══════════════════════════════════════════════
# Pipeline Lab Endpoints
# ══════════════════════════════════════════════


@app.get("/api/pipeline/status")
async def pipeline_status():
    if not app_state.pipeline_orchestrator:
        return {"running": False, "error": "Pipeline not initialised"}
    return app_state.pipeline_orchestrator.get_status()


@app.post("/api/pipeline/trigger")
async def trigger_pipeline(background_tasks: BackgroundTasks):
    if not app_state.pipeline_orchestrator:
        raise HTTPException(status_code=503, detail="Pipeline orchestrator not ready")
    status = app_state.pipeline_orchestrator.get_status()
    if status.get("running"):
        return {"status": "running"}
    background_tasks.add_task(app_state.pipeline_orchestrator.run_full_pipeline)
    return {"status": "queued"}


@app.get("/api/pipeline/discovered")
async def pipeline_discovered(status: Optional[str] = None, limit: int = 50):
    dr = app_state.data_router
    if not dr:
        return {"stocks": []}
    return {"stocks": dr.get_discovered_stocks(status=status, limit=limit)}


@app.get("/api/pipeline/algorithms")
async def pipeline_algorithms(status: Optional[str] = None, limit: int = 100):
    dr = app_state.data_router
    if not dr:
        return {"algorithms": []}
    return {"algorithms": dr.get_algorithms(status=status, limit=limit)}


@app.get("/api/pipeline/events")
async def pipeline_events(limit: int = 50):
    dr = app_state.data_router
    if not dr:
        return {"events": []}
    return {"events": dr.get_pipeline_events(limit=limit)}


@app.post("/api/pipeline/retire/{algo_id}")
async def pipeline_retire(algo_id: str, reason: Optional[str] = None):
    dr = app_state.data_router
    if not dr:
        raise HTTPException(status_code=503, detail="Pipeline data router not ready")
    dr.retire_algorithm(algo_id, reason or "")
    await ws_manager.broadcast("algorithm_retired", {"algorithm_id": algo_id, "reason": reason})
    return {"status": "retired", "algorithm_id": algo_id, "reason": reason}


# ══════════════════════════════════════════════
# LLM Cost + Mode Endpoints
# ══════════════════════════════════════════════

@app.get("/api/cost/today")
async def get_cost_today():
    """LLM spend summary for today."""
    return cost_tracker.get_today_summary()


@app.get("/api/cost/history")
async def get_cost_history(days: int = 7):
    """LLM spend history for last N days."""
    return {"history": cost_tracker.get_history(days=days)}


class LLMModeRequest(BaseModel):
    mode: str   # "testing" | "live" | "free"


@app.post("/api/llm-mode")
async def set_llm_mode(req: LLMModeRequest):
    """Switch LLM mode instantly — no restart needed."""
    if req.mode not in ("testing", "live", "free"):
        raise HTTPException(status_code=400, detail="mode must be: testing | live | free")
    settings.switch_llm_mode(req.mode)
    summary = cost_tracker.get_today_summary()
    await ws_manager.broadcast("llm_mode_changed", {"mode": req.mode})
    return {"mode": settings.LLM_MODE, "cost_today": summary}


class BudgetRequest(BaseModel):
    budget_usd: float


@app.post("/api/budget")
async def set_budget(req: BudgetRequest):
    """Update daily LLM budget."""
    if req.budget_usd < 0.10:
        raise HTTPException(status_code=400, detail="Budget must be at least $0.10")
    settings.set_daily_budget(req.budget_usd)
    return {"budget_usd": settings.LLM_DAILY_BUDGET_USD, "message": f"Daily budget set to ${req.budget_usd:.2f}"}


# ══════════════════════════════════════════════
# Mentor Book Suggester + Weekly Reports
# ══════════════════════════════════════════════

@app.get("/api/mentor/reading-list")
async def get_mentor_reading_list():
    """Books the mentor is recommending based on knowledge gaps from losses."""
    return {"books": get_reading_list()}


@app.post("/api/mentor/book-learned/{book_title}")
async def mark_book_as_learned(book_title: str):
    """Mark a book as learned (ingested into graph). Removes from reading list."""
    success = mark_book_learned(book_title)
    return {"success": success, "book": book_title}


@app.get("/api/mentor/weekly-reports")
async def get_mentor_weekly_reports(limit: int = 4):
    """Last N weekly self-analysis reports."""
    return {"reports": get_weekly_reports(limit=limit)}


@app.post("/api/mentor/run-weekly-analysis")
async def trigger_weekly_analysis(background_tasks: BackgroundTasks):
    """Manually trigger the weekly self-analysis (normally runs Sunday midnight)."""
    background_tasks.add_task(_weekly_analysis_job)
    return {"status": "queued", "message": "Weekly analysis started in background"}


@app.get("/api/open-trades")
async def get_open_trades_live():
    """
    Return all open trades enriched with live P&L from current market prices.
    Polls every 5 seconds from the frontend for live position tracking.
    """
    store = get_store()
    open_trades = await store.get_open_trades()
    if not open_trades or not app_state.market_data:
        return {"trades": open_trades or []}

    # Fetch current prices in parallel
    tickers = list({t.get("ticker") for t in open_trades if t.get("ticker")})
    price_tasks = {ticker: app_state.market_data.get_price(ticker) for ticker in tickers}
    live_prices: dict[str, float] = {}
    for ticker, task in price_tasks.items():
        try:
            p = await task
            price = next((v for v in [p.get("last"), p.get("ask"), p.get("bid"), p.get("close")] if v and v > 0), 0.0)
            if price > 0:
                live_prices[ticker] = price
        except Exception:
            pass

    enriched = []
    for trade in open_trades:
        ticker = trade.get("ticker", "")
        entry = float(trade.get("entry_price") or 0)
        qty = float(trade.get("quantity") or 0)
        side = trade.get("side", "buy")
        current = live_prices.get(ticker, 0.0)

        if current > 0 and entry > 0 and qty > 0:
            if side == "buy":
                live_pnl = (current - entry) * qty
                live_pnl_pct = (current - entry) / entry * 100
            else:
                live_pnl = (entry - current) * qty
                live_pnl_pct = (entry - current) / entry * 100
        else:
            live_pnl = 0.0
            live_pnl_pct = 0.0

        enriched.append({
            **trade,
            "current_price": round(current, 4) if current else None,
            "live_pnl": round(live_pnl, 2),
            "live_pnl_pct": round(live_pnl_pct, 3),
        })

    return {"trades": enriched}


# ══════════════════════════════════════════════
# Signal Queue — Scan → Approve/Reject Flow
# ══════════════════════════════════════════════

@app.post("/api/scan")
async def scan_all_tickers():
    """
    Scan all watched tickers with news + price data.
    Ranks them by AI score and returns pending signals
    the user can BUY, SELL, or SKIP.
    """
    if not app_state.news_agent or not app_state.market_data:
        raise HTTPException(status_code=503, detail="Agents not ready")

    import math

    def _finite_or(value, default=0.0):
        """Return a finite float, otherwise a safe default."""
        try:
            num = float(value)
            return num if math.isfinite(num) else default
        except Exception:
            return default

    tickers = app_state.watch_list
    if not tickers:
        return {"signals": []}

    # Clear old signals from previous scan (keep only actioned ones for reference)
    app_state.pending_signals = {
        sid: s for sid, s in app_state.pending_signals.items()
        if s.get("status") in ("approved", "rejected")
    }

    # Build set of tickers that already have an open Alpaca position (avoid duplicates)
    existing_order_tickers: set[str] = set()
    if app_state.executor:
        try:
            positions = await app_state.executor.get_all_positions()
            existing_order_tickers = set(positions.keys())
        except Exception:
            pass

    # Check if market is currently open
    market_open = False
    if app_state.executor:
        try:
            client = await asyncio.to_thread(app_state.executor._get_alpaca_client)
            clock = await asyncio.to_thread(client.get_clock)
            market_open = bool(clock.is_open)
        except Exception:
            pass

    # Fetch news and prices in parallel
    news_results = await app_state.news_agent.scan_tickers(tickers)

    price_tasks = {t: app_state.market_data.get_price(t) for t in tickers}
    price_results: dict[str, dict] = {}
    for ticker, task in price_tasks.items():
        try:
            price_results[ticker] = await task
        except Exception:
            price_results[ticker] = {}

    # Fetch yfinance data in parallel: day change % + ATR(14)
    import yfinance as yf

    yf_data: dict[str, dict] = {}
    def _fetch_yf_batch():
        result = {}
        for t in tickers:
            try:
                ticker_obj = yf.Ticker(t)
                info = ticker_obj.fast_info
                prev = getattr(info, "previous_close", None) or getattr(info, "regularMarketPreviousClose", None)
                curr = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
                vol = getattr(info, "three_month_average_volume", None) or getattr(info, "regularMarketVolume", None)
                day_vol = getattr(info, "day_volume", None) or getattr(info, "regularMarketVolume", None)
                day_change_pct = (curr - prev) / prev if prev and curr and prev > 0 else None
                # ATR from 20-day history
                hist_df = ticker_obj.history(period="22d", interval="1d")
                atr = None
                if not hist_df.empty and len(hist_df) >= 14:
                    highs = hist_df["High"].values
                    lows = hist_df["Low"].values
                    closes = hist_df["Close"].values
                    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(hist_df))]
                    atr = round(sum(trs[-14:]) / 14, 4) if len(trs) >= 14 else None
                # Volume ratio: today vs 3-month avg — None if unavailable
                vol_ratio = round(day_vol / vol, 2) if vol and day_vol and vol > 0 else None
                if day_change_pct is not None and not math.isfinite(float(day_change_pct)):
                    day_change_pct = None
                if atr is not None and not math.isfinite(float(atr)):
                    atr = None
                if vol_ratio is not None and not math.isfinite(float(vol_ratio)):
                    vol_ratio = None
                result[t] = {
                    "day_change": day_change_pct,
                    "atr": atr,
                    "vol_ratio": vol_ratio,
                }
            except Exception as exc:
                logger.debug("[Scan] yfinance fetch failed for %s: %s", t, exc)
                result[t] = {"day_change": None, "atr": None, "vol_ratio": None}
        return result
    yf_data = await asyncio.to_thread(_fetch_yf_batch)

    # Load per-ticker win rates from trade history to inform scoring
    store = get_store()
    ticker_history: dict[str, dict] = {}
    for ticker in tickers:
        try:
            ticker_history[ticker] = await store.get_ticker_stats(ticker)
        except Exception:
            ticker_history[ticker] = {}

    def _compute_grade(score: float) -> str:
        if score >= 0.85: return "A+"
        if score >= 0.70: return "A"
        if score >= 0.55: return "B"
        if score >= 0.40: return "C"
        if score >= 0.25: return "D"
        return "F"

    # Score each ticker and build ranked signal list
    signals = []
    for ticker in tickers:
        news = news_results.get(ticker)
        prices = price_results.get(ticker, {})
        price = next(
            (v for v in [prices.get("last"), prices.get("ask"), prices.get("bid"), prices.get("close")] if v and v > 0),
            0.0
        )

        if not news or price <= 0:
            continue

        if ticker.upper() in existing_order_tickers:
            logger.debug("Skipping %s — already has open position", ticker)
            continue

        yd = yf_data.get(ticker, {})
        price_momentum = yd.get("day_change")  # None if fetch failed
        atr = yd.get("atr")
        vol_ratio = yd.get("vol_ratio")        # None if fetch failed

        sentiment = _finite_or(news.sentiment_score, 0.0)   # -1 to +1
        hist = ticker_history.get(ticker, {})
        hist_win_rate = _finite_or(hist.get("win_rate", 0.5), 0.5)
        hist_trades = int(_finite_or(hist.get("total_trades", 0), 0))

        # When market is closed yfinance may not return intraday data.
        # Use neutral values but flag the factors as "no real data" in breakdown.
        momentum_data_ok = price_momentum is not None
        volume_data_ok = vol_ratio is not None
        price_momentum = _finite_or(price_momentum, 0.0) if momentum_data_ok else 0.0
        vol_ratio = _finite_or(vol_ratio, 1.0) if volume_data_ok else 1.0
        if not math.isfinite(price_momentum):
            momentum_data_ok = False
            price_momentum = 0.0
        if not math.isfinite(vol_ratio):
            volume_data_ok = False
            vol_ratio = 1.0
        atr = _finite_or(atr, 0.0) if atr is not None else None

        # ── 5-Factor Scoring ─────────────────────────────────────────────
        # Factor 1: News sentiment strength (0-1, weight 25%)
        # Weak signal (<0.1) → 0.15-0.30, Strong (>0.5) → 0.75+
        f1_news = min(1.0, 0.15 + abs(sentiment) * 1.7)

        # Factor 2: Price momentum alignment (0-1, weight 20%)
        momentum_aligned = (sentiment > 0 and price_momentum > 0) or (sentiment < 0 and price_momentum < 0)
        if not momentum_data_ok:
            f2_momentum = 0.40  # neutral — no real data, don't reward or penalise
            momentum_aligned = None  # unknown
        elif momentum_aligned:
            f2_momentum = min(1.0, 0.40 + abs(price_momentum) * 25)
        else:
            f2_momentum = max(0.0, 0.40 - abs(price_momentum) * 25)

        # Factor 3: Historical win rate on this ticker (0-1, weight 20%)
        # No history → neutral 0.50; 10+ trades → fully weighted
        confidence = min(hist_trades / 10, 1.0)
        f3_history = 0.50 + (hist_win_rate - 0.50) * confidence

        # Factor 4: Urgency / catalyst quality (0-1, weight 20%)
        urgency_map = {"immediate": 0.90, "wait": 0.60, "background": 0.35, "override_cancel": 0.0}
        catalyst_bonus = {"earnings": 0.15, "macro": 0.10, "analyst": 0.08, "insider": 0.15, "product": 0.10}.get(news.catalyst or "", 0.05)
        f4_urgency = min(1.0, urgency_map.get(news.urgency, 0.35) + catalyst_bonus)

        # Factor 5: Volume surge (0-1, weight 15%)
        # vol_ratio=1.0 (avg) → 0.50; 2.0x → 0.75; 3x+ → 1.0; below avg → < 0.50
        if not volume_data_ok:
            f5_volume = 0.50  # neutral — no real data
        elif vol_ratio >= 1.0:
            f5_volume = min(1.0, 0.50 + (vol_ratio - 1.0) * 0.25)
        else:
            f5_volume = max(0.10, vol_ratio * 0.50)

        # Weighted composite score
        score = (
            f1_news     * 0.25 +
            f2_momentum * 0.20 +
            f3_history  * 0.20 +
            f4_urgency  * 0.20 +
            f5_volume   * 0.15
        )
        score = round(min(1.0, max(0.0, score)), 3)

        # ── Determine AI action ───────────────────────────────────────────
        if sentiment > 0.05:
            action = "buy"
        elif sentiment < -0.05:
            action = "sell"
        elif price_momentum > 0.003:
            action = "buy"
            sentiment = price_momentum
        elif price_momentum < -0.003:
            action = "sell"
            sentiment = price_momentum
        else:
            action = "hold"

        # Lesson-based flip: only flip BUY→SELL if we hold the position already
        # (never flip to SELL on a ticker we don't hold — avoids naked shorts)
        if hist_trades >= 3:
            if hist.get("last_action") == action and hist.get("last_outcome") == "LOSS":
                if action == "buy":
                    action = "sell"
                else:
                    action = "buy"  # flip sell→buy is always safe

        if action == "hold" and news.breaking_override:
            action = "buy"
        elif action == "hold":
            continue  # skip neutral

        # ── Block naked shorts: only allow SELL if we already hold the position ──
        # Prevents the system from opening short positions accidentally
        if action == "sell" and ticker.upper() not in existing_order_tickers:
            logger.info("[Scan] Skipping SELL %s — no long position to sell (avoid naked short)", ticker)
            continue

        grade = _compute_grade(score)
        abs_sent = abs(sentiment)

        # ── Risk preview with ATR-based TP/SL ────────────────────────────
        risk_preview = {}
        if app_state.risk_agent and price > 0:
            passed, reason, rp = app_state.risk_agent.evaluate(
                portfolio_value=app_state.portfolio_value,
                entry_price=price,
                action=action,
                win_rate=app_state.win_rate,
                sentiment_confidence=abs_sent,
                daily_pnl=app_state.daily_pnl,
                atr=atr,
            )
            if rp:
                risk_preview = {
                    "position_size_usd": round(rp.position_size, 2),
                    "qty": round(rp.position_size / price, 4) if price else 0,
                    "stop_loss": round(rp.stop_loss, 4),
                    "take_profit": round(rp.take_profit, 4),
                    "risk_ok": passed,
                    "risk_note": reason,
                    "atr": atr,
                }

        # ── AI Decision — 2-3 sentence explanation ───────────────────────
        direction_word = "bullish" if sentiment > 0 else "bearish"
        action_word = "BUY" if action == "buy" else "SELL"
        sent_strength = "strong" if abs(sentiment) > 0.5 else "moderate" if abs(sentiment) > 0.2 else "weak"

        # Sentence 1: What the data shows
        sent1_parts = [
            f"News sentiment is {sent_strength}ly {direction_word} ({sentiment*100:+.0f}%)"
            f" on a {news.catalyst or 'general'} catalyst"
        ]
        if momentum_data_ok and abs(price_momentum) > 0.002:
            align_txt = "confirming the signal" if momentum_aligned else "warning of divergence"
            sent1_parts.append(f"with price {price_momentum*100:+.1f}% today {align_txt}")
        if volume_data_ok and vol_ratio > 1.3:
            sent1_parts.append(f"on {vol_ratio:.1f}× above-average volume")
        sentence1 = ", ".join(sent1_parts) + "."

        # Sentence 2: Why this leads to the action
        if news.urgency == "immediate":
            sentence2 = f"The breaking nature of this catalyst demands immediate action — waiting risks missing the move."
        elif momentum_aligned is False and abs(price_momentum) > 0.003:
            sentence2 = f"Despite the price fighting the signal, the news catalyst is strong enough to override short-term momentum."
        elif hist_trades >= 3 and hist_win_rate >= 0.6:
            sentence2 = f"Historical data shows {int(hist_win_rate*100)}% win rate on {hist_trades} prior trades for {ticker}, increasing conviction."
        elif hist_trades >= 3 and hist_win_rate < 0.4:
            sentence2 = f"Caution: {ticker} has only {int(hist_win_rate*100)}% historical win rate on {hist_trades} trades — size is reduced accordingly."
        elif score >= 0.70:
            sentence2 = f"All five scoring factors align positively, producing a high-confidence grade {grade} signal."
        else:
            sentence2 = f"The {news.catalyst or 'catalyst'} signal is actionable but watch for confirmation before adding to size."

        # Sentence 3: Risk/reward context
        if risk_preview.get("stop_loss") and risk_preview.get("take_profit"):
            sl = risk_preview["stop_loss"]
            tp = risk_preview["take_profit"]
            rr = abs(tp - price) / max(abs(sl - price), 0.01)
            sentence3 = f"Risk/reward is {rr:.1f}:1 with stop at ${sl:.2f} and target at ${tp:.2f}."
        else:
            sentence3 = f"Position sized at {grade} grade confidence with full mentor gate review before execution."

        ai_reasoning = f"{sentence1} {sentence2} {sentence3}"

        # ── Score breakdown for full transparency ─────────────────────────
        mom_label = (f"No data (market closed)" if not momentum_data_ok
                     else f"{'✓ Aligned' if momentum_aligned else '✗ Fighting'} {price_momentum*100:+.1f}%")
        vol_label = (f"No data (market closed)" if not volume_data_ok
                     else f"Vol {vol_ratio:.1f}x avg")
        score_breakdown = {
            "f1_news":     {"score": round(f1_news, 2),     "weight": "25%", "label": f"Sentiment {sentiment*100:+.0f}%", "real_data": True},
            "f2_momentum": {"score": round(f2_momentum, 2), "weight": "20%", "label": mom_label,                          "real_data": momentum_data_ok},
            "f3_history":  {"score": round(f3_history, 2),  "weight": "20%", "label": f"{int(hist_win_rate*100)}% WR ({hist_trades} trades)", "real_data": hist_trades > 0},
            "f4_urgency":  {"score": round(f4_urgency, 2),  "weight": "20%", "label": f"{news.urgency} / {news.catalyst or 'general'}",       "real_data": True},
            "f5_volume":   {"score": round(f5_volume, 2),   "weight": "15%", "label": vol_label,                          "real_data": volume_data_ok},
        }

        signal_id = str(uuid.uuid4())
        signal_data = {
            "signal_id": signal_id,
            "ticker": ticker,
            "action": action,
            "price": round(_finite_or(price, 0.0), 4),
            "sentiment_score": round(_finite_or(sentiment, 0.0), 3),
            "urgency": news.urgency,
            "catalyst": news.catalyst,
            "headline": news.headline,
            "ai_reasoning": ai_reasoning,
            "score": round(_finite_or(score, 0.0), 3),
            "grade": grade,
            "breaking": news.breaking_override,
            "risk": risk_preview,
            "created_at": datetime.utcnow().isoformat(),
            "status": "pending",
            # 5-factor breakdown for UI
            "factors": {
                "news_sentiment": round(_finite_or(f1_news, 0.0), 2),
                "price_momentum": round(_finite_or(f2_momentum, 0.0), 2),
                "history": round(_finite_or(f3_history, 0.0), 2),
                "urgency": round(_finite_or(f4_urgency, 0.0), 2),
                "volume": round(_finite_or(f5_volume, 0.0), 2),
            },
            "score_breakdown": score_breakdown,
            "momentum_aligned": momentum_aligned,
            "atr": atr,
            "vol_ratio": round(_finite_or(vol_ratio, 0.0), 4),
            "price_momentum_pct": round(_finite_or(price_momentum * 100, 0.0), 2),
        }

        app_state.pending_signals[signal_id] = signal_data
        signals.append(signal_data)

    # Sort by score descending (best picks first)
    signals.sort(key=lambda s: s["score"], reverse=True)

    # Broadcast to WebSocket so dashboard updates instantly
    await ws_manager.broadcast("signals_ready", {"count": len(signals), "signals": signals})

    logger.info("Scan complete: %d actionable signals from %d tickers (market_open=%s)", len(signals), len(tickers), market_open)

    # Return ALL signals (new + previously actioned) so UI state is complete
    all_signals = list(app_state.pending_signals.values())
    all_signals.sort(key=lambda s: s["score"], reverse=True)
    return {"signals": all_signals, "scanned": len(tickers), "market_open": market_open, "skipped_tickers": sorted(existing_order_tickers)}


@app.get("/api/signals")
async def get_pending_signals(all: bool = False):
    """
    Return signals. By default returns all signals (pending + approved + rejected)
    so the UI can restore state after a page refresh.
    Pass ?all=false to get only pending signals.
    """
    signals = list(app_state.pending_signals.values())
    if not all:
        # Still return everything — frontend filters by status
        pass
    signals.sort(key=lambda s: s["score"], reverse=True)
    return {"signals": signals}


class ApproveRequest(BaseModel):
    action: str = ""   # optional override: "buy" or "sell" (uses signal default if blank)


@app.post("/api/signals/{signal_id}/approve")
async def approve_signal(signal_id: str, req: ApproveRequest, background_tasks: BackgroundTasks):
    """
    User approves a pending signal.
    Runs the full 3-layer gate (news → risk → mentor) and executes if approved.
    """
    signal = app_state.pending_signals.get(signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found or already actioned")
    if signal.get("status") != "pending":
        raise HTTPException(status_code=400, detail=f"Signal already {signal['status']}")
    if not app_state.orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not ready")

    action = req.action.lower() if req.action else signal["action"]
    if action not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="action must be 'buy' or 'sell'")

    # Mark as approved (prevents double-execution)
    app_state.pending_signals[signal_id]["status"] = "approved"
    ticker = signal["ticker"]

    # Run in background so HTTP returns immediately
    background_tasks.add_task(
        _run_trade_pipeline,
        ticker,
        "stock",
        action,
        signal_id,
    )

    await ws_manager.broadcast("signal_approved", {
        "signal_id": signal_id,
        "ticker": ticker,
        "action": action,
    })

    logger.info("Signal APPROVED by user: %s %s (id=%s)", action.upper(), ticker, signal_id)
    return {
        "status": "approved",
        "ticker": ticker,
        "action": action,
        "message": f"Running full 3-layer gate for {action.upper()} {ticker}. Check the live feed.",
    }


@app.post("/api/signals/{signal_id}/reject")
async def reject_signal(signal_id: str):
    """User skips/rejects a pending signal."""
    signal = app_state.pending_signals.get(signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    app_state.pending_signals[signal_id]["status"] = "rejected"
    ticker = signal["ticker"]

    await ws_manager.broadcast("signal_rejected", {"signal_id": signal_id, "ticker": ticker})
    logger.info("Signal REJECTED by user: %s (id=%s)", ticker, signal_id)
    return {"status": "rejected", "ticker": ticker}


# ══════════════════════════════════════════════
# Model Registry — versioning + rollback
# ══════════════════════════════════════════════

@app.get("/api/models")
async def get_model_versions():
    """List all saved model performance snapshots, newest first."""
    return {
        "best": load_best(),
        "versions": list_versions(limit=30),
        "current_model": settings.LLM_MODEL,
    }


class SaveModelRequest(BaseModel):
    notes: str = ""


@app.post("/api/models/save")
async def save_model_snapshot(req: SaveModelRequest):
    """Manually save the current model performance as a named version."""
    store = get_store()
    wr_data = await store.compute_win_rate()
    meta = save_version(
        model_name=settings.LLM_MODEL,
        win_rate=wr_data.get("win_rate", 0.5),
        total_trades=wr_data.get("total_trades", 0),
        total_pnl=wr_data.get("total_pnl_dollars", 0.0),
        trigger="manual",
        notes=req.notes or f"Manual save — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
    )
    return {"status": "saved", "version": meta}


class RollbackRequest(BaseModel):
    version_id: str
    confirm: str   # must be "ROLLBACK"


@app.post("/api/models/rollback")
async def rollback_model(req: RollbackRequest):
    """
    Roll back to a previous model snapshot.
    Requires confirm="ROLLBACK" in the body.
    """
    if req.confirm != "ROLLBACK":
        raise HTTPException(status_code=400, detail='Send {"version_id": "...", "confirm": "ROLLBACK"}')
    try:
        meta = rollback_to(req.version_id)
        await ws_manager.broadcast("model_rollback", {
            "version_id": req.version_id,
            "win_rate": meta["win_rate"],
            "model_name": meta["model_name"],
        })
        return {"status": "rolled_back", "version": meta}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ══════════════════════════════════════════════
# WebSocket
# ══════════════════════════════════════════════

@app.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    """
    Live WebSocket feed.
    Emits 3 event types:
      - "review_note"  — fires BEFORE trade (mentor decision)
      - "trade_fill"   — fires on execution
      - "lesson"       — fires after trade closes
    Also: "news_update", "mode_changed", "pipeline_complete", "pipeline_error"
    """
    await ws_manager.connect(ws)
    try:
        # Send initial state
        await ws.send_text(json.dumps({
            "type": "connected",
            "data": {
                "mode": settings.TRADING_MODE,
                "portfolio_value": app_state.portfolio_value,
                "watch_list": app_state.watch_list,
            },
            "timestamp": datetime.utcnow().isoformat(),
        }))

        while True:
            # Keep connection alive — wait for client messages (heartbeat)
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                if msg == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send heartbeat
                await ws.send_text(json.dumps({"type": "heartbeat", "timestamp": datetime.utcnow().isoformat()}))
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
        logger.info("WebSocket disconnected")
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
        ws_manager.disconnect(ws)


# ══════════════════════════════════════════════
# Stub classes for degraded mode (no Neo4j)
# ══════════════════════════════════════════════

class _StubReasoner:
    """Stub GraphReasoner when Neo4j is unavailable."""
    def q1_news_to_trade_chain(self, *a, **k): return []
    def q2_macro_ripple(self, *a, **k): return []
    def q3_sector_contagion(self, *a, **k): return []
    def q4_mentor_learning_path(self, *a, **k): return []
    def q5_news_divergence(self, *a, **k): return []
    def q6_pretrade_subgraph(self, *a, **k): return []
    def q7_win_rate_by_pattern(self, *a, **k): return []
    def q8_similar_past_trades(self, *a, **k): return []
    def q9_principles_for_pattern(self, *a, **k): return []
    def q10_mentor_mastered(self, *a, **k): return []
    def q11_ticker_win_rate(self, *a, **k): return {"win_rate": 0.5, "total": 0}
    def q12_recent_news(self, *a, **k): return []
    def detect_pattern(self, *a, **k): return "Trend Continuation"
    def get_stats(self): return {"nodes": 0, "relationships": 0}
    def get_subgraph_for_explorer(self, *a, **k): return {"nodes": [], "links": []}


class _StubUpdater:
    """Stub GraphUpdater when Neo4j is unavailable."""
    def upsert_trade(self, *a, **k): pass
    def upsert_lesson(self, *a, **k): pass
    def upsert_news_event(self, *a, **k): pass
    def link_news_to_trade(self, *a, **k): pass
    def link_news_to_price_movement(self, *a, **k): pass
    def update_mentor_stats(self, *a, **k): pass
    def increment_principle_mastery(self, *a, **k): pass
    def strengthen_pattern(self, *a, **k): pass
    def wire_post_trade(self, *a, **k): pass


# ══════════════════════════════════════════════
# AI Chat Bot — uses live trading data as context
# ══════════════════════════════════════════════

class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@app.post("/api/chat")
async def ai_chat(req: ChatRequest):
    """
    AI trading assistant with full context of the user's portfolio,
    trades, win rate, open positions, and lessons.
    """
    store = get_store()

    # Gather live context
    try:
        alpaca_data = await app_state.executor.get_portfolio() if app_state.executor else {}
    except Exception:
        alpaca_data = {}

    try:
        recent_trades = await store.get_trades(limit=20)
    except Exception:
        recent_trades = []

    try:
        wr_data = await store.compute_win_rate()
    except Exception:
        wr_data = {}

    try:
        lessons = await store.get_lessons(limit=10)
    except Exception:
        lessons = []

    try:
        reviews = await store.get_review_notes(limit=5)
    except Exception:
        reviews = []

    equity = alpaca_data.get("equity", 0)
    buying_power = alpaca_data.get("buying_power", 0)
    cash = alpaca_data.get("cash", 0)
    day_pnl = alpaca_data.get("day_pnl", 0)
    positions = alpaca_data.get("positions", [])
    # compute_win_rate() returns a flat dict directly (not nested under "current")
    win_rate = wr_data.get("win_rate", 0)
    total_trades = wr_data.get("total_trades", 0)
    total_pnl = wr_data.get("total_pnl_dollars", 0)
    wins = wr_data.get("wins", 0)
    losses = wr_data.get("losses", 0)

    trades_summary = "\n".join(
        f"  - {t.get('ticker','?')} {t.get('side','?').upper()} "
        f"entry=${t.get('entry_price') or 0:.2f} "
        f"exit=${t.get('exit_price') or 0:.2f} "
        f"pnl=${t.get('pnl_dollars') or 0:.2f} ({t.get('pnl_pct') or 0:.1f}%) "
        f"[{t.get('outcome','?')}]"
        for t in (recent_trades[:10] if isinstance(recent_trades, list) else [])
    ) or "  No trades yet."

    positions_summary = "\n".join(
        f"  - {p.get('ticker','?')} qty={p.get('qty',0):.0f} "
        f"avg_entry=${p.get('avg_entry',0):.2f} "
        f"market_value=${p.get('market_value',0):.2f} "
        f"unrealized_pnl=${p.get('unrealized_pnl',0):.2f}"
        for p in positions
    ) or "  No open positions."

    lessons_summary = "\n".join(
        f"  - [{l.get('ticker','?')}] {l.get('what_happened','')[:100]}"
        for l in (lessons[:5] if isinstance(lessons, list) else [])
    ) or "  No lessons yet."

    system_prompt = f"""You are TradeSage AI — an expert trading mentor and analyst embedded inside the TradeSage paper trading platform.

You have LIVE access to the user's full trading context:

=== PORTFOLIO (Alpaca Paper Account) ===
Equity: ${equity:,.2f}
Buying Power: ${buying_power:,.2f}
Cash: ${cash:,.2f}
Today's P&L: ${day_pnl:+.2f}
Trading Mode: {settings.TRADING_MODE.upper()}

=== PERFORMANCE STATS ===
Win Rate: {win_rate*100:.1f}% ({wins} wins / {losses} losses out of {total_trades} trades)
Total Trades: {total_trades}
Total Realised P&L: ${total_pnl:+.2f}
Market Status: {"OPEN" if settings.TRADING_MODE == "live" else "PAPER mode — weekend orders simulate fill immediately"}

=== OPEN POSITIONS ===
{positions_summary}

=== RECENT TRADES (last 10) ===
{trades_summary}

=== LESSONS LEARNED ===
{lessons_summary}

=== WATCHLIST ===
{', '.join(app_state.watch_list) if app_state.watch_list else 'None'}

IMPORTANT RULES:
- Only reference trades, tickers, and numbers that appear in the data above. NEVER invent trade details.
- If a specific trade isn't shown in "RECENT TRADES", say you don't have that detail rather than guessing.
- Be concise and specific — always cite the actual numbers from the data.
- For market hours questions: paper trades submitted when market is closed will simulate fill immediately in TradeSage (no real Alpaca order queued)."""

    from backend.llm.router import call_llm

    # Build the user prompt from the conversation history
    history_text = "\n".join(
        f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
        for m in req.messages[:-1]
    )
    last_message = req.messages[-1].content if req.messages else ""
    prompt = f"{history_text}\nUser: {last_message}" if history_text else last_message

    reply = await call_llm("chat", prompt, system_prompt, max_tokens=1024)
    if not reply or reply.strip() in ("{}", ""):
        reply = "I'm unable to respond right now — the LLM budget may be exhausted or Anthropic API credits are low. Switch LLM mode to 'free' (Ollama) in the Cost Monitor to continue."
    return {"reply": reply}


# ══════════════════════════════════════════════
# Entry Point
# ══════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )
