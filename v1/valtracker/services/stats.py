"""Per-player numbers: rank, peak, recent games, rating, RR changes, profiles. All cached."""
from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Callable, Dict, List, Optional

from .. import matchdata
from ..model import rating as rating_model
from ..model.winprob import effective_skill
from ..ranks import bracket
from ..util import get

log = logging.getLogger("valtracker")

MMR_TTL = 600.0
UPDATES_TTL = 120.0


def _wavg(values: List[float]) -> float:
    n = min(len(values), len(rating_model.WEIGHTS))
    if n == 0:
        return 0.0
    w = rating_model.WEIGHTS[:n]
    return sum(v * wi for v, wi in zip(values[:n], w)) / sum(w)


class StatsService:
    def __init__(self, source, static, db, baselines):
        self.source, self.static, self.db, self.baselines = source, static, db, baselines
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    # ---- rank ------------------------------------------------------------
    def parse_mmr(self, raw: Optional[dict]) -> dict:
        info = {"tier": 0, "rr": 0, "peak_tier": 0, "peak_rr": 0, "peak_season": None, "peak_label": "",
                "act_games": 0, "act_wins": 0, "wr": None, "acts": [], "current_season": None, "current_label": ""}
        if not raw:
            return info
        comp = get(raw, "QueueSkills", "competitive", "SeasonalInfoBySeasonID", default={}) or {}
        seasons = {str(k).lower(): v for k, v in comp.items() if isinstance(v, dict)}
        latest = raw.get("LatestCompetitiveUpdate") or {}
        tier = int(latest.get("TierAfterUpdate") or 0)
        rr = int(latest.get("RankedRatingAfterUpdate") or 0)
        cur = self.static.current_act()
        if cur is None and latest.get("SeasonID"):
            cur = str(latest["SeasonID"]).lower()
        if tier == 0 and cur in seasons:
            tier = int(seasons[cur].get("CompetitiveTier") or 0)
            rr = int(seasons[cur].get("RankedRating") or 0)

        order = {a: i for i, a in enumerate(self.static.acts())}
        best = (0, 10 ** 6, None)
        for sid, s in seasons.items():
            tiers = [int(k) for k in (s.get("WinsByTier") or {}) if str(k).isdigit()]
            ct = int(s.get("CompetitiveTier") or 0)
            if ct:
                tiers.append(ct)
            t = max(tiers) if tiers else 0
            o = order.get(sid, 10 ** 5)
            if t > best[0] or (t == best[0] and t > 0 and o < best[1]):
                best = (t, o, sid)
        peak_tier, peak_season = best[0], best[2]
        if tier > peak_tier:
            peak_tier, peak_season = tier, cur
        info.update({"tier": tier, "rr": rr, "peak_tier": peak_tier,
                     "peak_rr": rr if peak_tier == tier else 0, "peak_season": peak_season,
                     "peak_label": self.static.season_label(peak_season) if peak_season else "",
                     "current_season": cur, "current_label": self.static.season_label(cur) if cur else ""})
        s = seasons.get(cur or "")
        if s:
            games, wins = int(s.get("NumberOfGames") or 0), int(s.get("NumberOfWins") or 0)
            info.update({"act_games": games, "act_wins": wins, "wr": (wins / games) if games else None})
        for a in (self.static.acts_before(cur, 3) if cur else []):
            sa = seasons.get(a)
            games = int(get(sa, "NumberOfGames", default=0) or 0) if sa else 0
            t = int(sa.get("CompetitiveTier") or 0) if sa and games > 0 else 0
            info["acts"].append({"season": a, "label": self.static.season_label(a), "tier": t or None, "games": games})
        return info

    def mmr_info(self, puuid: str, force: bool = False) -> dict:
        key = f"mmrinfo:{puuid}"
        if not force:
            cached = self.db.cache_get(key, MMR_TTL)
            if cached:
                return cached
        info = self.parse_mmr(self.source.mmr(puuid))
        self.db.cache_put(key, info)
        return info

    # ---- matches ---------------------------------------------------------
    def ensure_match(self, match_id: str) -> bool:
        """Make sure this match's per-player rows are cached. False if the match could not be loaded."""
        if self.db.has_match_rows(match_id):
            return True
        with self._lock_for(match_id):
            if self.db.has_match_rows(match_id):
                return True
            try:
                raw = self.source.match_details(match_id)
                if not raw or not raw.get("players"):
                    return False
                parsed = matchdata.parse_match(raw, self.static)
                self.db.save_match_rows(parsed["match_id"] or match_id, matchdata.digest_rows(parsed))
                matchdata.observe_baselines(parsed, self.baselines, self.db)
                return True
            except Exception as exc:
                log.warning("could not load match %s: %s", match_id, exc)
                return False

    def updates(self, puuid: str, n: int = 5) -> List[dict]:
        key = f"upd{n}:{puuid}"
        cached = self.db.cache_get(key, UPDATES_TTL)
        if cached is not None:
            return cached
        ups = self.source.updates(puuid, 0, n) or []
        if ups:
            self.db.cache_put(key, ups)
        return ups

    # ---- recent form -----------------------------------------------------
    def game_score(self, row: dict) -> float:
        role = self.static.agent_role(row["agent_id"])
        base = self.baselines.get(row["agent"], role, bracket(row.get("tier")))
        return rating_model.game_score(row["acs"], row["adr"], base)

    def summary(self, puuid: str, n_games: int = 3) -> dict:
        """Rating and weighted stats from the last few competitive games."""
        ups = self.updates(puuid, max(5, n_games))
        ids = [u["MatchID"] for u in ups if u.get("MatchID")][:n_games]
        for mid in ids:
            self.ensure_match(mid)
        rows_by_id = self.db.games_by_id(puuid, ids)
        rows = [rows_by_id[m] for m in ids if m in rows_by_id]
        out = {"games": len(rows), "rating": None, "acs": None, "adr": None, "hs": None, "kd": None,
               "kast": None, "last_played_ms": None}
        if ups:
            out["last_played_ms"] = ups[0].get("MatchStartTime")
        elif rows:
            out["last_played_ms"] = rows[0]["started_ms"]
        if rows:
            scores = [self.game_score(r) for r in rows]
            out.update({"rating": rating_model.player_rating(scores),
                        "acs": _wavg([r["acs"] for r in rows]), "adr": _wavg([r["adr"] for r in rows]),
                        "hs": _wavg([r["hs"] for r in rows]), "kast": _wavg([r["kast"] for r in rows]),
                        "kd": _wavg([r["kills"] / max(1, r["deaths"]) for r in rows])})
        return out

    def player_info(self, puuid: str) -> dict:
        mmr = self.mmr_info(puuid)
        s = self.summary(puuid)
        skill = effective_skill(mmr["tier"], mmr["rr"], mmr["peak_tier"], mmr["peak_rr"], s["rating"])
        return {"mmr": mmr, "summary": s, "skill": skill}

    # ---- click-through detail --------------------------------------------
    def _game_line(self, u: dict, row: Optional[dict]) -> dict:
        won = None
        if row:
            won = "W" if row["won"] else ("D" if row["score_for"] == row["score_against"] else "L")
        return {"match_id": u.get("MatchID"), "started_ms": u.get("MatchStartTime") or (row or {}).get("started_ms"),
                "rr": int(u.get("RankedRatingEarned") or 0), "tier_after": int(u.get("TierAfterUpdate") or 0),
                "rr_after": int(u.get("RankedRatingAfterUpdate") or 0),
                "map": self.static.map_name(u.get("MapID") or (row or {}).get("map_url")),
                "agent": row["agent"] if row else "?", "result": won or "?",
                "score": f'{row["score_for"]}–{row["score_against"]}' if row else "?",
                "k": row["kills"] if row else None, "d": row["deaths"] if row else None,
                "a": row["assists"] if row else None, "acs": row["acs"] if row else None,
                "adr": row["adr"] if row else None}

    def detail(self, puuid: str, n: int = 5) -> dict:
        info = self.player_info(puuid)
        ups = self.updates(puuid, n)[:n]
        for u in ups:
            if u.get("MatchID"):
                self.ensure_match(u["MatchID"])
        rows = self.db.games_by_id(puuid, [u["MatchID"] for u in ups if u.get("MatchID")])
        games = [self._game_line(u, rows.get(u.get("MatchID"))) for u in ups]
        return {"puuid": puuid, "mmr": info["mmr"], "summary": info["summary"], "skill": info["skill"],
                "games": games, "net_rr": sum(g["rr"] for g in games),
                "rr_series": [g["rr"] for g in reversed(games)],
                "last_played_ms": info["summary"]["last_played_ms"]}

    # ---- profile ---------------------------------------------------------
    def profile(self, puuid: str, depth: int = 15, progress: Optional[Callable[[int, int], None]] = None) -> dict:
        mmr = self.mmr_info(puuid, force=True)
        key = f"upd{depth}:{puuid}"
        ups = self.db.cache_get(key, UPDATES_TTL)
        if ups is None:
            ups = self.source.updates(puuid, 0, depth) or []
            if ups:
                self.db.cache_put(key, ups)
        ids = [u["MatchID"] for u in ups if u.get("MatchID")]
        for i, mid in enumerate(ids):
            self.ensure_match(mid)
            if progress:
                progress(i + 1, len(ids))
        rows_by_id = self.db.games_by_id(puuid, ids)
        rows = [rows_by_id[m] for m in ids if m in rows_by_id]
        games = len(rows)
        wins = sum(1 for r in rows if r["won"])
        stats = {"games": games, "wins": wins, "wr": (wins / games) if games else None}
        if rows:
            stats.update({"kd": sum(r["kills"] for r in rows) / max(1, sum(r["deaths"] for r in rows)),
                          "acs": sum(r["acs"] for r in rows) / games, "adr": sum(r["adr"] for r in rows) / games,
                          "hs": sum(r["hs"] for r in rows) / games, "kast": sum(r["kast"] for r in rows) / games})
        agents: Dict[str, List[dict]] = {}
        for r in rows:
            agents.setdefault(r["agent"], []).append(r)
        agent_rows = []
        for name, rs in agents.items():
            scores = [self.game_score(r) for r in rs]
            raw = sum(scores) / len(scores)
            agent_rows.append({"agent": name, "games": len(rs), "wr": sum(1 for r in rs if r["won"]) / len(rs),
                               "acs": sum(r["acs"] for r in rs) / len(rs),
                               "rating": rating_model.clamp(rating_model.shrink(raw, len(rs)), rating_model.RATING_MIN,
                                                           rating_model.RATING_MAX)})
        agent_rows.sort(key=lambda a: (-a["games"], a["agent"]))
        weapons: Counter = Counter()
        for r in rows:
            weapons.update(r["weapons"])
        total = sum(weapons.values())
        top_weapons = [{"weapon": w, "kills": c, "share": c / total} for w, c in weapons.most_common(3)] if total else []
        lines = [self._game_line(u, rows_by_id.get(u.get("MatchID"))) for u in ups]
        return {"puuid": puuid, "mmr": mmr, "stats": stats, "agents": agent_rows, "weapons": top_weapons,
                "matches": lines, "depth": depth}
