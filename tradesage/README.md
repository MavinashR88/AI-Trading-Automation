# TradeSage — Multi-Agent AI Trading System

A production-grade AI trading system combining LangGraph orchestration, Neo4j knowledge graph, Claude LLM, and a React dashboard. Features a 3-layer pre-trade gate (News → Risk → Mentor) and composite wisdom from the world's top 10 traders.

## Architecture

```
tradesage/
├── backend/
│   ├── main.py                  # FastAPI entrypoint + WebSocket server
│   ├── config.py                # Environment config (fails loudly if keys missing)
│   ├── agents/
│   │   ├── orchestrator.py      # LangGraph StateGraph — 3-layer gate system
│   │   ├── news_agent.py        # Hourly news reader + sentiment scorer
│   │   ├── risk_agent.py        # Kelly position sizing + circuit breakers
│   │   ├── trade_executor.py    # Alpaca (stocks) + CCXT (crypto/forex)
│   │   └── mentor_agent.py      # Knowledge graph mentor — top 10 traders
│   ├── knowledge/
│   │   ├── graph_schema.py      # Neo4j constraints + vector indexes
│   │   ├── ingest.py            # Seed graph: companies, sectors, principles
│   │   ├── graph_reasoner.py    # 13 Cypher query functions
│   │   ├── graph_updater.py     # Post-trade graph wiring (MERGE idempotent)
│   │   └── web_scraper.py       # Tavily-powered research
│   ├── data/
│   │   ├── market_data.py       # Unified Alpaca + CCXT + yfinance feed
│   │   ├── options_data.py      # Tradier + yfinance options chains
│   │   └── trade_history.py     # SQLite trade history helpers
│   ├── models/                  # Pydantic models (Trade, Signal, Lesson, ReviewNote)
│   ├── db/sqlite_store.py       # Async SQLAlchemy SQLite store
│   └── analytics/stats.py       # scipy CI math + $100 projector + Kelly
├── frontend/
│   └── src/
│       ├── App.tsx              # Router + sidebar
│       ├── pages/               # Dashboard, Analytics, MentorSchool, NewsRoom
│       ├── components/          # All UI components
│       └── hooks/useWebSocket.ts
└── uploads/                     # PDF books for mentor to study
```

## Setup

### 1. Prerequisites

- Python 3.11+
- Node.js 20+
- Docker (for Neo4j)
- API keys (see `.env.example`)

### 2. Environment

```bash
cd tradesage
cp .env.example .env
# Fill in all required API keys in .env
```

