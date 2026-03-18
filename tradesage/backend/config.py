"""
TradeSage Configuration
Loads and validates all environment variables at startup.
"""
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()


class Settings(BaseSettings):
    # ── Alpaca ──────────────────────────────────────────────────────────────
    ALPACA_API_KEY: str
    ALPACA_SECRET_KEY: str
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"
    ALPACA_LIVE_URL: str = "https://api.alpaca.markets"

    # ── LLM Mode Switch ──────────────────────────────────────────────────────
    # testing = Haiku for everything (~$0.05-0.20/day)
    # live    = Sonnet heavy / Haiku light (~$0.30-0.80/day)
    # free    = Ollama locally ($0/day)
    LLM_MODE: Literal["testing", "live", "free"] = "testing"
    LLM_DAILY_BUDGET_USD: float = 25.00    # hard daily cap
    LLM_MODEL: str = "claude-haiku-4-5-20251001"   # legacy compat

    # ── Anthropic ────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str

    # ── Ollama (optional, for LLM_MODE=free) ────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"

    # ── News + Web Research ──────────────────────────────────────────────────
    TAVILY_API_KEY: str = ""    # optional — Google News RSS is primary

    # ── Neo4j ────────────────────────────────────────────────────────────────
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "tradesage"

    # ── Trading Mode ─────────────────────────────────────────────────────────
    TRADING_MODE: Literal["paper", "live"] = "paper"

    # ── Portfolio ────────────────────────────────────────────────────────────
    STARTING_CAPITAL: float = 50_000.0

    # ── Crypto (optional) ────────────────────────────────────────────────────
    EXCHANGE_ID: str = "binance"
    EXCHANGE_API_KEY: str = ""
    EXCHANGE_SECRET: str = ""

    # ── Risk Defaults ────────────────────────────────────────────────────────
    MAX_POSITION_PCT: float = 0.10
    RISK_PER_TRADE: float = 0.02
    REWARD_RISK_RATIO: float = 2.0
    MAX_DRAWDOWN_PCT: float = 0.15
    MAX_DAILY_LOSS_PCT: float = 0.05

    # ── App ──────────────────────────────────────────────────────────────────
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    UPLOADS_DIR: str = str(Path(__file__).parent.parent / "uploads")
    NEWS_SCAN_INTERVAL_MINUTES: int = 60

    # ── Default Watch List ───────────────────────────────────────────────────
    DEFAULT_TICKERS: list[str] = [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
        "SPY", "QQQ", "IWM",
        "JPM", "BAC", "XOM",
        "AMD", "INTC", "SMCI",
        "LLY", "MRNA",
        "GLD", "TLT",
    ]

    model_config = {
        "env_file": str(_env_path),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @field_validator("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY")
    @classmethod
    def must_not_be_empty(cls, v: str, info) -> str:
        if not v or v.strip() == "":
            raise ValueError(
                f"\n\n{'='*60}\n"
                f"  STARTUP FAILED: Required key '{info.field_name}' is missing.\n"
                f"  Please set it in your .env file.\n"
                f"{'='*60}\n"
            )
        return v

    @property
    def alpaca_url(self) -> str:
        return self.ALPACA_LIVE_URL if self.TRADING_MODE == "live" else self.ALPACA_BASE_URL

    def switch_mode(self, new_mode: Literal["paper", "live"]) -> None:
        object.__setattr__(self, "TRADING_MODE", new_mode)
        logger.warning("Trading mode switched to: %s", new_mode.upper())

    def switch_llm_mode(self, new_mode: Literal["testing", "live", "free"]) -> None:
        """Hot-swap LLM mode at runtime — no restart needed."""
        object.__setattr__(self, "LLM_MODE", new_mode)
        logger.warning("LLM mode switched to: %s", new_mode.upper())

    def set_daily_budget(self, usd: float) -> None:
        object.__setattr__(self, "LLM_DAILY_BUDGET_USD", usd)


def load_settings() -> Settings:
    try:
        s = Settings()
        if s.STARTING_CAPITAL < 100:
            print(f"[WARN] STARTING_CAPITAL={s.STARTING_CAPITAL} is very low.", file=sys.stderr)
        return s
    except Exception as exc:
        print(f"\n[TradeSage STARTUP ERROR]\n{exc}", file=sys.stderr)
        sys.exit(1)


settings: Settings = load_settings()
