"""Per-player performance rating. 1.00 = average for the agents they played.

Our own formula (not copied from any site):
  1. Each game: half ACS-vs-baseline, half ADR-vs-baseline, both for the agent played.
  2. Last 3 games weighted newest first (50/30/20).
  3. Shrunk toward 1.00, because three games is a small sample.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from ..util import clamp

WEIGHTS = (0.5, 0.3, 0.2)
SHRINK_K = 0.75            # n games -> keep n / (n + K) of the deviation (3 games = 80%)
RATING_MIN, RATING_MAX = 0.70, 1.50


def game_score(acs: float, adr: float, base: Tuple[float, float]) -> float:
    base_acs, base_adr = base
    if base_acs <= 0 or base_adr <= 0:
        return 1.0
    return 0.5 * (acs / base_acs) + 0.5 * (adr / base_adr)


def combine(scores_newest_first: Iterable[float]) -> Tuple[float, int]:
    """Weighted average of up to three game scores. Returns (raw, n_games)."""
    scores: List[float] = list(scores_newest_first)[: len(WEIGHTS)]
    n = len(scores)
    if n == 0:
        return 1.0, 0
    w = WEIGHTS[:n]
    total = sum(w)
    return sum(s * wi for s, wi in zip(scores, w)) / total, n


def shrink(raw: float, n: int, k: float = SHRINK_K) -> float:
    if n <= 0:
        return 1.0
    return 1.0 + (raw - 1.0) * (n / (n + k))


def player_rating(scores_newest_first: Iterable[float]) -> Optional[float]:
    """Final displayed rating, or None when there are no games."""
    raw, n = combine(scores_newest_first)
    if n == 0:
        return None
    return clamp(shrink(raw, n), RATING_MIN, RATING_MAX)
