# TradeSage — Claude Code Session Context

## Project Overview

TradeSage is a multi-agent AI trading system at `/home/manne/Projects/Trading/tradesage/`.

**Status:** Complete initial build. All files created. Ready for API keys and testing.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Orchestration | LangGraph StateGraph |
| LLM | Claude claude-sonnet-4-20250514 via LangChain |
| Knowledge Graph | Neo4j 5 Community (Docker) |
| Statistics | scipy.stats (t-distribution CI) |
| Backend | FastAPI + WebSocket + APScheduler |
| Database | SQLite via SQLAlchemy async |
| Market Data | Alpaca (stocks) + CCXT (crypto) + yfinance (fallback) |
| News | NewsAPI + Benzinga + Tavily |
| Frontend | React + TypeScript + Vite + Tailwind + Recharts + D3 |
| Trade Execution | Alpaca paper/live + CCXT |

## Key Files

```
tradesage/
├── .env.example                         — Copy to .env and fill in keys
├── requirements.txt                     — Python dependencies
├── backend/
│   ├── config.py                        — Settings + startup validation (crashes if keys missing)
│   ├── main.py                          — FastAPI app + startup sequence + WebSocket
│   ├── agents/
│   │   ├── orchestrator.py              — LangGraph StateGraph (3-layer gate system)
│   │   ├── news_agent.py                — NewsAPI + Benzinga + Tavily + Claude sentiment
│   │   ├── risk_agent.py                — Kelly criterion + circuit breakers
│   │   ├── trade_executor.py            — Alpaca + CCXT execution
│   │   └── mentor_agent.py              — Knowledge graph mentor + lesson generation
│   ├── knowledge/
│   │   ├── graph_schema.py              — Neo4j constraints + vector indexes + Docker auto-start
│   │   ├── ingest.py                    — Seeds graph: 18 companies, 13 sectors, 17 principles, etc.
│   │   ├── graph_reasoner.py            — 13 parameterized Cypher query functions
│   │   ├── graph_updater.py             — Post-trade MERGE wiring (idempotent)
│   │   └── web_scraper.py               — Tavily web research → WebArticle nodes
│   ├── analytics/stats.py               — scipy CI + $100 projector + Kelly + Sharpe
│   ├── db/sqlite_store.py               — Async SQLAlchemy (trades, lessons, reviews, news)
│   ├── data/market_data.py              — Unified MarketDataFeed (Alpaca + CCXT + yfinance)
│   └── models/                          — Pydantic: Trade, Signal, Lesson, ReviewNote, ProbabilityScore
└── frontend/src/
    ├── App.tsx                          — Router + sidebar (4 pages)
    ├── pages/
    │   ├── Dashboard.tsx                — Live P&L + trade trigger + mentor feed
    │   ├── Analytics.tsx                — Win rate + $100 projector + CI chart
    │   ├── MentorSchool.tsx             — Knowledge graph explorer + PDF upload + lessons
    │   └── NewsRoom.tsx                 — News scan + sentiment overview
    ├── components/
    │   ├── TradeDetailCard.tsx          — Full trade card with probability + $100 projector
    │   ├── ReviewPanel.tsx              — Pre-trade mentor review notes stream
    │   ├── MentorFeed.tsx               — Post-trade lessons with trader quotes
    │   ├── NewsPanel.tsx                — Hourly news with sentiment bars
    │   ├── ModeToggle.tsx               — Paper/Live switch with 2-step confirmation
    │   ├── GraphExplorer.tsx            — D3 force-directed knowledge graph
    │   ├── WinRateChart.tsx             — Rolling 100-trade win rate (Recharts)
    │   ├── ProjectionCalc.tsx           — Interactive $100 projector with CI band chart
    │   ├── ProbabilityGauge.tsx         — SVG semicircle gauge
    │   └── ConfidenceBandChart.tsx      — Recharts AreaChart with CI bands
    └── hooks/useWebSocket.ts            — Auto-reconnecting WebSocket hook
```

## Environment Setup

```bash
cd /home/manne/Projects/Trading/tradesage
cp .env.example .env
# Edit .env with your API keys
```

Required keys:
- `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` — alpaca.markets
- `ANTHROPIC_API_KEY` — console.anthropic.com
- `NEWS_API_KEY` — newsapi.org
- `TAVILY_API_KEY` — tavily.com
- Neo4j uses Docker (auto-started by main.py) with default password `tradesage`

