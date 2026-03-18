# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend
```bash
# Start backend (from tradesage/ root)
source .venv/bin/activate
python -m backend.main
# Runs on http://localhost:8000

# Kill and restart
kill $(ps aux | grep "python -m backend" | grep -v grep | awk '{print $2}') 2>/dev/null
python -m backend.main &> /tmp/tradesage_backend.log &
```

### Frontend
```bash
cd frontend
npm run dev -- --host 0.0.0.0   # Dev server → http://localhost:3000
npm run build                    # Production build
npm run lint                     # ESLint + TypeScript check
```

### Verify both are up
```bash
curl http://localhost:8000/health
curl -o /dev/null -w "%{http_code}" http://localhost:3000
```

### Inspect SQLite DB
```bash
source .venv/bin/activate
python3 -c "
import asyncio
from backend.db.sqlite_store import init_db, TradeStore
async def check():
    await init_db()
    store = TradeStore()
    trades = await store.get_trades(limit=5)
    wr = await store.compute_win_rate()
    print('Trades:', len(trades), '| Win rate:', wr)
asyncio.run(check())
"
```

## Required Environment Variables (`.env`)
```
ANTHROPIC_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_LIVE_URL=https://api.alpaca.markets
TAVILY_API_KEY=
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=tradesage
TRADING_MODE=paper
STARTING_CAPITAL=100000
```
Config crashes at startup with clear errors if required keys are missing (`backend/config.py`).

## Architecture

### Three-Layer Trade Gate (LangGraph StateGraph)
Every trade signal runs through three sequential gates before execution:

```
Signal → [1. News Gate] → [2. Risk Gate] → [3. Mentor Gate] → Execute → PostTrade
```

1. **NewsAgent** (`agents/news_agent.py`) — Tavily parallel search (5 queries/ticker) + Claude sentiment synthesis → `NewsSignal`. Results cached 30 min. Blocks on `override_cancel` urgency.
2. **RiskAgent** (`agents/risk_agent.py`) — Kelly criterion position sizing, 10% max single position, 15% drawdown halt, 5% daily loss limit → `RiskParams`.
3. **MentorAgent** (`agents/mentor_agent.py`) — Neo4j knowledge graph traversal + Claude review against trading principles → `ReviewNote` (APPROVED/BLOCKED/DELAYED). `confidence_score` is clamped to `[0.0, 1.0]` — never pass raw LLM output directly.
4. **TradeExecutor** (`agents/trade_executor.py`) — Alpaca (stocks, paper/live) or CCXT (crypto). In **paper mode**, if market is closed, simulates fill immediately (no ghost orders). Always check `_is_market_open()` before submitting real Alpaca orders.
5. **PostTrade** — Generates lesson, wires Neo4j graph, saves to SQLite.

The orchestrator (`agents/orchestrator.py`) wires these as a `StateGraph` and broadcasts WebSocket events at each stage.

### Signal Queue Flow (Scan → Approve)
Distinct from the manual trade trigger — this is the main user workflow:

1. `POST /api/scan` — fetches news + prices for all 20 watched tickers in parallel, scores them, stores in `app_state.pending_signals` (in-memory dict). Skips tickers that already have a pending Alpaca order.
2. `GET /api/signals` — returns all signals (pending + approved + rejected) so UI restores state on refresh.
3. `POST /api/signals/{signal_id}/approve` — marks signal approved, runs `_run_trade_pipeline()` in background. Marks signal as `"approved"` immediately to prevent double-execution.
4. `_run_trade_pipeline()` — calls `orchestrator.run()`, broadcasts `pipeline_complete` WebSocket event with outcome.

`pending_signals` is **in-memory only** — cleared on backend restart. Signal results are persisted back into the dict so refresh restores them.

### Persistence
- **SQLite** (`tradesage/tradesage.db`) — Trades, Lessons, ReviewNotes, WinRateSnapshots, NewsEvents. Async via `aiosqlite` + `SQLAlchemy`. `compute_win_rate()` saves a snapshot **only when total_trades changes** (not on every call).
- **Neo4j** (Docker) — Knowledge graph for mentor reasoning. Auto-started at backend launch. 13 node types (Ticker, Principle, Pattern, Lesson, Trader, etc.), 25+ relationship types.
- **Model Registry** (`tradesage_models/`) — JSON snapshots of win-rate/PnL per version. Auto-saved every 5 completed trades. `best.json` updated when win rate improves.

### WebSocket Events
`/ws/live` broadcasts:
- `signals_ready` — after scan completes
- `signal_approved` — when user approves
- `pipeline_complete` — `{ticker, signal_id, blocked, reason, outcome}`
- `trade_fill` — `TradeResult` after order executes (outcome=OPEN at this point)
- `trade_closed` — `TradeResult` with final WIN/LOSS/BREAKEVEN + exit price
- `lesson` — after post-trade lesson generation
- `review_note` — pre-trade mentor review
- `news_update` — from hourly scheduled scan

Frontend SignalQueue handles all these to update trade cards in real time.

### AI Chat Bot (`/api/chat`)
`POST /api/chat` — fetches live Alpaca portfolio + last 20 trades + win rate + lessons from SQLite, builds a system prompt with all context, calls Claude. `compute_win_rate()` returns a **flat dict** `{win_rate, total_trades, wins, losses, ...}` — not nested under `"current"`.

### Frontend Data Flow
- `Dashboard.tsx` — polls 7 endpoints every 30s via `loadAll()`. Primary data source is Alpaca (`portfolio.alpaca`), falls back to `portfolio.db_summary`.
- `SignalQueue.tsx` — handles WebSocket events to update signal cards. On mount, fetches `GET /api/signals` to restore full state. Uses `const API = 'http://localhost:8000'` directly (not proxied) for WS.
- Vite proxy: `/api` → `http://localhost:8000`, `/ws` → `ws://localhost:8000`.

### Key Config Defaults (`backend/config.py`)
- `MAX_POSITION_PCT = 0.10` (10% max per position)
- `RISK_PER_TRADE = 0.02` (2% stop-loss)
- `REWARD_RISK_RATIO = 2.0`
- `NEWS_SCAN_INTERVAL_MINUTES = 60`
- `DEFAULT_TICKERS` — 20 tickers: AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, SPY, QQQ, IWM, JPM, BAC, XOM, AMD, INTC, SMCI, LLY, MRNA, GLD, TLT

### Known Gotchas
- **Alpaca DAY orders on weekends** — paper mode now simulates fill immediately when market is closed instead of submitting to Alpaca. Check `_is_market_open()` before any real order submission.
- **`confidence_score` from LLM can be negative** — always clamp: `max(0.0, min(1.0, value))` before passing to `ReviewNote`.
- **`compute_win_rate()` returns flat dict** — callers that expect `{"current": {...}}` nesting must read directly: `wr_data.get("win_rate")`, not `wr_data.get("current", {}).get("win_rate")`.
- **`pending_signals` is in-memory** — cleared on restart. Don't rely on it for durable state.
- **Win-rate snapshot flood** — snapshots only save when `total_trades` changes; do not call `compute_win_rate()` in tight loops.
