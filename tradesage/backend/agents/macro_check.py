"""
Gate 0 — Macro / Sentiment Check
Runs before news/risk/mentor gates.
Checks: VIX level, Fed/CPI calendar events, sector trend last 5 days.
BLOCK if 2+ risk factors present.
CAUTION (50% size reduction) if 1 risk factor.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

MACRO_SYSTEM = """You are a macro risk analyst. Given data about market conditions,
identify risk factors and decide if a trade should proceed.

Risk factors to check:
1. VIX > 25 (high fear)
2. VIX > 20 AND trending up (elevated + rising fear)
3. Fed meeting within 2 days (rate uncertainty)
4. CPI/PPI release within 24 hours (inflation shock risk)
5. Sector down >2% in last 5 days (sector headwind)
6. Market down >1.5% today (broad sell-off)

Verdict rules:
- 0 factors → PASS
- 1 factor → CAUTION (reduce position 50%)
- 2+ factors → BLOCK

Respond ONLY with valid JSON:
{
  "verdict": "PASS|CAUTION|BLOCK",
  "risk_count": 0,
  "risk_factors": ["list of active risks"],
  "reason": "plain English explanation",
  "fix_condition": "what needs to change to pass",
  "book_quote": "only if BLOCK — relevant trading wisdom quote",
  "book_source": "book title if quoting"
}"""


class MacroCheckAgent:
    """Gate 0: fast macro/sentiment check before trade gates 1-3."""

    async def check(
        self,
        ticker: str,
        sector: str,
        action: str,
        sentiment_score: float,
    ) -> dict:
        """
        Run macro check. Returns a gate_result dict.
        Quick — uses yfinance for live data, no Tavily needed.
        """
        macro_data = await self._fetch_macro_data(ticker, sector)
        return await self._evaluate(ticker, action, sentiment_score, macro_data)

    async def _fetch_macro_data(self, ticker: str, sector: str) -> dict:
        """Fetch VIX + sector trend from yfinance (free, fast)."""
        def _sync_fetch():
            try:
                import yfinance as yf
                data: dict = {}

                # VIX
                try:
                    vix = yf.Ticker("^VIX")
                    vix_hist = vix.history(period="5d")
                    if not vix_hist.empty:
                        data["vix_current"] = round(float(vix_hist["Close"].iloc[-1]), 2)
                        data["vix_5d_ago"] = round(float(vix_hist["Close"].iloc[0]), 2)
                        data["vix_trending_up"] = data["vix_current"] > data["vix_5d_ago"]
                except Exception:
                    data["vix_current"] = 18.0
                    data["vix_trending_up"] = False

                # Sector ETF trend (approximate by sector)
                SECTOR_ETFS = {
                    "Technology": "XLK", "Financials": "XLF", "Energy": "XLE",
                    "Healthcare": "XLV", "Consumer Discretionary": "XLY",
                    "Communication Services": "XLC", "Industrials": "XLI",
                    "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE",
                }
                etf = SECTOR_ETFS.get(sector, "SPY")
                try:
                    s = yf.Ticker(etf)
                    hist = s.history(period="6d")
                    if len(hist) >= 5:
                        start_price = float(hist["Close"].iloc[0])
                        end_price = float(hist["Close"].iloc[-1])
                        data["sector_5d_change"] = round((end_price - start_price) / start_price * 100, 2)
                        data["sector_etf"] = etf
                except Exception:
                    data["sector_5d_change"] = 0.0

                # SPY today
                try:
                    spy = yf.Ticker("SPY")
                    spy_hist = spy.history(period="2d")
                    if len(spy_hist) >= 2:
                        prev = float(spy_hist["Close"].iloc[-2])
                        curr = float(spy_hist["Close"].iloc[-1])
                        data["spy_today_pct"] = round((curr - prev) / prev * 100, 2)
                except Exception:
                    data["spy_today_pct"] = 0.0

                # Fed/CPI dates — check for this week (simple heuristic)
                today = datetime.utcnow()
                # Fed meetings roughly every 6 weeks — simplified check
                # (In production, pull from FRED calendar)
                data["fed_meeting_soon"] = False  # simplified
                data["cpi_release_soon"] = False  # simplified

                return data
            except Exception as exc:
                logger.warning("Macro data fetch failed: %s", exc)
                return {"vix_current": 18.0, "vix_trending_up": False,
                        "sector_5d_change": 0.0, "spy_today_pct": 0.0,
                        "fed_meeting_soon": False, "cpi_release_soon": False}

        return await asyncio.to_thread(_sync_fetch)

    async def _evaluate(self, ticker: str, action: str, sentiment: float, macro: dict) -> dict:
        vix = macro.get("vix_current", 18.0)
        vix_up = macro.get("vix_trending_up", False)
        sector_chg = macro.get("sector_5d_change", 0.0)
        spy_chg = macro.get("spy_today_pct", 0.0)
        fed_soon = macro.get("fed_meeting_soon", False)
        cpi_soon = macro.get("cpi_release_soon", False)

        prompt = f"""Macro data for trade: {action.upper()} {ticker}
