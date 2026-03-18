from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class TradingAlgorithm(BaseModel):
    id: Optional[str] = None
    ticker: str
    name: str                        # e.g. "NVDA_momentum_v1"
    strategy_type: str               # "momentum" | "event_driven" | "breakout" | "mean_reversion"

    # Rules (Python source code as strings)
    entry_rules_code: str = ""
    exit_rules_code: str = ""
    position_sizing_code: str = ""
    indicators: list[str] = []       # ["RSI", "MACD", "ATR", ...]

    # Parameters
    params: dict = {}                # {"rsi_period": 14, "atr_multiplier": 2.0, ...}

    # Status
    status: str = "DRAFT"           # DRAFT → SIMULATED → PAPER_TRADING → LIVE | RETIRED
    paper_trades_done: int = 0
    paper_trades_required: int = 50

    # Performance (filled after simulation/paper trading)
    backtest_win_rate: float = 0.0
    backtest_sharpe: float = 0.0
    backtest_max_drawdown_pct: float = 0.0
    backtest_profit_factor: float = 0.0
    scenarios_passed: int = 0        # out of 8
    paper_win_rate: float = 0.0
    paper_pnl_pct: float = 0.0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    deployed_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    retire_reason: str = ""
