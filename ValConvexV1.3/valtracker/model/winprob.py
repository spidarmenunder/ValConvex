"""Effective skill and team win probability.

effective skill = rank points (tier*100 + RR)
                  + 25% of the gap up to their peak
                  + rating adjustment (0.10 above average = +40 points, capped +-150)
Team strength is the average; party stacks add a small bonus. The probability is a
logistic curve on the difference. It is an estimate, and matchmaking already balances
teams, so most lobbies land between 45% and 55%.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from ..ranks import tier_points
from ..util import clamp

S = 250.0                  # logistic scale
RATING_POINTS = 400.0      # skill points per 1.00 of rating
ADJ_CAP = 150.0
PEAK_BLEND = 0.25
PARTY_BONUS = {2: 5, 3: 12, 4: 18, 5: 25}
UNRANKED_FALLBACK = 900.0  # Gold 1, only used if nobody in the lobby has a rank
THIN_GAMES = 3


def effective_skill(tier, rr, peak_tier, peak_rr, rating) -> Optional[float]:
    cur = tier_points(tier, rr)
    if cur is None:
        return None
    peak = tier_points(peak_tier, peak_rr)
    peak = cur if peak is None else peak
    blend = PEAK_BLEND * max(0, peak - cur)
    adj = 0.0 if rating is None else clamp((rating - 1.0) * RATING_POINTS, -ADJ_CAP, ADJ_CAP)
    return cur + blend + adj


def party_bonus(party_ids: Sequence[Optional[str]]) -> int:
    """Bonus for the stacks inside one team (solo players have party id None)."""
    counts: Dict[str, int] = {}
    for p in party_ids:
        if p:
            counts[p] = counts.get(p, 0) + 1
    return sum(PARTY_BONUS.get(min(n, 5), 0) for n in counts.values() if n >= 2)


def win_probability(mine: List[dict], theirs: List[dict], s: float = S) -> dict:
    """mine / theirs: [{"skill": float|None, "party": str|None, "games": int}, ...]"""
    known = [p["skill"] for p in mine + theirs if p.get("skill") is not None]
    fallback = (sum(known) / len(known)) if known else UNRANKED_FALLBACK

    def avg(team):
        vals = [p["skill"] if p.get("skill") is not None else fallback for p in team]
        return sum(vals) / len(vals) if vals else fallback

    my_avg, en_avg = avg(mine), avg(theirs)
    my_bonus = party_bonus([p.get("party") for p in mine])
    en_bonus = party_bonus([p.get("party") for p in theirs])
    diff = (my_avg + my_bonus) - (en_avg + en_bonus)
    p_me = 1.0 / (1.0 + math.exp(-diff / s))

    everyone = mine + theirs
    unknown = sum(1 for p in everyone if p.get("skill") is None)
    thin = sum(1 for p in everyone if p.get("skill") is None or p.get("games", 0) < THIN_GAMES)
    if unknown >= 3 or thin >= 6:
        confidence = "low"
    elif unknown >= 1 or thin >= 2:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "p_me": p_me, "p_enemy": 1.0 - p_me,
        "my_avg": my_avg, "en_avg": en_avg,
        "my_bonus": my_bonus, "en_bonus": en_bonus,
        "confidence": confidence, "thin": thin, "unknown": unknown,
    }
