"""
LLM Router — Single entry point for ALL LLM calls in TradeSage.
Never import ChatAnthropic/Ollama directly from agents. Always use call_llm().

Usage:
    from backend.llm.router import call_llm
    result = await call_llm("mentor_review_note", user_prompt, system_prompt)
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Task tiers ────────────────────────────────────────────────────────────────

class TaskTier(Enum):
    HEAVY = "heavy"   # Best reasoning: Sonnet in live, Haiku in testing
    LIGHT = "light"   # Good enough: Haiku always
    NANO  = "nano"    # Simple classify: Haiku always


TASK_TIERS: dict[str, TaskTier] = {
    # HEAVY — mentor decisions, deep analysis
    "mentor_veto":           TaskTier.HEAVY,
    "mentor_review_note":    TaskTier.HEAVY,
    "weekly_analysis":       TaskTier.HEAVY,
    "book_gap_analysis":     TaskTier.HEAVY,

    # LIGHT — news, gates, lessons, chat
    "chat":                  TaskTier.LIGHT,
    "news_scoring":          TaskTier.LIGHT,
    "sentiment_analysis":    TaskTier.LIGHT,
    "macro_check":           TaskTier.LIGHT,
    "lesson_generation":     TaskTier.LIGHT,
    "book_suggestion":       TaskTier.LIGHT,
    "risk_reasoning":        TaskTier.LIGHT,
    "pattern_analysis":      TaskTier.LIGHT,

    # NANO — simple classify
    "signal_grade":          TaskTier.NANO,
    "gate_pass_fail":        TaskTier.NANO,
    "urgency_classify":      TaskTier.NANO,
    "sector_classify":       TaskTier.NANO,

    # RESEARCH pipeline
    "research_classify":     TaskTier.LIGHT,   # catalyst / insider extraction
    "research_verdict":      TaskTier.HEAVY,   # senior-quant final verdict

    # ALGORITHM generation pipeline
    "algorithm_assemble":    TaskTier.HEAVY,   # write entry/exit Python code

    # SIMULATION pipeline
    "sim_verdict_simple":    TaskTier.LIGHT,   # simulation narrative summary

    # VALIDATION pipeline
    "risk_committee":        TaskTier.HEAVY,   # senior risk committee review
}

# Model map: llm_mode → tier → model_id
MODEL_MAP: dict[str, dict[TaskTier, str]] = {
    "testing": {
        TaskTier.HEAVY: "claude-haiku-4-5-20251001",
        TaskTier.LIGHT: "claude-haiku-4-5-20251001",
        TaskTier.NANO:  "claude-haiku-4-5-20251001",
    },
    "live": {
        TaskTier.HEAVY: "claude-sonnet-4-20250514",
        TaskTier.LIGHT: "claude-haiku-4-5-20251001",
        TaskTier.NANO:  "claude-haiku-4-5-20251001",
    },
    "free": {
        TaskTier.HEAVY: "ollama",
        TaskTier.LIGHT: "ollama",
        TaskTier.NANO:  "ollama",
    },
}

MAX_TOKENS: dict[TaskTier, int] = {
    TaskTier.HEAVY: 2048,
    TaskTier.LIGHT: 1024,
    TaskTier.NANO:  256,
}

TEMPERATURE: dict[TaskTier, float] = {
    TaskTier.HEAVY: 0.3,
    TaskTier.LIGHT: 0.1,
    TaskTier.NANO:  0.0,
}


class BudgetExceededError(Exception):
    pass


# ── Core async callers ────────────────────────────────────────────────────────

async def _call_anthropic(model: str, user: str, system: str, max_tokens: int, temperature: float) -> tuple[str, dict]:
    """Call Anthropic API. Returns (content, usage_dict)."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage
    from backend.config import settings

    llm = ChatAnthropic(
        model=model,
        api_key=settings.ANTHROPIC_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=user))

    response = await llm.ainvoke(messages)
    usage = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = {
            "input_tokens": response.usage_metadata.get("input_tokens", 0),
            "output_tokens": response.usage_metadata.get("output_tokens", 0),
        }
    elif hasattr(response, "response_metadata"):
        meta = response.response_metadata or {}
        usage = meta.get("usage", {})
    return response.content, usage


async def _call_ollama(user: str, system: str, max_tokens: int) -> str:
    """Call local Ollama. Falls back gracefully."""
    from backend.config import settings
    try:
        import httpx
        payload = {
            "model": settings.OLLAMA_MODEL,
            "prompt": f"{system}\n\n{user}" if system else user,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{settings.OLLAMA_BASE_URL}/api/generate", json=payload)
            r.raise_for_status()
            return r.json().get("response", "")
    except Exception as exc:
        logger.error("Ollama call failed: %s", exc)
        return "{}"


# ── Public API ────────────────────────────────────────────────────────────────

async def call_llm(
    task: str,
    prompt: str,
    system: str = "",
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> str:
    """
    Main entry point for every LLM call in TradeSage.
    Routes to correct model based on LLM_MODE + task tier.
    Enforces daily budget.
    """
    from backend.config import settings
    from backend.llm.cost_tracker import cost_tracker

    tier = TASK_TIERS.get(task, TaskTier.LIGHT)
    mode = settings.LLM_MODE
    model = MODEL_MAP.get(mode, MODEL_MAP["testing"])[tier]

    mt = max_tokens or MAX_TOKENS[tier]
    temp = temperature if temperature is not None else TEMPERATURE[tier]

    # Budget guard
    if not cost_tracker.has_budget_remaining():
        logger.warning("[BUDGET] Daily budget $%.2f exceeded. Falling back to free/stub.",
                       settings.LLM_DAILY_BUDGET_USD)
        if mode == "free" or settings.OLLAMA_BASE_URL:
            return await _call_ollama(prompt, system, mt)
        # Return empty JSON rather than crash the trade pipeline
        return "{}"

    if model == "ollama":
        return await _call_ollama(prompt, system, mt)

    try:
        content, usage = await _call_anthropic(model, prompt, system, mt, temp)
        await cost_tracker.record(task, model, usage)
        return content
    except Exception as exc:
        logger.error("[LLM] %s call failed (task=%s model=%s): %s", mode, task, model, exc)
        return "{}"


def get_active_model(task: str) -> str:
    """Return which model would be used for this task right now."""
    from backend.config import settings
    tier = TASK_TIERS.get(task, TaskTier.LIGHT)
    return MODEL_MAP.get(settings.LLM_MODE, MODEL_MAP["testing"])[tier]