## Running the System

```bash
# Backend (from tradesage/ directory)
pip install -r requirements.txt
python -m backend.main
# or: uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Frontend (from tradesage/frontend/)
npm install
npm run dev
```

## 3-Layer Gate System (Pre-Trade)

Every trade passes through 3 sequential gates. Failure triggers 1 retry, then block+log.

1. **News Gate** (`news_agent.py`) — urgency check, breaking news override
2. **Risk Gate** (`risk_agent.py`) — drawdown check, position sizing, stop-loss
3. **Mentor Gate** (`mentor_agent.py`) — Claude review with graph context → ReviewNote

LangGraph routes: `news_layer → risk_layer → mentor_layer → execute_trade → post_trade`

## Key Data Flow

```
POST /api/trade
  → orchestrator.run(ticker, action, portfolio_value)
    → news_agent.scan_ticker(ticker)          [Layer 1]
    → risk_agent.evaluate(portfolio, price)   [Layer 2]
    → mentor_agent.pre_trade_review(...)      [Layer 3]
    → trade_executor.execute(signal, params)
    → mentor_agent.generate_lesson(result)
    → graph_updater.wire_post_trade(...)
    → store.save_lesson(lesson)
    → ws_manager.broadcast("lesson", ...)     [WebSocket event]
```

## WebSocket Events

Connect to `ws://localhost:8000/ws/live`:
- `review_note` — before trade (mentor decision + ReviewNote)
- `trade_fill` — on execution (TradeResult)
- `lesson` — after close (Lesson)
- `news_update` — hourly scan results
- `mode_changed` — paper/live switch
- `pipeline_complete` — full pipeline result

## Graph Schema (Neo4j)

13 node types: Company, Sector, AssetClass, NewsEvent, MacroEvent, PriceMovement, MarketPattern, TraderPrinciple, Trade, Lesson, BookChunk, WebArticle, Mentor

Key relationships:
- `(NewsEvent)-[:CAUSED|DIVERGED_FROM]->(PriceMovement)-[:MATCHES]->(MarketPattern)`
- `(Trade)-[:GENERATED]->(Lesson)-[:CORRECTS_PATTERN|STRENGTHENS_PATTERN]->(MarketPattern)`
- `(Mentor)-[:LEARNED_FROM]->(Lesson)`
- `(MacroEvent)-[:IMPACTS]->(Sector)<-[:BELONGS_TO]-(Company)`

## Common Tasks for Claude

### Add a new agent
1. Create `backend/agents/new_agent.py` with the agent class
2. Add to `backend/agents/__init__.py`
3. Wire into `orchestrator.py` StateGraph
4. Add to `main.py` lifespan startup

### Add a new Cypher query
1. Add method `q{N}_name()` to `backend/knowledge/graph_reasoner.py`
2. Call from `mentor_agent.py` or `orchestrator.py` as needed

### Add a new API endpoint
1. Add route to `backend/main.py`
2. Call `get_store()` for SQLite or `app_state.graph_reasoner` for Neo4j

### Add a new frontend component
1. Create in `frontend/src/components/`
2. Import in the relevant page

## Testing

```bash
# Health check
curl http://localhost:8000/health

# Trigger paper trade
curl -X POST http://localhost:8000/api/trade \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "action": "buy"}'

# View portfolio
curl http://localhost:8000/api/portfolio

# Switch to live (careful!)
curl -X POST http://localhost:8000/api/toggle-mode \
  -H "Content-Type: application/json" \
  -d '{"confirm": "SWITCH_TO_LIVE", "reason": "My reason here"}'
```

## Notes for Future Sessions

- The system degrades gracefully if Neo4j is unavailable (stub classes in `main.py`)
- Alpaca paper mode simulates fills at the signal's entry price
- The orchestrator max retry count is 1 (per spec)
- All Cypher queries are parameterized (no injection risk)
- Graph updates use MERGE exclusively (idempotent)
- PDF ingestion uses PyMuPDF (fitz) with ~500-char chunks
- The $100 projector math is in `analytics/stats.py:project_100_from_ci()`
- Probability score weights: news=20%, risk=25%, mentor=35%, history=20%
