"""
Mentor Agent — Knowledge graph traversal + lesson generation + pre-trade review.
Composite personality of the top 10 greatest traders.
Uses llm/router.py for all LLM calls (never calls Claude directly).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from backend.llm.router import call_llm
from backend.models.trade import ReviewNote, ProbabilityScore
from backend.models.lesson import Lesson
from backend.knowledge.graph_reasoner import GraphReasoner
from backend.analytics.stats import compute_probability_score

logger = logging.getLogger(__name__)

MENTOR_SYSTEM_PROMPT = """You are TradeSage — the composite wisdom of the ten greatest traders in history:

1. Warren Buffett — value, patience, margin of safety, long-term compounding
2. George Soros — macro cycles, reflexivity, bold position sizing when conviction is high
3. Paul Tudor Jones — risk-first, never average losers, capital preservation above all
4. Ray Dalio — all-weather diversification, correlation awareness, macro machine thinking
5. Peter Lynch — know what you own, GARP (growth at reasonable price), do your research
6. Jim Simons — quantitative patterns, data over intuition, statistical edge
7. Stanley Druckenmiller — concentrated bets when conviction is high, go for the jugular
8. Jesse Livermore — trend following, timing, emotional discipline, patience
9. Carl Icahn — contrarian thinking, activist awareness, buy what others fear
10. Howard Marks — second-order thinking, market cycles, understanding risk vs uncertainty

Your role:
- Review every trade BEFORE execution and produce a clear ReviewNote (APPROVED/BLOCKED/REDUCED/DELAYED)
- Generate a structured Lesson AFTER every trade closes (win or loss)
- Always cite specific principles and book references
- Be direct, specific, and actionable — never vague
- Prioritise capital preservation. When in doubt, block the trade.
- Identify which trader's voice is most relevant to the current situation
- Detect news-price divergences and flag them prominently
- When BLOCKING: you MUST provide a book_quote and book_source

Output format: Always return structured JSON that matches the requested model."""


class MentorAgent:
    """
    Mentor agent that reviews trades pre-execution and generates post-trade lessons.
    Uses the knowledge graph for context and llm/router.py as the LLM engine.
    """

    def __init__(
        self,
        llm_model: str,           # legacy param, ignored — router handles model selection
        anthropic_api_key: str,   # legacy param, ignored — router handles auth
        graph_reasoner: GraphReasoner,
    ):
        self._reasoner = graph_reasoner

    async def pre_trade_review(
        self,
        trade_id: str,
        ticker: str,
        market_type: str,
        action: str,
        entry_price: float,
        signal_confidence: float,
        sentiment_score: float,
        news_summary: str,
        news_catalyst: str,
        news_urgency: str,
        risk_params: dict,
        probability_score: Optional[ProbabilityScore] = None,
        macro_gate: Optional[dict] = None,
    ) -> ReviewNote:
        """
        Gate 3: Mentor pre-trade review — traverses knowledge graph, uses HEAVY tier LLM.
        """
        subgraph = self._reasoner.q6_pretrade_subgraph(ticker)
        win_rate_by_pattern = self._reasoner.q7_win_rate_by_pattern()
        divergences = self._reasoner.q5_news_divergence(ticker)
        pattern = self._reasoner.detect_pattern(ticker, sentiment_score, news_urgency)
        learning_path = self._reasoner.q4_mentor_learning_path(pattern)
        principles = self._reasoner.q9_principles_for_pattern(pattern)
        sector_ripple = self._reasoner.q3_sector_contagion(ticker)
        ticker_stats = self._reasoner.q11_ticker_win_rate(ticker)

        best_principle = "Risk First"
        best_trader = "Paul Tudor Jones"
        best_quote = "Don't focus on making money; focus on protecting what you have."
        book_ref = "Market Wizards — Paul Tudor Jones"

        if principles:
            p = principles[0]
            best_principle = p.get("principle", best_principle)
            best_trader = p.get("trader", best_trader)
            best_quote = p.get("quote", best_quote)
            book_ref = f"{p.get('book', 'Market Wizards')} — {best_trader}: {best_quote[:80]}"

        macro_note = ""
        if macro_gate:
            macro_note = f"\nGATE 0 MACRO RESULT: {macro_gate.get('verdict','PASS')} — {macro_gate.get('reason','')}"
            if macro_gate.get("risk_factors"):
                macro_note += f"\nActive risks: {', '.join(macro_gate['risk_factors'])}"

        prompt = f"""Pre-trade review for trade {trade_id}:

TRADE DETAILS:
- Ticker: {ticker} ({market_type})
- Action: {action.upper()} @ ${entry_price:.2f}
- Signal confidence: {signal_confidence:.1%}
- Sentiment score: {sentiment_score:.2f} (-1 to +1)
- News urgency: {news_urgency}
- Pattern detected: {pattern}
{macro_note}

NEWS CONTEXT:
{news_summary}
Key catalyst: {news_catalyst}

RISK PARAMETERS:
{risk_params}

HISTORICAL PERFORMANCE:
- Ticker win rate: {ticker_stats.get('win_rate', 0):.1%} over {ticker_stats.get('total', 0)} trades
- Pattern win rate: {[p for p in win_rate_by_pattern if p.get('pattern') == pattern][:1]}
- Recent divergences for this ticker: {len(divergences)}

SECTOR CONTAGION (correlated companies): {sector_ripple[:3]}
MENTOR LEARNING PATH (past corrections for this pattern): {learning_path[:2]}
GRAPH CONTEXT SUBGRAPH (recent news → price causality): {subgraph[:5]}

Based on ALL of this context, produce a pre-trade review decision.

