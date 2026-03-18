"""
Trade-related Pydantic models.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field
import uuid


class RiskParams(BaseModel):
    position_size: float = Field(..., description="Dollar amount to risk")
    position_size_pct: float = Field(..., description="Fraction of portfolio (0-1)")
    stop_loss: float = Field(..., description="Stop-loss price")
    take_profit: float = Field(..., description="Take-profit price")
    entry_price: float = Field(..., description="Intended entry price")
    risk_per_trade: float = Field(default=0.02, description="Risk fraction (default 2%)")
    kelly_fraction: float = Field(default=0.25, description="Kelly criterion fraction used")
    max_loss_dollars: float = Field(..., description="Max dollar loss on this trade")


class Signal(BaseModel):
    trade_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ticker: str
    market_type: Literal["stock", "crypto", "forex", "options"]
    action: Literal["buy", "sell", "hold"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    entry_price: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    timeframe: str = "1D"


class TradeResult(BaseModel):
    trade_id: str
    ticker: str
    side: Literal["buy", "sell"]
    entry_price: float
    exit_price: Optional[float] = None
    quantity: float
    filled_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    pnl_dollars: Optional[float] = None
    pnl_pct: Optional[float] = None
    outcome: Optional[Literal["WIN", "LOSS", "BREAKEVEN", "OPEN"]] = "OPEN"
    hold_minutes: Optional[int] = None
    order_id: Optional[str] = None
    mode: Literal["paper", "live"] = "paper"
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class ProbabilityScore(BaseModel):
    trade_id: str
    news_score: float = Field(..., ge=0.0, le=1.0, description="Sentiment strength + alignment + freshness")
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Risk/reward setup quality")
    mentor_score: float = Field(..., ge=0.0, le=1.0, description="Mentor conviction from review gate")
    historical_win_rate: float = Field(..., ge=0.0, le=1.0, description="Win rate of past trades with same pattern")
    composite_score: float = Field(..., ge=0.0, le=1.0, description="Weighted composite probability")
    composite_pct: str = Field(..., description='e.g. "78.4% probability of winning"')
    ci_lower: float = Field(..., description="95% CI lower bound return %")
    ci_upper: float = Field(..., description="95% CI upper bound return %")
    expected_return: float = Field(..., description="Mean expected return %")
    proj_100_expected: float = Field(..., description="$100 grows to this amount on expected return")
    proj_100_best: float = Field(..., description="$100 grows to this amount on best-case (95% CI upper)")
    proj_100_worst: float = Field(..., description="$100 shrinks/grows to this at worst-case (95% CI lower)")
    proj_100_double_trades: int = Field(..., description="Estimated trades to double $100 at this win rate")
    signal_grade: Literal["A+", "A", "B", "C", "D", "F"]


class ReviewNote(BaseModel):
    trade_id: str
    decision: Literal["APPROVED", "BLOCKED", "REDUCED", "DELAYED"]
    trader_voice: str = Field(..., description="Which trader's principle dominated the decision")
    reasoning: str = Field(..., description="Plain English — what the mentor saw")
    news_alignment: Literal["CONFIRMS", "CONTRADICTS", "NEUTRAL", "OVERRIDE"]
    news_catalyst: str = Field(..., description="The specific news item that influenced the decision")
    price_vs_news: str = Field(..., description="Does price action match the news?")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Mentor conviction 0-1")
    book_reference: str = Field(..., description='e.g. "Market Wizards — Paul Tudor Jones: never average losers"')
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    probability_score: Optional[ProbabilityScore] = None


class TradeDetail(BaseModel):
    """Complete trade record combining signal, result, probability, and review."""
    trade_id: str
    ticker: str
    market_type: Literal["stock", "crypto", "forex", "options"]
    signal: Signal
    risk_params: RiskParams
    review_note: ReviewNote
    probability_score: ProbabilityScore
    trade_result: Optional[TradeResult] = None
    mentor_lesson: Optional["Lesson"] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    mode: Literal["paper", "live"] = "paper"


# Avoid circular import
from backend.models.lesson import Lesson  # noqa: E402
TradeDetail.model_rebuild()
