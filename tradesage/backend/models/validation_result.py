from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ValidationCheck(BaseModel):
    name: str           # "out_of_sample" | "monte_carlo" | "paper_trading" | "correlation" | "capacity" | "risk_committee"
    passed: bool
    score: float = 0.0
    detail: str = ""
    metric: dict = {}   # raw numbers for display

class ValidationResult(BaseModel):
    id: Optional[str] = None
    algorithm_id: str
    ticker: str

    checks: list[ValidationCheck] = []

    # Summary
    all_passed: bool = False
    pass_count: int = 0
    fail_count: int = 0
    overall_verdict: str = "PENDING"  # "APPROVED" | "REJECTED" | "PENDING"
    rejection_reason: str = ""

    # Key metrics
    oos_degradation_pct: float = 0.0
    monte_carlo_p_value: float = 1.0
    correlation_with_portfolio: float = 0.0
    capacity_daily_volume_pct: float = 0.0

    created_at: datetime = Field(default_factory=datetime.utcnow)
