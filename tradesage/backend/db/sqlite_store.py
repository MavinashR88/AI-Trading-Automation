"""
SQLite persistent store using SQLAlchemy async.
Tables: trades, lessons, review_notes, news_events, win_rate_snapshots
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Any, List, Optional

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text, JSON,
    select, func, desc, update
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from backend.models.trade import TradeResult, ProbabilityScore, ReviewNote, TradeDetail
from backend.models.lesson import Lesson
from backend.models.signal import NewsSignal

logger = logging.getLogger(__name__)

# Database path
DB_PATH = Path(__file__).parent.parent.parent / "tradesage.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"


class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────
# ORM Tables
# ──────────────────────────────────────────────

class TradeRow(Base):
    __tablename__ = "trades"

    trade_id = Column(String, primary_key=True)
    ticker = Column(String, nullable=False, index=True)
    market_type = Column(String, nullable=False)
    side = Column(String, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    quantity = Column(Float, nullable=False)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    filled_at = Column(DateTime)
    closed_at = Column(DateTime)
    pnl_dollars = Column(Float)
    pnl_pct = Column(Float)
    outcome = Column(String)
    hold_minutes = Column(Integer)
    mode = Column(String, default="paper")
    order_id = Column(String)
    signal_reasoning = Column(Text)
    signal_confidence = Column(Float)
    risk_params_json = Column(JSON)
    probability_score_json = Column(JSON)
    review_note_json = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class LessonRow(Base):
    __tablename__ = "lessons"

    lesson_id = Column(String, primary_key=True)
    trade_id = Column(String, nullable=False, index=True)
    ticker = Column(String)
    outcome = Column(String, nullable=False)
    trader_principle = Column(String)
    principle_quote = Column(Text)
    what_happened = Column(Text)
    correction = Column(Text)
    confidence_adjustment = Column(Float)
    consecutive_wins = Column(Integer, default=0)
    win_rate = Column(Float)
    pnl_pct = Column(Float)
    pnl_dollars = Column(Float)
    book_reference = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)


class ReviewNoteRow(Base):
    __tablename__ = "review_notes"

    review_id = Column(String, primary_key=True)
    trade_id = Column(String, nullable=False, index=True)
    decision = Column(String, nullable=False)
    trader_voice = Column(String)
    reasoning = Column(Text)
    news_alignment = Column(String)
    news_catalyst = Column(Text)
    price_vs_news = Column(Text)
    confidence_score = Column(Float)
    book_reference = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)


class NewsEventRow(Base):
    __tablename__ = "news_events"

    event_id = Column(String, primary_key=True)
    ticker = Column(String, index=True)
    headline = Column(Text, nullable=False)
    source = Column(String)
    url = Column(Text)
    sentiment_score = Column(Float)
    urgency = Column(String)
    catalyst = Column(Text)
    age_minutes = Column(Integer)
    breaking_override = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class WinRateSnapshotRow(Base):
    __tablename__ = "win_rate_snapshots"

    snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(DateTime, default=datetime.utcnow)
    rolling_100_win_rate = Column(Float)
    total_trades = Column(Integer)
    consecutive_wins = Column(Integer)
    total_pnl_dollars = Column(Float)


# ──────────────────────────────────────────────
# Engine + session factory
# ──────────────────────────────────────────────

_engine = create_async_engine(DATABASE_URL, echo=False)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables if they don't exist."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("SQLite database initialized at %s", DB_PATH)


def get_session() -> async_sessionmaker:
    return _session_factory


# ──────────────────────────────────────────────
# CRUD helpers
# ──────────────────────────────────────────────

class TradeStore:
    """High-level async CRUD for trades + lessons + news."""

    def __init__(self, session_factory: async_sessionmaker = _session_factory):
        self._sf = session_factory

    # ── Trades ──────────────────────────────────────────────────────────────

    async def save_trade(self, detail: TradeDetail) -> None:
        async with self._sf() as session:
            # Use actual fill price/qty from trade result; fall back to signal values
            actual_qty = (
                detail.trade_result.quantity
                if detail.trade_result and detail.trade_result.quantity
                else (detail.risk_params.position_size / detail.signal.entry_price
                      if detail.signal.entry_price else 0)
            )
            actual_entry = (
                detail.trade_result.entry_price
                if detail.trade_result and detail.trade_result.entry_price
                else detail.signal.entry_price
            )
            row = TradeRow(
                trade_id=detail.trade_id,
                ticker=detail.ticker,
                market_type=detail.market_type,
                side=detail.signal.action,
                entry_price=actual_entry,
                quantity=actual_qty,
                stop_loss=detail.risk_params.stop_loss,
                take_profit=detail.risk_params.take_profit,
                mode=detail.mode,
                signal_reasoning=detail.signal.reasoning,
                signal_confidence=detail.signal.confidence,
                risk_params_json=detail.risk_params.model_dump(),
                probability_score_json=detail.probability_score.model_dump(),
                review_note_json=detail.review_note.model_dump(mode="json"),
                order_id=detail.trade_result.order_id if detail.trade_result else None,
                created_at=detail.created_at,
                updated_at=detail.updated_at,
                outcome="OPEN",
            )
            session.add(row)
            await session.commit()
        logger.debug("Saved trade %s for %s", detail.trade_id, detail.ticker)

    async def update_trade_fill(self, trade_id: str, quantity: float, entry_price: float, order_id: str = None) -> None:
        """Update an open trade with actual Alpaca fill data (quantity, price, order_id)."""
        async with self._sf() as session:
            vals = {"quantity": quantity, "entry_price": entry_price, "updated_at": datetime.utcnow()}
            if order_id:
                vals["order_id"] = order_id
            await session.execute(
                update(TradeRow)
                .where(TradeRow.trade_id == trade_id)
                .values(**vals)
            )
            await session.commit()

    async def update_trade_result(self, result: TradeResult) -> None:
        async with self._sf() as session:
            await session.execute(
                update(TradeRow)
                .where(TradeRow.trade_id == result.trade_id)
                .values(
                    exit_price=result.exit_price,
                    pnl_dollars=result.pnl_dollars,
                    pnl_pct=result.pnl_pct,
                    outcome=result.outcome,
                    hold_minutes=result.hold_minutes,
                    closed_at=result.closed_at,
                    order_id=result.order_id,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()

    async def get_trade(self, trade_id: str) -> Optional[dict]:
        async with self._sf() as session:
            result = await session.execute(
                select(TradeRow).where(TradeRow.trade_id == trade_id)
            )
            row = result.scalar_one_or_none()
            return _row_to_dict(row) if row else None

    async def get_trades(
        self,
        limit: int = 50,
        offset: int = 0,
        ticker: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> list[dict]:
        async with self._sf() as session:
            q = select(TradeRow).order_by(desc(TradeRow.created_at))
            if ticker:
                q = q.where(TradeRow.ticker == ticker)
            if outcome:
                q = q.where(TradeRow.outcome == outcome)
            q = q.limit(limit).offset(offset)
            result = await session.execute(q)
            return [_row_to_dict(r) for r in result.scalars().all()]

    async def get_open_trades(self) -> list[dict]:
        """Return all trades with outcome=OPEN (positions not yet closed)."""
        async with self._sf() as session:
            result = await session.execute(
                select(TradeRow)
                .where(TradeRow.outcome == "OPEN")
                .order_by(desc(TradeRow.created_at))
            )
            return [_row_to_dict(r) for r in result.scalars().all()]

    async def get_recent_closed_trades(self, n: int = 100) -> list[dict]:
        async with self._sf() as session:
            result = await session.execute(
                select(TradeRow)
                .where(TradeRow.outcome.in_(["WIN", "LOSS", "BREAKEVEN"]))
                .order_by(desc(TradeRow.closed_at))
                .limit(n)
            )
            return [_row_to_dict(r) for r in result.scalars().all()]

    # ── Lessons ─────────────────────────────────────────────────────────────

    async def save_lesson(self, lesson: Lesson) -> None:
        async with self._sf() as session:
            row = LessonRow(
                lesson_id=lesson.lesson_id,
                trade_id=lesson.trade_id,
                ticker=lesson.ticker,
                outcome=lesson.outcome,
                trader_principle=lesson.trader_principle,
                principle_quote=lesson.principle_quote,
                what_happened=lesson.what_happened,
                correction=lesson.correction,
                confidence_adjustment=lesson.confidence_adjustment,
                consecutive_wins=lesson.consecutive_wins,
                win_rate=lesson.win_rate,
                pnl_pct=lesson.pnl_pct,
                pnl_dollars=lesson.pnl_dollars,
                book_reference=lesson.book_reference,
                timestamp=lesson.timestamp,
            )
            session.add(row)
            await session.commit()

    async def get_lessons(self, limit: int = 20, offset: int = 0) -> list[dict]:
        async with self._sf() as session:
            result = await session.execute(
                select(LessonRow).order_by(desc(LessonRow.timestamp)).limit(limit).offset(offset)
            )
            return [_row_to_dict(r) for r in result.scalars().all()]

    # ── Review Notes ─────────────────────────────────────────────────────────

    async def save_review_note(self, note: ReviewNote) -> None:
        import uuid
        async with self._sf() as session:
            row = ReviewNoteRow(
                review_id=str(uuid.uuid4()),
                trade_id=note.trade_id,
                decision=note.decision,
                trader_voice=note.trader_voice,
                reasoning=note.reasoning,
                news_alignment=note.news_alignment,
                news_catalyst=note.news_catalyst,
                price_vs_news=note.price_vs_news,
                confidence_score=note.confidence_score,
                book_reference=note.book_reference,
                timestamp=note.timestamp,
            )
            session.add(row)
            await session.commit()

    async def get_review_notes(self, limit: int = 20, offset: int = 0) -> list[dict]:
        async with self._sf() as session:
            result = await session.execute(
                select(ReviewNoteRow)
                .order_by(desc(ReviewNoteRow.timestamp))
                .limit(limit)
                .offset(offset)
            )
            return [_row_to_dict(r) for r in result.scalars().all()]

    # ── News Events ──────────────────────────────────────────────────────────

    async def save_news_event(self, news: NewsSignal) -> None:
        async with self._sf() as session:
            row = NewsEventRow(
                event_id=news.signal_id,
                ticker=news.ticker,
                headline=news.headline,
                source=news.source,
                url=news.url,
                sentiment_score=news.sentiment_score,
                urgency=news.urgency,
                catalyst=news.catalyst,
                age_minutes=news.age_minutes,
                breaking_override=news.breaking_override,
                timestamp=news.timestamp,
            )
            session.add(row)
            await session.commit()

    async def get_news_for_ticker(self, ticker: str, limit: int = 20) -> list[dict]:
        async with self._sf() as session:
            result = await session.execute(
                select(NewsEventRow)
                .where(NewsEventRow.ticker == ticker)
                .order_by(desc(NewsEventRow.timestamp))
                .limit(limit)
            )
            return [_row_to_dict(r) for r in result.scalars().all()]

    # ── Win Rate ──────────────────────────────────────────────────────────────

    async def compute_win_rate(self) -> dict:
        """Compute rolling 100-trade win rate."""
        async with self._sf() as session:
            result = await session.execute(
                select(TradeRow)
                .where(TradeRow.outcome.in_(["WIN", "LOSS", "BREAKEVEN"]))
                .order_by(desc(TradeRow.closed_at))
                .limit(100)
            )
            rows = result.scalars().all()

        if not rows:
            return {
                "win_rate": 0.0,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "consecutive_wins": 0,
                "total_pnl_dollars": 0.0,
            }

        wins = sum(1 for r in rows if r.outcome == "WIN")
        losses = sum(1 for r in rows if r.outcome == "LOSS")
        total = len(rows)

        # Consecutive wins from most recent
        consec = 0
        for r in rows:
            if r.outcome == "WIN":
                consec += 1
            else:
                break

        total_pnl = sum(r.pnl_dollars or 0.0 for r in rows)

        data = {
            "win_rate": wins / total if total else 0.0,
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "consecutive_wins": consec,
            "total_pnl_dollars": total_pnl,
        }

        # Only save snapshot if total_trades changed since last snapshot
        async with self._sf() as session:
            last = await session.execute(
                select(WinRateSnapshotRow)
                .order_by(desc(WinRateSnapshotRow.snapshot_date))
                .limit(1)
            )
            last_row = last.scalars().first()
            if not last_row or last_row.total_trades != total:
                snap = WinRateSnapshotRow(
                    rolling_100_win_rate=data["win_rate"],
                    total_trades=total,
                    consecutive_wins=consec,
                    total_pnl_dollars=total_pnl,
                )
                session.add(snap)
                await session.commit()

        return data

    async def get_win_rate_history(self, limit: int = 100) -> list[dict]:
        async with self._sf() as session:
            result = await session.execute(
                select(WinRateSnapshotRow)
                .order_by(desc(WinRateSnapshotRow.snapshot_date))
                .limit(limit)
            )
            return [_row_to_dict(r) for r in result.scalars().all()]

    # ── Per-ticker Stats (for scan scoring) ──────────────────────────────────

    async def get_ticker_stats(self, ticker: str) -> dict:
        """Return win rate + last trade direction/outcome for a ticker."""
        async with self._sf() as session:
            result = await session.execute(
                select(TradeRow)
                .where(
                    TradeRow.ticker == ticker,
                    TradeRow.outcome.in_(["WIN", "LOSS", "BREAKEVEN"]),
                )
                .order_by(desc(TradeRow.closed_at))
                .limit(20)
            )
            rows = result.scalars().all()

        if not rows:
            return {"win_rate": 0.5, "total_trades": 0, "last_action": "", "last_outcome": ""}

        wins = sum(1 for r in rows if r.outcome == "WIN")
        total = len(rows)
        last = rows[0]
        return {
            "win_rate": wins / total,
            "total_trades": total,
            "last_action": last.side or "",
            "last_outcome": last.outcome or "",
        }

    # ── Portfolio ─────────────────────────────────────────────────────────────

    async def get_portfolio_summary(self, starting_capital: float) -> dict:
        async with self._sf() as session:
            # Total realised P&L
            result = await session.execute(
                select(func.sum(TradeRow.pnl_dollars))
                .where(TradeRow.outcome.in_(["WIN", "LOSS", "BREAKEVEN"]))
            )
            realised_pnl = result.scalar() or 0.0

            # Open positions
            result2 = await session.execute(
                select(TradeRow).where(TradeRow.outcome == "OPEN")
            )
            open_trades = result2.scalars().all()

        portfolio_value = starting_capital + realised_pnl
        return {
            "portfolio_value": portfolio_value,
            "starting_capital": starting_capital,
            "realised_pnl": realised_pnl,
            "pnl_pct": (realised_pnl / starting_capital * 100) if starting_capital else 0.0,
            "open_positions": len(open_trades),
            "open_trades": [_row_to_dict(r) for r in open_trades],
        }


def _row_to_dict(row) -> dict:
    """Convert an ORM row to a plain dict."""
    if row is None:
        return {}
    d = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        if isinstance(val, datetime):
            val = val.isoformat()
        d[col.name] = val
    return d


# ── Singleton ────────────────────────────────────────────────────────────────
_store: Optional[TradeStore] = None


def get_store() -> TradeStore:
    global _store
    if _store is None:
        _store = TradeStore()
    return _store
