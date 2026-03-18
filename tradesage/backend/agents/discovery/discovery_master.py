"""
DiscoveryMaster — orchestrates all discovery micro agents, ranks results,
and returns a DiscoveryBatch.

Pipeline:
  decompose()  → 6 scanner micro agents run in parallel
  synthesize() → DiscoveryRankerMicro scores and deduplicates
  run()        → returns DiscoveryBatch
  save_to_db() → persists batch via data_router
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.agents.base.master import MasterAgent
from backend.agents.base.micro import MicroAgent
from backend.agents.discovery.micro.volume_scanner_micro import VolumeScannerMicro
from backend.agents.discovery.micro.earnings_scanner_micro import EarningsScannerMicro
from backend.agents.discovery.micro.ipo_scanner_micro import IpoScannerMicro
from backend.agents.discovery.micro.options_flow_micro import OptionsFlowMicro
from backend.agents.discovery.micro.sector_rotation_micro import SectorRotationMicro
from backend.agents.discovery.micro.short_squeeze_micro import ShortSqueezeMicro
from backend.agents.discovery.micro.discovery_ranker_micro import DiscoveryRankerMicro
from backend.models.discovered_stock import DiscoveredStock, DiscoveryBatch

logger = logging.getLogger(__name__)

# Keys must match the order returned by decompose()
_SCANNER_KEYS = ["volume", "earnings", "ipo", "options", "sector", "squeeze"]


class DiscoveryMaster(MasterAgent):
    name = "DiscoveryMaster"

    def __init__(self):
        super().__init__()
        self._volume_agent   = VolumeScannerMicro()
        self._earnings_agent = EarningsScannerMicro()
        self._ipo_agent      = IpoScannerMicro()
        self._options_agent  = OptionsFlowMicro()
        self._sector_agent   = SectorRotationMicro()
        self._squeeze_agent  = ShortSqueezeMicro()
        self._ranker         = DiscoveryRankerMicro()

    async def decompose(self, state: Any) -> list[tuple[MicroAgent, Any]]:
        """Return all 6 scanner micro agents with their tasks."""
        task = state or {}
        return [
            (self._volume_agent,   task),
            (self._earnings_agent, task),
            (self._ipo_agent,      task),
            (self._options_agent,  task),
            (self._sector_agent,   task),
            (self._squeeze_agent,  task),
        ]

    async def synthesize(self, results: list[Any], state: Any) -> DiscoveryBatch:
        """
        Combine the 6 scanner results into a DiscoveryBatch via the ranker.
        results order matches decompose() order, but some may be missing on failure.
        We use positional matching against the original micro task list.
        """
        # Re-run decompose to know which agents were expected (order matters)
        micro_tasks = await self.decompose(state)
        expected_count = len(micro_tasks)

        # Pad results in case some micro agents failed (MasterAgent already filtered)
        # We need to map surviving results back to their keys.
        # MasterAgent passes only successful results — we can't rely on positional match.
        # Safer: re-run each scanner individually to build the dict.
        # Instead, we run ranker with whatever we have.
        # MasterAgent.run() gives us valid results in the SAME ORDER as decompose()
        # for successes; failures are dropped and logged. To preserve keys we need
        # the full list. Re-run decompose and gather ourselves here.
        raw = await asyncio.gather(
            *[agent.run(task) for agent, task in micro_tasks],
            return_exceptions=True,
        )

        scanner_dict: dict[str, Any] = {}
        scanner_counts: dict[str, int] = {}
        for key, r in zip(_SCANNER_KEYS, raw):
            if isinstance(r, Exception):
                logger.warning("[DiscoveryMaster] %s scanner failed: %s", key, r)
                scanner_dict[key] = [] if key != "sector" else {}
                scanner_counts[key] = 0
            else:
                scanner_dict[key] = r
                if isinstance(r, list):
                    scanner_counts[key] = len(r)
                elif isinstance(r, dict):
                    scanner_counts[key] = 1 if r else 0
                else:
                    scanner_counts[key] = 0

        total_scanned = sum(
            len(v) if isinstance(v, list) else (1 if v else 0)
            for v in scanner_dict.values()
        )

        # Rank and deduplicate
        try:
            top_stocks: list[DiscoveredStock] = await self._ranker.run(scanner_dict)
        except Exception as exc:
            logger.error("[DiscoveryMaster] ranker failed: %s", exc)
            top_stocks = []

        self._log_run({
            "scanner_counts": scanner_counts,
            "top_stocks": len(top_stocks),
            "total_scanned": total_scanned,
        })

        return DiscoveryBatch(
            stocks=top_stocks,
            total_scanned=total_scanned,
            scanner_results=scanner_counts,
        )

    async def run(self, state: Any = None) -> DiscoveryBatch:
        """Run the full discovery pipeline and return a DiscoveryBatch."""
        # We override run() so synthesize() can do its own parallel gather.
        # MasterAgent.run() would call decompose+gather+synthesize, but synthesize
        # here re-gathers independently to preserve key order — so we call synthesize
        # directly to avoid double-fetching.
        logger.info("[DiscoveryMaster] starting discovery pipeline")
        batch = await self.synthesize([], state)
        self._completion_count += 1
        if self._completion_count % self.improvement_interval == 0:
            asyncio.create_task(self._self_improve())
        logger.info(
            "[DiscoveryMaster] pipeline complete: %d stocks discovered",
            len(batch.stocks),
        )
        return batch

    async def save_to_db(self, batch: DiscoveryBatch, data_router: Any) -> None:
        """
        Persist a DiscoveryBatch via data_router.
        data_router is expected to have a save_discovery_batch(batch) coroutine,
        or alternatively a store attribute with that method.
        """
        try:
            if hasattr(data_router, "save_discovery_batch"):
                await data_router.save_discovery_batch(batch)
            elif hasattr(data_router, "store") and hasattr(data_router.store, "save_discovery_batch"):
                await data_router.store.save_discovery_batch(batch)
            else:
                logger.warning(
                    "[DiscoveryMaster] data_router has no save_discovery_batch method; batch not persisted"
                )
                return
            logger.info(
                "[DiscoveryMaster] saved discovery batch: %d stocks, scanned_at=%s",
                len(batch.stocks),
                batch.scanned_at,
            )
        except Exception as exc:
            logger.error("[DiscoveryMaster] save_to_db failed: %s", exc)
