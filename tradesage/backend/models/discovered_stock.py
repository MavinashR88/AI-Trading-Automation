from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class DiscoveredStock(BaseModel):
    id: Optional[str] = None
    ticker: str
    company_name: str = ""
    sector: str = ""
    discovery_reason: str        # "volume_spike" | "earnings_surprise" | "ipo" | "options_flow" | "sector_rotation" | "short_squeeze"
    discovery_score: float       # 0–100
    volume_ratio: float = 1.0    # current vol / avg vol
    market_cap: float = 0.0
    price: float = 0.0
    short_interest_pct: float = 0.0
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "DISCOVERED"  # DISCOVERED → RESEARCHED → ALGO_BUILT → SIMULATED → VALIDATING → LIVE | REJECTED

class DiscoveryBatch(BaseModel):
    stocks: list[DiscoveredStock]
    scanned_at: datetime = Field(default_factory=datetime.utcnow)
    total_scanned: int = 0
    scanner_results: dict = {}   # per-scanner raw counts
