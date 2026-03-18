"""
EntryRulesMicroAgent
--------------------
Uses call_llm("algorithm_assemble") to generate Python source code for an
entry condition function tailored to the given strategy type and
selected indicators.

The generated code must be a standalone function:
    def check_entry(data: dict) -> bool

`data` is a dict with keys matching the selected indicators
(e.g. data["rsi"], data["macd"], data["atr"], data["close"], etc.)

task: {
    "ticker": str,
    "strategy_type": str,       # "momentum" | "event_driven" | "breakout" | "mean_reversion"
    "indicators": list[str],
    "params": dict,
}

Returns:
    {"ticker": str, "strategy_type": str, "entry_rules_code": str}
"""
from __future__ import annotations

import logging

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

_SYSTEM = """You are an expert algorithmic trading engineer.
Write clean, readable Python code for a trading entry condition.
The function signature must be exactly: def check_entry(data: dict) -> bool
Include a brief docstring. Use only standard Python — no external imports.
Return ONLY the Python function code (no markdown fences, no extra text)."""

_FALLBACK_CODE = """\
def check_entry(data: dict) -> bool:
    \"\"\"High-WR entry: confirmed uptrend + RSI momentum sweet spot + MACD bullish.\"\"\"
    close = data.get("close", 0.0)
    ema20 = data.get("ema20", 0.0)
    ema50 = data.get("ema50", 0.0)
    rsi = data.get("rsi", 50.0)
    macd = data.get("macd", 0.0)
    macd_signal = data.get("macd_signal", 0.0)
    if close <= 0 or ema20 <= 0 or ema50 <= 0:
        return False
    if not (close > ema20 > ema50):
        return False  # confirmed uptrend only
    if not (55 <= rsi <= 70):
        return False  # momentum sweet spot, not overbought
    if macd <= macd_signal:
        return False  # MACD must be bullish
    return True
"""


class EntryRulesMicroAgent(MicroAgent):
    name = "EntryRulesMicroAgent"
    timeout_seconds = 60.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        strategy_type: str = task.get("strategy_type", "momentum")
        indicators: list = task.get("indicators", [])
        params: dict = task.get("params", {})
        predecessor: dict | None = task.get("predecessor_context")

        prompt = self._build_prompt(ticker, strategy_type, indicators, params, predecessor)
        code = await call_llm("algorithm_assemble", prompt, _SYSTEM)
        code = self._clean_code(code)

        return {
            "ticker": ticker,
            "strategy_type": strategy_type,
            "entry_rules_code": code,
        }

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------
    @staticmethod
    def _build_prompt(
        ticker: str,
        strategy: str,
        indicators: list,
        params: dict,
        predecessor: dict | None = None,
    ) -> str:
        indicator_str = ", ".join(indicators) if indicators else "RSI, ATR, EMA_20"
        params_str = str(params) if params else "{}"

        predecessor_section = ""
        if predecessor:
            wr = predecessor.get("backtest_win_rate", 0)
            st = predecessor.get("strategy_type", "?")
            pwr = predecessor.get("paper_win_rate", 0)
            predecessor_section = f"""
PREDECESSOR ALGORITHM CONTEXT (learn from this):
- Previous strategy type: {st}
- Backtest win rate: {wr:.1%}
- Paper trading win rate: {pwr:.1%}
- Status: {predecessor.get("status", "?")}
IMPORTANT: The predecessor used strict multi-condition entry logic which fired rarely (< 20 trades/year).
Build a SIMPLER entry rule that fires more frequently (aim for 1-3 entries per month on daily bars).
Use fewer conditions (2-3 max) and less restrictive thresholds.
"""

        return f"""Write a Python entry condition function for:
Ticker: {ticker}
Strategy type: {strategy}
Selected indicators: {indicator_str}
Parameters: {params_str}
{predecessor_section}
The function signature must be EXACTLY:
    def check_entry(data: dict) -> bool

The `data` dict will contain these keys (lowercase, underscored):
- "close": current closing price (float)
- "volume": current volume (float)
- "rsi": RSI value (float, 0-100)
- "macd": MACD line value (float)
- "macd_signal": MACD signal line (float)
- "atr": Average True Range (float)
- "ema20": 20-period EMA (float)
- "ema50": 50-period EMA (float)
- "volume_ma20": 20-period volume moving average (float)
- "prev_close": previous day close (float)

TARGET: Achieve 90%+ win rate. The exit takes profit at +0.5% and stops at -1%.
To win 9 out of 10 trades with +0.5% TP, only enter when the stock is in a
VERY STRONG confirmed uptrend where the next candle has high probability of being positive.

PROVEN high-WR conditions:
- close > ema20 > ema50 (confirmed uptrend, both EMA slopes positive)
- RSI between 55-70 (momentum without being overbought)
- macd > macd_signal (MACD bullish crossover)

Use EXACTLY these 3 conditions for maximum win rate.
Do NOT add more conditions (fewer trades is ok, higher WR is critical).

Return ONLY the Python function. No markdown. No imports."""

    # ------------------------------------------------------------------
    # Code cleaner
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_code(raw: str) -> str:
        """Strip markdown fences and validate the function is present."""
        code = raw.strip()
        # Remove markdown code fences
        for fence in ("```python", "```py", "```"):
            if code.startswith(fence):
                code = code[len(fence):]
        if code.endswith("```"):
            code = code[:-3]
        code = code.strip()

        if "def check_entry" not in code:
            logger.warning("[EntryRulesMicroAgent] LLM did not return check_entry function, using fallback")
            return _FALLBACK_CODE

        return code
