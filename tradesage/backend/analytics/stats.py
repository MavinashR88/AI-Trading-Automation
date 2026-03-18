"""
TradeSage Analytics — Confidence intervals, $100 projector, probability engine.
Uses scipy.stats for t-distribution confidence intervals.
"""
from __future__ import annotations

import math
import logging
from typing import Optional

import numpy as np
from scipy import stats

from backend.models.trade import ProbabilityScore

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Confidence Interval Engine
# ──────────────────────────────────────────────

def compute_confidence_interval(
    returns: list[float],
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """
    Compute a t-distribution confidence interval for a list of % returns.

    Returns:
        (ci_lower, mean_return, ci_upper) — all as percentages
    """
    if not returns or len(returns) < 2:
        # Not enough data — return zero-based defaults
        return -2.0, 0.0, 2.0

    arr = np.array(returns, dtype=float)
    n = len(arr)
    mean = float(np.mean(arr))
    se = float(stats.sem(arr))          # standard error of the mean
    t_crit = float(stats.t.ppf((1 + confidence) / 2.0, df=n - 1))

    ci_lower = mean - t_crit * se
    ci_upper = mean + t_crit * se

    return round(ci_lower, 4), round(mean, 4), round(ci_upper, 4)


def compute_population_ci(
    win_rate: float,
    n_trades: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """
    Wilson score interval for a binary win-rate proportion.
    More accurate than normal approximation for small samples.

    Returns:
        (lower_bound, upper_bound) win-rate proportions (0-1)
    """
    if n_trades == 0:
        return 0.0, 1.0

    z = stats.norm.ppf((1 + confidence) / 2.0)
    p = win_rate
    n = n_trades

    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom

    lower = max(0.0, centre - margin)
    upper = min(1.0, centre + margin)
    return round(lower, 4), round(upper, 4)


# ──────────────────────────────────────────────
# $100 Projector
# ──────────────────────────────────────────────

def project_100_dollars(
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    initial: float = 100.0,
) -> dict:
    """
    Project a $100 investment based on win rate and average win/loss percentages.

    Uses expected value:
        E[R] = win_rate * avg_win_pct + (1 - win_rate) * avg_loss_pct

    avg_loss_pct should be negative (e.g. -0.02 for -2% loss).

    Returns a dict with expected, best-case, worst-case dollar values
    and estimated trades to double.
    """
    if avg_loss_pct > 0:
        avg_loss_pct = -avg_loss_pct   # ensure negative

    expected_return_pct = (win_rate * avg_win_pct) + ((1 - win_rate) * avg_loss_pct)

    # Expected outcome
    proj_expected = initial * (1 + expected_return_pct)

    # Best case: 95% CI upper on win rate with double the win pct
    best_win_rate = min(1.0, win_rate + 0.15)
    best_return = (best_win_rate * avg_win_pct * 1.5) + ((1 - best_win_rate) * avg_loss_pct * 0.5)
    proj_best = initial * (1 + best_return)

    # Worst case: 95% CI lower
    worst_win_rate = max(0.0, win_rate - 0.15)
    worst_return = (worst_win_rate * avg_win_pct * 0.5) + ((1 - worst_win_rate) * avg_loss_pct * 1.5)
    proj_worst = initial * (1 + worst_return)

    # Trades to double $100 at expected return per trade
    if expected_return_pct > 0:
        # Rule of 72 approximation: n = ln(2) / ln(1 + r)
        trades_to_double = math.ceil(math.log(2) / math.log(1 + expected_return_pct))
    else:
        trades_to_double = 9999   # never at this rate

    return {
        "proj_100_expected": round(proj_expected, 2),
        "proj_100_best": round(proj_best, 2),
        "proj_100_worst": round(proj_worst, 2),
        "proj_100_double_trades": trades_to_double,
        "expected_return_pct": round(expected_return_pct * 100, 4),
    }


def project_100_from_ci(
    ci_lower: float,
    expected_return: float,
    ci_upper: float,
    initial: float = 100.0,
) -> dict:
    """
    Simple $100 projection from CI values (all as %).

    ci_lower, expected_return, ci_upper are percentages (e.g. 2.5 = 2.5%).
    """
    proj_expected = initial * (1 + expected_return / 100)
    proj_best = initial * (1 + ci_upper / 100)
    proj_worst = initial * (1 + ci_lower / 100)

    if expected_return > 0:
        trades_to_double = math.ceil(math.log(2) / math.log(1 + expected_return / 100))
    else:
        trades_to_double = 9999

    return {
        "proj_100_expected": round(proj_expected, 2),
        "proj_100_best": round(proj_best, 2),
        "proj_100_worst": round(proj_worst, 2),
        "proj_100_double_trades": trades_to_double,
    }


# ──────────────────────────────────────────────
# Probability Scoring Engine
# ──────────────────────────────────────────────

WEIGHTS = {
    "news_score": 0.20,
    "risk_score": 0.25,
    "mentor_score": 0.35,
    "historical_win_rate": 0.20,
}

GRADE_THRESHOLDS = [
    (0.88, "A+"),
    (0.78, "A"),
    (0.65, "B"),
    (0.50, "C"),
    (0.38, "D"),
    (0.0,  "F"),
]


def grade_signal(composite: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if composite >= threshold:
            return grade
    return "F"


def compute_probability_score(
    trade_id: str,
    news_score: float,
    risk_score: float,
    mentor_score: float,
    historical_win_rate: float,
    past_returns: Optional[list[float]] = None,
    avg_win_pct: float = 0.04,    # 4% average win
    avg_loss_pct: float = -0.02,  # 2% average loss (default 2:1 R:R)
) -> ProbabilityScore:
    """
    Compute the full ProbabilityScore for a trade.

    Args:
        trade_id: Unique trade ID.
        news_score: 0-1 sentiment strength.
        risk_score: 0-1 risk/reward quality.
        mentor_score: 0-1 mentor conviction.
        historical_win_rate: 0-1 win rate of matching patterns.
        past_returns: List of % returns from similar past trades (for CI).
        avg_win_pct: Average win as decimal (e.g. 0.04 = 4%).
        avg_loss_pct: Average loss as decimal (e.g. -0.02 = -2%).
    """
    # Clamp all inputs
    news_score = max(0.0, min(1.0, news_score))
    risk_score = max(0.0, min(1.0, risk_score))
    mentor_score = max(0.0, min(1.0, mentor_score))
    historical_win_rate = max(0.0, min(1.0, historical_win_rate))

    composite = (
        news_score * WEIGHTS["news_score"]
        + risk_score * WEIGHTS["risk_score"]
        + mentor_score * WEIGHTS["mentor_score"]
        + historical_win_rate * WEIGHTS["historical_win_rate"]
    )
    composite = round(composite, 4)

    # Confidence interval from past returns
    if past_returns and len(past_returns) >= 2:
        ci_lower, expected_return, ci_upper = compute_confidence_interval(past_returns)
    else:
        # Synthetic CI based on composite score
        expected_return = composite * avg_win_pct * 100  # scale to %
        ci_lower = expected_return - 2.0
        ci_upper = expected_return + 2.0

    # $100 projector
    proj = project_100_from_ci(ci_lower, expected_return, ci_upper)

    return ProbabilityScore(
        trade_id=trade_id,
        news_score=news_score,
        risk_score=risk_score,
        mentor_score=mentor_score,
        historical_win_rate=historical_win_rate,
        composite_score=composite,
        composite_pct=f"{composite * 100:.1f}% probability of winning",
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        expected_return=round(expected_return, 4),
        proj_100_expected=proj["proj_100_expected"],
        proj_100_best=proj["proj_100_best"],
        proj_100_worst=proj["proj_100_worst"],
        proj_100_double_trades=proj["proj_100_double_trades"],
        signal_grade=grade_signal(composite),
    )


# ──────────────────────────────────────────────
# Kelly Criterion Helper
# ──────────────────────────────────────────────

def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Full Kelly fraction.
        f* = (p * b - q) / b
    where:
        p = win probability
        q = 1 - p
        b = avg win / avg loss ratio

    Returns a fraction capped at 0.25 (quarter-Kelly for safety).
    """
    if avg_loss <= 0:
        avg_loss = 0.001  # avoid division by zero
    b = abs(avg_win) / abs(avg_loss)
    p = win_rate
    q = 1 - p
    if b <= 0:
        return 0.1
    f = (p * b - q) / b
    # Full Kelly is often too aggressive — cap at 25% (quarter-Kelly)
    return max(0.01, min(0.25, round(f, 4)))


# ──────────────────────────────────────────────
# Rolling Statistics
# ──────────────────────────────────────────────

def rolling_win_rate(outcomes: list[str], window: int = 100) -> float:
    """Compute rolling win rate from a list of 'WIN'/'LOSS'/'BREAKEVEN' strings."""
    recent = outcomes[-window:]
    if not recent:
        return 0.0
    wins = sum(1 for o in recent if o == "WIN")
    return round(wins / len(recent), 4)


def sharpe_ratio(returns: list[float], risk_free_rate: float = 0.05) -> float:
    """
    Annualised Sharpe Ratio.
    Assumes daily returns (252 trading days).
    """
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    daily_rf = risk_free_rate / 252
    excess = arr - daily_rf
    if np.std(excess) == 0:
        return 0.0
    return float(np.mean(excess) / np.std(excess) * math.sqrt(252))


def max_drawdown(equity_curve: list[float]) -> float:
    """
    Maximum drawdown as a fraction (0-1) from a list of portfolio values.
    """
    if len(equity_curve) < 2:
        return 0.0
    arr = np.array(equity_curve)
    peak = np.maximum.accumulate(arr)
    drawdown = (arr - peak) / peak
    return float(abs(np.min(drawdown)))
