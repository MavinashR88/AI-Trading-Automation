"""
Trade Executor Agent
Paper mode: Alpaca paper trading API
Live mode: Alpaca live (stocks) + CCXT (crypto/forex)
Logs every order to SQLite + emits WebSocket events.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Optional

from backend.models.trade import RiskParams, Signal, TradeResult
from backend.db.sqlite_store import TradeStore

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Executes paper or live orders.
    TRADING_MODE is read from config and hot-swappable.
    """

    CRYPTO_SYMBOLS = {"BTC", "ETH", "BNB", "SOL", "ADA", "MATIC", "DOT", "AVAX"}

    def __init__(
        self,
        alpaca_api_key: str,
        alpaca_secret: str,
        alpaca_paper_url: str,
        alpaca_live_url: str,
        exchange_id: str,
        exchange_api_key: str,
        exchange_secret: str,
        store: TradeStore,
        mode: str = "paper",
        websocket_emitter: Optional[Callable] = None,
    ):
        self._alpaca_key = alpaca_api_key
        self._alpaca_secret = alpaca_secret
        self._paper_url = alpaca_paper_url
        self._live_url = alpaca_live_url
        self._exchange_id = exchange_id
        self._exchange_api_key = exchange_api_key
        self._exchange_secret = exchange_secret
        self._store = store
        self._mode = mode
        self._emit = websocket_emitter   # async callable(event_type, data)
        self._alpaca_client = None
        self._ccxt_exchange = None

    # ── Mode ──────────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def switch_mode(self, new_mode: str) -> None:
        self._mode = new_mode
        self._alpaca_client = None   # force reconnect with correct URL
        logger.warning("Trade executor mode switched to: %s", new_mode.upper())

    # ── Alpaca Client ─────────────────────────────────────────────────────────

    def _get_alpaca_client(self):
        if self._alpaca_client is None:
            try:
                from alpaca.trading.client import TradingClient
                url = self._live_url if self._mode == "live" else self._paper_url
                self._alpaca_client = TradingClient(
                    api_key=self._alpaca_key,
                    secret_key=self._alpaca_secret,
                    paper=(self._mode == "paper"),
                    url_override=url,
                )
            except Exception as exc:
                logger.error("Alpaca client init failed: %s", exc)
                raise
        return self._alpaca_client

    # ── CCXT Client ───────────────────────────────────────────────────────────

    def _get_ccxt_exchange(self):
        if self._ccxt_exchange is None:
            try:
                import ccxt
                ExchangeClass = getattr(ccxt, self._exchange_id)
                self._ccxt_exchange = ExchangeClass({
                    "apiKey": self._exchange_api_key,
                    "secret": self._exchange_secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": "spot"},
                })
            except Exception as exc:
                logger.error("CCXT init failed for %s: %s", self._exchange_id, exc)
                raise
        return self._ccxt_exchange

    # ── Execute Trade ─────────────────────────────────────────────────────────

    async def execute(
        self,
        signal: Signal,
        risk_params: RiskParams,
    ) -> TradeResult:
        """
        Execute a trade based on a signal and risk parameters.
        Routes to Alpaca (stocks) or CCXT (crypto/forex).
        """
        ticker = signal.ticker
        action = signal.action
        is_crypto = ticker.upper() in self.CRYPTO_SYMBOLS or "/" in ticker

        try:
            if is_crypto and (self._exchange_api_key or self._mode == "paper"):
                result = await self._execute_crypto(signal, risk_params)
            else:
                result = await self._execute_stock(signal, risk_params)
        except Exception as exc:
            logger.error("Trade execution failed for %s: %s — falling back to paper simulation", ticker, exc)
            result = self._simulate_fill(
                signal, risk_params,
                quantity=risk_params.position_size / max(signal.entry_price, 0.01),
            )

        # Emit WebSocket event
        if self._emit:
            try:
                await self._emit("trade_fill", result.model_dump(mode="json"))
            except Exception as exc:
                logger.warning("WebSocket emit failed: %s", exc)

        return result

    async def _is_market_open(self) -> bool:
        """Check Alpaca clock to see if market is currently open."""
        try:
            client = await asyncio.to_thread(self._get_alpaca_client)
            clock = await asyncio.to_thread(client.get_clock)
            return bool(clock.is_open)
        except Exception:
            return False  # assume closed if we can't check

    async def _execute_stock(self, signal: Signal, risk_params: RiskParams) -> TradeResult:
        """Execute a stock bracket order via Alpaca (auto TP + SL attached)."""
        from alpaca.trading.requests import (
            MarketOrderRequest, TakeProfitRequest, StopLossRequest
        )
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        quantity = risk_params.position_size / max(signal.entry_price, 0.01)
        quantity = max(1.0, round(quantity, 0))

        side = OrderSide.BUY if signal.action == "buy" else OrderSide.SELL

        # Market closed in paper mode → simulate fill (no real order)
        if self._mode == "paper":
            market_open = await self._is_market_open()
            if not market_open:
                logger.info("[PAPER] Market closed — simulating fill for %s", signal.ticker)
                return self._simulate_fill(signal, risk_params, quantity)

        # Round TP/SL to 2 decimal places
        tp = round(risk_params.take_profit, 2)
        sl = round(risk_params.stop_loss, 2)

        # Build bracket order so Alpaca auto-closes at TP or SL
        order_request = MarketOrderRequest(
            symbol=signal.ticker,
            qty=quantity,
            side=side,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=tp),
            stop_loss=StopLossRequest(stop_price=sl),
        )

        try:
            client = await asyncio.to_thread(self._get_alpaca_client)
            order = await asyncio.to_thread(client.submit_order, order_request)
            order_id = str(order.id)

            # Poll for actual fill — market orders fill within 1-2s
            filled_qty = float(order.filled_qty or 0)
            filled_price = float(order.filled_avg_price or 0)
            if filled_qty == 0:
                await asyncio.sleep(2)
                try:
                    order = await asyncio.to_thread(client.get_order_by_id, order_id)
                    filled_qty = float(order.filled_qty or 0)
                    filled_price = float(order.filled_avg_price or signal.entry_price)
                except Exception:
                    pass
            # Final fallback to computed quantity
            if filled_qty == 0:
                filled_qty = quantity
            if filled_price == 0:
                filled_price = signal.entry_price

            result = TradeResult(
                trade_id=signal.trade_id,
                ticker=signal.ticker,
                side=signal.action,
                entry_price=filled_price,
                quantity=filled_qty,
                filled_at=datetime.utcnow(),
                outcome="OPEN",
                mode=self._mode,
                order_id=order_id,
                stop_loss=risk_params.stop_loss,
                take_profit=risk_params.take_profit,
            )
            logger.info(
                "Alpaca bracket order: %s %s x%.0f @ $%.4f  SL=$%.2f  TP=$%.2f",
                self._mode, signal.ticker, filled_qty, filled_price, sl, tp
            )
            return result

        except Exception as exc:
            logger.error("Alpaca bracket order failed: %s — falling back to simple market order", exc)
            # Try plain market order as fallback
            try:
                plain_request = MarketOrderRequest(
                    symbol=signal.ticker,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )
                client = await asyncio.to_thread(self._get_alpaca_client)
                order = await asyncio.to_thread(client.submit_order, plain_request)
                plain_id = str(order.id)
                filled_qty = float(order.filled_qty or 0)
                filled_price = float(order.filled_avg_price or 0)
                if filled_qty == 0:
                    await asyncio.sleep(2)
                    try:
                        order = await asyncio.to_thread(client.get_order_by_id, plain_id)
                        filled_qty = float(order.filled_qty or 0)
                        filled_price = float(order.filled_avg_price or signal.entry_price)
                    except Exception:
                        pass
                if filled_qty == 0:
                    filled_qty = quantity
                if filled_price == 0:
                    filled_price = signal.entry_price
                return TradeResult(
                    trade_id=signal.trade_id,
                    ticker=signal.ticker,
                    side=signal.action,
                    entry_price=filled_price,
                    quantity=filled_qty,
                    filled_at=datetime.utcnow(),
                    outcome="OPEN",
                    mode=self._mode,
                    order_id=plain_id,
                    stop_loss=risk_params.stop_loss,
                    take_profit=risk_params.take_profit,
                )
            except Exception as exc2:
                logger.error("Plain market order also failed: %s", exc2)
                if self._mode == "paper":
                    return self._simulate_fill(signal, risk_params, quantity)
                raise

    async def _execute_crypto(self, signal: Signal, risk_params: RiskParams) -> TradeResult:
        """Execute a crypto order via CCXT, or simulate in paper mode."""
        symbol = signal.ticker
        if "/" not in symbol:
            symbol = f"{symbol}/USDT"

        quantity = risk_params.position_size / max(signal.entry_price, 0.0001)

        if self._mode == "paper" or not self._exchange_api_key:
            return self._simulate_fill(signal, risk_params, quantity)

        try:
            exchange = await asyncio.to_thread(self._get_ccxt_exchange)
            side = "buy" if signal.action == "buy" else "sell"
            order = await asyncio.to_thread(
                exchange.create_order,
                symbol=symbol,
                type="market",
                side=side,
                amount=quantity,
            )
            filled_price = float(order.get("price") or order.get("average") or signal.entry_price)
            filled_qty = float(order.get("filled") or quantity)

            return TradeResult(
                trade_id=signal.trade_id,
                ticker=signal.ticker,
                side=signal.action,
                entry_price=filled_price,
                quantity=filled_qty,
                filled_at=datetime.utcnow(),
                outcome="OPEN",
                mode=self._mode,
                order_id=str(order.get("id", "")),
                stop_loss=risk_params.stop_loss,
                take_profit=risk_params.take_profit,
            )
        except Exception as exc:
            logger.error("CCXT order failed: %s", exc)
            return self._simulate_fill(signal, risk_params, quantity)

    def _simulate_fill(
        self, signal: Signal, risk_params: RiskParams, quantity: float
    ) -> TradeResult:
        """Simulate a paper trade fill at the signal's entry price."""
        logger.info(
            "[PAPER] Simulated fill: %s %s x%.4f @ $%.4f",
            signal.action.upper(), signal.ticker, quantity, signal.entry_price
        )
        return TradeResult(
            trade_id=signal.trade_id,
            ticker=signal.ticker,
            side=signal.action,
            entry_price=signal.entry_price,
            quantity=quantity,
            filled_at=datetime.utcnow(),
            outcome="OPEN",
            mode="paper",
            order_id=f"paper_{signal.trade_id[:8]}",
            stop_loss=risk_params.stop_loss,
            take_profit=risk_params.take_profit,
        )

    # ── Close Trade ───────────────────────────────────────────────────────────

    async def close_trade(
        self,
        result: TradeResult,
        current_price: float,
    ) -> TradeResult:
        """
        Close an open position on Alpaca and compute P&L.
        Submits the closing order to Alpaca so buying power is restored.
        """
        entry = result.entry_price
        qty = result.quantity
        side = result.side

        # Submit closing order to Alpaca to actually free up buying power
        # Only when market is open (avoid queuing weekend DAY orders)
        market_open = await self._is_market_open()
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            if not market_open:
                raise RuntimeError("Market is closed — simulating close price")

            close_side = OrderSide.SELL if side == "buy" else OrderSide.BUY
            close_qty = max(1.0, round(qty, 0))
            close_request = MarketOrderRequest(
                symbol=result.ticker,
                qty=close_qty,
                side=close_side,
                time_in_force=TimeInForce.DAY,
            )
            client = await asyncio.to_thread(self._get_alpaca_client)
            close_order = await asyncio.to_thread(client.submit_order, close_request)
            filled_close_price = float(close_order.filled_avg_price or current_price)
            current_price = filled_close_price
            logger.info("Closed position on Alpaca: %s %s qty=%s @ $%.4f",
                       close_side, result.ticker, close_qty, current_price)
        except Exception as exc:
            logger.warning("Alpaca close order failed (using simulated price): %s", exc)

        if side == "buy":
            pnl_pct = (current_price - entry) / entry * 100
            pnl_dollars = (current_price - entry) * qty
        else:  # short
            pnl_pct = (entry - current_price) / entry * 100
            pnl_dollars = (entry - current_price) * qty

        hold_seconds = 0
        if result.filled_at:
            hold_seconds = int((datetime.utcnow() - result.filled_at).total_seconds())
        hold_minutes = max(1, hold_seconds // 60)

        if pnl_pct > 0.1:
            outcome = "WIN"
        elif pnl_pct < -0.1:
            outcome = "LOSS"
        else:
            outcome = "BREAKEVEN"

        result.exit_price = current_price
        result.pnl_pct = round(pnl_pct, 4)
        result.pnl_dollars = round(pnl_dollars, 2)
        result.outcome = outcome
        result.closed_at = datetime.utcnow()
        result.hold_minutes = hold_minutes

        await self._store.update_trade_result(result)

        if self._emit:
            try:
                await self._emit("trade_closed", result.model_dump(mode="json"))
            except Exception as exc:
                logger.warning("WebSocket emit (trade_closed) failed: %s", exc)

        logger.info(
            "Trade closed: %s %s %s P&L=%.2f%% ($%.2f)",
            result.ticker, result.side, outcome, pnl_pct, pnl_dollars
        )
        return result

    # ── Portfolio ─────────────────────────────────────────────────────────────

    async def get_portfolio(self) -> dict:
        """Get current portfolio from Alpaca (paper or live)."""
        try:
            client = await asyncio.to_thread(self._get_alpaca_client)
            account = await asyncio.to_thread(client.get_account)
            positions = await asyncio.to_thread(client.get_all_positions)

            return {
                "portfolio_value": float(account.portfolio_value or 0),
                "cash": float(account.cash or 0),
                "equity": float(account.equity or 0),
                "buying_power": float(account.buying_power or 0),
                "day_pnl": float((account.equity or 0)) - float((account.last_equity or 0)),
                "mode": self._mode,
                "positions": [
                    {
                        "ticker": p.symbol,
                        "qty": float(p.qty),
                        "avg_entry": float(p.avg_entry_price),
                        "market_value": float(p.market_value),
                        "unrealized_pnl": float(p.unrealized_pl),
                        "unrealized_pnl_pct": float(p.unrealized_plpc) * 100,
                    }
                    for p in (positions or [])
                ],
            }
        except Exception as exc:
            logger.warning("Could not fetch Alpaca portfolio: %s", exc)
            return {
                "portfolio_value": 0.0,
                "cash": 0.0,
                "mode": self._mode,
                "positions": [],
                "error": str(exc),
            }

    async def get_all_positions(self) -> dict[str, dict]:
        """Return current Alpaca positions as {ticker: {qty, avg_entry, market_value, unrealized_pnl}}."""
        try:
            client = await asyncio.to_thread(self._get_alpaca_client)
            positions = await asyncio.to_thread(client.get_all_positions)
            return {
                p.symbol: {
                    "qty": float(p.qty),
                    "avg_entry": float(p.avg_entry_price),
                    "market_value": float(p.market_value),
                    "unrealized_pnl": float(p.unrealized_pl),
                    "current_price": float(p.current_price),
                    "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                }
                for p in (positions or [])
            }
        except Exception as exc:
            logger.warning("get_all_positions failed: %s", exc)
            return {}

    async def close_position_for_monitor(
        self,
        trade_id: str,
        ticker: str,
        side: str,
        entry_price: float,
        quantity: float,
        current_price: float,
        filled_at: Optional[datetime],
    ) -> TradeResult:
        """Called by position monitor when Alpaca confirms a position closed. Records P&L in SQLite."""
        if side == "buy":
            pnl_pct = (current_price - entry_price) / entry_price * 100
            pnl_dollars = (current_price - entry_price) * quantity
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100
            pnl_dollars = (entry_price - current_price) * quantity

        hold_seconds = 0
        if filled_at:
            hold_seconds = int((datetime.utcnow() - filled_at).total_seconds())
        hold_minutes = max(1, hold_seconds // 60)

        if pnl_pct > 0.1:
            outcome = "WIN"
        elif pnl_pct < -0.1:
            outcome = "LOSS"
        else:
            outcome = "BREAKEVEN"

        result = TradeResult(
            trade_id=trade_id,
            ticker=ticker,
            side=side,
            entry_price=entry_price,
            exit_price=current_price,
            quantity=quantity,
            pnl_pct=round(pnl_pct, 4),
            pnl_dollars=round(pnl_dollars, 2),
            outcome=outcome,
            closed_at=datetime.utcnow(),
            hold_minutes=hold_minutes,
            mode=self._mode,
        )

        await self._store.update_trade_result(result)

        if self._emit:
            try:
                await self._emit("trade_closed", result.model_dump(mode="json"))
            except Exception as exc:
                logger.warning("WebSocket emit (trade_closed) failed: %s", exc)

        logger.info(
            "[MONITOR] Trade closed: %s %s %s  P&L=%.2f%% ($%.2f)",
            ticker, side, outcome, pnl_pct, pnl_dollars
        )
        return result

    async def get_orders(self, limit: int = 20) -> list[dict]:
        """Get recent orders from Alpaca."""
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            client = await asyncio.to_thread(self._get_alpaca_client)
            req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit)
            orders = await asyncio.to_thread(client.get_orders, req)
            return [
                {
                    "id": str(o.id),
                    "ticker": o.symbol,
                    "side": str(o.side).replace("OrderSide.", "").lower(),
                    "qty": float(o.qty or 0),
                    "filled_qty": float(o.filled_qty or 0),
                    "status": str(o.status).replace("OrderStatus.", "").lower(),
                    "filled_avg_price": float(o.filled_avg_price or 0),
                    "submitted_at": o.submitted_at.isoformat() if o.submitted_at else "",
                    "filled_at": o.filled_at.isoformat() if o.filled_at else "",
                }
                for o in (orders or [])
            ]
        except Exception as exc:
            logger.warning("Could not fetch Alpaca orders: %s", exc)
            return []
