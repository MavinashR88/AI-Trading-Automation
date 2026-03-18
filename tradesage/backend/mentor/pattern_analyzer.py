"""
Pattern Analyzer — Weekly self-analysis of trading performance.
Runs every Sunday midnight.
Identifies winning/losing patterns, generates 3 specific rule improvements.
Stored as WeeklyAnalysis in SQLite.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "tradesage.db"

WEEKLY_SYSTEM = """You are TradeSage — a composite of the world's best traders — conducting a weekly self-analysis.

Review all trades from the past week and:
1. Identify the top 2-3 winning patterns and why they worked
2. Identify the top 2-3 losing patterns and why they failed
3. Generate exactly 3 specific, actionable rule improvements for next week
4. Assign a letter grade to this week's performance: A+ | A | B | C | D | F
5. Identify which of the 10 master traders' principles were most used and most neglected

Rules for improvements:
- Must be specific (not vague like "be more patient")
- Must be testable (you can observe whether you followed it)
- Must reference a specific pattern or condition

Respond ONLY with valid JSON:
{
  "week_ending": "YYYY-MM-DD",
  "total_trades": 0,
  "wins": 0,
  "losses": 0,
  "win_rate": 0.0,
  "total_pnl": 0.0,
  "grade": "B",
  "winning_patterns": [{"pattern": "...", "why_worked": "...", "times": 0}],
  "losing_patterns": [{"pattern": "...", "why_failed": "...", "times": 0}],
  "rule_improvements": [
    {"rule": "specific rule text", "based_on": "pattern name", "applies_when": "condition"},
    {"rule": "specific rule text", "based_on": "pattern name", "applies_when": "condition"},
    {"rule": "specific rule text", "based_on": "pattern name", "applies_when": "condition"}
  ],
  "most_used_trader": "trader name",
  "neglected_trader": "trader name",
  "key_insight": "one key insight that will improve next week"
}"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_analyses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            week_ending     TEXT NOT NULL UNIQUE,
            total_trades    INTEGER NOT NULL DEFAULT 0,
            wins            INTEGER NOT NULL DEFAULT 0,
            losses          INTEGER NOT NULL DEFAULT 0,
            win_rate        REAL NOT NULL DEFAULT 0.0,
            total_pnl       REAL NOT NULL DEFAULT 0.0,
            grade           TEXT NOT NULL DEFAULT 'C',
            rule_improvements TEXT NOT NULL DEFAULT '[]',
            winning_patterns  TEXT NOT NULL DEFAULT '[]',
            losing_patterns   TEXT NOT NULL DEFAULT '[]',
            key_insight     TEXT NOT NULL DEFAULT '',
            most_used_trader TEXT NOT NULL DEFAULT '',
            neglected_trader TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


async def run_weekly_analysis() -> dict:
    """
    Main entry: run analysis for the past 7 days.
    Uses HEAVY LLM tier (Sonnet in live mode).
    """
    conn = _get_conn()
    week_ending = datetime.utcnow().date().isoformat()

    # Load last 7 days of trades from SQLite
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    try:
        trades = conn.execute(
            """SELECT ticker, side, entry_price, exit_price, pnl_pct, pnl_dollars,
                      outcome, hold_minutes, signal_reasoning, created_at
               FROM trades
               WHERE created_at >= ? AND outcome IN ('WIN','LOSS','BREAKEVEN')
               ORDER BY created_at""",
            (seven_days_ago,)
        ).fetchall()
    except Exception:
        trades = []

    # Load this week's lessons
    try:
        lessons = conn.execute(
            "SELECT ticker, outcome, what_happened, correction, trader_principle FROM lessons WHERE created_at >= ?",
            (seven_days_ago,)
        ).fetchall()
    except Exception:
        lessons = []

    conn.close()

    if not trades:
        logger.info("[WeeklyAnalysis] No trades to analyze this week")
        return {"message": "No trades this week", "week_ending": week_ending}

    wins = sum(1 for t in trades if t[6] == "WIN")
    losses = sum(1 for t in trades if t[6] == "LOSS")
    total_pnl = sum(float(t[5] or 0) for t in trades)
    win_rate = wins / len(trades) if trades else 0.0

    trade_summary = "\n".join(
        f"- {t[0]} {t[1].upper()} ${t[2]:.2f}→${t[3] or 0:.2f} | {t[6]} {t[4] or 0:.2%} ${t[5] or 0:.2f} | hold {t[7] or 0}min"
        for t in trades[:30]
    )
    lesson_summary = "\n".join(
        f"- {l[0]} {l[1]}: {l[2][:100]} | Fix: {l[3][:80]} | Trader: {l[4]}"
        for l in lessons[:20]
    ) or "No lessons recorded this week."

    prompt = f"""Weekly trading analysis — week ending {week_ending}

TRADE SUMMARY ({len(trades)} trades, {wins} wins, {losses} losses, {win_rate:.0%} win rate, ${total_pnl:+.2f} P&L):
{trade_summary}

MENTOR LESSONS FROM THIS WEEK:
{lesson_summary}

Analyze this week's performance and generate 3 specific rule improvements."""

    raw = await call_llm("weekly_analysis", prompt, WEEKLY_SYSTEM)

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
    except Exception as exc:
        logger.warning("Weekly analysis JSON parse failed: %s", exc)
        data = {}

    # Store in SQLite
    result = {
        "week_ending": week_ending,
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "grade": data.get("grade", "C"),
        "rule_improvements": data.get("rule_improvements", []),
        "winning_patterns": data.get("winning_patterns", []),
        "losing_patterns": data.get("losing_patterns", []),
        "key_insight": data.get("key_insight", ""),
        "most_used_trader": data.get("most_used_trader", ""),
        "neglected_trader": data.get("neglected_trader", ""),
    }

    try:
        conn2 = _get_conn()
        conn2.execute(
            """INSERT OR REPLACE INTO weekly_analyses
               (week_ending, total_trades, wins, losses, win_rate, total_pnl, grade,
                rule_improvements, winning_patterns, losing_patterns, key_insight,
                most_used_trader, neglected_trader, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                week_ending, len(trades), wins, losses, win_rate, total_pnl,
                result["grade"],
                json.dumps(result["rule_improvements"]),
                json.dumps(result["winning_patterns"]),
                json.dumps(result["losing_patterns"]),
                result["key_insight"],
                result["most_used_trader"],
                result["neglected_trader"],
                datetime.utcnow().isoformat(),
            )
        )
        conn2.commit()
        conn2.close()
        logger.info("[WeeklyAnalysis] Week %s: %d trades, %s grade, %+.2f P&L",
                    week_ending, len(trades), result["grade"], total_pnl)
    except Exception as exc:
        logger.error("Weekly analysis DB write failed: %s", exc)

    return result


def get_weekly_reports(limit: int = 4) -> list[dict]:
    """Return the last N weekly analysis reports."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT week_ending, total_trades, wins, losses, win_rate, total_pnl,
                      grade, rule_improvements, winning_patterns, losing_patterns,
                      key_insight, most_used_trader, neglected_trader, created_at
               FROM weekly_analyses ORDER BY week_ending DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append({
                "week_ending": r[0], "total_trades": r[1], "wins": r[2],
                "losses": r[3], "win_rate": r[4], "total_pnl": r[5],
                "grade": r[6],
                "rule_improvements": json.loads(r[7] or "[]"),
                "winning_patterns": json.loads(r[8] or "[]"),
                "losing_patterns": json.loads(r[9] or "[]"),
                "key_insight": r[10], "most_used_trader": r[11],
                "neglected_trader": r[12], "created_at": r[13],
            })
        return result
    except Exception:
        return []
