"""
AlgorithmAssemblerMicroAgent
-----------------------------
Combines the outputs of IndicatorSelectorMicroAgent, EntryRulesMicroAgent,
and ExitRulesMicroAgent into 3 concrete TradingAlgorithm instances —
one for each strategy variant: momentum, event_driven, and breakout.

Each variant gets slightly adjusted indicator parameters to reflect
the different holding period and risk profile. A simple position-sizing
code snippet is also generated inline (no LLM call needed here — it
follows a fixed ATR-based formula).

task: {
    "ticker": str,
    "base_indicators": list[str],          # from IndicatorSelectorMicro
    "base_params": dict,                   # from IndicatorSelectorMicro
    "entry_rules_code": str,               # from EntryRulesMicro
    "exit_rules_code": str,                # from ExitRulesMicro
    "strategy_type": str,                  # primary strategy from research verdict
}

Returns: list[TradingAlgorithm]  (3 variants)
"""
from __future__ import annotations

import logging
import uuid
from copy import deepcopy

from backend.agents.base.micro import MicroAgent
from backend.models.trading_algorithm import TradingAlgorithm

logger = logging.getLogger(__name__)

# Position-sizing code template (ATR-based, 1% risk per trade)
_POSITION_SIZING_CODE = """\
def position_size(portfolio_value: float, entry_price: float, atr: float,
                  risk_pct: float = 0.01, atr_stop_mult: float = {atr_mult}) -> int:
    \"\"\"ATR-based position sizing. Risks risk_pct of portfolio per trade.\"\"\"
    if entry_price <= 0 or atr <= 0:
        return 0
    stop_distance = atr * atr_stop_mult
    dollar_risk = portfolio_value * risk_pct
    shares = int(dollar_risk / stop_distance)
    # Cap at 10% of portfolio in any single position
    max_shares = int((portfolio_value * 0.10) / entry_price)
    return min(shares, max_shares)
"""

# Per-variant parameter overrides applied on top of base_params
_VARIANT_PARAM_OVERRIDES: dict[str, dict] = {
    "momentum": {
        "rsi_entry_min": 50,
        "rsi_entry_max": 70,
        "atr_stop_multiplier": 2.0,
        "take_profit_r_multiple": 3.0,
        "max_bars_held": 20,
    },
    "event_driven": {
        "rsi_entry_min": 40,
        "rsi_entry_max": 80,
        "atr_stop_multiplier": 1.0,
        "take_profit_r_multiple": 2.0,
        "max_bars_held": 5,
    },
    "breakout": {
        "rsi_entry_min": 55,
        "rsi_entry_max": 80,
        "atr_stop_multiplier": 1.5,
        "take_profit_r_multiple": 4.0,
        "max_bars_held": 30,
    },
}


class AlgorithmAssemblerMicroAgent(MicroAgent):
    name = "AlgorithmAssemblerMicroAgent"
    timeout_seconds = 10.0  # pure in-memory assembly — fast

    async def execute(self, task: dict) -> list[TradingAlgorithm]:
        ticker: str = task["ticker"]
        base_indicators: list = task.get("base_indicators", ["RSI", "ATR", "EMA_20"])
        base_params: dict = task.get("base_params", {})
        entry_code: str = task.get("entry_rules_code", "")
        exit_code: str = task.get("exit_rules_code", "")
        primary_strategy: str = task.get("strategy_type", "momentum")

        variants = ["momentum", "event_driven", "breakout"]
        algorithms: list[TradingAlgorithm] = []

        for strategy in variants:
            params = deepcopy(base_params)
            params.update(_VARIANT_PARAM_OVERRIDES.get(strategy, {}))

            atr_mult = params.get("atr_stop_multiplier", 2.0)
            sizing_code = _POSITION_SIZING_CODE.format(atr_mult=atr_mult)

            algo = TradingAlgorithm(
                id=str(uuid.uuid4()),
                ticker=ticker,
                name=f"{ticker}_{strategy}_v1",
                strategy_type=strategy,
                entry_rules_code=entry_code,
                exit_rules_code=exit_code,
                position_sizing_code=sizing_code,
                indicators=list(base_indicators),
                params=params,
                status="DRAFT",
                paper_trades_done=0,
                paper_trades_required=50,
            )
            algorithms.append(algo)
            logger.debug(
                "[AlgorithmAssemblerMicroAgent] Built %s for %s", algo.name, ticker
            )

        # Put the primary recommended strategy first
        algorithms.sort(key=lambda a: (0 if a.strategy_type == primary_strategy else 1))

        return algorithms
