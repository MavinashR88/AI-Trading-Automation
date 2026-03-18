"""
DataRouter — single access point for all persistence.
  PostgreSQL: reporting, trading history, pipeline state
  Neo4j:      causal reasoning, pattern graph
  Bridge:     trade_id links both stores

Currently backed by SQLite (via sqlite_store.py) with Neo4j alongside.
Swap database.py for real PostgreSQL without changing callers.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from backend.db.sqlite_store import TradeStore, init_db

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "tradesage.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    # Handle numpy scalars without importing numpy as hard dependency.
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _ensure_pipeline_tables():
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS discovered_stocks (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            company_name TEXT DEFAULT '',
            sector TEXT DEFAULT '',
            discovery_reason TEXT NOT NULL,
            discovery_score REAL DEFAULT 0,
            volume_ratio REAL DEFAULT 1,
            market_cap REAL DEFAULT 0,
            price REAL DEFAULT 0,
            short_interest_pct REAL DEFAULT 0,
            status TEXT DEFAULT 'DISCOVERED',
            discovered_at TEXT NOT NULL,
            data_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS research_reports (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            research_verdict TEXT DEFAULT 'NEUTRAL',
            verdict_confidence REAL DEFAULT 0,
            suggested_strategy TEXT DEFAULT '',
            data_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trading_algorithms (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            name TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            status TEXT DEFAULT 'DRAFT',
            paper_trades_done INTEGER DEFAULT 0,
            paper_trades_required INTEGER DEFAULT 10,
            backtest_win_rate REAL DEFAULT 0,
            backtest_sharpe REAL DEFAULT 0,
            backtest_max_drawdown_pct REAL DEFAULT 0,
            scenarios_passed INTEGER DEFAULT 0,
            paper_win_rate REAL DEFAULT 0,
            paper_pnl_pct REAL DEFAULT 0,
            data_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            deployed_at TEXT,
            retired_at TEXT,
            retire_reason TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS validation_results (
            id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            all_passed INTEGER DEFAULT 0,
            pass_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            overall_verdict TEXT DEFAULT 'PENDING',
            rejection_reason TEXT DEFAULT '',
            data_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS deployed_algorithms (
            id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            live_trades INTEGER DEFAULT 0,
            live_win_rate REAL DEFAULT 0,
            live_pnl_pct REAL DEFAULT 0,
            deployed_at TEXT NOT NULL,
            last_reviewed_at TEXT,
            retired_at TEXT,
            retire_reason TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS pipeline_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            ticker TEXT,
            algorithm_id TEXT,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
    """)
    c.commit()
    c.close()


_ensure_pipeline_tables()


def _ensure_extra_columns():
    """Add columns that were added after initial schema creation."""
    c = _conn()
    try:
        c.execute("ALTER TABLE trading_algorithms ADD COLUMN sim_retry_count INTEGER DEFAULT 0")
        c.commit()
        logger.info("[DB] Added sim_retry_count column to trading_algorithms")
    except Exception:
        pass  # Column already exists
    try:
        c.execute("ALTER TABLE trading_algorithms ADD COLUMN backtest_profit_factor REAL DEFAULT 0")
        c.commit()
        logger.info("[DB] Added backtest_profit_factor column to trading_algorithms")
    except Exception:
        pass
    c.close()


_ensure_extra_columns()


