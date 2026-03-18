from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ResearchReport(BaseModel):
    id: Optional[str] = None
    ticker: str
    company_name: str = ""

    # Fundamental
    pe_ratio: Optional[float] = None
    revenue_growth_pct: float = 0.0
    gross_margin_pct: float = 0.0
    debt_to_equity: float = 0.0
    earnings_surprise_pct: float = 0.0

    # Technical
    atr_pct: float = 0.0         # ATR as % of price
    trend_direction: str = ""    # "uptrend" | "downtrend" | "sideways"
    rsi_14: float = 50.0
    above_200ma: bool = False
    support_level: float = 0.0
    resistance_level: float = 0.0

    # News history
    avg_move_on_catalyst_pct: float = 0.0
    news_sensitivity: str = "medium"  # "low" | "medium" | "high"
    key_catalysts: list[str] = []

    # SEC / insider
    insider_buying_last_90d: bool = False
    institutional_ownership_change_pct: float = 0.0

    # Competitors
    competitors: list[str] = []
    relative_strength_vs_peers: float = 0.0  # -1 to +1

    # Verdict
    research_verdict: str = "NEUTRAL"  # "STRONG_BUY" | "BUY" | "NEUTRAL" | "AVOID"
    verdict_confidence: float = 0.0
    verdict_reasoning: str = ""
    suggested_strategy: str = ""        # "momentum" | "event_driven" | "breakout" | "mean_reversion"

    created_at: datetime = Field(default_factory=datetime.utcnow)
