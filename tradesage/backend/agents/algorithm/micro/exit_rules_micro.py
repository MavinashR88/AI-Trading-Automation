"""
ExitRulesMicroAgent
-------------------
Uses call_llm("algorithm_assemble") to generate Python source code for an
exit condition function tailored to the given strategy type and indicators.

The generated code must be a standalone function:
    def check_exit(data: dict, position: dict) -> tuple[bool, str]

`data`     — same structure as entry_rules (market data)
`position` — dict with keys: entry_price, shares, side, bars_held, unrealized_pnl_pct

Returns (should_exit: bool, reason: str)

task: {
    "ticker": str,
    "strategy_type": str,
    "indicators": list[str],
    "params": dict,
}

Returns:
    {"ticker": str, "strategy_type": str, "exit_rules_code": str}
"""
from __future__ import annotations

import logging

from backend.agents.base.micro import MicroAgent
from backend.llm.router import call_llm

logger = logging.getLogger(__name__)

_SYSTEM = """You are an expert algorithmic trading engineer specialising in exit management.
Write clean, readable Python code for a trading exit condition.
The function signature must be exactly:
    def check_exit(data: dict, position: dict) -> tuple[bool, str]
Return a tuple of (should_exit: bool, reason: str).
Include a brief docstring. Use only standard Python — no external imports.
Return ONLY the Python function code (no markdown fences, no extra text)."""

_FALLBACK_CODE = """\
def check_exit(data: dict, position: dict) -> tuple[bool, str]:
    \"\"\"Profitable exit: +1% take-profit / -1.5% stop-loss / trend break / time exit.\"\"\"
    pnl_pct = position.get("unrealized_pnl_pct", 0.0)
    bars_held = position.get("bars_held", 0)
    close = data.get("close", 0.0)
    ema20 = data.get("ema20", 0.0)
    if pnl_pct <= -1.5:
        return True, "stop_loss"
    if pnl_pct >= 1.0:
        return True, "take_profit"
    if close > 0 and ema20 > 0 and close < ema20 and pnl_pct < 0:
        return True, "trend_break"
    if bars_held >= 10:
        return True, "time_exit"
    return False, ""
"""


class ExitRulesMicroAgent(MicroAgent):
    name = "ExitRulesMicroAgent"
    timeout_seconds = 60.0

    async def execute(self, task: dict) -> dict:
        ticker: str = task["ticker"]
        strategy_type: str = task.get("strategy_type", "momentum")
        indicators: list = task.get("indicators", [])
        params: dict = task.get("params", {})

        prompt = self._build_prompt(ticker, strategy_type, indicators, params)
        code = await call_llm("algorithm_assemble", prompt, _SYSTEM)
        code = self._clean_code(code)

        return {
            "ticker": ticker,
            "strategy_type": strategy_type,
            "exit_rules_code": code,
        }

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------
    @staticmethod
    def _build_prompt(ticker: str, strategy: str, indicators: list, params: dict) -> str:
        indicator_str = ", ".join(indicators) if indicators else "RSI, ATR, EMA_20"
        params_str = str(params) if params else "{}"
        return f"""Write a Python exit condition function for:
Ticker: {ticker}
Strategy type: {strategy}
Selected indicators: {indicator_str}
Parameters: {params_str}

The function signature must be EXACTLY:
    def check_exit(data: dict, position: dict) -> tuple[bool, str]

The `data` dict keys are identical to the entry function:
- "close", "rsi", "macd", "macd_signal", "atr", "ema20", "ema50",
  "ema200", "bb_upper", "bb_lower", "volume", "volume_ma20", "adx", "prev_close"

The `position` dict contains:
- "entry_price": float — price at which the position was opened
- "shares": float — number of shares held
- "side": str — "long" or "short"
- "bars_held": int — number of bars since entry
- "unrealized_pnl_pct": float — current unrealized P&L as a percentage

Return tuple (should_exit: bool, reason: str) where reason is one of:
stop_loss, take_profit, trailing_stop, time_exit, signal_reversal, or ""

Target a profitable strategy with decent win rate:
- Stop loss: exit if unrealized_pnl_pct <= -1.5 (reason: "stop_loss")
- Take profit: exit if unrealized_pnl_pct >= 1.0 (reason: "take_profit")
- Trend break: exit if close < ema20 AND pnl_pct < 0 (reason: "trend_break")
- Time exit: exit if bars_held >= 10 (reason: "time_exit")

This 1%TP / 1.5%stop ratio gives positive expected value at 55%+ win rate.
Do NOT use ATR-based exits — use EXACTLY the percentage values above.

Return ONLY the Python function. No markdown. No imports."""

    # ------------------------------------------------------------------
    # Code cleaner
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_code(raw: str) -> str:
        code = raw.strip()
        for fence in ("```python", "```py", "```"):
            if code.startswith(fence):
                code = code[len(fence):]
        if code.endswith("```"):
            code = code[:-3]
        code = code.strip()

        if "def check_exit" not in code:
            logger.warning("[ExitRulesMicroAgent] LLM did not return check_exit function, using fallback")
            return _FALLBACK_CODE

        return code
