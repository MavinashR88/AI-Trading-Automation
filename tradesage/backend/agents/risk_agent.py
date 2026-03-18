"""
Risk Manager Agent
Position sizing: Kelly criterion + portfolio constraints.
Stop-loss, take-profit, drawdown circuit breakers.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Tuple

from backend.models.trade import RiskParams
from backend.analytics.stats import kelly_fraction

logger = logging.getLogger(__name__)


class RiskAgent:
    """
    Computes position sizing, stop-loss, take-profit.
    Acts as Layer 2 of the pre-trade gate system.

    Gate result: (passed: bool, reason: str, params: RiskParams)
    """

    def __init__(
        self,
        starting_capital: float,
        max_position_pct: float = 0.10,      # 10% max single position
        risk_per_trade: float = 0.02,         # 2% stop-loss default
        reward_risk_ratio: float = 2.0,       # 2:1 R:R
        max_drawdown_pct: float = 0.15,       # halt at 15% drawdown
        max_daily_loss_pct: float = 0.05,     # 5% daily loss limit
    ):
        self._starting_capital = starting_capital
        self._max_position_pct = max_position_pct
        self._risk_per_trade = risk_per_trade
        self._reward_risk_ratio = reward_risk_ratio
        self._max_drawdown_pct = max_drawdown_pct
        self._max_daily_loss_pct = max_daily_loss_pct
        self._daily_loss: float = 0.0
        self._daily_loss_reset: datetime = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    def evaluate(
        self,
        portfolio_value: float,
        entry_price: float,
        action: str,
        win_rate: float,
        sentiment_confidence: float,
        daily_pnl: float = 0.0,
        starting_capital: Optional[float] = None,
        atr: Optional[float] = None,         # Average True Range for dynamic TP/SL
        atr_sl_mult: float = 1.5,            # SL = entry ± atr_sl_mult * ATR
        atr_tp_mult: float = 2.5,            # TP = entry ± atr_tp_mult * ATR
    ) -> Tuple[bool, str, Optional[RiskParams]]:
        """
        Run the risk gate.

        Returns:
            (gate_passed, reason, risk_params)
        """
        capital = starting_capital or self._starting_capital

        # ── Circuit Breaker 1: Max Drawdown ──────────────────────────────────
        drawdown = (capital - portfolio_value) / capital if capital > 0 else 0.0
        if drawdown >= self._max_drawdown_pct:
            return (
                False,
                f"MAX DRAWDOWN BREACHED: Portfolio down {drawdown:.1%} (limit: {self._max_drawdown_pct:.1%}). Trading halted.",
                None,
            )

        # ── Circuit Breaker 2: Daily Loss Limit ──────────────────────────────
        self._reset_daily_loss_if_needed()
        if daily_pnl < 0:
            daily_loss_pct = abs(daily_pnl) / portfolio_value
            if daily_loss_pct >= self._max_daily_loss_pct:
                return (
                    False,
                    f"DAILY LOSS LIMIT: Down {daily_loss_pct:.1%} today (limit: {self._max_daily_loss_pct:.1%}). No more trades today.",
                    None,
                )

        # ── Position Sizing ───────────────────────────────────────────────────
        avg_win_pct = self._risk_per_trade * self._reward_risk_ratio
        avg_loss_pct = self._risk_per_trade
        kelly = kelly_fraction(win_rate, avg_win_pct, avg_loss_pct)

        # Scale by sentiment confidence (0-1)
        sentiment_confidence = max(0.1, min(1.0, sentiment_confidence))
        raw_position_size = portfolio_value * kelly * sentiment_confidence

        # Cap at max single position
        max_position = portfolio_value * self._max_position_pct
        position_size = min(raw_position_size, max_position)
        position_size = max(position_size, 100.0)   # minimum $100

        position_size_pct = position_size / portfolio_value

        # ── Stop-Loss and Take-Profit ─────────────────────────────────────────
        if atr and atr > 0:
            # Dynamic ATR-based exits (wider, market-adapted)
            if action == "buy":
                stop_loss = entry_price - atr_sl_mult * atr
                take_profit = entry_price + atr_tp_mult * atr
            else:  # sell/short
                stop_loss = entry_price + atr_sl_mult * atr
                take_profit = entry_price - atr_tp_mult * atr
            logger.info("ATR-based TP/SL: ATR=%.4f  SL=%.4f  TP=%.4f", atr, stop_loss, take_profit)
        else:
            # Fixed % fallback
            if action == "buy":
                stop_loss = entry_price * (1 - self._risk_per_trade)
                take_profit = entry_price * (1 + self._risk_per_trade * self._reward_risk_ratio)
            else:  # sell/short
                stop_loss = entry_price * (1 + self._risk_per_trade)
                take_profit = entry_price * (1 - self._risk_per_trade * self._reward_risk_ratio)

        max_loss_dollars = position_size * self._risk_per_trade

        # ── Sanity Checks ─────────────────────────────────────────────────────
        if entry_price <= 0:
            return (False, f"Invalid entry price: {entry_price}", None)

        if position_size < 50:
            return (
                False,
                f"Position size ${position_size:.2f} too small to be meaningful. Portfolio may be too depleted.",
                None,
            )

        params = RiskParams(
            position_size=round(position_size, 2),
            position_size_pct=round(position_size_pct, 4),
            stop_loss=round(stop_loss, 4),
            take_profit=round(take_profit, 4),
            entry_price=round(entry_price, 4),
            risk_per_trade=self._risk_per_trade,
            kelly_fraction=kelly,
            max_loss_dollars=round(max_loss_dollars, 2),
        )

        reason = (
            f"Risk gate PASSED. Kelly={kelly:.3f}, sentiment_conf={sentiment_confidence:.2f}, "
            f"size=${position_size:.2f} ({position_size_pct:.1%}), "
            f"SL=${stop_loss:.2f}, TP=${take_profit:.2f}"
        )
        logger.info(reason)
        return (True, reason, params)

    @staticmethod
    def compute_atr(ticker: str, period: int = 14) -> Optional[float]:
        """
        Fetch OHLCV from yfinance and compute ATR(14).
        Returns None if data unavailable.
        """
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period=f"{period + 5}d", interval="1d")
            if hist.empty or len(hist) < period:
                return None
            highs = hist["High"].values
            lows = hist["Low"].values
            closes = hist["Close"].values
            trs = []
            for i in range(1, len(hist)):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
                trs.append(tr)
            if not trs:
                return None
            atr = sum(trs[-period:]) / min(len(trs), period)
            return round(atr, 4)
        except Exception as exc:
            logger.warning("ATR computation failed for %s: %s", ticker, exc)
            return None

    def record_trade_pnl(self, pnl_dollars: float) -> None:
        """Track daily P&L for daily loss limit enforcement."""
        self._reset_daily_loss_if_needed()
        if pnl_dollars < 0:
            self._daily_loss += abs(pnl_dollars)

    def _reset_daily_loss_if_needed(self) -> None:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if today_start > self._daily_loss_reset:
            self._daily_loss = 0.0
            self._daily_loss_reset = today_start

    def get_risk_summary(self, portfolio_value: float) -> dict:
        """Return current risk metrics for the dashboard."""
        capital = self._starting_capital
        drawdown = (capital - portfolio_value) / capital if capital > 0 else 0.0
        return {
            "portfolio_value": portfolio_value,
            "starting_capital": capital,
            "current_drawdown_pct": round(drawdown * 100, 2),
            "max_drawdown_limit_pct": round(self._max_drawdown_pct * 100, 2),
            "daily_loss_dollars": round(self._daily_loss, 2),
            "daily_loss_limit_pct": round(self._max_daily_loss_pct * 100, 2),
            "max_position_pct": round(self._max_position_pct * 100, 2),
            "risk_per_trade_pct": round(self._risk_per_trade * 100, 2),
            "reward_risk_ratio": self._reward_risk_ratio,
            "trading_halted": drawdown >= self._max_drawdown_pct,
        }