class DataRouter:
    """Thin facade — all reads/writes go through here."""

    def __init__(self):
        self._store = TradeStore()

    # ── Trades (delegate to existing SQLite store) ──────────────

    async def get_trades(self, limit: int = 50, ticker: str = None, outcome: str = None) -> list[dict]:
        trades = await self._store.get_trades(limit=limit)
        if ticker:
            trades = [t for t in trades if t.get("ticker") == ticker]
        if outcome:
            trades = [t for t in trades if t.get("outcome") == outcome]
        return trades

    async def get_open_trades(self) -> list[dict]:
        return await self._store.get_open_trades()

    async def get_win_rate(self, last_n: int = 100) -> dict:
        return await self._store.compute_win_rate()

    async def get_portfolio_stats(self) -> dict:
        wr = await self._store.compute_win_rate()
        trades = await self._store.get_trades(limit=500)
        closed = [t for t in trades if t.get("outcome") not in ("OPEN", None)]
        pnl_values = [t.get("pnl_pct", 0) for t in closed if t.get("pnl_pct") is not None]

        import statistics
        sharpe = 0.0
        if len(pnl_values) > 2:
            avg = statistics.mean(pnl_values)
            std = statistics.stdev(pnl_values) or 1
            sharpe = round(avg / std * (252 ** 0.5), 2)

        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p < 0]
        profit_factor = (
            sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0.0
        )
        return {
            "total_trades": wr.get("total_trades", 0),
            "win_rate": wr.get("win_rate", 0),
            "realised_pnl": wr.get("realised_pnl", 0),
            "sharpe_ratio": sharpe,
            "profit_factor": round(profit_factor, 2),
            "best_trade_pct": max(pnl_values, default=0),
            "worst_trade_pct": min(pnl_values, default=0),
        }

    # ── Pipeline persistence ─────────────────────────────────────

    # Status progression order — never downgrade a stock
    _STATUS_ORDER = ["DISCOVERED", "RESEARCHED", "ALGO_BUILT", "VALIDATING", "LIVE"]

    def save_discovered_stock(self, stock: dict) -> str:
        sid = stock.get("id") or str(uuid.uuid4())
        new_status = stock.get("status", "DISCOVERED")
        c = _conn()

        # Check if stock already exists at a higher pipeline stage
        row = c.execute(
            "SELECT id, status FROM discovered_stocks WHERE ticker=?", (stock["ticker"],)
        ).fetchone()

        if row:
            existing_status = row["status"]
            existing_id = row["id"]
            # Preserve the higher status — never downgrade
            order = self._STATUS_ORDER
            if order.index(existing_status) if existing_status in order else 0 > \
               order.index(new_status) if new_status in order else 0:
                new_status = existing_status
            sid = existing_id  # keep the same id

        c.execute(
            """INSERT OR REPLACE INTO discovered_stocks
               (id, ticker, company_name, sector, discovery_reason,
                discovery_score, volume_ratio, market_cap, price,
                short_interest_pct, status, discovered_at, data_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, stock["ticker"], stock.get("company_name", ""),
             stock.get("sector", ""), stock.get("discovery_reason", ""),
             stock.get("discovery_score", 0), stock.get("volume_ratio", 1),
             stock.get("market_cap", 0), stock.get("price", 0),
             stock.get("short_interest_pct", 0),
             new_status,
             datetime.utcnow().isoformat(),
             _json_dumps({k: v for k, v in stock.items() if k not in (
                 "id","ticker","company_name","sector","discovery_reason",
                 "discovery_score","volume_ratio","market_cap","price",
                 "short_interest_pct","status","discovered_at")})),
        )
        c.commit()
        c.close()
        return sid

    def get_discovered_stocks(self, status: str = None, limit: int = 50) -> list[dict]:
        c = _conn()
        if status:
            rows = c.execute(
                "SELECT * FROM discovered_stocks WHERE status=? ORDER BY discovery_score DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM discovered_stocks ORDER BY discovered_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        c.close()
        result = []
        for r in rows:
            d = dict(r)
            d.pop("data_json", None)   # don't leak raw JSON column to API consumers
            result.append(d)
        return result

    def update_stock_status(self, ticker: str, status: str):
        c = _conn()
        c.execute("UPDATE discovered_stocks SET status=? WHERE ticker=?", (status, ticker))
        c.commit()
        c.close()

    def save_algorithm(self, algo: dict) -> str:
        aid = algo.get("id") or str(uuid.uuid4())
        c = _conn()
        c.execute(
            """INSERT OR REPLACE INTO trading_algorithms
               (id, ticker, name, strategy_type, status,
                paper_trades_done, paper_trades_required,
                backtest_win_rate, backtest_sharpe, backtest_max_drawdown_pct,
                scenarios_passed, paper_win_rate, paper_pnl_pct,
                data_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, algo["ticker"], algo.get("name",""),
             algo.get("strategy_type",""), algo.get("status","DRAFT"),
             algo.get("paper_trades_done",0), algo.get("paper_trades_required",10),
             algo.get("backtest_win_rate",0), algo.get("backtest_sharpe",0),
             algo.get("backtest_max_drawdown_pct",0),
             algo.get("scenarios_passed",0),
             algo.get("paper_win_rate",0), algo.get("paper_pnl_pct",0),
             _json_dumps(algo), datetime.utcnow().isoformat()),
        )
        c.commit()
        c.close()
        return aid

    def get_algorithms(self, status: str = None, ticker: str = None, limit: int = 100) -> list[dict]:
        c = _conn()
        conds, params = [], []
        if status:
            conds.append("status=?"); params.append(status)
        if ticker:
            conds.append("ticker=?"); params.append(ticker)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = c.execute(
            f"SELECT * FROM trading_algorithms {where} ORDER BY created_at DESC LIMIT ?",
            [*params, limit]
        ).fetchall()
        c.close()
        result = []
        for r in rows:
            d = dict(r)
            # Save ALL authoritative DB columns before merging data_json (which has stale values from creation time)
            db_cols = {
                "id": d.get("id"),
                "status": d.get("status"),
                "ticker": d.get("ticker"),
                "paper_trades_done": d.get("paper_trades_done"),
                "paper_trades_required": d.get("paper_trades_required"),
                "paper_win_rate": d.get("paper_win_rate"),
                "paper_pnl_pct": d.get("paper_pnl_pct"),
                "backtest_win_rate": d.get("backtest_win_rate"),
                "backtest_sharpe": d.get("backtest_sharpe"),
                "backtest_max_drawdown_pct": d.get("backtest_max_drawdown_pct"),
                "backtest_profit_factor": d.get("backtest_profit_factor"),
                "scenarios_passed": d.get("scenarios_passed"),
                "sim_retry_count": d.get("sim_retry_count", 0),
            }
            try:
                d.update(json.loads(d.pop("data_json", "{}")))
            except Exception:
                pass
            # Restore authoritative DB columns — always wins over stale data_json
            for col, val in db_cols.items():
                if val is not None:
                    d[col] = val
            result.append(d)
        return result

    def update_algorithm_status(self, algo_id: str, status: str, **kwargs):
        c = _conn()
        sets = ["status=?"]
        vals = [status]
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(algo_id)
        c.execute(f"UPDATE trading_algorithms SET {', '.join(sets)} WHERE id=?", vals)
        c.commit()
        c.close()

    def save_validation_result(self, result: dict) -> str:
        rid = result.get("id") or str(uuid.uuid4())
        c = _conn()
        c.execute(
            """INSERT OR REPLACE INTO validation_results
               (id, algorithm_id, ticker, all_passed, pass_count, fail_count,
                overall_verdict, rejection_reason, data_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rid, result["algorithm_id"], result.get("ticker",""),
             int(result.get("all_passed", False)),
             result.get("pass_count",0), result.get("fail_count",0),
             result.get("overall_verdict","PENDING"),
             result.get("rejection_reason",""),
             _json_dumps(result), datetime.utcnow().isoformat()),
        )
        c.commit()
        c.close()
        return rid

    def deploy_algorithm(self, algo: dict) -> str:
        did = str(uuid.uuid4())
        c = _conn()
        c.execute(
            """INSERT INTO deployed_algorithms
               (id, algorithm_id, ticker, name, strategy_type,
                is_active, deployed_at)
               VALUES (?,?,?,?,?,1,?)""",
            (did, algo["id"], algo["ticker"], algo.get("name",""),
             algo.get("strategy_type",""),
             datetime.utcnow().isoformat()),
        )
        c.commit()
        c.close()
        return did

    def get_deployed_algorithms(self, active_only: bool = True) -> list[dict]:
        c = _conn()
        where = "WHERE is_active=1" if active_only else ""
        rows = c.execute(
            f"SELECT * FROM deployed_algorithms {where} ORDER BY deployed_at DESC"
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]

    def retire_algorithm(self, algo_id: str, reason: str = ""):
        """Retire an algorithm and deactivate any live deployment."""
        now = datetime.utcnow().isoformat()
        c = _conn()
        c.execute(
            "UPDATE trading_algorithms SET status=?, retire_reason=?, retired_at=? WHERE id=?",
            ("RETIRED", reason, now, algo_id),
        )
        c.execute(
            "UPDATE deployed_algorithms SET is_active=0, retired_at=?, retire_reason=? WHERE algorithm_id=?",
            (now, reason, algo_id),
        )
        c.commit()
        c.close()

    def log_pipeline_event(self, event_type: str, stage: str, status: str,
                           ticker: str = None, algorithm_id: str = None, detail: str = ""):
        c = _conn()
        c.execute(
            """INSERT INTO pipeline_events
               (event_type, ticker, algorithm_id, stage, status, detail, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (event_type, ticker, algorithm_id, stage, status, detail,
             datetime.utcnow().isoformat()),
        )
        c.commit()
        c.close()

    def get_pipeline_events(self, limit: int = 50) -> list[dict]:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM pipeline_events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]


# ── Singleton ────────────────────────────────────────────────────

_router: Optional[DataRouter] = None

async def get_data_router() -> DataRouter:
    global _router
    if _router is None:
        await init_db()
        _router = DataRouter()
    return _router