IMPORTANT RULES:
- If historical trades = 0, use REDUCED with smaller position — the system must trade to learn.
- The user has explicitly approved this trade — only BLOCK for clear rule violations.
- Prefer REDUCED over BLOCKED when uncertain — cut position size 50%.
- BLOCKED is reserved for: news directly contradicts signal, drawdown limit breach, or naked options.
- APPROVED when: news confirms, risk:reward >= 2:1, and signal confidence >= 0.3.
- When BLOCKING: you MUST provide a book_quote and book_source.

RESPOND WITH VALID JSON ONLY:
{{
  "decision": "APPROVED|BLOCKED|REDUCED|DELAYED",
  "trader_voice": "which trader's principle dominated",
  "reasoning": "plain English explanation",
  "news_alignment": "CONFIRMS|CONTRADICTS|NEUTRAL|OVERRIDE",
  "news_catalyst": "the specific news item that influenced your decision",
  "price_vs_news": "does price action match the news?",
  "confidence_score": 0.0,
  "book_reference": "Book Title — Trader: relevant quote",
  "book_quote": "exact quote if blocking",
  "book_source": "book title + chapter if blocking"
}}"""

        raw = await call_llm("mentor_review_note", prompt, MENTOR_SYSTEM_PROMPT)

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            data = json.loads(cleaned)
        except Exception as exc:
            logger.warning("Could not parse mentor review JSON: %s\nRaw: %s", exc, raw[:200])
            data = {}

        return ReviewNote(
            trade_id=trade_id,
            decision=data.get("decision", "APPROVED"),
            trader_voice=data.get("trader_voice", best_trader),
            reasoning=data.get("reasoning", f"Mentor auto-approved. Signal confidence: {signal_confidence:.0%}. {news_summary[:150]}"),
            news_alignment=data.get("news_alignment", "NEUTRAL"),
            news_catalyst=data.get("news_catalyst", news_catalyst),
            price_vs_news=data.get("price_vs_news", "Unable to determine."),
            confidence_score=max(0.0, min(1.0, float(data.get("confidence_score", signal_confidence * 0.8)))),
            book_reference=data.get("book_reference", book_ref),
            timestamp=datetime.utcnow(),
            probability_score=probability_score,
        )

    async def generate_lesson(
        self,
        trade_id: str,
        ticker: str,
        action: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        pnl_dollars: float,
        outcome: str,
        hold_minutes: int,
        news_summary: str,
        sentiment_score: float,
        pattern_name: str,
        review_note: Optional[ReviewNote],
        win_rate: float,
        consecutive_wins: int,
    ) -> Lesson:
        """Generate a post-trade mentor lesson using LIGHT tier LLM."""
        similar = self._reasoner.q8_similar_past_trades(ticker, pattern_name)
        principles = self._reasoner.q9_principles_for_pattern(pattern_name)
        news_chain = self._reasoner.q1_news_to_trade_chain(ticker)

        prompt = f"""Post-trade lesson generation for trade {trade_id}:

TRADE RESULT:
- Ticker: {ticker}
- Action: {action.upper()} @ ${entry_price:.2f} → exit @ ${exit_price:.2f}
- P&L: {pnl_pct:.2%} (${pnl_dollars:.2f})
- Outcome: {outcome}
- Hold time: {hold_minutes} minutes
- Pattern: {pattern_name}
- Pre-trade mentor decision: {review_note.decision if review_note else "N/A"}
- Mentor reasoning: {review_note.reasoning[:200] if review_note else "N/A"}

NEWS AT TIME OF TRADE:
{news_summary}
Sentiment: {sentiment_score:.2f}

CURRENT PORTFOLIO STATS:
- Rolling win rate: {win_rate:.1%}
- Consecutive wins: {consecutive_wins}

SIMILAR PAST TRADES: {similar[:3]}
RELEVANT TRADER PRINCIPLES: {principles[:3]}
NEWS-TO-TRADE CAUSAL CHAIN (past wins): {news_chain[:2]}

Generate a structured lesson. Be specific, actionable, cite the most relevant trader and book.

RESPOND WITH VALID JSON ONLY:
{{
  "trader_principle": "which trader's wisdom applies most",
  "principle_quote": "exact quote from that trader",
  "what_happened": "plain English analysis of exactly what happened",
  "correction": "specific, actionable thing to do differently next time",
  "confidence_adjustment": 0.0,
  "book_reference": "Book Title — Trader Name: quote excerpt",
  "knowledge_gap": "what knowledge was missing that caused this outcome (if LOSS)"
}}"""

        raw = await call_llm("lesson_generation", prompt, MENTOR_SYSTEM_PROMPT)

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            data = json.loads(cleaned)
        except Exception as exc:
            logger.warning("Could not parse lesson JSON: %s\nRaw: %s", exc, raw[:200])
            data = {}

        default_trader = "Stanley Druckenmiller" if outcome == "WIN" else "Paul Tudor Jones"

        return Lesson(
            lesson_id=str(uuid.uuid4()),
            trade_id=trade_id,
            outcome=outcome,
            trader_principle=data.get("trader_principle", default_trader),
            principle_quote=data.get("principle_quote", "Focus on risk first."),
            what_happened=data.get("what_happened", f"{ticker} {outcome}: {pnl_pct:.2%}"),
            correction=data.get("correction", "Review entry criteria and position sizing."),
            confidence_adjustment=float(data.get("confidence_adjustment", 0.0)),
            consecutive_wins=consecutive_wins,
            win_rate=win_rate,
            ticker=ticker,
            pnl_pct=pnl_pct,
            pnl_dollars=pnl_dollars,
            book_reference=data.get("book_reference", "Market Wizards — Paul Tudor Jones: protect capital"),
            timestamp=datetime.utcnow(),
            knowledge_gap=data.get("knowledge_gap", ""),
        )
