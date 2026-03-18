"""
Unified Market Data Feed
Stocks: Alpaca Markets API (with yfinance fallback)
Crypto/Forex: CCXT unified exchange library
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Literal

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Data Models (lightweight dicts for speed)
# ──────────────────────────────────────────────

def _bar(
    ticker: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    timestamp: datetime,
) -> dict:
    return {
        "ticker": ticker,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "timestamp": timestamp.isoformat(),
    }


# ──────────────────────────────────────────────
# Alpaca Stock Data
# ──────────────────────────────────────────────

class AlpacaDataClient:
    """Fetches stock + ETF data from Alpaca."""

    def __init__(self, api_key: str, secret_key: str, base_url: str):
        self._api_key = api_key
        self._secret_key = secret_key
        self._base_url = base_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from alpaca.data.historical import StockHistoricalDataClient
                self._client = StockHistoricalDataClient(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                )
            except Exception as exc:
                logger.error("Failed to create Alpaca data client: %s", exc)
                raise
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def get_latest_quote(self, ticker: str) -> dict:
        """Get latest quote (bid/ask/last price) for a stock."""
        try:
            from alpaca.data.requests import StockLatestQuoteRequest
            client = await asyncio.to_thread(self._get_client)
            request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
            result = await asyncio.to_thread(client.get_stock_latest_quote, request)
            quote = result.get(ticker)
            if quote:
                mid = (quote.ask_price + quote.bid_price) / 2 if quote.ask_price else quote.ask_price
                return {
                    "ticker": ticker,
                    "bid": float(quote.bid_price or 0),
                    "ask": float(quote.ask_price or 0),
                    "last": float(mid or 0),
                    "timestamp": quote.timestamp.isoformat() if quote.timestamp else datetime.utcnow().isoformat(),
                    "source": "alpaca",
                }
        except Exception as exc:
            logger.warning("Alpaca quote failed for %s, falling back to yfinance: %s", ticker, exc)
            return await self._yfinance_quote(ticker)
        return await self._yfinance_quote(ticker)

    async def get_bars(
        self,
        ticker: str,
        timeframe: str = "1Day",
        limit: int = 100,
    ) -> list[dict]:
        """Get historical OHLCV bars."""
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            tf_map = {
                "1Min": TimeFrame.Minute,
                "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                "1Hour": TimeFrame.Hour,
                "1Day": TimeFrame.Day,
            }
            tf = tf_map.get(timeframe, TimeFrame.Day)

            start = datetime.utcnow() - timedelta(days=limit + 10)
            request = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=tf,
                start=start,
                limit=limit,
            )
            client = await asyncio.to_thread(self._get_client)
            result = await asyncio.to_thread(client.get_stock_bars, request)
            bars = result.get(ticker, [])
            return [
                _bar(
                    ticker=ticker,
                    open_=float(b.open),
                    high=float(b.high),
                    low=float(b.low),
                    close=float(b.close),
                    volume=float(b.volume),
                    timestamp=b.timestamp,
                )
                for b in bars
            ]
        except Exception as exc:
            logger.warning("Alpaca bars failed for %s: %s. Falling back to yfinance.", ticker, exc)
            return await self._yfinance_bars(ticker, limit)

    async def _yfinance_quote(self, ticker: str) -> dict:
        """yfinance fallback for latest price."""
        try:
            import yfinance as yf
            tick = await asyncio.to_thread(yf.Ticker, ticker)
            info = await asyncio.to_thread(lambda: tick.fast_info)
            last = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", 0)
            return {
                "ticker": ticker,
                "bid": float(last or 0),
                "ask": float(last or 0),
                "last": float(last or 0),
                "timestamp": datetime.utcnow().isoformat(),
                "source": "yfinance",
            }
        except Exception as exc:
            logger.error("yfinance quote failed for %s: %s", ticker, exc)
            return {"ticker": ticker, "last": 0.0, "source": "error"}

    async def _yfinance_bars(self, ticker: str, limit: int = 100) -> list[dict]:
        """yfinance fallback for historical bars."""
        try:
            import yfinance as yf
            period = f"{min(limit, 365)}d"
            df = await asyncio.to_thread(yf.download, ticker, period=period, progress=False)
            bars = []
            for ts, row in df.iterrows():
                bars.append(_bar(
                    ticker=ticker,
                    open_=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                    timestamp=ts.to_pydatetime(),
                ))
            return bars[-limit:]
        except Exception as exc:
            logger.error("yfinance bars failed for %s: %s", ticker, exc)
            return []


# ──────────────────────────────────────────────
# CCXT Crypto/Forex Data
# ──────────────────────────────────────────────

class CCXTDataClient:
    """Fetches crypto and forex data via CCXT."""

    def __init__(self, exchange_id: str, api_key: str = "", secret: str = ""):
        self._exchange_id = exchange_id
        self._api_key = api_key
        self._secret = secret
        self._exchange = None

    def _get_exchange(self):
        if self._exchange is None:
            try:
                import ccxt
                ExchangeClass = getattr(ccxt, self._exchange_id)
                self._exchange = ExchangeClass({
                    "apiKey": self._api_key,
                    "secret": self._secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": "spot"},
                })
            except Exception as exc:
                logger.error("CCXT init failed for %s: %s", self._exchange_id, exc)
                raise
        return self._exchange

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_latest_price(self, symbol: str) -> dict:
        """Fetch latest ticker price for a crypto/forex symbol."""
        try:
            exchange = await asyncio.to_thread(self._get_exchange)
            ticker = await asyncio.to_thread(exchange.fetch_ticker, symbol)
            return {
                "ticker": symbol,
                "bid": float(ticker.get("bid", 0) or 0),
                "ask": float(ticker.get("ask", 0) or 0),
                "last": float(ticker.get("last", 0) or 0),
                "volume": float(ticker.get("quoteVolume", 0) or 0),
                "timestamp": datetime.utcnow().isoformat(),
                "source": f"ccxt:{self._exchange_id}",
            }
        except Exception as exc:
            logger.error("CCXT price failed for %s: %s", symbol, exc)
            return {"ticker": symbol, "last": 0.0, "source": "error"}

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 100,
    ) -> list[dict]:
        """Fetch OHLCV candles for a crypto/forex symbol."""
        try:
            exchange = await asyncio.to_thread(self._get_exchange)
            raw = await asyncio.to_thread(exchange.fetch_ohlcv, symbol, timeframe, limit=limit)
            return [
                _bar(
                    ticker=symbol,
                    open_=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    volume=float(c[5]),
                    timestamp=datetime.utcfromtimestamp(c[0] / 1000),
                )
                for c in raw
            ]
        except Exception as exc:
            logger.error("CCXT ohlcv failed for %s: %s", symbol, exc)
            return []


# ──────────────────────────────────────────────
# Unified Market Data Feed
# ──────────────────────────────────────────────

class MarketDataFeed:
    """
    Unified interface for all market data sources.
    Routes requests to Alpaca (stocks) or CCXT (crypto/forex) automatically.
    """

    CRYPTO_SYMBOLS = {"BTC", "ETH", "BNB", "SOL", "ADA", "MATIC", "DOT", "AVAX"}
    FOREX_SYMBOLS = {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "USD/CAD"}

    def __init__(
        self,
        alpaca_api_key: str,
        alpaca_secret: str,
        alpaca_base_url: str,
        exchange_id: str = "binance",
        exchange_api_key: str = "",
        exchange_secret: str = "",
    ):
        self._alpaca = AlpacaDataClient(alpaca_api_key, alpaca_secret, alpaca_base_url)
        self._ccxt = CCXTDataClient(exchange_id, exchange_api_key, exchange_secret)

    def _market_type(self, ticker: str) -> Literal["stock", "crypto", "forex"]:
        if ticker.upper() in self.CRYPTO_SYMBOLS or "/" in ticker:
            return "crypto" if "/" not in ticker else "forex"
        if ticker in self.FOREX_SYMBOLS:
            return "forex"
        return "stock"

    async def get_price(self, ticker: str) -> dict:
        """Get latest price for any ticker."""
        mtype = self._market_type(ticker)
        if mtype == "stock":
            return await self._alpaca.get_latest_quote(ticker)
        else:
            # Normalise crypto symbol to CCXT format (BTC -> BTC/USDT)
            symbol = ticker if "/" in ticker else f"{ticker}/USDT"
            return await self._ccxt.get_latest_price(symbol)

    async def get_bars(
        self,
        ticker: str,
        timeframe: str = "1Day",
        limit: int = 100,
    ) -> list[dict]:
        """Get historical OHLCV bars for any ticker."""
        mtype = self._market_type(ticker)
        if mtype == "stock":
            return await self._alpaca.get_bars(ticker, timeframe, limit)
        else:
            symbol = ticker if "/" in ticker else f"{ticker}/USDT"
            ccxt_tf = {"1Day": "1d", "1Hour": "1h", "5Min": "5m", "1Min": "1m"}.get(timeframe, "1d")
            return await self._ccxt.get_ohlcv(symbol, ccxt_tf, limit)

    async def get_prices_batch(self, tickers: list[str]) -> dict[str, dict]:
        """Fetch prices for multiple tickers concurrently."""
        tasks = {ticker: asyncio.create_task(self.get_price(ticker)) for ticker in tickers}
        results = {}
        for ticker, task in tasks.items():
            try:
                results[ticker] = await task
            except Exception as exc:
                logger.error("Batch price fetch failed for %s: %s", ticker, exc)
                results[ticker] = {"ticker": ticker, "last": 0.0, "source": "error"}
        return results

    async def get_returns(self, ticker: str, limit: int = 100) -> list[float]:
        """
        Get list of daily returns (as decimals, e.g. 0.02 = 2%) for a ticker.
        Used for CI calculations.
        """
        bars = await self.get_bars(ticker, "1Day", limit + 1)
        if len(bars) < 2:
            return []
        returns = []
        for i in range(1, len(bars)):
            prev_close = bars[i - 1]["close"]
            curr_close = bars[i]["close"]
            if prev_close > 0:
                returns.append((curr_close - prev_close) / prev_close * 100)
        return returns

    async def get_volume_ratio(self, ticker: str) -> float:
        """Compare today's volume to 20-day average."""
        bars = await self.get_bars(ticker, "1Day", 21)
        if len(bars) < 2:
            return 1.0
        avg_volume = sum(b["volume"] for b in bars[:-1]) / max(len(bars) - 1, 1)
        today_volume = bars[-1]["volume"] if bars else 0
        return today_volume / avg_volume if avg_volume > 0 else 1.0