Required keys:
- `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` — [alpaca.markets](https://alpaca.markets) (free paper trading)
- `ANTHROPIC_API_KEY` — [console.anthropic.com](https://console.anthropic.com)
- `NEWS_API_KEY` — [newsapi.org](https://newsapi.org) (free tier: 100 req/day)
- `TAVILY_API_KEY` — [tavily.com](https://tavily.com) (free tier available)
- `NEO4J_PASSWORD` — set to match your Docker setup (default: `tradesage`)

### 3. Backend

```bash
cd tradesage

# Install with uv (recommended)
pip install uv
uv pip install -r requirements.txt

# Or with pip
pip install -r requirements.txt

# Run (auto-starts Neo4j Docker, runs graph seeding in background)
python -m backend.main
```

Server starts at `http://localhost:8000`

### 4. Neo4j (auto-started by main.py)

Or manually:
```bash
docker run -d \
  --name tradesage-neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/tradesage \
  neo4j:5-community
```

Neo4j Browser: `http://localhost:7474`

### 5. Frontend

```bash
cd tradesage/frontend
npm install
npm run dev
```

Dashboard at `http://localhost:3000`

## Usage

### Paper Trading (default)

The system starts in paper mode with a $50,000 virtual portfolio. To trigger a trade analysis:

```bash
curl -X POST http://localhost:8000/api/trade \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "market_type": "stock", "action": "buy"}'
```

The orchestrator runs the 3-layer gate:
1. **News Gate** — fetches and scores news via NewsAPI + Benzinga + Tavily
2. **Risk Gate** — Kelly position sizing, drawdown checks
3. **Mentor Gate** — Claude reviews the trade against the knowledge graph

### Switch to Live Trading

```bash
curl -X POST http://localhost:8000/api/toggle-mode \
  -H "Content-Type: application/json" \
  -d '{"confirm": "SWITCH_TO_LIVE", "reason": "I have tested in paper for 30 days with positive results"}'
```

### Upload a Trading Book

```bash
curl -X POST http://localhost:8000/api/upload-book \
  -F "file=@market_wizards.pdf"
```

### WebSocket

Connect to `ws://localhost:8000/ws/live` to receive real-time events:
- `review_note` — fires **before** trade execution (mentor decision)
- `trade_fill` — fires when order executes
- `lesson` — fires after trade closes
- `news_update` — hourly news scan results

## 3-Layer Gate System

No trade executes without passing all three layers. Failure at any layer triggers one retry, then the trade is blocked and logged.

```
Signal Request
     │
     ▼
[Layer 1: News Gate]
  NewsAPI + Benzinga + Tavily → Claude sentiment scoring
  Blocks on: urgency=override_cancel, breaking news
     │ PASS
     ▼
[Layer 2: Risk Gate]
  Kelly fraction × sentiment confidence = position size
  Blocks on: max drawdown (15%), daily loss limit (5%), price=0
     │ PASS
     ▼
[Layer 3: Mentor Gate]
  Graph traversal → Claude review → ReviewNote (APPROVED/BLOCKED/REDUCED/DELAYED)
  Blocks on: mentor BLOCKED or DELAYED
     │ APPROVED
     ▼
[Execute Trade]
  Alpaca paper/live (stocks) or CCXT (crypto/forex)
     │
     ▼
[Post-Trade]
  Lesson generation → Graph wiring → WebSocket broadcast
```

## Knowledge Graph

Neo4j graph with 13 node types and 25+ relationship types. Key reasoning queries:

- **Q1** News → PriceMovement → Pattern → Principle → Trade causal chain
- **Q3** Sector contagion (correlation > 0.6)
- **Q5** News-price divergence detector (last 7 days)
- **Q6** Full pre-trade context subgraph (last 4 hours)
- **Q7** Win rate by market pattern

## Risk Management

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_POSITION_PCT` | 10% | Max single position size |
| `RISK_PER_TRADE` | 2% | Stop-loss distance |
| `REWARD_RISK_RATIO` | 2:1 | Take-profit = 2× stop distance |
| `MAX_DRAWDOWN_PCT` | 15% | Halt trading threshold |
| `MAX_DAILY_LOSS_PCT` | 5% | Daily loss circuit breaker |

Position size = `portfolio_value × kelly_fraction × sentiment_confidence`

## Probability Score

```
composite = (news_score × 0.20) + (risk_score × 0.25) + (mentor_score × 0.35) + (historical_win_rate × 0.20)
```

Grades: A+ (≥88%) · A (≥78%) · B (≥65%) · C (≥50%) · D (≥38%) · F (<38%)

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Portfolio state + P&L + positions |
| GET | `/api/trades` | Paginated trade history |
| GET | `/api/news/{ticker}` | Latest news + sentiment |
| GET | `/api/lessons` | Mentor lessons (latest 20) |
| GET | `/api/reviews` | Pre-trade review notes (latest 20) |
| GET | `/api/win-rate` | Rolling win rate data |
| GET | `/api/graph/subgraph/{ticker}` | D3 graph data |
| POST | `/api/trade` | Trigger trade signal |
| POST | `/api/toggle-mode` | Switch paper/live |
| POST | `/api/upload-book` | Upload PDF for mentor |
| POST | `/api/add-ticker` | Add to watch list |
| WS | `/ws/live` | Live event stream |

## LLM Model

All LLM calls use `claude-sonnet-4-20250514` via LangChain ChatAnthropic.
