"""
PaperTradingRunner — runs every 5 minutes.

Strategy:
  On first encounter of a PAPER_TRADING algorithm, run a HISTORICAL BACKTEST
  on the last 60 days of real hourly bars. This gives 20+ trades quickly and
  tests against real (not synthetic) market conditions.

  After the historical backtest completes, switch to LIVE SIGNAL mode —
  checking each new hourly bar as it forms for ongoing monitoring.

Simulation (stage 4) = synthetic random data  →  stress test worst-case
Paper Trading (stage 5) = real historical data →  real market validation
Live Trading (stage 6) = real live money       →  graduated algos only
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.db.router import DataRouter
    from backend.data.market_data import MarketDataFeed

logger = logging.getLogger(__name__)


class PaperTradingRunner:
    def __init__(self, data_router: "DataRouter", market_data: "MarketDataFeed"):
        self._dr = data_router
        self._md = market_data
        # algo_id → last live bar timestamp (for live signal mode)
        self._last_bar_ts: dict[str, str] = {}
        # algo_ids that have completed historical backtest
        self._backtested: set[str] = set()

    # ── Public ─────────────────────────────────────────────────────

    async def run_cycle(self) -> None:
        algos = self._dr.get_algorithms(status="PAPER_TRADING")
        if not algos:
            return
        logger.info("[PaperTrader] Cycle: %d algorithms", len(algos))
        for algo in algos:
            try:
                await self._process_algo(algo)
            except Exception as exc:
                logger.warning("[PaperTrader] Error on %s: %s", algo.get("name"), exc)

    # ── Per-algorithm logic ────────────────────────────────────────

    async def _process_algo(self, algo: dict) -> None:
        algo_id = algo["id"]

        trades_done = algo.get("paper_trades_done", 0)
        trades_required = algo.get("paper_trades_required", 20)

        # If already done paper trading, skip
        if trades_done >= trades_required:
            return

        # Determine if historical backtest has already run.
        # Use trades_done == 0 as the authoritative check (survives restarts).
        # Clear in-memory cache if DB says 0 trades (algo was reset externally).
        if trades_done == 0:
            self._backtested.discard(algo_id)  # remove stale cache if algo was reset

        if trades_done == 0 and algo_id not in self._backtested:
            await self._run_historical_backtest(algo)
            self._backtested.add(algo_id)
        else:
            # Historical backtest done — check live signal on new daily bars
            self._backtested.add(algo_id)  # ensure in-memory set stays consistent
            await self._check_live_bar(algo)

    # ── Historical backtest on real 60-day hourly data ─────────────

    async def _run_historical_backtest(self, algo: dict) -> None:
        algo_id = algo["id"]
        ticker = algo["ticker"]
        logger.info("[PaperTrader] Running historical backtest for %s (%s)", algo.get("name"), ticker)

        hist = await asyncio.to_thread(self._fetch_history, ticker)
        if hist is None or len(hist) < 60:
            logger.warning("[PaperTrader] Not enough history for %s", ticker)
            return

        bars = self._build_bars(hist)
        if not bars:
            return

        position = None
        wins, losses = 0, 0
        total_pnl = 0.0

        for i, bar in enumerate(bars):
            if position is None:
                if self._run_entry(algo, bar):
                    position = {
                        "entry_price": bar["close"],
                        "entry_time": bar.get("timestamp", ""),
                        "bars_held": 0,
                        "side": "long",
                    }
            else:
                position["bars_held"] += 1
                should_exit, reason = self._run_exit(algo, bar, position)
                if should_exit:
                    entry_price = position["entry_price"]
                    pnl_pct = (bar["close"] - entry_price) / entry_price * 100
                    if pnl_pct > 0:
                        wins += 1
                    else:
                        losses += 1
                    total_pnl += pnl_pct
                    position = None

                    self._dr.log_pipeline_event(
                        "paper_trade", "PAPER_TRADING",
                        "WIN" if pnl_pct > 0 else "LOSS",
                        ticker=ticker, algorithm_id=algo_id,
                        detail=f"Historical | PnL:{pnl_pct:+.2f}% | {reason}",
                    )

        total_trades = wins + losses
        if total_trades == 0:
            logger.warning("[PaperTrader] %s: no trades fired in historical backtest — entry conditions too strict", algo.get("name"))
            # Mark as having tried to avoid infinite loops
            return

        win_rate = wins / total_trades
        self._dr.update_algorithm_status(
            algo_id, "PAPER_TRADING",
            paper_trades_done=total_trades,
            paper_win_rate=round(win_rate, 4),
            paper_pnl_pct=round(total_pnl, 4),
        )
        logger.info(
            "[PaperTrader] %s historical backtest: %d trades | WR %.0f%% | PnL %.2f%%",
            algo.get("name"), total_trades, win_rate * 100, total_pnl,
        )

    # ── Live signal check (post-backtest) ─────────────────────────

    async def _check_live_bar(self, algo: dict) -> None:
        """After historical backtest, monitor new hourly bars for ongoing signal tracking."""
        algo_id = algo["id"]
        bar = await self._get_latest_bar(algo["ticker"])
        if not bar:
            return

        bar_ts = bar.get("timestamp", "")
        if bar_ts == self._last_bar_ts.get(algo_id):
            return  # same candle, skip
        self._last_bar_ts[algo_id] = bar_ts

        entry = self._run_entry(algo, bar)
        logger.debug("[PaperTrader] %s live bar %s → entry=%s", algo.get("name"), bar_ts, entry)

    # ── Entry / Exit ───────────────────────────────────────────────

    def _run_entry(self, algo: dict, bar: dict) -> bool:
        code = algo.get("entry_rules_code", "")
        if not code:
            return False
        try:
            ns: dict = {}
            exec(code, ns)  # noqa: S102
            if "check_entry" in ns:
                return bool(ns["check_entry"](bar))
        except Exception as exc:
            logger.debug("[PaperTrader] entry exec %s: %s", algo.get("id"), exc)
        return False

    def _run_exit(self, algo: dict, bar: dict, pos: dict) -> tuple[bool, str]:
        code = algo.get("exit_rules_code", "")
        if not code:
            return pos.get("bars_held", 0) >= 20, "time_exit"
        try:
            ns: dict = {}
            exec(code, ns)  # noqa: S102
            entry_price = pos.get("entry_price", bar["close"])
            pnl_pct = (bar["close"] - entry_price) / entry_price * 100 if entry_price else 0
            pos_ctx = {
                "entry_price": entry_price,
                "unrealized_pnl_pct": pnl_pct,
                "bars_held": pos.get("bars_held", 0),
                "side": pos.get("side", "long"),
            }
            if "check_exit" in ns:
                result = ns["check_exit"](bar, pos_ctx)
                if isinstance(result, tuple):
                    return bool(result[0]), str(result[1]) if len(result) > 1 else ""
                return bool(result), ""
        except Exception as exc:
            logger.debug("[PaperTrader] exit exec %s: %s", algo.get("id"), exc)
        return False, ""

    # ── Bar building ───────────────────────────────────────────────

    def _build_bars(self, hist) -> list[dict]:
        """Convert history DataFrame to list of bar dicts with all indicators."""
        try:
            import numpy as np
            import pandas as pd

            close = hist["close"].astype(float)
            high = hist["high"].astype(float)
            low = hist["low"].astype(float)
            volume = hist["volume"].astype(float)

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

            bars = []
            for i in range(50, len(close)):  # skip warmup bars
                bars.append({
                    "timestamp": str(hist.index[i]),
                    "close": float(close.iloc[i]),
                    "open": float(hist["open"].astype(float).iloc[i]),
                    "high": float(high.iloc[i]),
                    "low": float(low.iloc[i]),
                    "volume": float(volume.iloc[i]),
                    "prev_close": float(close.iloc[i - 1]),
                    "ema20": float(ema20.iloc[i]),
                    "ema50": float(ema50.iloc[i]),
                    "macd": float(macd_line.iloc[i]),
                    "macd_signal": float(macd_signal.iloc[i]),
                    "rsi": float(rsi14.iloc[i]),
                    "atr": float(atr14.iloc[i]),
                    "volume_ma20": float(vol_ma20.iloc[i]),
                })
            return bars
        except Exception as exc:
            logger.warning("[PaperTrader] bar build error: %s", exc)
            return []

    async def _get_latest_bar(self, ticker: str) -> dict | None:
        """Get only the most recent bar for live signal checking."""
        hist = await asyncio.to_thread(self._fetch_history, ticker)
        if hist is None or len(hist) < 60:
            return None
        bars = self._build_bars(hist)
        return bars[-1] if bars else None

    # ── For AlgoGate compatibility ─────────────────────────────────

    async def _get_bar(self, ticker: str) -> dict | None:
        return await self._get_latest_bar(ticker)

    @staticmethod
    def _fetch_history(ticker: str):
        try:
            import yfinance as yf
            # Maximum available history — up to 10+ years for major stocks.
            # Needed so selective strategies accumulate 50 paper trades.
            for period in ("max", "10y", "5y"):
                df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
                if df is not None and not df.empty and len(df) >= 120:
                    break
            else:
                return None
            df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
            return df
        except Exception:
            return None
