"""
Model Performance Registry — tracks which AI model configuration performs best
and saves versioned snapshots with rollback support.

A "model version" captures:
  - LLM model name + temperature used at that time
  - Rolling win rate at time of snapshot
  - Total trades, P&L
  - What triggered the snapshot (manual / auto-best)

Snapshots saved to: tradesage_models/ directory as JSON files.
Best model pointer: tradesage_models/best.json
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent.parent.parent / "tradesage_models"


def _ensure_dir() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


# ── Save a new version snapshot ───────────────────────────────────────────────

def save_version(
    model_name: str,
    win_rate: float,
    total_trades: int,
    total_pnl: float,
    trigger: str = "auto",
    notes: str = "",
) -> dict:
    """
    Save a versioned snapshot of the current model performance.
    Returns the version metadata dict.
    """
    _ensure_dir()
    version_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    meta = {
        "version_id": version_id,
        "model_name": model_name,
        "win_rate": round(win_rate, 4),
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "trigger": trigger,      # "auto" | "manual" | "best"
        "notes": notes,
        "saved_at": datetime.utcnow().isoformat(),
    }

    path = MODELS_DIR / f"v_{version_id}.json"
    path.write_text(json.dumps(meta, indent=2))
    logger.info("Model snapshot saved: %s  win_rate=%.1f%%  pnl=$%.2f",
                version_id, win_rate * 100, total_pnl)

    # Check if this is the new best
    best = load_best()
    if best is None or win_rate > best.get("win_rate", 0):
        _set_best(meta)
        logger.info("New BEST model: %s (%.1f%% win rate)", version_id, win_rate * 100)

    return meta


# ── Load versions ─────────────────────────────────────────────────────────────

def list_versions(limit: int = 20) -> list[dict]:
    """Return all saved versions, newest first."""
    _ensure_dir()
    files = sorted(MODELS_DIR.glob("v_*.json"), reverse=True)
    versions = []
    for f in files[:limit]:
        try:
            versions.append(json.loads(f.read_text()))
        except Exception:
            pass
    return versions


def load_version(version_id: str) -> Optional[dict]:
    """Load a specific version by ID."""
    path = MODELS_DIR / f"v_{version_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_best() -> Optional[dict]:
    """Load the current best-performing model snapshot."""
    best_path = MODELS_DIR / "best.json"
    if not best_path.exists():
        return None
    try:
        return json.loads(best_path.read_text())
    except Exception:
        return None


# ── Rollback ──────────────────────────────────────────────────────────────────

def rollback_to(version_id: str) -> dict:
    """
    Set a previous version as the active best model.
    Returns the rolled-back version metadata.
    Raises FileNotFoundError if version_id does not exist.
    """
    meta = load_version(version_id)
    if meta is None:
        raise FileNotFoundError(f"Version '{version_id}' not found in {MODELS_DIR}")
    _set_best(meta)
    logger.warning("ROLLBACK to model version %s (win_rate=%.1f%%)", version_id, meta["win_rate"] * 100)
    return meta


# ── Internals ─────────────────────────────────────────────────────────────────

def _set_best(meta: dict) -> None:
    best_path = MODELS_DIR / "best.json"
    best_path.write_text(json.dumps({**meta, "set_as_best_at": datetime.utcnow().isoformat()}, indent=2))
