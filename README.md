# TradeSage — AI Trading Automation

> A fully autonomous multi-agent AI trading system that discovers stocks, builds algorithms, tests them, and graduates profitable strategies to live trading — all without human intervention.

---

## What It Does

TradeSage runs a continuous 7-stage pipeline every 4 hours:

```
Discovery → Research → Algorithm Build → Simulation → Validation → Paper Trading → Live
```

1. **Discovery** — Scans volume spikes, sector rotation, short squeeze setups, earnings surprises, and options flow across 20+ tickers
2. **Research** — Deep fundamental + news + SEC filing analysis per stock using Claude AI
3. **Algorithm Build** — Claude generates Python entry/exit trading rules tailored to each stock's characteristics; learns from predecessor algorithm performance
4. **Simulation** — Backtests on up to 10 years of real daily OHLCV data; must pass win rate, profit factor, and drawdown thresholds
5. **Validation** — 6-check quant validation: Monte Carlo, out-of-sample, correlation, capacity, risk committee review
6. **Paper Trading** — Runs historical backtests on max available data (10+ years) to accumulate 50+ realistic trades
7. **Live Graduation** — Algorithms that hit the win rate target deploy automatically to Alpaca live account

---

## Architecture

```
tradesage/
├── backend/
│   ├── main.py                              # FastAPI + WebSocket + APScheduler
│   ├── config.py                            # Env config (crashes if keys missing)
│   ├── agents/
│   │   ├── orchestrator.py                  # LangGraph 3-gate pre-trade system
│   │   ├── pipeline/
│   │   │   └── pipeline_orchestrator.py     # 7-stage autonomous pipeline
│   │   ├── discovery/                       # 7 discovery micro-agents
│   │   ├── research/                        # 6 research micro-agents
│   │   ├── algorithm/                       # Algorithm builder + entry/exit generators
│   │   ├── simulation/                      # Backtest + verdict micro-agents
│   │   ├── validation/                      # 6-check quant validation suite
│   │   ├── paper_trading/                   # Historical paper trading runner
│   │   ├── deployment/                      # Live deployment agent
│   │   ├── news_agent.py                    # Hourly news + sentiment
│   │   ├── risk_agent.py                    # Kelly sizing + circuit breakers
│   │   ├── trade_executor.py                # Alpaca + CCXT execution
│   │   └── mentor_agent.py                  # Knowledge graph mentor
│   ├── knowledge/
│   │   ├── graph_schema.py                  # Neo4j 13-node schema
│   │   ├── graph_reasoner.py                # 13 Cypher query functions
│   │   ├── graph_updater.py                 # Post-trade MERGE wiring
│   │   └── ingest.py                        # Seed: 18 companies, 13 sectors, 17 principles
│   ├── db/
│   │   ├── router.py                        # DataRouter — single persistence facade
│   │   └── sqlite_store.py                  # Async SQLAlchemy SQLite
│   ├── data/
│   │   ├── market_data.py                   # Alpaca + CCXT + yfinance unified feed
│   │   └── options_data.py                  # Options chain data
│   ├── llm/router.py                        # Claude API router + cost tracker
│   └── analytics/stats.py                   # Kelly, Sharpe, CI, $100 projector
└── frontend/
    └── src/
        ├── pages/
        │   ├── Dashboard.tsx                # Live P&L, trade triggers, mentor feed
        │   ├── Lab.tsx                      # Pipeline monitor + algorithm cards
        │   ├── Analytics.tsx                # Win rate + $100 projector + CI charts
        │   ├── MentorSchool.tsx             # Knowledge graph + PDF upload + lessons
        │   └── NewsRoom.tsx                 # News scan + sentiment
        └── components/                      # 15+ reusable UI components
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Orchestration | LangGraph StateGraph |
| LLM | Claude Sonnet (Anthropic) via LangChain |
| Knowledge Graph | Neo4j 5 Community (Docker) |
| Backend | FastAPI + WebSocket + APScheduler |
| Database | SQLite (WAL mode) via SQLAlchemy |
| Market Data | Alpaca + CCXT + yfinance |
| News | NewsAPI + Tavily |
| Frontend | React + TypeScript + Vite + Tailwind + Recharts |
| Trade Execution | Alpaca (paper/live) + CCXT |
| Scheduler | APScheduler — pipeline every 4h, paper trader every 5min, news every 1h |

---

## Pipeline Detail

### Algorithm Learning
Every new algorithm receives **predecessor context** — the previous algorithm's win rate, strategy type, and performance. The LLM uses this to generate simpler, more selective entry conditions that fire more frequently and achieve higher win rates.

### Simulation Thresholds
| Metric | Threshold |
|--------|-----------|
| Minimum trades | 20 |
| Minimum win rate | 52% |
| Minimum profit factor | 0.3 |
| Max drawdown | 60% |

### Graduation to Live
| Metric | Requirement |
|--------|-------------|
| Paper trades | 50+ |
| Win rate | 90% |

Algorithms that don't graduate keep accumulating paper trades indefinitely — no auto-rejection.

### Retry System
Failed simulations don't get discarded. The algorithm is kept as DRAFT and retried with extended data (up to 10 years) for up to 5 attempts before permanent rejection.

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Node.js 20+
- Docker (for Neo4j)

### 2. Environment

```bash
cd tradesage
cp .env.example .env
# Fill in your API keys
```

Required keys:
| Key | Source |
|-----|--------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | [alpaca.markets](https://alpaca.markets) (free paper trading) |
| `NEWS_API_KEY` | [newsapi.org](https://newsapi.org) |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com) |
| `NEO4J_PASSWORD` | Set to `tradesage` (matches Docker default) |

### 3. Backend

```bash
cd tradesage
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m backend.main
```

Backend starts at `http://localhost:8000`

