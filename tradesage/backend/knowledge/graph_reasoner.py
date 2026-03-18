"""
Graph Reasoner — Cypher query library for causal chain traversal.
All queries are parameterized and return structured data.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from neo4j import Driver

logger = logging.getLogger(__name__)


class GraphReasoner:
    """
    High-level interface for all TradeSage Cypher queries.
    Each method maps to a named query in the spec.
    """

    def __init__(self, driver: Driver):
        self._driver = driver

    def _run(self, cypher: str, params: dict) -> list[dict]:
        try:
            with self._driver.session() as session:
                result = session.run(cypher, params)
                return [record.data() for record in result]
        except Exception as exc:
            logger.error("Cypher error: %s | params=%s | error=%s", cypher[:80], params, exc)
            return []

    # ── Q1 — News-to-Trade Causal Chain ──────────────────────────────────────

    def q1_news_to_trade_chain(self, ticker: str) -> list[dict]:
        """Find winning trade patterns driven by positive news for a ticker."""
        return self._run(
            """
            MATCH (n:NewsEvent)-[:CAUSED]->(pm:PriceMovement)
                  -[:MATCHES]->(pattern:MarketPattern)
                  <-[:APPLIES_TO]-(principle:TraderPrinciple)
                  <-[:APPLIED_PRINCIPLE]-(t:Trade {outcome:"WIN"})
                  -[:GENERATED]->(l:Lesson)
            WHERE n.ticker = $ticker
              AND n.sentiment_score > 0.3
            RETURN principle.trader_name AS trader,
                   principle.principle_name AS principle,
                   principle.quote AS quote,
                   pattern.name AS pattern,
                   l.correction AS lesson_correction,
                   t.pnl_pct AS pnl_pct
            ORDER BY t.pnl_pct DESC
            LIMIT 5
            """,
            {"ticker": ticker},
        )

    # ── Q2 — Macro Ripple Effect ──────────────────────────────────────────────

    def q2_macro_ripple(self, event_type: str, ticker: str) -> list[dict]:
        """Find how a macro event impacts sectors and companies."""
        return self._run(
            """
            MATCH (macro:MacroEvent {type: $event_type})
                  -[:IMPACTS]->(sector:Sector)
                  <-[:BELONGS_TO]-(company:Company {ticker: $ticker})
            RETURN macro.description AS macro_description,
                   sector.name AS sector_name,
                   macro.impact_direction AS direction,
                   macro.avg_move_pct AS avg_move_pct
            """,
            {"event_type": event_type, "ticker": ticker},
        )

    # ── Q3 — Sector Contagion Chain ───────────────────────────────────────────

    def q3_sector_contagion(self, ticker: str) -> list[dict]:
        """Find correlated sectors and related companies (correlation > 0.6)."""
        return self._run(
            """
            MATCH (c:Company {ticker: $ticker})-[:BELONGS_TO]->(s1:Sector)
                  -[corr:CORRELATED_WITH]->(s2:Sector)
                  <-[:BELONGS_TO]-(related:Company)
            WHERE abs(corr.correlation_coeff) > 0.6
              AND related.ticker <> $ticker
            RETURN related.ticker AS related_ticker,
                   related.name AS company_name,
                   s1.name AS source_sector,
                   s2.name AS related_sector,
                   corr.correlation_coeff AS correlation
            ORDER BY abs(corr.correlation_coeff) DESC
            LIMIT 10
            """,
            {"ticker": ticker},
        )

    # ── Q4 — Mentor Learning Path ─────────────────────────────────────────────

    def q4_mentor_learning_path(self, current_pattern: str) -> list[dict]:
        """Retrieve mentor lessons that corrected a specific market pattern."""
        return self._run(
            """
            MATCH (mentor:Mentor)-[:LEARNED_FROM]->(l:Lesson)
                  -[:CORRECTS_PATTERN]->(pattern:MarketPattern)
            WHERE pattern.name = $current_pattern
            RETURN l.correction AS correction,
                   l.trader_voice AS trader_voice,
                   l.book_reference AS book_reference,
                   l.timestamp AS timestamp
            ORDER BY l.timestamp DESC
            LIMIT 3
            """,
            {"current_pattern": current_pattern},
        )

    # ── Q5 — News Divergence Detector ────────────────────────────────────────

    def q5_news_divergence(self, ticker: str) -> list[dict]:
        """Detect cases where price moved against news sentiment in last 7 days."""
        return self._run(
            """
            MATCH (n:NewsEvent {ticker: $ticker})-[:DIVERGED_FROM]->(pm:PriceMovement)
            WHERE n.timestamp > datetime() - duration("P7D")
            RETURN n.headline AS headline,
                   n.sentiment_score AS sentiment,
                   pm.direction AS price_direction,
                   pm.pct_change AS price_change,
                   "DIVERGENCE" AS signal_type,
                   n.timestamp AS timestamp
            ORDER BY n.timestamp DESC
            """,
            {"ticker": ticker},
        )

    # ── Q6 — Full Pre-Trade Context Subgraph ─────────────────────────────────

    def q6_pretrade_subgraph(self, ticker: str) -> list[dict]:
        """Fetch the full causal context subgraph for a ticker (last 4 hours)."""
        return self._run(
            """
            MATCH (n:NewsEvent)-[:MENTIONS]->(c:Company {ticker: $ticker})
                  -[:BELONGS_TO]->(s:Sector)
            OPTIONAL MATCH (s)-[:CORRELATED_WITH*1..2]->(related:Sector)
            OPTIONAL MATCH (n)-[:CAUSED]->(pm:PriceMovement)-[:MATCHES]->(p:MarketPattern)
                          <-[:APPLIES_TO]-(pr:TraderPrinciple)
            WHERE n.timestamp > datetime() - duration("PT4H")
            RETURN n.headline AS headline,
                   n.sentiment_score AS sentiment,
                   n.catalyst AS catalyst,
                   c.name AS company,
                   s.name AS sector,
                   related.name AS correlated_sector,
                   p.name AS pattern,
                   pr.trader_name AS trader,
                   pr.quote AS quote
            LIMIT 75
            """,
            {"ticker": ticker},
        )

    # ── Q7 — Win Rate by Pattern ──────────────────────────────────────────────

    def q7_win_rate_by_pattern(self) -> list[dict]:
        """Compute win rate for each market pattern (minimum 5 trades)."""
        return self._run(
            """
            MATCH (t:Trade)-[:FOLLOWED_PATTERN]->(p:MarketPattern)
            WITH p.name AS pattern,
                 sum(CASE WHEN t.outcome = "WIN" THEN 1 ELSE 0 END) AS wins,
                 count(t) AS total
            WHERE total >= 5
            RETURN pattern,
                   wins,
                   total,
                   round(100.0 * wins / total, 1) AS win_rate_pct
            ORDER BY win_rate_pct DESC
            """,
            {},
        )

    # ── Q8 — Similar Past Trades ──────────────────────────────────────────────

    def q8_similar_past_trades(self, ticker: str, pattern: str) -> list[dict]:
        """Find past trades for the same ticker and pattern."""
        return self._run(
            """
            MATCH (t:Trade)-[:FOR_COMPANY]->(c:Company {ticker: $ticker}),
                  (t)-[:FOLLOWED_PATTERN]->(p:MarketPattern {name: $pattern})
            RETURN t.trade_id AS trade_id,
                   t.outcome AS outcome,
                   t.pnl_pct AS pnl_pct,
                   t.entry_price AS entry_price,
                   t.exit_price AS exit_price,
                   t.hold_minutes AS hold_minutes
            ORDER BY t.entry_price DESC
            LIMIT 20
            """,
            {"ticker": ticker, "pattern": pattern},
        )

    # ── Q9 — Trader Principles for Pattern ───────────────────────────────────

    def q9_principles_for_pattern(self, pattern_name: str) -> list[dict]:
        """Retrieve trader principles that apply to a given market pattern."""
        return self._run(
            """
            MATCH (tp:TraderPrinciple)-[:APPLIES_TO]->(p:MarketPattern {name: $pattern})
            OPTIONAL MATCH (tp)-[:SOURCED_FROM]->(bc:BookChunk)
            RETURN tp.trader_name AS trader,
                   tp.principle_name AS principle,
                   tp.quote AS quote,
                   tp.description AS description,
                   bc.content AS book_chunk
            """,
            {"pattern": pattern_name},
        )

    # ── Q10 — Mentor Mastered Principles ─────────────────────────────────────

    def q10_mentor_mastered(self) -> list[dict]:
        """Get all principles the mentor has mastered with trade counts."""
        return self._run(
            """
            MATCH (m:Mentor {name: 'TradeSage Mentor'})-[r:MASTERED]->(tp:TraderPrinciple)
            RETURN tp.trader_name AS trader,
                   tp.principle_name AS principle,
                   tp.quote AS quote,
                   r.trades_applied AS trades_applied
            ORDER BY r.trades_applied DESC
            """,
            {},
        )

    # ── Q11 — Historical Win Rate for Ticker ─────────────────────────────────

    def q11_ticker_win_rate(self, ticker: str) -> dict:
        """Compute overall win rate for a specific ticker."""
        rows = self._run(
            """
            MATCH (t:Trade)-[:FOR_COMPANY]->(c:Company {ticker: $ticker})
            WHERE t.outcome IN ["WIN", "LOSS", "BREAKEVEN"]
            WITH sum(CASE WHEN t.outcome = "WIN" THEN 1 ELSE 0 END) AS wins,
                 count(t) AS total,
                 avg(t.pnl_pct) AS avg_pnl_pct
            RETURN wins, total, avg_pnl_pct
            """,
            {"ticker": ticker},
        )
        if not rows:
            return {"wins": 0, "total": 0, "win_rate": 0.0, "avg_pnl_pct": 0.0}
        r = rows[0]
        total = r.get("total", 0)
        wins = r.get("wins", 0)
        return {
            "wins": wins,
            "total": total,
            "win_rate": wins / total if total else 0.0,
            "avg_pnl_pct": r.get("avg_pnl_pct", 0.0) or 0.0,
        }

    # ── Q12 — Recent NewsEvents for Ticker ────────────────────────────────────

    def q12_recent_news(self, ticker: str, hours: int = 24) -> list[dict]:
        """Fetch recent news events for a ticker from the graph."""
        return self._run(
            """
            MATCH (n:NewsEvent {ticker: $ticker})
            WHERE n.timestamp > datetime() - duration({hours: $hours})
            RETURN n.headline AS headline,
                   n.source AS source,
                   n.url AS url,
                   n.sentiment_score AS sentiment_score,
                   n.urgency AS urgency,
                   n.catalyst AS catalyst,
                   n.timestamp AS timestamp
            ORDER BY n.timestamp DESC
            LIMIT 20
            """,
            {"ticker": ticker, "hours": hours},
        )

    # ── Q13 — Detect Pattern from State ──────────────────────────────────────

    def detect_pattern(self, ticker: str, sentiment_score: float, news_urgency: str) -> str:
        """
        Heuristic pattern detection based on current market context.
        Returns the best-matching MarketPattern name.
        """
        if news_urgency == "immediate" and sentiment_score > 0.6:
            return "News Catalyst Spike"
        if sentiment_score > 0.5 and news_urgency == "wait":
            return "Trend Continuation"
        if sentiment_score < -0.3:
            return "Mean Reversion Oversold"
        if news_urgency == "override_cancel":
            return "Sentiment Divergence"

        # Check graph for divergences in last 7 days
        divs = self.q5_news_divergence(ticker)
        if divs:
            return "Sentiment Divergence"

        return "Trend Continuation"

    # ── Graph Stats ───────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        rows = self._run("MATCH (n) RETURN count(n) AS nodes", {})
        rels = self._run("MATCH ()-[r]->() RETURN count(r) AS rels", {})
        return {
            "nodes": rows[0]["nodes"] if rows else 0,
            "relationships": rels[0]["rels"] if rels else 0,
        }

    def get_subgraph_for_explorer(self, ticker: str, depth: int = 2) -> dict:
        """
        Return a D3-compatible node+link structure for the GraphExplorer component.
        Covers: Company → Sector → TraderPrinciple + recent NewsEvents + Trades + Lessons.
        """
        nodes_raw = self._run(
            """
            MATCH (c:Company {ticker: $ticker})
            OPTIONAL MATCH (c)-[:BELONGS_TO]->(s:Sector)
            OPTIONAL MATCH (n:NewsEvent {ticker: $ticker})
            WHERE n.timestamp > datetime() - duration("P7D")
            OPTIONAL MATCH (t:Trade)-[:FOR_COMPANY]->(c)
            OPTIONAL MATCH (t)-[:GENERATED]->(l:Lesson)
            OPTIONAL MATCH (t)-[:FOLLOWED_PATTERN]->(p:MarketPattern)
            RETURN c, s, n, t, l, p
            LIMIT 50
            """,
            {"ticker": ticker},
        )

        seen_nodes: dict[str, dict] = {}
        links: list[dict] = []

        def add_node(id_: str, label: str, properties: dict) -> None:
            if id_ not in seen_nodes:
                seen_nodes[id_] = {"id": id_, "label": label, **properties}

        for row in nodes_raw:
            if row.get("c"):
                n = row["c"]
                add_node(n.get("ticker", ""), "Company", {"name": n.get("name", "")})
            if row.get("s"):
                s = row["s"]
                add_node(s.get("name", ""), "Sector", {"name": s.get("name", "")})
                links.append({"source": ticker, "target": s.get("name", ""), "type": "BELONGS_TO"})
            if row.get("n"):
                news = row["n"]
                nid = news.get("event_id", news.get("headline", "")[:20])
                add_node(nid, "NewsEvent", {
                    "headline": news.get("headline", "")[:60],
                    "sentiment": news.get("sentiment_score", 0),
                })
                links.append({"source": nid, "target": ticker, "type": "MENTIONS"})
            if row.get("t"):
                trade = row["t"]
                tid = trade.get("trade_id", "")
                add_node(tid, "Trade", {
                    "outcome": trade.get("outcome", ""),
                    "pnl_pct": trade.get("pnl_pct", 0),
                })
                links.append({"source": tid, "target": ticker, "type": "FOR_COMPANY"})
            if row.get("l"):
                lesson = row["l"]
                lid = lesson.get("lesson_id", "")
                add_node(lid, "Lesson", {"correction": lesson.get("correction", "")[:60]})
                if row.get("t"):
                    links.append({"source": row["t"].get("trade_id", ""), "target": lid, "type": "GENERATED"})
            if row.get("p"):
                pattern = row["p"]
                add_node(pattern.get("name", ""), "MarketPattern", {
                    "win_rate": pattern.get("historical_success_rate", 0),
                })
                if row.get("t"):
                    links.append({
                        "source": row["t"].get("trade_id", ""),
                        "target": pattern.get("name", ""),
                        "type": "FOLLOWED_PATTERN",
                    })

        return {"nodes": list(seen_nodes.values()), "links": links}
