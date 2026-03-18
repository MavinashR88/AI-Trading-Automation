"""
LLM Cost Tracker
Tracks per-call costs, enforces daily budget, stores history in SQLite.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Per-million-token pricing (input / output)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514":    {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":   {"input": 0.25,  "output": 1.25},
    "claude-haiku-4-5":            {"input": 0.25,  "output": 1.25},
    "ollama":                      {"input": 0.0,   "output": 0.0},
}

_DB_PATH = Path(__file__).parent.parent.parent / "tradesage.db"
_lock = asyncio.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_costs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            task        TEXT    NOT NULL,
            model       TEXT    NOT NULL,
            input_tok   INTEGER NOT NULL DEFAULT 0,
            output_tok  INTEGER NOT NULL DEFAULT 0,
            cost_usd    REAL    NOT NULL DEFAULT 0.0,
            day         TEXT    NOT NULL
        )
    """)
    conn.commit()
    return conn


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, MODEL_PRICING.get("claude-haiku-4-5-20251001", {"input": 0.25, "output": 1.25}))
    cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
    return round(cost, 6)


class CostTracker:
    """Thread-safe LLM cost tracker with daily budget enforcement."""

    def __init__(self):
        # In-memory daily total — refreshed on date change
        self._today: str = str(date.today())
        self._daily_total: float = 0.0
        self._call_count: int = 0
        self._loaded = False

    def _ensure_loaded(self) -> None:
        today = str(date.today())
        if self._loaded and today == self._today:
            return
        self._today = today
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT SUM(cost_usd), COUNT(*) FROM llm_costs WHERE day = ?", (today,)
            ).fetchone()
            self._daily_total = row[0] or 0.0
            self._call_count = row[1] or 0
            conn.close()
        except Exception:
            self._daily_total = 0.0
            self._call_count = 0
        self._loaded = True

    async def record(self, task: str, model: str, usage: dict) -> float:
        """Record a call. Returns the cost of this call."""
        from backend.config import settings
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        cost = _calc_cost(model, input_tok, output_tok)

        async with _lock:
            self._ensure_loaded()
            self._daily_total += cost
            self._call_count += 1

        try:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO llm_costs (ts, task, model, input_tok, output_tok, cost_usd, day) VALUES (?,?,?,?,?,?,?)",
                (datetime.utcnow().isoformat(), task, model, input_tok, output_tok, cost, self._today)
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Cost record failed: %s", exc)

        logger.debug("[LLM] task=%s model=%s in=%d out=%d cost=$%.4f total=$%.4f",
                     task, model, input_tok, output_tok, cost, self._daily_total)

        # Budget warning at 80%
        from backend.config import settings as cfg
        if self._daily_total >= cfg.LLM_DAILY_BUDGET_USD * 0.80:
            logger.warning("[BUDGET] At %.0f%% of daily budget ($%.3f / $%.2f)",
                           100 * self._daily_total / cfg.LLM_DAILY_BUDGET_USD,
                           self._daily_total, cfg.LLM_DAILY_BUDGET_USD)
        return cost

    def has_budget_remaining(self) -> bool:
        from backend.config import settings as cfg
        self._ensure_loaded()
        return self._daily_total < cfg.LLM_DAILY_BUDGET_USD

    def get_today_summary(self) -> dict:
        self._ensure_loaded()
        from backend.config import settings as cfg
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT model, COUNT(*), SUM(input_tok), SUM(output_tok), SUM(cost_usd) "
                "FROM llm_costs WHERE day = ? GROUP BY model", (self._today,)
            ).fetchall()
            conn.close()
        except Exception:
            rows = []

        by_model = {}
        for r in rows:
            by_model[r[0]] = {
                "calls": r[1], "input_tokens": r[2],
                "output_tokens": r[3], "cost_usd": round(r[4], 4)
            }

        return {
            "date": self._today,
            "total_usd": round(self._daily_total, 4),
            "budget_usd": cfg.LLM_DAILY_BUDGET_USD,
            "budget_pct": round(self._daily_total / max(cfg.LLM_DAILY_BUDGET_USD, 0.001) * 100, 1),
            "total_calls": self._call_count,
            "by_model": by_model,
            "llm_mode": cfg.LLM_MODE,
        }

    def get_history(self, days: int = 7) -> list[dict]:
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT day, SUM(cost_usd), COUNT(*) FROM llm_costs "
                "GROUP BY day ORDER BY day DESC LIMIT ?", (days,)
            ).fetchall()
            conn.close()
            return [{"date": r[0], "cost_usd": round(r[1], 4), "calls": r[2]} for r in rows]
        except Exception:
            return []


# Global singleton
cost_tracker = CostTracker()
