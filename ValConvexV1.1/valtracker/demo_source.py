"""A pretend Riot client: cycles menu -> agent select -> in match -> menu, and serves fake players,
histories and finished matches in Riot's JSON shapes. Runs the exact same app code as the real thing."""
from __future__ import annotations

import copy
import random
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import demo
from .static import StaticData

DEFAULT_PHASES = [("menu", 4), ("pregame", 8), ("ingame", 35), ("menu", 25)]
HIDDEN_DEFAULT = ("mango#8821",)          # shows how Streamer Mode players are handled


class DemoSource:
    def __init__(self, static: Optional[StaticData] = None, phases=None, speed: float = 1.0,
                 details_delay: float = 3.0, hidden=HIDDEN_DEFAULT, clock: Callable[[], float] = time.time,
                 n_past: int = 6, blank_raw: bool = False):
        self.static = static or demo.demo_static()
        self.phases = phases or DEFAULT_PHASES
        self.speed = speed
        self.details_delay = details_delay
        self.clock = clock
        self.t0 = clock()
        self.roster = demo.demo_roster()
        self.hidden = {demo.puuid_of(h) for h in hidden}
        self.blank_raw = blank_raw
        self.puuid = demo.puuid_of("You#PH")
        self.by_id = {p["puuid"]: p for p in self.roster}
        self._past: Dict[str, dict] = {}
        self._past_order: List[str] = []
        self._live: Dict[str, dict] = {}
        self._build_past(n_past)

    # ---- fake history ----------------------------------------------------
    def _hide(self, raw: dict) -> dict:
        for p in raw["players"]:
            if p["subject"] in self.hidden or self.blank_raw:
                p["gameName"], p["tagLine"] = "", ""
            if self.blank_raw:
                p["partyId"] = None
        return raw

    def _build_past(self, n: int) -> None:
        rng = random.Random(42)
        now = int(self.clock() * 1000)
        hours_ago = [3, 5, 27, 50, 74, 98, 122, 150]
        for i in range(n):
            roster = copy.deepcopy(self.roster)
            for p in roster:
                if rng.random() < .5:
                    p["agent"] = rng.choice(list(demo.AGENT_DEFS))
            blue_wins = 13 if i in (0, 1, 3, 4) else 9
            win = set(rng.sample(range(1, 23), blue_wins))
            mid = f"demo-past-{i}"
            raw = self._hide(demo.make_raw_match(100 + i, mid, now - hours_ago[i] * 3600 * 1000, roster, win))
            self._past[mid] = raw
            self._past_order.append(mid)
        # per-player RR chains (newest first)
        self._updates: Dict[str, List[dict]] = {}
        for p in self.roster:
            after_tier, after_rr = p["tier"], p["rr"]
            chain = []
            for mid in self._past_order:
                raw = self._past[mid]
                won = next(t["won"] for t in raw["teams"] if t["teamId"] == p["team"])
                earned = rng.randint(14, 24) if won else -rng.randint(12, 21)
                before_rr, before_tier = after_rr - earned, after_tier
                if before_rr < 0:
                    before_rr, before_tier = before_rr + 100, max(3, after_tier - 1)
                elif before_rr > 99:
                    before_rr, before_tier = before_rr - 100, min(26, after_tier + 1)
                chain.append({"MatchID": mid, "MapID": demo.MAP_URL, "SeasonID": "demo-act5",
                              "MatchStartTime": raw["matchInfo"]["gameStartMillis"],
                              "TierAfterUpdate": after_tier, "TierBeforeUpdate": before_tier,
                              "RankedRatingAfterUpdate": after_rr, "RankedRatingBeforeUpdate": before_rr,
                              "RankedRatingEarned": earned, "RankedRatingPerformanceBonus": 0})
                after_tier, after_rr = before_tier, before_rr
            self._updates[p["puuid"]] = chain

    # ---- phases ----------------------------------------------------------
    def _elapsed(self) -> float:
        return (self.clock() - self.t0) * self.speed

    def _position(self) -> Tuple[int, str, float]:
        total = sum(d for _, d in self.phases)
        e = self._elapsed()
        cycle, pos = int(e // total), e % total
        for name, dur in self.phases:
            if pos < dur:
                return cycle, name, pos
            pos -= dur
        return cycle, self.phases[-1][0], 0.0

    def state(self) -> Tuple[str, Optional[str]]:
        cycle, name, _ = self._position()
        return (name, f"demo-live-{cycle}") if name in ("pregame", "ingame") else ("menu", None)

    def _ingame_end_elapsed(self, cycle: int) -> float:
        total = sum(d for _, d in self.phases)
        t = cycle * total
        for name, dur in self.phases:
            t += dur
            if name == "ingame":
                return t
        return t

    def lobby(self, phase: str, match_id: str) -> Optional[dict]:
        players = []
        for p in self.roster:
            if phase == "pregame" and p["team"] != "Blue":
                continue
            players.append({"puuid": p["puuid"], "agent_id": f"demo-agent-{p['agent']}",
                            "incognito": p["puuid"] in self.hidden, "level": 100 + len(p["name"]), "tier": p["tier"],
                            "team": "ally" if p["team"] == "Blue" else "enemy"})
        return {"phase": phase, "match_id": match_id, "map_url": demo.MAP_URL, "queue": "competitive",
                "players": players, "enemy_size": 5 if phase == "pregame" else None}

    # ---- lookups ---------------------------------------------------------
    def names(self, puuids: List[str]) -> Dict[str, Tuple[str, str]]:
        """Like Riot's name service: a name for every player asked about. The live roster itself never asks about
        players flagged as Streamer Mode, so those stay hidden there."""
        return {u: (self.by_id[u]["name"], self.by_id[u]["tag"]) for u in puuids if u in self.by_id}

    def parties(self) -> Dict[str, str]:
        return {p["puuid"]: p["party"] for p in self.roster if p["party"]}

    def my_name(self) -> Tuple[str, str]:
        p = self.by_id[self.puuid]
        return p["name"], p["tag"]

    def mmr(self, puuid: str) -> Optional[dict]:
        p = self.by_id.get(puuid)
        if not p:
            return None
        idx = self.roster.index(p)
        tier, rr = p["tier"], p["rr"]
        peak = min(27, tier + idx % 3)
        act_tiers = {"demo-act1": max(3, tier - 3), "demo-act2": peak, "demo-act3": None,
                     "demo-act4": max(3, tier - 1), "demo-act5": tier}
        seasons = {}
        for sid, t in act_tiers.items():
            if t is None:
                seasons[sid] = {"SeasonID": sid, "NumberOfGames": 0, "NumberOfWins": 0, "CompetitiveTier": 0,
                                "RankedRating": 0, "WinsByTier": None}
                continue
            games = 46 if sid == "demo-act5" else 30 + idx
            wins = 23 if sid == "demo-act5" else 15 + idx // 2
            seasons[sid] = {"SeasonID": sid, "NumberOfGames": games, "NumberOfWins": wins, "CompetitiveTier": t,
                            "RankedRating": rr if sid == "demo-act5" else 40,
                            "WinsByTier": {str(t): wins // 2, str(max(3, t - 1)): wins - wins // 2}}
        latest = self._updates[puuid][0]
        return {"Subject": puuid, "LatestCompetitiveUpdate": latest,
                "QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": seasons}}}

    def updates(self, puuid: str, start: int = 0, end: int = 5) -> List[dict]:
        return copy.deepcopy(self._updates.get(puuid, [])[start:end])

    def history(self, puuid: str, start: int = 0, end: int = 10, queue: Optional[str] = None) -> List[dict]:
        out = [{"MatchID": mid, "GameStartTime": self._past[mid]["matchInfo"]["gameStartMillis"],
                "QueueID": "competitive"} for mid in self._past_order]
        for mid, raw in sorted(self._live.items(), key=lambda kv: -kv[1]["matchInfo"]["gameStartMillis"]):
            out.insert(0, {"MatchID": mid, "GameStartTime": raw["matchInfo"]["gameStartMillis"],
                           "QueueID": "competitive"})
        return out[start:end]

    def match_details(self, match_id: str) -> Optional[dict]:
        if match_id in self._past:
            return copy.deepcopy(self._past[match_id])
        if not match_id.startswith("demo-live-"):
            return None
        cycle = int(match_id.rsplit("-", 1)[1])
        if self._elapsed() < self._ingame_end_elapsed(cycle) + self.details_delay * self.speed:
            return None                                     # "still processing", like the real service
        if match_id not in self._live:
            rng = random.Random(500 + cycle)
            blue_wins = 13 if cycle % 2 == 0 else 9
            win = set(rng.sample(range(1, 23), blue_wins))
            started = int(self.clock() * 1000) - 25 * 60 * 1000
            self._live[match_id] = self._hide(demo.make_raw_match(900 + cycle, match_id, started,
                                                                  copy.deepcopy(self.roster), win))
        return copy.deepcopy(self._live[match_id])
