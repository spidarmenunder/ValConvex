"""What is 'average' ACS / ADR for an agent at a given skill bracket?

The rating for a player compares their performance to this baseline, so a Sage with
190 ACS and a Jett with 240 ACS can both come out around 1.05.

Defaults below are starting estimates by role. Every match the tracker processes adds
observations (all ten players) to the database, and the baseline blends toward what it
has actually seen for that agent and bracket.
"""
from __future__ import annotations

from typing import Optional, Tuple

ROLE_DEFAULTS = {
    "Duelist": (222.0, 146.0),
    "Initiator": (200.0, 134.0),
    "Controller": (188.0, 127.0),
    "Sentinel": (183.0, 124.0),
}
FALLBACK = (200.0, 133.0)
BRACKET_SCALE = {"low": 1.03, "mid": 1.0, "high": 0.99, "top": 0.98, "unranked": 1.0}
PRIOR_N = 40          # how many observations the default estimate is "worth"


class Baselines:
    def __init__(self, db=None):
        self.db = db

    @staticmethod
    def default(role: Optional[str], bracket: str) -> Tuple[float, float]:
        acs, adr = ROLE_DEFAULTS.get(role or "", FALLBACK)
        s = BRACKET_SCALE.get(bracket, 1.0)
        return acs * s, adr * s

    def get(self, agent: str, role: Optional[str], bracket: str) -> Tuple[float, float]:
        acs, adr = self.default(role, bracket)
        if self.db is None or not agent:
            return acs, adr
        n, sum_acs, sum_adr = self.db.baseline_obs(agent, bracket)
        if n <= 0:
            return acs, adr
        return ((PRIOR_N * acs + sum_acs) / (PRIOR_N + n),
                (PRIOR_N * adr + sum_adr) / (PRIOR_N + n))

    def observe(self, agent: str, bracket: str, acs: float, adr: float) -> None:
        if self.db is not None and agent and acs > 0 and adr > 0:
            self.db.add_baseline_obs(agent, bracket, acs, adr)