### 4. Neo4j (auto-started, or manually)

```bash
docker run -d \
  --name tradesage-neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/tradesage \
  neo4j:5-community
```

### 5. Frontend

```bash
cd tradesage/frontend
npm install
npm run dev
```

Dashboard at `http://localhost:3000`

---

## 3-Layer Pre-Trade Gate

Every manual trade signal passes through 3 sequential gates before execution:

```
Signal → [News Gate] → [Risk Gate] → [Mentor Gate] → Execute → Post-Trade
```

| Gate | Agent | Blocks On |
|------|-------|-----------|
| Layer 1 | NewsAgent | Breaking news, override_cancel urgency |
| Layer 2 | RiskAgent | 15% drawdown, 5% daily loss, position > 10% |
| Layer 3 | MentorAgent | Knowledge graph BLOCKED / DELAYED verdict |

Failure at any gate → 1 retry → block + log.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/portfolio` | Portfolio state + P&L |
| GET | `/api/trades` | Trade history |
| GET | `/api/pipeline/status` | Pipeline current status |
| GET | `/api/pipeline/algorithms` | All algorithms + metrics |
| GET | `/api/pipeline/discovered` | Discovered stocks |
| GET | `/api/pipeline/events` | Pipeline event log |
| POST | `/api/pipeline/trigger` | Manually trigger pipeline run |
| POST | `/api/trade` | Trigger a manual trade signal |
| POST | `/api/toggle-mode` | Switch paper ↔ live |
| POST | `/api/upload-book` | Upload PDF trading book for mentor |
| WS | `/ws/live` | Real-time event stream |

---

## WebSocket Events

Connect to `ws://localhost:8000/ws/live`:

| Event | Fires When |
|-------|-----------|
| `pipeline_complete` | Each pipeline stage completes |
| `review_note` | Before trade (mentor decision) |
| `trade_fill` | Trade executes |
| `lesson` | After trade closes |
| `news_update` | Hourly scan completes |
| `mode_changed` | Paper/live toggle |

---

## Knowledge Graph (Neo4j)

**13 node types:** Company, Sector, AssetClass, NewsEvent, MacroEvent, PriceMovement, MarketPattern, TraderPrinciple, Trade, Lesson, BookChunk, WebArticle, Mentor

**Key relationships:**
```
(NewsEvent) -[:CAUSED]-> (PriceMovement) -[:MATCHES]-> (MarketPattern)
(Trade) -[:GENERATED]-> (Lesson) -[:STRENGTHENS_PATTERN]-> (MarketPattern)
(Mentor) -[:LEARNED_FROM]-> (Lesson)
(MacroEvent) -[:IMPACTS]-> (Sector) <-[:BELONGS_TO]- (Company)
```

---

## Risk Parameters

| Parameter | Default |
|-----------|---------|
| Max position size | 10% of portfolio |
| Stop-loss | 2% per trade |
| Reward:risk ratio | 2:1 |
| Max drawdown halt | 15% |
| Daily loss limit | 5% |

---

## Screenshots

> Lab page showing autonomous pipeline with algorithm cards, paper trading win rates, and simulation results.

> Dashboard with live P&L, open positions, and mentor lesson feed.

---

## License

MIT — use at your own risk. This is not financial advice.
