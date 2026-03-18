"""
DiscoveryRankerMicro — deduplicates scanner results, scores each ticker 0-100,
and returns the top 15 DiscoveredStock objects.

Scoring weights:
  volume_ratio      30%
  earnings_surprise 25%
  options_activity  20%
  sector_momentum   15%
  short_squeeze     10%
"""
from __future__ import annotations

import logging
from typing import Any

import yfinance as yf
import asyncio

from backend.agents.base.micro import MicroAgent
from backend.models.discovered_stock import DiscoveredStock

logger = logging.getLogger(__name__)

TOP_N = 15

# Score weights (must sum to 1.0)
W_VOLUME   = 0.30
W_EARNINGS = 0.25
W_OPTIONS  = 0.20
W_SECTOR   = 0.15
W_SQUEEZE  = 0.10

# Normalisation caps (values above cap score 100 on that dimension)
CAP_VOLUME_RATIO    = 10.0   # 10× avg = full score
CAP_SURPRISE_PCT    = 20.0   # 20% surprise = full score
CAP_CP_RATIO        = 5.0    # 5 call/put ratio = full score
CAP_MOMENTUM_PCT    = 5.0    # 5% sector momentum = full score
CAP_SQUEEZE_SCORE   = 100.0  # combined: (short_pct/50 + vol_ratio/10) * 50


def _norm(value: float, cap: float) -> float:
    """Normalise value to 0-100 capped at cap."""
    return min(100.0, (value / cap) * 100.0)


def _enrich_ticker(ticker: str) -> dict:
    """Fetch basic info for a ticker synchronously."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="2d")
        price = float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
        return {
            "company_name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector") or "Unknown",
            "market_cap": float(info.get("marketCap") or 0),
            "price": price,
        }
    except Exception:
        return {"company_name": ticker, "sector": "Unknown", "market_cap": 0.0, "price": 0.0}


class DiscoveryRankerMicro(MicroAgent):
    name = "DiscoveryRankerMicro"
    timeout_seconds = 120.0

    async def execute(self, task: Any) -> list[DiscoveredStock]:
        """
        task is expected to be a dict:
          {
            "volume":   [...],   # from VolumeScannerMicro
            "earnings": [...],   # from EarningsScannerMicro
            "ipo":      [...],   # from IpoScannerMicro
            "options":  [...],   # from OptionsFlowMicro
            "sector":   {...},   # from SectorRotationMicro
            "squeeze":  [...],   # from ShortSqueezeMicro
          }
        """
        task = task or {}
        volume_results   = task.get("volume", [])
        earnings_results = task.get("earnings", [])
        ipo_results      = task.get("ipo", [])
        options_results  = task.get("options", [])
        sector_result    = task.get("sector", {})
        squeeze_results  = task.get("squeeze", [])

        # Build per-ticker signal maps
        vol_map:      dict[str, dict] = {r["ticker"]: r for r in volume_results}
        earn_map:     dict[str, dict] = {r["ticker"]: r for r in earnings_results}
        ipo_map:      dict[str, dict] = {r["ticker"]: r for r in ipo_results}
        options_map:  dict[str, dict] = {r["ticker"]: r for r in options_results}
        squeeze_map:  dict[str, dict] = {r["ticker"]: r for r in squeeze_results}

        sector_tickers: set[str] = set(sector_result.get("tickers_in_sector", []))
        sector_momentum = abs(sector_result.get("momentum_pct", 0.0))
        leading_etf = sector_result.get("leading_sector_etf", "")

        # Collect all unique tickers
        all_tickers: set[str] = (
            set(vol_map) | set(earn_map) | set(ipo_map) | set(options_map) | set(squeeze_map)
        )

        if not all_tickers:
            logger.info("[DiscoveryRankerMicro] no tickers to rank")
            return []

        scored: list[tuple[float, str, str]] = []  # (score, ticker, primary_reason)

        for ticker in all_tickers:
            # Component scores (0-100 each)
            vol_data = vol_map.get(ticker, {})
            s_volume = _norm(float(vol_data.get("volume_ratio", 0)), CAP_VOLUME_RATIO)

            earn_data = earn_map.get(ticker, {})
            s_earnings = _norm(abs(float(earn_data.get("surprise_pct", 0))), CAP_SURPRISE_PCT)

            opt_data = options_map.get(ticker, {})
            s_options = _norm(float(opt_data.get("call_put_ratio", 0)), CAP_CP_RATIO)

            # Sector momentum: full score only if ticker is in leading sector
            s_sector = _norm(sector_momentum, CAP_MOMENTUM_PCT) if ticker in sector_tickers else 0.0

            sq_data = squeeze_map.get(ticker, {})
            raw_squeeze = (
                sq_data.get("short_interest_pct", 0) / 50.0
                + sq_data.get("volume_ratio", 0) / 10.0
            ) * 50.0
            s_squeeze = _norm(raw_squeeze, CAP_SQUEEZE_SCORE)

            total_score = (
                W_VOLUME   * s_volume
                + W_EARNINGS * s_earnings
                + W_OPTIONS  * s_options
                + W_SECTOR   * s_sector
                + W_SQUEEZE  * s_squeeze
            )

            # Determine primary discovery reason
            reasons = []
            if vol_data:   reasons.append(("volume_spike",       s_volume))
            if earn_data:  reasons.append(("earnings_surprise",  s_earnings))
            if ipo_map.get(ticker): reasons.append(("ipo",       50.0))
            if opt_data:   reasons.append(("options_flow",       s_options))
            if ticker in sector_tickers: reasons.append(("sector_rotation", s_sector))
            if sq_data:    reasons.append(("short_squeeze",      s_squeeze))
            primary_reason = max(reasons, key=lambda x: x[1])[0] if reasons else "volume_spike"

            scored.append((total_score, ticker, primary_reason))

        # Sort descending, take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:TOP_N]

        # Enrich with yfinance info in parallel
        enriched = await asyncio.gather(
            *[asyncio.to_thread(_enrich_ticker, ticker) for _, ticker, _ in top],
            return_exceptions=True,
        )

        stocks: list[DiscoveredStock] = []
        for (score, ticker, reason), info in zip(top, enriched):
            if isinstance(info, Exception):
                info = {"company_name": ticker, "sector": "Unknown", "market_cap": 0.0, "price": 0.0}

            vol_ratio = float(vol_map.get(ticker, {}).get("volume_ratio", 1.0))
            short_pct = float(squeeze_map.get(ticker, {}).get("short_interest_pct", 0.0))
            price = float(vol_map.get(ticker, {}).get("price", info.get("price", 0.0)))

            # Override sector from sector_rotation if applicable
            sector = info.get("sector") or "Unknown"
            if ticker in sector_tickers and leading_etf:
                sector = sector or f"Sector:{leading_etf}"

            stocks.append(DiscoveredStock(
                ticker=ticker,
                company_name=info.get("company_name", ticker),
                sector=sector,
                discovery_reason=reason,
                discovery_score=round(score, 2),
                volume_ratio=round(vol_ratio, 2),
                market_cap=info.get("market_cap", 0.0),
                price=price or info.get("price", 0.0),
                short_interest_pct=round(short_pct, 2),
            ))

        logger.info("[DiscoveryRankerMicro] ranked %d stocks, returning top %d", len(scored), len(stocks))
        return stocks
