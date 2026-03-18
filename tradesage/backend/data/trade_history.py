"""
Trade history helpers — thin wrapper around the SQLite store.
"""
from __future__ import annotations

from backend.db.sqlite_store import get_store


async def get_all_trades(limit: int = 100, offset: int = 0) -> list[dict]:
    return await get_store().get_trades(limit=limit, offset=offset)


async def get_trade_by_id(trade_id: str) -> dict:
    return await get_store().get_trade(trade_id) or {}


async def get_recent_returns(n: int = 100) -> list[float]:
    """Returns list of pnl_pct values for the last N closed trades."""
    trades = await get_store().get_recent_closed_trades(n)
    return [t.get("pnl_pct", 0.0) or 0.0 for t in trades if t.get("pnl_pct") is not None]


async def get_outcomes(n: int = 100) -> list[str]:
    """Returns list of outcome strings for the last N closed trades."""
    trades = await get_store().get_recent_closed_trades(n)
    return [t.get("outcome", "LOSS") for t in trades]
