"""
Mentor Lesson model.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field
import uuid


class Lesson(BaseModel):
    lesson_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trade_id: str
    outcome: Literal["WIN", "LOSS", "BREAKEVEN"]
    trader_principle: str = Field(..., description="Which trader's wisdom applies")
    principle_quote: str = Field(..., description="Actual quote from the knowledge base")
    what_happened: str = Field(..., description="Plain English trade analysis")
    correction: str = Field(..., description="Specific, actionable change for next trade")
    confidence_adjustment: float = Field(
        ...,
        description="How to adjust signal confidence threshold (+/-)"
    )
    consecutive_wins: int = 0
    win_rate: float = Field(..., ge=0.0, le=1.0)
    ticker: str = ""
    pnl_pct: float = 0.0
    pnl_dollars: float = 0.0
    book_reference: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    graph_node_id: Optional[str] = None
    knowledge_gap: str = ""   # identifies missing knowledge (for book_suggester)
