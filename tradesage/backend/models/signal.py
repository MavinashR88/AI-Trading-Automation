"""
Signal model — buy/sell/hold with confidence and full reasoning.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field
import uuid


class MarketSignal(BaseModel):
    """Extended signal model with full context for the orchestrator."""
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trade_id: str
    ticker: str
    market_type: Literal["stock", "crypto", "forex", "options"]
    action: Literal["buy", "sell", "hold"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    entry_price: float
    suggested_quantity: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    timeframe: str = "1D"
    pattern_detected: Optional[str] = None
    news_catalyst: Optional[str] = None
    sentiment_score: float = 0.0
    news_urgency: Literal["immediate", "wait", "override_cancel"] = "wait"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = "orchestrator"   # which agent generated this signal


class NewsSignal(BaseModel):
    """Lightweight signal from news scanning."""
    model_config = {"extra": "allow"}

    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ticker: str
    headline: str
    source: str
    url: str
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    urgency: Literal["immediate", "wait", "override_cancel"]
    catalyst: str
    age_minutes: int
    breaking_override: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    raw_text: str = ""
    already_priced_in: bool = False
    sector_ripple: list = Field(default_factory=list)
    divergence_flag: bool = False
