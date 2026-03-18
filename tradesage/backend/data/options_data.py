"""
Options Chain Data
Primary: Tradier API
Fallback: yfinance options chain
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class OptionsDataClient:
    """Fetches options chains from Tradier or yfinance."""

    TRADIER_BASE = "https://api.tradier.com/v1"

    def __init__(self, tradier_token: str = ""):
        self._token = tradier_token
        self._headers = {
            "Authorization": f"Bearer {tradier_token}",
            "Accept": "application/json",
        }

    async def get_options_chain(
        self, ticker: str, expiration: Optional[str] = None
    ) -> list[dict]:
        """
        Fetch full options chain for a ticker.
        expiration: YYYY-MM-DD format, or None for nearest expiry.
        """
        if self._token:
            try:
                return await self._tradier_chain(ticker, expiration)
            except Exception as exc:
                logger.warning("Tradier options failed for %s: %s. Falling back to yfinance.", ticker, exc)

        return await self._yfinance_chain(ticker, expiration)

    async def _tradier_chain(self, ticker: str, expiration: Optional[str]) -> list[dict]:
        """Fetch from Tradier API."""
        params: dict = {"symbol": ticker, "greeks": "true"}
        if expiration:
            params["expiration"] = expiration

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.TRADIER_BASE}/markets/options/chains",
                headers=self._headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        options = data.get("options", {}).get("option", [])
        if isinstance(options, dict):
            options = [options]

        return [
            {
                "ticker": ticker,
                "type": o.get("option_type", ""),
                "strike": float(o.get("strike", 0)),
                "expiration": o.get("expiration_date", ""),
                "bid": float(o.get("bid", 0) or 0),
                "ask": float(o.get("ask", 0) or 0),
                "volume": int(o.get("volume", 0) or 0),
                "open_interest": int(o.get("open_interest", 0) or 0),
                "iv": float(o.get("greeks", {}).get("mid_iv", 0) or 0),
                "delta": float(o.get("greeks", {}).get("delta", 0) or 0),
                "gamma": float(o.get("greeks", {}).get("gamma", 0) or 0),
                "theta": float(o.get("greeks", {}).get("theta", 0) or 0),
                "vega": float(o.get("greeks", {}).get("vega", 0) or 0),
                "source": "tradier",
            }
            for o in options
        ]

    async def _yfinance_chain(self, ticker: str, expiration: Optional[str]) -> list[dict]:
        """yfinance fallback for options chain."""
        try:
            import yfinance as yf

            tick = await asyncio.to_thread(yf.Ticker, ticker)
            expirations = await asyncio.to_thread(lambda: tick.options)

            if not expirations:
                return []

            exp = expiration if expiration in expirations else expirations[0]
            chain = await asyncio.to_thread(tick.option_chain, exp)

            def _parse_df(df, option_type: str) -> list[dict]:
                records = []
                for _, row in df.iterrows():
                    records.append({
                        "ticker": ticker,
                        "type": option_type,
                        "strike": float(row.get("strike", 0)),
                        "expiration": exp,
                        "bid": float(row.get("bid", 0) or 0),
                        "ask": float(row.get("ask", 0) or 0),
                        "volume": int(row.get("volume", 0) or 0),
                        "open_interest": int(row.get("openInterest", 0) or 0),
                        "iv": float(row.get("impliedVolatility", 0) or 0),
                        "delta": 0.0,
                        "gamma": 0.0,
                        "theta": 0.0,
                        "vega": 0.0,
                        "source": "yfinance",
                    })
                return records

            calls = _parse_df(chain.calls, "call")
            puts = _parse_df(chain.puts, "put")
            return calls + puts

        except Exception as exc:
            logger.error("yfinance options failed for %s: %s", ticker, exc)
            return []

    async def get_expirations(self, ticker: str) -> list[str]:
        """Get available expiration dates for a ticker."""
        try:
            if self._token:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        f"{self.TRADIER_BASE}/markets/options/expirations",
                        headers=self._headers,
                        params={"symbol": ticker},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    dates = data.get("expirations", {}).get("date", [])
                    return dates if isinstance(dates, list) else [dates]
        except Exception as exc:
            logger.warning("Could not get expirations from Tradier: %s", exc)

        try:
            import yfinance as yf
            tick = await asyncio.to_thread(yf.Ticker, ticker)
            return list(await asyncio.to_thread(lambda: tick.options))
        except Exception as exc:
            logger.error("Could not get expirations for %s: %s", ticker, exc)
            return []
