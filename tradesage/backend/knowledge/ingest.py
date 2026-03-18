"""
Knowledge Graph Ingestion
Seeds the graph with companies, sectors, asset classes, trader principles,
macro events, and market patterns. Also ingests PDFs from uploads/.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

from neo4j import Driver

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Seed Data
# ──────────────────────────────────────────────

ASSET_CLASSES = ["stocks", "crypto", "forex", "options"]

SECTORS = [
    {"name": "Technology", "description": "Software, hardware, semiconductors, internet"},
    {"name": "Healthcare", "description": "Pharma, biotech, medical devices, insurance"},
    {"name": "Financials", "description": "Banks, insurance, asset management, fintech"},
    {"name": "Energy", "description": "Oil, gas, renewables, utilities"},
    {"name": "Consumer Discretionary", "description": "Retail, autos, leisure, e-commerce"},
    {"name": "Consumer Staples", "description": "Food, beverages, household products"},
    {"name": "Industrials", "description": "Aerospace, defense, machinery, transportation"},
    {"name": "Materials", "description": "Metals, mining, chemicals, paper"},
    {"name": "Real Estate", "description": "REITs, commercial, residential"},
    {"name": "Communication Services", "description": "Telecom, media, social networks"},
    {"name": "Utilities", "description": "Electric, gas, water utilities"},
    {"name": "Crypto", "description": "Cryptocurrencies and digital assets"},
    {"name": "Forex", "description": "Foreign exchange currency pairs"},
]

COMPANIES = [
    {"ticker": "AAPL",  "name": "Apple Inc.",              "sector": "Technology",               "exchange": "NASDAQ"},
    {"ticker": "MSFT",  "name": "Microsoft Corporation",   "sector": "Technology",               "exchange": "NASDAQ"},
    {"ticker": "NVDA",  "name": "NVIDIA Corporation",      "sector": "Technology",               "exchange": "NASDAQ"},
    {"ticker": "GOOGL", "name": "Alphabet Inc.",            "sector": "Communication Services",   "exchange": "NASDAQ"},
    {"ticker": "META",  "name": "Meta Platforms Inc.",      "sector": "Communication Services",   "exchange": "NASDAQ"},
    {"ticker": "AMZN",  "name": "Amazon.com Inc.",          "sector": "Consumer Discretionary",   "exchange": "NASDAQ"},
    {"ticker": "TSLA",  "name": "Tesla Inc.",               "sector": "Consumer Discretionary",   "exchange": "NASDAQ"},
    {"ticker": "JPM",   "name": "JPMorgan Chase & Co.",    "sector": "Financials",               "exchange": "NYSE"},
    {"ticker": "BAC",   "name": "Bank of America Corp.",   "sector": "Financials",               "exchange": "NYSE"},
    {"ticker": "GS",    "name": "Goldman Sachs Group",     "sector": "Financials",               "exchange": "NYSE"},
    {"ticker": "XOM",   "name": "Exxon Mobil Corp.",       "sector": "Energy",                   "exchange": "NYSE"},
    {"ticker": "CVX",   "name": "Chevron Corporation",     "sector": "Energy",                   "exchange": "NYSE"},
    {"ticker": "SPY",   "name": "SPDR S&P 500 ETF",        "sector": "Technology",               "exchange": "NYSE"},
    {"ticker": "QQQ",   "name": "Invesco QQQ Trust",       "sector": "Technology",               "exchange": "NASDAQ"},
    {"ticker": "BTC",   "name": "Bitcoin",                  "sector": "Crypto",                   "exchange": "Crypto"},
    {"ticker": "ETH",   "name": "Ethereum",                 "sector": "Crypto",                   "exchange": "Crypto"},
    {"ticker": "EUR/USD","name": "Euro / US Dollar",        "sector": "Forex",                    "exchange": "Forex"},
    {"ticker": "GBP/USD","name": "British Pound / US Dollar","sector": "Forex",                  "exchange": "Forex"},
]

COMPETITORS = [
    ("AAPL", "MSFT"), ("AAPL", "GOOGL"), ("MSFT", "GOOGL"),
    ("AMZN", "MSFT"), ("META", "GOOGL"), ("JPM", "BAC"), ("JPM", "GS"),
    ("XOM", "CVX"),
]

SECTOR_CORRELATIONS = [
    ("Technology", "Communication Services", 0.82, "1Y"),
    ("Technology", "Consumer Discretionary",  0.71, "1Y"),
    ("Financials", "Real Estate",              0.68, "1Y"),
    ("Energy", "Materials",                   0.75, "1Y"),
    ("Consumer Staples", "Utilities",          0.65, "1Y"),
    ("Technology", "Healthcare",              -0.15, "1Y"),
    ("Energy", "Technology",                 -0.20, "1Y"),
]

TRADER_PRINCIPLES = [
    {
        "trader_name": "Warren Buffett",
        "principle_name": "Margin of Safety",
        "description": "Buy securities at a significant discount to their intrinsic value.",
        "quote": "It's far better to buy a wonderful company at a fair price than a fair company at a wonderful price.",
        "book_source": "The Intelligent Investor",
        "chapter": "Chapter 20",
    },
    {
        "trader_name": "Warren Buffett",
        "principle_name": "Long-Term Compounding",
        "description": "Hold quality businesses for years, letting compounding work.",
        "quote": "Our favorite holding period is forever.",
        "book_source": "Berkshire Hathaway Annual Letters",
        "chapter": "1988 Letter",
    },
    {
        "trader_name": "George Soros",
        "principle_name": "Reflexivity",
        "description": "Market prices influence fundamentals which in turn influence prices — a feedback loop.",
        "quote": "Markets are constantly in a state of uncertainty and flux, and money is made by discounting the obvious and betting on the unexpected.",
        "book_source": "The Alchemy of Finance",
        "chapter": "Chapter 1",
    },
    {
        "trader_name": "George Soros",
        "principle_name": "Bold Position Sizing",
        "description": "When conviction is high and macro thesis is clear, size up aggressively.",
        "quote": "It's not whether you're right or wrong, but how much money you make when you're right and how much you lose when you're wrong.",
        "book_source": "Soros on Soros",
        "chapter": "Chapter 3",
    },
    {
        "trader_name": "Paul Tudor Jones",
        "principle_name": "Risk First",
        "description": "Define your risk before entering any trade. Capital preservation above all.",
        "quote": "Don't focus on making money; focus on protecting what you have.",
        "book_source": "Market Wizards",
        "chapter": "Paul Tudor Jones Interview",
    },
    {
        "trader_name": "Paul Tudor Jones",
        "principle_name": "Never Average Losers",
        "description": "Never add to a losing position. Cut losses ruthlessly.",
        "quote": "Never average losers. Decrease your trading volume when you are not trading well; increase it when you are.",
        "book_source": "Market Wizards",
        "chapter": "Paul Tudor Jones Interview",
    },
    {
        "trader_name": "Ray Dalio",
        "principle_name": "All-Weather Diversification",
        "description": "Diversify across uncorrelated assets to reduce risk without sacrificing return.",
        "quote": "He who lives by the crystal ball will eat shattered glass.",
        "book_source": "Principles",
        "chapter": "Chapter 5",
    },
    {
        "trader_name": "Ray Dalio",
        "principle_name": "Radical Transparency",
        "description": "Understand the macro machine — how economies and markets work at a systems level.",
        "quote": "The economy is like a machine.",
        "book_source": "How the Economic Machine Works",
        "chapter": "Full Document",
    },
    {
        "trader_name": "Peter Lynch",
        "principle_name": "Know What You Own",
        "description": "Only invest in businesses you understand. Research thoroughly before buying.",
        "quote": "Know what you own, and know why you own it.",
        "book_source": "One Up On Wall Street",
        "chapter": "Chapter 2",
    },
    {
        "trader_name": "Peter Lynch",
        "principle_name": "GARP — Growth at Reasonable Price",
        "description": "Look for growth stocks trading at a reasonable P/E relative to growth rate.",
        "quote": "The P/E ratio of any company that's fairly priced will equal its growth rate.",
        "book_source": "Beating the Street",
        "chapter": "Chapter 4",
    },
    {
        "trader_name": "Jim Simons",
        "principle_name": "Quantitative Pattern Recognition",
        "description": "Let data and models guide decisions, not intuition or emotion.",
        "quote": "We search for statistically validated patterns that repeat.",
        "book_source": "The Man Who Solved the Market",
        "chapter": "Chapter 7",
    },
    {
        "trader_name": "Stanley Druckenmiller",
        "principle_name": "Concentrated Conviction Bets",
        "description": "When your macro thesis is right, concentrate capital — don't diversify into mediocrity.",
        "quote": "Soros has taught me that when you have tremendous conviction on a trade, you have to go for the jugular.",
        "book_source": "Market Wizards",
        "chapter": "Stanley Druckenmiller Interview",
    },
    {
        "trader_name": "Jesse Livermore",
        "principle_name": "Trend Following and Timing",
        "description": "The big money is made by sitting, not trading. Wait for the right moment.",
        "quote": "There is a time to go long, a time to go short, and a time to go fishing.",
        "book_source": "Reminiscences of a Stock Operator",
        "chapter": "Chapter 8",
    },
    {
        "trader_name": "Jesse Livermore",
        "principle_name": "Emotional Discipline",
        "description": "The stock market does not beat people. People beat themselves through fear, hope, and greed.",
        "quote": "The speculator's chief enemies are always boring from within. It is inseparable from human nature to hope and to fear.",
        "book_source": "Reminiscences of a Stock Operator",
        "chapter": "Chapter 3",
    },
    {
        "trader_name": "Carl Icahn",
        "principle_name": "Contrarian Activism",
        "description": "Buy deeply undervalued assets or companies others are afraid to touch.",
        "quote": "In life and business, there are two cardinal sins: the first is to act precipitously without thought, and the second is to not act at all.",
        "book_source": "King Icahn",
        "chapter": "Chapter 1",
    },
    {
        "trader_name": "Howard Marks",
        "principle_name": "Second-Order Thinking",
        "description": "Think about what others are thinking and what they're missing. Markets price in first-order thinking.",
        "quote": "The most important thing is not what the future holds but how the future compares to what's priced in.",
        "book_source": "The Most Important Thing",
        "chapter": "Chapter 2",
    },
    {
        "trader_name": "Howard Marks",
        "principle_name": "Market Cycles Awareness",
        "description": "Recognise where we are in the cycle and position accordingly.",
        "quote": "Rule number one: most things will prove to be cyclical. Rule number two: some of the greatest opportunities for gain and loss come when other people forget rule number one.",
        "book_source": "Mastering the Market Cycle",
        "chapter": "Chapter 1",
    },
]

MARKET_PATTERNS = [
    {
        "name": "Breakout with Volume",
        "description": "Price breaks above key resistance on above-average volume",
        "indicators": "price > resistance, volume > 1.5x 20-day avg",
        "historical_success_rate": 0.65,
        "times_seen": 0,
    },
    {
        "name": "Earnings Momentum",
        "description": "Strong earnings beat driving sustained upward momentum",
        "indicators": "EPS beat >10%, guidance raised, price gap up",
        "historical_success_rate": 0.72,
        "times_seen": 0,
    },
    {
        "name": "News Catalyst Spike",
        "description": "Major positive news causing a sharp intraday move with follow-through",
        "indicators": "sentiment_score > 0.7, volume spike, price +3%+",
        "historical_success_rate": 0.58,
        "times_seen": 0,
    },
    {
        "name": "Mean Reversion Oversold",
        "description": "Asset oversold after sharp decline, RSI < 30, bouncing from support",
        "indicators": "RSI < 30, price at major support, volume declining on selloff",
        "historical_success_rate": 0.62,
        "times_seen": 0,
    },
    {
        "name": "Macro Tailwind Sector Rotation",
        "description": "Macro event (e.g., Fed cut) driving capital into a beneficiary sector",
        "indicators": "macro event detected, sector correlation > 0.7, momentum shift",
        "historical_success_rate": 0.68,
        "times_seen": 0,
    },
    {
        "name": "Sentiment Divergence",
        "description": "News very positive but price declining — potential accumulation by smart money",
        "indicators": "sentiment > 0.5, price down > 2%, volume steady",
        "historical_success_rate": 0.55,
        "times_seen": 0,
    },
    {
        "name": "Trend Continuation",
        "description": "Asset in a confirmed uptrend, pullback to 50-day MA, resuming trend",
        "indicators": "price > 50MA > 200MA, pullback < 5%, volume drying up on dip",
        "historical_success_rate": 0.70,
        "times_seen": 0,
    },
]

MACRO_EVENTS = [
    {
        "macro_id": "FED_RATE_CUT",
        "type": "Fed",
        "description": "Federal Reserve interest rate cut",
        "impact_direction": "BULLISH",
        "impacts": [
            {"sector": "Technology",    "direction": "BULLISH", "avg_move_pct": 2.5},
            {"sector": "Real Estate",   "direction": "BULLISH", "avg_move_pct": 3.0},
            {"sector": "Financials",    "direction": "BEARISH", "avg_move_pct": -1.5},
            {"sector": "Utilities",     "direction": "BULLISH", "avg_move_pct": 1.8},
        ],
    },
    {
        "macro_id": "FED_RATE_HIKE",
        "type": "Fed",
        "description": "Federal Reserve interest rate hike",
        "impact_direction": "BEARISH",
        "impacts": [
            {"sector": "Technology",    "direction": "BEARISH", "avg_move_pct": -3.0},
            {"sector": "Real Estate",   "direction": "BEARISH", "avg_move_pct": -4.0},
            {"sector": "Financials",    "direction": "BULLISH", "avg_move_pct": 1.5},
        ],
    },
    {
        "macro_id": "CPI_HIGH",
        "type": "CPI",
        "description": "Higher-than-expected CPI (inflation) print",
        "impact_direction": "BEARISH",
        "impacts": [
            {"sector": "Energy",        "direction": "BULLISH", "avg_move_pct": 2.0},
            {"sector": "Materials",     "direction": "BULLISH", "avg_move_pct": 1.5},
            {"sector": "Technology",    "direction": "BEARISH", "avg_move_pct": -2.5},
        ],
    },
    {
        "macro_id": "STRONG_GDP",
        "type": "GDP",
        "description": "Stronger-than-expected GDP growth",
        "impact_direction": "BULLISH",
        "impacts": [
            {"sector": "Consumer Discretionary", "direction": "BULLISH", "avg_move_pct": 2.0},
            {"sector": "Industrials",             "direction": "BULLISH", "avg_move_pct": 1.8},
            {"sector": "Financials",              "direction": "BULLISH", "avg_move_pct": 1.5},
        ],
    },
]


# ──────────────────────────────────────────────
# Ingestion Functions
# ──────────────────────────────────────────────

def ingest_asset_classes(driver: Driver) -> None:
    with driver.session() as session:
        for ac in ASSET_CLASSES:
            session.run(
                "MERGE (a:AssetClass {name: $name})",
                {"name": ac}
            )
    logger.info("Ingested %d asset classes.", len(ASSET_CLASSES))


def ingest_sectors(driver: Driver) -> None:
    with driver.session() as session:
        for s in SECTORS:
            session.run(
                "MERGE (s:Sector {name: $name}) SET s.description = $desc",
                {"name": s["name"], "desc": s["description"]}
            )

        # Sector correlations
        for s1, s2, coeff, timeframe in SECTOR_CORRELATIONS:
            session.run(
                """
                MATCH (a:Sector {name: $s1}), (b:Sector {name: $s2})
                MERGE (a)-[r:CORRELATED_WITH {timeframe: $tf}]->(b)
                SET r.correlation_coeff = $coeff
                MERGE (b)-[r2:CORRELATED_WITH {timeframe: $tf}]->(a)
                SET r2.correlation_coeff = $coeff
                """,
                {"s1": s1, "s2": s2, "coeff": coeff, "tf": timeframe}
            )
    logger.info("Ingested %d sectors + correlations.", len(SECTORS))


def ingest_companies(driver: Driver) -> None:
    with driver.session() as session:
        for c in COMPANIES:
            # Map sector name to asset class
            if c["sector"] == "Crypto":
                ac = "crypto"
            elif c["sector"] == "Forex":
                ac = "forex"
            else:
                ac = "stocks"

            session.run(
                """
                MERGE (co:Company {ticker: $ticker})
                SET co.name = $name,
                    co.sector = $sector,
                    co.exchange = $exchange
                WITH co
                MATCH (s:Sector {name: $sector})
                MERGE (co)-[:BELONGS_TO]->(s)
                WITH co
                MATCH (a:AssetClass {name: $ac})
                MERGE (co)-[:TRADES_AS]->(a)
                """,
                {
                    "ticker": c["ticker"],
                    "name": c["name"],
                    "sector": c["sector"],
                    "exchange": c["exchange"],
                    "ac": ac,
                }
            )

        # Competitor relationships
        for t1, t2 in COMPETITORS:
            session.run(
                """
                MATCH (a:Company {ticker: $t1}), (b:Company {ticker: $t2})
                MERGE (a)-[:COMPETITOR_OF]->(b)
                MERGE (b)-[:COMPETITOR_OF]->(a)
                """,
                {"t1": t1, "t2": t2}
            )
    logger.info("Ingested %d companies.", len(COMPANIES))


def ingest_trader_principles(driver: Driver) -> None:
    with driver.session() as session:
        for tp in TRADER_PRINCIPLES:
            session.run(
                """
                MERGE (tp:TraderPrinciple {principle_name: $pname})
                SET tp.trader_name = $trader,
                    tp.description = $desc,
                    tp.quote = $quote,
                    tp.book_source = $book,
                    tp.chapter = $chapter
                """,
                {
                    "pname": tp["principle_name"],
                    "trader": tp["trader_name"],
                    "desc": tp["description"],
                    "quote": tp["quote"],
                    "book": tp["book_source"],
                    "chapter": tp["chapter"],
                }
            )
    logger.info("Ingested %d trader principles.", len(TRADER_PRINCIPLES))


def ingest_market_patterns(driver: Driver) -> None:
    with driver.session() as session:
        for mp in MARKET_PATTERNS:
            session.run(
                """
                MERGE (p:MarketPattern {name: $name})
                SET p.description = $desc,
                    p.indicators = $indicators,
                    p.historical_success_rate = $hsr,
                    p.times_seen = $ts
                """,
                {
                    "name": mp["name"],
                    "desc": mp["description"],
                    "indicators": mp["indicators"],
                    "hsr": mp["historical_success_rate"],
                    "ts": mp["times_seen"],
                }
            )

        # Wire TraderPrinciples to relevant patterns
        principle_pattern_map = [
            ("Risk First", "News Catalyst Spike"),
            ("Never Average Losers", "Mean Reversion Oversold"),
            ("Trend Following and Timing", "Trend Continuation"),
            ("Trend Following and Timing", "Breakout with Volume"),
            ("Margin of Safety", "Mean Reversion Oversold"),
            ("Quantitative Pattern Recognition", "Breakout with Volume"),
            ("Macro Tailwind Sector Rotation", "Macro Tailwind Sector Rotation"),
            ("Second-Order Thinking", "Sentiment Divergence"),
            ("Concentrated Conviction Bets", "Earnings Momentum"),
        ]
        for principle, pattern in principle_pattern_map:
            session.run(
                """
                MATCH (tp:TraderPrinciple {principle_name: $principle}),
                      (p:MarketPattern {name: $pattern})
                MERGE (tp)-[:APPLIES_TO]->(p)
                """,
                {"principle": principle, "pattern": pattern}
            )

    logger.info("Ingested %d market patterns.", len(MARKET_PATTERNS))


def ingest_macro_events(driver: Driver) -> None:
    with driver.session() as session:
        for me in MACRO_EVENTS:
            session.run(
                """
                MERGE (m:MacroEvent {macro_id: $mid})
                SET m.type = $type,
                    m.description = $desc,
                    m.impact_direction = $direction
                """,
                {
                    "mid": me["macro_id"],
                    "type": me["type"],
                    "desc": me["description"],
                    "direction": me["impact_direction"],
                }
            )

            for imp in me.get("impacts", []):
                session.run(
                    """
                    MATCH (m:MacroEvent {macro_id: $mid}),
                          (s:Sector {name: $sector})
                    MERGE (m)-[r:IMPACTS {sector: $sector}]->(s)
                    SET r.direction = $direction,
                        r.avg_move_pct = $avg_move_pct
                    """,
                    {
                        "mid": me["macro_id"],
                        "sector": imp["sector"],
                        "direction": imp["direction"],
                        "avg_move_pct": imp["avg_move_pct"],
                    }
                )
    logger.info("Ingested %d macro events.", len(MACRO_EVENTS))


def ingest_mentor_node(driver: Driver) -> None:
    with driver.session() as session:
        session.run(
            """
            MERGE (m:Mentor {name: 'TradeSage Mentor'})
            SET m.win_rate = 0.0,
                m.total_trades = 0,
                m.consecutive_wins = 0
            """
        )
    logger.info("Mentor node created/confirmed.")


_SQLITE_DB = Path(__file__).parent.parent.parent / "tradesage.db"


def _ensure_book_chunks_table() -> None:
    """Create book_chunks table in SQLite if it doesn't exist."""
    import sqlite3
    con = sqlite3.connect(str(_SQLITE_DB))
    con.execute("""
        CREATE TABLE IF NOT EXISTS book_chunks (
            chunk_id   TEXT PRIMARY KEY,
            book_title TEXT NOT NULL,
            page_num   INTEGER,
            content    TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()
    con.close()


def ingest_pdf(driver: Optional[Driver], pdf_path: str) -> int:
    """
    Ingest a PDF into SQLite book_chunks (always) and Neo4j (if available).
    Returns the number of chunks ingested.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF not installed. Cannot ingest PDF.")
        return 0

    path = Path(pdf_path)
    if not path.exists():
        logger.warning("PDF not found: %s", pdf_path)
        return 0

    doc = fitz.open(str(path))
    book_title = path.stem.replace("_", " ").replace("-", " ").title()
    chunks_created = 0

    # Always store in SQLite
    import sqlite3 as _sqlite3
    _ensure_book_chunks_table()
    con = _sqlite3.connect(str(_SQLITE_DB))

    for page_num, page in enumerate(doc):
        text = page.get_text("text").strip()
        if not text or len(text) < 100:
            continue

        chunk_size = 500
        for i in range(0, len(text), chunk_size):
            chunk_text = text[i:i + chunk_size]
            chunk_id = hashlib.sha256(
                f"{book_title}-{page_num}-{i}".encode()
            ).hexdigest()[:16]

            con.execute(
                "INSERT OR REPLACE INTO book_chunks (chunk_id, book_title, page_num, content) VALUES (?,?,?,?)",
                (chunk_id, book_title, page_num + 1, chunk_text),
            )

            # Also store in Neo4j if available
            if driver:
                try:
                    with driver.session() as session:
                        session.run(
                            """
                            MERGE (bc:BookChunk {chunk_id: $chunk_id})
                            SET bc.book_title = $title,
                                bc.chapter = $chapter,
                                bc.content = $content,
                                bc.page_num = $page_num
                            """,
                            {
                                "chunk_id": chunk_id,
                                "title": book_title,
                                "chapter": f"Page {page_num + 1}",
                                "content": chunk_text,
                                "page_num": page_num + 1,
                            }
                        )
                except Exception as neo4j_err:
                    logger.debug("Neo4j chunk write skipped: %s", neo4j_err)

            chunks_created += 1

    con.commit()
    con.close()
    doc.close()
    logger.info("Ingested PDF '%s': %d chunks saved to SQLite.", book_title, chunks_created)
    return chunks_created


def ingest_all(driver: Driver) -> None:
    """Run the full ingestion pipeline."""
    logger.info("Starting knowledge graph seed ingestion...")
    ingest_asset_classes(driver)
    ingest_sectors(driver)
    ingest_companies(driver)
    ingest_trader_principles(driver)
    ingest_market_patterns(driver)
    ingest_macro_events(driver)
    ingest_mentor_node(driver)

    # Ingest any existing PDFs in uploads/
    uploads_dir = Path(__file__).parent.parent.parent / "uploads"
    for pdf_file in uploads_dir.glob("*.pdf"):
        ingest_pdf(driver, str(pdf_file))

    logger.info("Knowledge graph ingestion complete.")
