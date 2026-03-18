"""
Book Suggester — detects knowledge gaps after losses and suggests trading books.
After every LOSS:
  1. Claude identifies the knowledge gap that caused it (HEAVY tier)
  2. Creates a BookSuggestion if < 3 graph chunks cover that gap
  3. Shown on Mentor School page as "📚 Mentor Reading List"
  4. When user uploads the PDF → status: SUGGESTED → UPLOADED → LEARNED
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "tradesage.db"

KNOWN_BOOKS = [
    {"title": "Market Wizards", "author": "Jack Schwager", "covers": ["risk management", "trend following", "psychology", "position sizing"]},
    {"title": "Reminiscences of a Stock Operator", "author": "Edwin Lefèvre", "covers": ["trend following", "timing", "patience", "momentum"]},
    {"title": "The Intelligent Investor", "author": "Benjamin Graham", "covers": ["value investing", "margin of safety", "fundamentals"]},
    {"title": "One Up On Wall Street", "author": "Peter Lynch", "covers": ["growth stocks", "research", "sector analysis", "GARP"]},
    {"title": "Principles", "author": "Ray Dalio", "covers": ["macro", "diversification", "correlation", "risk parity"]},
    {"title": "The Big Short", "author": "Michael Lewis", "covers": ["shorting", "contrarian", "research", "derivatives"]},
    {"title": "Flash Boys", "author": "Michael Lewis", "covers": ["market microstructure", "HFT", "execution"]},
    {"title": "How to Make Money in Stocks", "author": "William O'Neil", "covers": ["momentum", "CAN SLIM", "breakouts", "technical analysis"]},
    {"title": "Trading in the Zone", "author": "Mark Douglas", "covers": ["psychology", "discipline", "consistency", "mindset"]},
    {"title": "The New Market Wizards", "author": "Jack Schwager", "covers": ["risk", "systematic trading", "quantitative", "edge"]},
    {"title": "Quantitative Trading", "author": "Ernest Chan", "covers": ["quantitative", "algorithmic", "backtesting", "statistics"]},
    {"title": "Expected Returns", "author": "Antti Ilmanen", "covers": ["macro", "factor investing", "cycles", "diversification"]},
]

BOOK_GAP_SYSTEM = """You are a trading mentor identifying knowledge gaps that caused a trading loss.
Given the trade details and lesson, identify:
1. The primary knowledge gap that led to this loss
2. Which book from the provided list would best fill that gap
3. A specific chapter or concept from that book that applies

Be specific and actionable. If no book perfectly fits, suggest the closest one.

Respond ONLY with valid JSON:
{
  "knowledge_gap": "specific concept or skill that was missing (1 sentence)",
  "gap_category": "psychology|risk_management|technical_analysis|macro|sector_analysis|timing|position_sizing|research",
  "suggested_book": "exact book title from the list",
  "suggested_author": "author name",
  "relevant_concept": "specific chapter or concept from that book",
  "urgency": "high|medium|low"
}"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS book_suggestions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id    TEXT NOT NULL,
            knowledge_gap TEXT NOT NULL,
            gap_category  TEXT NOT NULL DEFAULT '',
            book_title    TEXT NOT NULL,
            book_author   TEXT NOT NULL DEFAULT '',
            relevant_concept TEXT NOT NULL DEFAULT '',
            urgency       TEXT NOT NULL DEFAULT 'medium',
            status        TEXT NOT NULL DEFAULT 'SUGGESTED',
            created_at    TEXT NOT NULL,
            uploaded_at   TEXT,
            learned_at    TEXT
        )
    """)
    conn.commit()
    return conn


async def suggest_book_for_loss(
    trade_id: str,
    ticker: str,
    outcome: str,
    pnl_pct: float,
    what_happened: str,
    correction: str,
    knowledge_gap: str = "",
) -> Optional[dict]:
    """
    Called after a LOSS. Identifies knowledge gap + suggests best book.
    Returns suggestion dict or None if no suggestion needed.
    """
    if outcome not in ("LOSS",):
        return None  # Only suggest after losses

    # Build prompt
    books_list = "\n".join(f"- {b['title']} by {b['author']}: covers {', '.join(b['covers'])}" for b in KNOWN_BOOKS)
    prompt = f"""Trade LOSS analysis for {ticker}:
P&L: {pnl_pct:.2%}
What happened: {what_happened}
Correction needed: {correction}
Knowledge gap identified by mentor: {knowledge_gap or 'not specified'}

Available trading books:
{books_list}

Which book would fill the knowledge gap that caused this loss?"""

    raw = await call_llm("book_gap_analysis", prompt, BOOK_GAP_SYSTEM)

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
    except Exception as exc:
        logger.warning("Book suggester JSON parse failed: %s", exc)
        return None

    if not data.get("suggested_book"):
        return None

    # Check if we already suggested this book recently (avoid duplicates)
    try:
        conn = _get_conn()
        existing = conn.execute(
            "SELECT id FROM book_suggestions WHERE book_title = ? AND status != 'LEARNED' LIMIT 1",
            (data["suggested_book"],)
        ).fetchone()

        if not existing:
            conn.execute(
                """INSERT INTO book_suggestions
                   (trade_id, knowledge_gap, gap_category, book_title, book_author,
                    relevant_concept, urgency, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    trade_id,
                    data.get("knowledge_gap", ""),
                    data.get("gap_category", ""),
                    data["suggested_book"],
                    data.get("suggested_author", ""),
                    data.get("relevant_concept", ""),
                    data.get("urgency", "medium"),
                    "SUGGESTED",
                    datetime.utcnow().isoformat(),
                )
            )
            conn.commit()
            logger.info("[BookSuggester] Suggested: %s for gap: %s", data["suggested_book"], data.get("knowledge_gap", ""))

        conn.close()
    except Exception as exc:
        logger.warning("Book suggestion DB write failed: %s", exc)
        return None

    return {
        "book_title": data["suggested_book"],
        "book_author": data.get("suggested_author", ""),
        "knowledge_gap": data.get("knowledge_gap", ""),
        "relevant_concept": data.get("relevant_concept", ""),
        "urgency": data.get("urgency", "medium"),
        "status": "SUGGESTED",
    }


def get_reading_list() -> list[dict]:
    """Return all pending book suggestions (not yet LEARNED)."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, book_title, book_author, knowledge_gap, gap_category, relevant_concept, urgency, status, created_at "
            "FROM book_suggestions WHERE status != 'LEARNED' ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0], "book_title": r[1], "book_author": r[2],
                "knowledge_gap": r[3], "gap_category": r[4],
                "relevant_concept": r[5], "urgency": r[6],
                "status": r[7], "created_at": r[8],
            }
            for r in rows
        ]
    except Exception:
        return []


def mark_book_uploaded(book_title: str) -> bool:
    """Call when user uploads the PDF."""
    try:
        conn = _get_conn()
        conn.execute(
            "UPDATE book_suggestions SET status='UPLOADED', uploaded_at=? WHERE book_title=?",
            (datetime.utcnow().isoformat(), book_title)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def mark_book_learned(book_title: str) -> bool:
    """Call after PDF is ingested into graph (>10 chunks created)."""
    try:
        conn = _get_conn()
        conn.execute(
            "UPDATE book_suggestions SET status='LEARNED', learned_at=? WHERE book_title=?",
            (datetime.utcnow().isoformat(), book_title)
        )
        conn.commit()
        conn.close()
        logger.info("[BookSuggester] %s marked as LEARNED — removed from reading list", book_title)
        return True
    except Exception:
        return False
