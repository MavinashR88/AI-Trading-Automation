from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class DeployedAlgorithm(BaseModel):
    id: Optional[str] = None
    algorithm_id: str
    ticker: str
    name: str
    strategy_type: str

    # Live performance
    live_trades: int = 0
    live_win_rate: float = 0.0
    live_pnl_pct: float = 0.0
    live_sharpe: float = 0.0
    live_max_drawdown_pct: float = 0.0

    # Status
    is_active: bool = True
    deployed_at: datetime = Field(default_factory=datetime.utcnow)
    last_reviewed_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None

    # Performance monitoring thresholds
    win_rate_floor: float = 0.45    # retire if live WR drops below this
    drawdown_ceiling_pct: float = 15.0

    retired_at: Optional[datetime] = None
    retire_reason: str = ""
