"""
Graph Updater — Wires new trades and lessons into the Neo4j graph after each trade.
All Cypher uses MERGE for idempotency.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from neo4j import Driver

from backend.models.trade import TradeResult, ReviewNote
from backend.models.lesson import Lesson
from backend.models.signal import NewsSignal

logger = logging.getLogger(__name__)


class GraphUpdater:
    """Post-trade graph wiring."""

    def __init__(self, driver: Driver):
        self._driver = driver

    def _run(self, cypher: str, params: dict) -> list[dict]:
        try:
            with self._driver.session() as session:
                result = session.run(cypher, params)
                return [record.data() for record in result]
        except Exception as exc:
            logger.error("GraphUpdater Cypher error: %s | %s", cypher[:80], exc)
            return []

    # ── Trade Node ────────────────────────────────────────────────────────────

    def upsert_trade(
        self,
        trade_id: str,
        ticker: str,
        side: str,
        entry_price: float,
        exit_price: Optional[float],
        pnl_pct: Optional[float],
        outcome: str,
        hold_minutes: Optional[int],
        probability_score: float,
        pattern_name: str,
        principle_name: str,
        mode: str = "paper",
    ) -> None:
        self._run(
            """
            MERGE (t:Trade {trade_id: $trade_id})
            SET t.ticker = $ticker,
                t.side = $side,
                t.entry_price = $entry_price,
                t.exit_price = $exit_price,
                t.pnl_pct = $pnl_pct,
                t.outcome = $outcome,
                t.hold_minutes = $hold_minutes,
                t.probability_score = $probability_score,
                t.mode = $mode,
                t.updated_at = datetime()
            WITH t
            MATCH (c:Company {ticker: $ticker})
            MERGE (t)-[:FOR_COMPANY]->(c)
            WITH t
            OPTIONAL MATCH (p:MarketPattern {name: $pattern_name})
            FOREACH (p IN CASE WHEN p IS NOT NULL THEN [p] ELSE [] END |
                MERGE (t)-[:FOLLOWED_PATTERN]->(p)
                SET p.times_seen = COALESCE(p.times_seen, 0) + 1
            )
            WITH t
            OPTIONAL MATCH (pr:TraderPrinciple {principle_name: $principle_name})
            FOREACH (pr IN CASE WHEN pr IS NOT NULL THEN [pr] ELSE [] END |
                MERGE (t)-[:APPLIED_PRINCIPLE]->(pr)
            )
            """,
            {
                "trade_id": trade_id,
                "ticker": ticker,
                "side": side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "outcome": outcome,
                "hold_minutes": hold_minutes,
                "probability_score": probability_score,
                "pattern_name": pattern_name,
                "principle_name": principle_name,
                "mode": mode,
            },
        )
        logger.debug("Graph: upserted Trade %s", trade_id)

    # ── Lesson Node ───────────────────────────────────────────────────────────

    def upsert_lesson(self, lesson: Lesson, trade_id: str, pattern_name: str) -> None:
        self._run(
            """
            MERGE (l:Lesson {lesson_id: $lesson_id})
            SET l.outcome = $outcome,
                l.correction = $correction,
                l.trader_voice = $trader_voice,
                l.book_reference = $book_reference,
                l.confidence_adjustment = $confidence_adjustment,
                l.timestamp = datetime($timestamp)
            WITH l
            MATCH (t:Trade {trade_id: $trade_id})
            MERGE (t)-[:GENERATED]->(l)
            WITH l
            MATCH (mentor:Mentor {name: 'TradeSage Mentor'})
            MERGE (mentor)-[:LEARNED_FROM]->(l)
            WITH l
            OPTIONAL MATCH (p:MarketPattern {name: $pattern_name})
            FOREACH (p IN CASE WHEN p IS NOT NULL THEN [p] ELSE [] END |
                MERGE (l)-[:CORRECTS_PATTERN]->(p)
            )
            """,
            {
                "lesson_id": lesson.lesson_id,
                "outcome": lesson.outcome,
                "correction": lesson.correction,
                "trader_voice": lesson.trader_principle,
                "book_reference": lesson.book_reference,
                "confidence_adjustment": lesson.confidence_adjustment,
                "timestamp": lesson.timestamp.isoformat(),
                "trade_id": trade_id,
                "pattern_name": pattern_name,
            },
        )
        logger.debug("Graph: upserted Lesson %s", lesson.lesson_id)

    def strengthen_pattern(self, lesson_id: str, pattern_name: str, outcome: str) -> None:
        """Add STRENGTHENS_PATTERN or CORRECTS_PATTERN relationship based on outcome."""
        if outcome == "WIN":
            rel = "STRENGTHENS_PATTERN"
        else:
            rel = "CORRECTS_PATTERN"

        self._run(
            f"""
            MATCH (l:Lesson {{lesson_id: $lesson_id}}),
                  (p:MarketPattern {{name: $pattern_name}})
            MERGE (l)-[r:{rel}]->(p)
            SET r.weight = COALESCE(r.weight, 0) + 1
            """,
            {"lesson_id": lesson_id, "pattern_name": pattern_name},
        )

    # ── News Event Node ───────────────────────────────────────────────────────

    def upsert_news_event(self, news: NewsSignal) -> None:
        self._run(
            """
            MERGE (n:NewsEvent {event_id: $event_id})
            SET n.headline = $headline,
                n.source = $source,
                n.url = $url,
                n.ticker = $ticker,
                n.sentiment_score = $sentiment_score,
                n.urgency = $urgency,
                n.catalyst = $catalyst,
                n.age_minutes = $age_minutes,
                n.timestamp = datetime($timestamp)
            WITH n
            OPTIONAL MATCH (c:Company {ticker: $ticker})
            FOREACH (c IN CASE WHEN c IS NOT NULL THEN [c] ELSE [] END |
                MERGE (n)-[:MENTIONS]->(c)
            )
            """,
            {
                "event_id": news.signal_id,
                "headline": news.headline,
                "source": news.source,
                "url": news.url,
                "ticker": news.ticker,
                "sentiment_score": news.sentiment_score,
                "urgency": news.urgency,
                "catalyst": news.catalyst,
                "age_minutes": news.age_minutes,
                "timestamp": news.timestamp.isoformat(),
            },
        )

    def link_news_to_trade(self, event_id: str, trade_id: str) -> None:
        self._run(
            """
            MATCH (n:NewsEvent {event_id: $event_id}),
                  (t:Trade {trade_id: $trade_id})
            MERGE (t)-[:BASED_ON_NEWS]->(n)
            """,
            {"event_id": event_id, "trade_id": trade_id},
        )

    def link_news_to_price_movement(
        self,
        event_id: str,
        ticker: str,
        pct_change: float,
        direction: str,
        diverged: bool,
        timeframe: str = "1D",
    ) -> None:
        """Create PriceMovement node and link to NewsEvent."""
        pm_id = f"{event_id}_{ticker}_pm"
        rel = "DIVERGED_FROM" if diverged else "CAUSED"

        self._run(
            f"""
            MERGE (pm:PriceMovement {{pm_id: $pm_id}})
            SET pm.ticker = $ticker,
                pm.pct_change = $pct_change,
                pm.direction = $direction,
                pm.timeframe = $timeframe,
                pm.followed_news = $followed_news,
                pm.timestamp = datetime()
            WITH pm
            MATCH (n:NewsEvent {{event_id: $event_id}})
            MERGE (n)-[:{rel} {{confidence: $confidence}}]->(pm)
            """,
            {
                "pm_id": pm_id,
                "ticker": ticker,
                "pct_change": pct_change,
                "direction": direction,
                "timeframe": timeframe,
                "followed_news": not diverged,
                "event_id": event_id,
                "confidence": abs(pct_change) / 10,
            },
        )

    # ── Mentor Stats ──────────────────────────────────────────────────────────

    def update_mentor_stats(
        self, win_rate: float, total_trades: int, consecutive_wins: int
    ) -> None:
        self._run(
            """
            MERGE (m:Mentor {name: 'TradeSage Mentor'})
            SET m.win_rate = $win_rate,
                m.total_trades = $total_trades,
                m.consecutive_wins = $consecutive_wins,
                m.updated_at = datetime()
            """,
            {
                "win_rate": win_rate,
                "total_trades": total_trades,
                "consecutive_wins": consecutive_wins,
            },
        )

    def increment_principle_mastery(self, principle_name: str) -> None:
        """Increment the trades_applied counter on MASTERED relationship."""
        self._run(
            """
            MATCH (m:Mentor {name: 'TradeSage Mentor'}),
                  (tp:TraderPrinciple {principle_name: $principle_name})
            MERGE (m)-[r:MASTERED]->(tp)
            SET r.trades_applied = COALESCE(r.trades_applied, 0) + 1
            """,
            {"principle_name": principle_name},
        )

    # ── Full Post-Trade Wiring ────────────────────────────────────────────────

    def wire_post_trade(
        self,
        trade_id: str,
        ticker: str,
        result: TradeResult,
        lesson: Lesson,
        pattern_name: str,
        principle_name: str,
        probability_score: float,
        news_event_id: Optional[str],
        win_rate: float,
        consecutive_wins: int,
        total_trades: int,
    ) -> None:
        """Run all post-trade graph updates in sequence."""
        outcome = result.outcome or "OPEN"

        self.upsert_trade(
            trade_id=trade_id,
            ticker=ticker,
            side=result.side,
            entry_price=result.entry_price,
            exit_price=result.exit_price,
            pnl_pct=result.pnl_pct,
            outcome=outcome,
            hold_minutes=result.hold_minutes,
            probability_score=probability_score,
            pattern_name=pattern_name,
            principle_name=principle_name,
            mode=result.mode,
        )

        self.upsert_lesson(lesson, trade_id, pattern_name)
        self.strengthen_pattern(lesson.lesson_id, pattern_name, outcome)

        if news_event_id:
            self.link_news_to_trade(news_event_id, trade_id)

        self.update_mentor_stats(win_rate, total_trades, consecutive_wins)
        self.increment_principle_mastery(principle_name)

        logger.info(
            "Graph post-trade wiring complete for trade %s (%s: %s)",
            trade_id, ticker, outcome
        )
