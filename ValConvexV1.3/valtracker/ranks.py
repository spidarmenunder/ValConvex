"""Competitive tier helpers.

Riot tier numbers: 3-5 Iron, 6-8 Bronze, 9-11 Silver, 12-14 Gold, 15-17 Platinum,
18-20 Diamond, 21-23 Ascendant, 24-26 Immortal, 27 Radiant. 0 = unranked.
"skill points" = (tier - 3) * 100 + RR, so Iron 1 / 0 RR is 0 points.
"""
from __future__ import annotations

from typing import Optional, Tuple

_FAMILIES = ["Iron", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Ascendant", "Immortal"]
_SHORT = {"Iron": "Iron", "Bronze": "Bronze", "Silver": "Silver", "Gold": "Gold",
          "Platinum": "Plat", "Diamond": "Dia", "Ascendant": "Asc", "Immortal": "Imm"}

TIER_NAMES = {0: "Unranked"}
TIER_SHORT = {0: "Unranked"}
for _i, _fam in enumerate(_FAMILIES):
    for _j in (1, 2, 3):
        _t = 3 + _i * 3 + (_j - 1)
        TIER_NAMES[_t] = f"{_fam} {_j}"
        TIER_SHORT[_t] = f"{_SHORT[_fam]} {_j}"
TIER_NAMES[27] = "Radiant"
TIER_SHORT[27] = "Radiant"

MAX_TIER = 27


def tier_name(tier: Optional[int]) -> str:
    return TIER_NAMES.get(int(tier or 0), "Unranked")


def tier_short(tier: Optional[int]) -> str:
    return TIER_SHORT.get(int(tier or 0), "Unranked")


def tier_family(tier: Optional[int]) -> str:
    """'Diamond', 'Ascendant', ... or 'Unranked'."""
    t = int(tier or 0)
    if t < 3:
        return "Unranked"
    if t >= 27:
        return "Radiant"
    return _FAMILIES[(t - 3) // 3]


def bracket(tier: Optional[int]) -> str:
    """Coarse skill bucket used for baselines."""
    t = int(tier or 0)
    if t < 3:
        return "unranked"
    if t <= 8:
        return "low"
    if t <= 14:
        return "mid"
    if t <= 20:
        return "high"
    return "top"


def tier_points(tier: Optional[int], rr: Optional[int] = 0) -> Optional[int]:
    t = int(tier or 0)
    if t < 3:
        return None
    return (min(t, MAX_TIER) - 3) * 100 + int(rr or 0)


def rank_from_points(points: float) -> Tuple[int, int]:
    """Inverse of tier_points: returns (tier, rr)."""
    p = max(0, int(round(points)))
    tier = min(MAX_TIER, 3 + p // 100)
    rr = p - (tier - 3) * 100
    return tier, rr


def rank_label(tier: Optional[int], rr: Optional[int] = None) -> str:
    """'Diamond 3 · 78 RR' (RR omitted for unranked)."""
    if int(tier or 0) < 3:
        return "Unranked"
    if rr is None:
        return tier_name(tier)
    return f"{tier_name(tier)} · {int(rr)} RR"
