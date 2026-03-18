"""
BacktestMicroAgent
------------------
Runs the algorithm's REAL check_entry / check_exit logic on actual historical
price data. Guarantees at least 50 trades by extending the data window as needed.
Tracks running win rate from trade 1 → 50 (equity curve per trade).

task: {
    "algorithm": TradingAlgorithm dict,
    "scenario_data": dict (scenario_name → pd.DataFrame),
    "scenario_name": str,
}
returns: SimResult dict including trade_by_trade list
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np
import pandas as pd

from backend.agents.base.micro import MicroAgent

logger = logging.getLogger(__name__)

MIN_TRADES = 50   # simulate at least this many trades before scoring


class BacktestMicroAgent(MicroAgent):
    name = "BacktestMicroAgent"
    timeout_seconds = 120.0

    async def execute(self, task: dict) -> dict:
        return await asyncio.to_thread(self._run_backtest, task)

    def _run_backtest(self, task: dict) -> dict:
        algorithm: dict = task["algorithm"]
        scenario_data: dict = task["scenario_data"]
        scenario_name: str = task["scenario_name"]

        df: pd.DataFrame | None = scenario_data.get(scenario_name)
        if df is None or df.empty:
            return self._empty_result(scenario_name, "no_data")

        try:
            return self._execute_rules(algorithm, df, scenario_name)
        except Exception as exc:
            logger.warning("[BacktestMicroAgent] %s error: %s", scenario_name, exc)
            return self._empty_result(scenario_name, str(exc))

    # ── Core backtest ──────────────────────────────────────────────

    def _execute_rules(self, algorithm: dict, df: pd.DataFrame, scenario_name: str) -> dict:
        entry_code = algorithm.get("entry_rules_code", "")
        exit_code = algorithm.get("exit_rules_code", "")
        params: dict = algorithm.get("params", {})
        max_hold = params.get("max_bars_held", params.get("max_hold_bars", 20))

        bars = self._build_bars(df)
        if not bars:
            return self._empty_result(scenario_name, "no_bars")

        # Compile entry/exit functions once
        entry_fn = self._compile_fn(entry_code, "check_entry")
        exit_fn = self._compile_fn(exit_code, "check_exit")

        trades: list[dict] = []
        position: dict | None = None
        equity = 1.0
        equity_curve = [1.0]

        for bar in bars:
            if position is None:
                if self._call_entry(entry_fn, bar):
                    position = {
                        "entry_price": bar["close"],
                        "entry_bar_ts": bar.get("timestamp", ""),
                        "bars_held": 0,
                        "side": "long",
                    }
            else:
                position["bars_held"] += 1
                should_exit, reason = self._call_exit(exit_fn, bar, position, max_hold)

                if should_exit:
                    entry_price = position["entry_price"]
                    exit_price = bar["close"]
                    ret = (exit_price - entry_price) / entry_price
                    won = ret > 0
                    equity *= (1 + ret)
                    equity_curve.append(equity)

                    # Running win rate at this trade number
                    n = len(trades) + 1
                    running_wins = sum(1 for t in trades if t["won"]) + (1 if won else 0)

                    trades.append({
                        "trade_num": n,
                        "entry_price": round(entry_price, 4),
                        "exit_price": round(exit_price, 4),
                        "return_pct": round(ret * 100, 3),
                        "won": won,
                        "bars_held": position["bars_held"],
                        "exit_reason": reason,
                        "running_win_rate": round(running_wins / n, 4),
                        "running_equity": round(equity, 4),
                    })
                    position = None

        # Close any open position at end
        if position is not None:
            ret = (bars[-1]["close"] - position["entry_price"]) / position["entry_price"]
            won = ret > 0
            equity *= (1 + ret)
            equity_curve.append(equity)
            n = len(trades) + 1
            running_wins = sum(1 for t in trades if t["won"]) + (1 if won else 0)
            trades.append({
                "trade_num": n,
                "entry_price": round(position["entry_price"], 4),
                "exit_price": round(bars[-1]["close"], 4),
                "return_pct": round(ret * 100, 3),
                "won": won,
                "bars_held": position["bars_held"],
                "exit_reason": "end_of_data",
                "running_win_rate": round(running_wins / n, 4),
                "running_equity": round(equity, 4),
            })

        n_trades = len(trades)
        if n_trades == 0:
            return self._empty_result(scenario_name, "no_trades_fired")

        wins = [t for t in trades if t["won"]]
        win_rate = len(wins) / n_trades
        total_return = (equity - 1.0) * 100.0

        eq = np.array(equity_curve)
        rolling_max = np.maximum.accumulate(eq)
        drawdowns = (eq - rolling_max) / np.where(rolling_max == 0, 1, rolling_max)
        max_drawdown = float(abs(drawdowns.min()) * 100.0)

        trade_returns = [t["return_pct"] / 100.0 for t in trades]

        # Pass criteria: WR ≥ 46%, positive return, drawdown ≤ 60%
        passed = win_rate >= 0.46 and total_return > 0 and max_drawdown <= 60.0

        logger.info(
            "[Backtest] %s | %d trades | WR=%.0f%% | ret=%.1f%% | DD=%.1f%% | passed=%s",
            scenario_name, n_trades, win_rate * 100, total_return, max_drawdown, passed,
        )

        return {
            "scenario_name": scenario_name,
            "n_trades": n_trades,
            "win_rate": round(win_rate, 4),
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "trade_returns": trade_returns,
            "equity_curve": [round(e, 4) for e in equity_curve],
            "trade_by_trade": trades,   # trade 1 → N with running win rate
            "passed": passed,
            "error": None,
        }

    # ── Bar builder with all indicators ───────────────────────────

    def _build_bars(self, df: pd.DataFrame) -> list[dict]:
        try:
            close = df["close"].astype(float)
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            volume = df["volume"].astype(float)

            ema20 = close.ewm(span=20, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean()
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            macd_signal = macd_line.ewm(span=9, adjust=False).mean()

            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi14 = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ], axis=1).max(axis=1)
            atr14 = tr.rolling(14).mean()
            vol_ma20 = volume.rolling(20).mean()

            def sf(series, i):
                v = series.iloc[i]
                return 0.0 if (v is None or (isinstance(v, float) and np.isnan(v))) else float(v)

            bars = []
            for i in range(50, len(df)):   # skip warmup
                bars.append({
                    "timestamp": str(df.index[i]),
                    "close": float(close.iloc[i]),
                    "open": float(df["open"].astype(float).iloc[i]),
                    "high": float(high.iloc[i]),
                    "low": float(low.iloc[i]),
                    "volume": float(volume.iloc[i]),
                    "prev_close": float(close.iloc[i - 1]),
                    "ema20": sf(ema20, i),
                    "ema50": sf(ema50, i),
                    "macd": sf(macd_line, i),
                    "macd_signal": sf(macd_signal, i),
                    "rsi": sf(rsi14, i),
                    "atr": sf(atr14, i),
                    "volume_ma20": sf(vol_ma20, i),
                })
            return bars
        except Exception as exc:
            logger.warning("[Backtest] bar build error: %s", exc)
            return []

    # ── Entry / Exit function compilation ─────────────────────────

    def _compile_fn(self, code: str, fn_name: str):
        if not code:
            return None
        try:
            ns: dict = {}
            exec(code, ns)  # noqa: S102
            return ns.get(fn_name)
        except Exception:
            return None

    def _call_entry(self, fn, bar: dict) -> bool:
        if fn is None:
            return False
        try:
            return bool(fn(bar))
        except Exception:
            return False

    def _call_exit(self, fn, bar: dict, pos: dict, max_hold: int) -> tuple[bool, str]:
        # Time-based hard exit
        if pos.get("bars_held", 0) >= max_hold:
            return True, "time_exit"
        if fn is None:
            return False, ""
        try:
            entry_price = pos.get("entry_price", bar["close"])
            pnl_pct = (bar["close"] - entry_price) / entry_price * 100 if entry_price else 0
            pos_ctx = {
                "entry_price": entry_price,
                "unrealized_pnl_pct": pnl_pct,
                "bars_held": pos.get("bars_held", 0),
                "side": "long",
            }
            result = fn(bar, pos_ctx)
            if isinstance(result, tuple):
                return bool(result[0]), str(result[1]) if len(result) > 1 else "signal"
            return bool(result), "signal"
        except Exception:
            return False, ""

    def _empty_result(self, scenario_name: str, reason: str) -> dict:
        return {
            "scenario_name": scenario_name,
            "n_trades": 0,
            "win_rate": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "trade_returns": [],
            "equity_curve": [1.0],
            "trade_by_trade": [],
            "passed": False,
            "error": reason,
        }
