"""
MicroAgent base class.
Each micro agent does ONE focused job, returns a structured result.
All errors are caught and re-raised so MasterAgent can handle them gracefully.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class MicroAgent(ABC):
    name: str = "MicroAgent"
    timeout_seconds: float = 30.0

    @abstractmethod
    async def execute(self, task: Any) -> Any:
        """Core logic. Raise exceptions freely — MasterAgent handles them."""

    async def run(self, task: Any) -> Any:
        import asyncio
        try:
            return await asyncio.wait_for(self.execute(task), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            raise TimeoutError(f"{self.name} timed out after {self.timeout_seconds}s")
        except Exception as exc:
            logger.debug("[%s] failed: %s", self.name, exc)
            raise