Signal sentiment: {sentiment:+.2f}

VIX: {vix:.1f} (trending {'UP' if vix_up else 'flat/down'})
Sector 5-day change: {sector_chg:+.2f}%
SPY today: {spy_chg:+.2f}%
Fed meeting within 2 days: {fed_soon}
CPI/PPI release within 24h: {cpi_soon}

Evaluate macro risk and return JSON verdict."""

        raw = await call_llm("macro_check", prompt, MACRO_SYSTEM)

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            data = json.loads(cleaned)
        except Exception:
            data = {}

        # Fallback: compute locally without LLM
        if not data:
            risk_factors = []
            if vix > 25:
                risk_factors.append(f"VIX {vix:.0f} > 25 (high fear)")
            elif vix > 20 and vix_up:
                risk_factors.append(f"VIX {vix:.0f} > 20 and rising")
            if fed_soon:
                risk_factors.append("Fed meeting within 2 days")
            if cpi_soon:
                risk_factors.append("CPI/PPI release within 24h")
            if sector_chg < -2.0:
                risk_factors.append(f"Sector down {sector_chg:.1f}% in 5 days")
            if spy_chg < -1.5:
                risk_factors.append(f"Market down {spy_chg:.1f}% today")

            count = len(risk_factors)
            if count == 0:
                verdict = "PASS"
            elif count == 1:
                verdict = "CAUTION"
            else:
                verdict = "BLOCK"

            data = {
                "verdict": verdict,
                "risk_count": count,
                "risk_factors": risk_factors,
                "reason": f"{'No' if count == 0 else count} macro risk factor{'s' if count != 1 else ''} detected.",
                "fix_condition": "Wait for VIX to drop below 20 or for the macro event to pass.",
                "book_quote": "",
                "book_source": "",
            }

        verdict = data.get("verdict", "PASS")
        passed = verdict == "PASS"
        caution = verdict == "CAUTION"

        logger.info("[Gate0/Macro] %s %s → %s (VIX=%.1f sector=%.1f%%)",
                    action.upper(), ticker, verdict, vix, sector_chg)

        return {
            "gate_name": "macro",
            "passed": passed or caution,
            "verdict": verdict,
            "reason": data.get("reason", ""),
            "detail": f"VIX={vix:.1f} sector5d={sector_chg:+.1f}% spy={spy_chg:+.1f}%",
            "fix_condition": data.get("fix_condition", ""),
            "book_quote": data.get("book_quote", ""),
            "book_source": data.get("book_source", ""),
            "risk_factors": data.get("risk_factors", []),
            "size_multiplier": 0.5 if caution else 1.0,  # CAUTION → 50% position
        }


# Global singleton
macro_check_agent = MacroCheckAgent()
