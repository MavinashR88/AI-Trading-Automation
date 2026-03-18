"""
MasterAgent base class.
Every pipeline stage (Discovery, Research, Algorithm, Simulation, Validation)
extends this class. Runs micro agents in parallel, synthesizes results,
and fires a self-improvement loop every N completions.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class MasterAgent(ABC):
    name: str = "MasterAgent"
    improvement_interval: int = 10  # self-improve every N completions

    def __init__(self):
        self._completion_count = 0
        self._improvement_store: list[dict] = []  # in-memory log

    @abstractmethod
    async def decompose(self, state: Any) -> list[tuple["MicroAgent", Any]]:
        """Break state into (micro_agent, task) pairs."""

    @abstractmethod
    async def synthesize(self, results: list[Any], state: Any) -> Any:
        """Combine micro agent results into a single output."""

    async def run(self, state: Any) -> Any:
        micro_tasks = await self.decompose(state)

        # Run all micro agents in parallel; collect successes
        raw = await asyncio.gather(
            *[agent.run(task) for agent, task in micro_tasks],
            return_exceptions=True,
        )
        valid = []
        for i, r in enumerate(raw):
            if isinstance(r, Exception):
                agent_name = micro_tasks[i][0].__class__.__name__
                logger.warning("[%s] micro agent %s failed: %s", self.name, agent_name, r)
            else:
                valid.append(r)

        output = await self.synthesize(valid, state)

        self._completion_count += 1
        if self._completion_count % self.improvement_interval == 0:
            asyncio.create_task(self._self_improve())

        return output

    async def _self_improve(self):
        """Async self-review loop — fires in background every N runs."""
        try:
            from backend.llm.router import call_llm
            recent = self._improvement_store[-10:]
            suggestion = await call_llm(
                "loop_self_check",
                f"Agent: {self.name}\nRecent run summaries: {recent}\n"
                f"Identify top issues and suggest parameter improvements. "
                f"Return JSON: {{\"issues\": [], \"suggestions\": [], \"priority\": \"high|medium|low\"}}",
            )
            logger.info("[%s] Self-improvement suggestion: %s", self.name, suggestion[:200])
        except Exception as exc:
            logger.debug("[%s] Self-improve skipped: %s", self.name, exc)

    def _log_run(self, summary: dict):
        """Call from synthesize() to feed the self-improvement loop."""
        self._improvement_store.append(summary)
        if len(self._improvement_store) > 50:
            self._improvement_store = self._improvement_store[-50:]
