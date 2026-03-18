"""
DeploymentAgent
----------------
Promotes validated algorithms to LIVE by marking them deployed and
broadcasting to WebSocket subscribers. Also supports retiring algos.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any


logger = logging.getLogger(__name__)


class DeploymentAgent:
    name = "DeploymentAgent"

    async def deploy(self, algorithm: dict, data_router, ws_manager=None) -> dict[str, Any]:
        """Mark an algorithm as LIVE and persist deployment metadata."""
        algo_id = algorithm.get("id", "")
        ticker = algorithm.get("ticker", "")

        data_router.update_algorithm_status(
            algo_id,
            "LIVE",
            deployed_at=datetime.utcnow().isoformat(),
        )
        deploy_id = data_router.deploy_algorithm(algorithm)
        data_router.update_stock_status(ticker, "LIVE")

        payload = {"deployed_id": deploy_id, "algorithm_id": algo_id, "ticker": ticker}
        if ws_manager:
            try:
                await ws_manager.broadcast("algorithm_deployed", payload)
            except Exception as exc:
                logger.debug("[DeploymentAgent] WS broadcast failed: %s", exc)

        logger.info("[DeploymentAgent] Deployed %s for %s", algo_id, ticker)
        return payload

    async def retire(self, algo_id: str, data_router, reason: str = "") -> bool:
        data_router.retire_algorithm(algo_id, reason)
        logger.info("[DeploymentAgent] Retired %s (%s)", algo_id, reason or "no reason")
        return True
