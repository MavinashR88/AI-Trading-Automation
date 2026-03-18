from pydantic import BaseModel
from typing import Optional

class GateResult(BaseModel):
    gate: str                  # "macro" | "news" | "risk" | "mentor"
    passed: bool
    verdict: str               # "PASS" | "FAIL" | "CAUTION"
    reason: str
    detail: dict = {}
    latency_ms: int = 0
    cost_usd: float = 0.0
    size_multiplier: float = 1.0   # 0.5 on CAUTION
