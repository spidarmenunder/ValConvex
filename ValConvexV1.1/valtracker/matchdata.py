"""Turn Riot's raw match-details JSON into (1) rows for the stats cache and
(2) a portable MatchData dict used by the leaderboard, heatmap and exports (.vtrack).

MatchData schema (version 1) - all positions are normalised 0..1 minimap coordinates:
  match_id, started_ms, duration_ms, queue, is_ranked, source, map{uuid,name,url,x_mult,...},
  me, result{won,draw,score[me,en]}, mvp{match,team}, pre_win_prob,
  players[{puuid,name,tag,team('me'|'en'),party,party_size,agent,role,tier,acs,adr,hs,kast,
           kills,deaths,assists,rounds,rating}],
  rounds[{n,winner,attacker,plant{site,nx,ny,rt,by}|None}],
  events[{r,rt,killer,victim,assists[],kx,ky,vx,vy,w,cat,kc,vc}]
"""
from __future__ import annotations

import time
import copy
from collections import Counter
from typing import Dict, List, Optional

from . import __version__
from .model import rating as rating_model
from .ranks import bracket
from .static import StaticData, map_transform
from .util import clamp, now_ms

SCHEMA = "vtrack"
VERSION = 1


def _xy(d) -> Optional[tuple]:
    if not isinstance(d, dict):
        return None
    if "location" in d and isinstance(d["location"], dict):
        d = d["location"]
    try:
        return float(d["x"]), float(d["y"])
    except (KeyError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- sides
def infer_attackers(planter_teams: List[Optional[str]], team_ids: List[str]) -> List[Optional[str]]:
    """Which team attacked in each round. The spike planter is always an attacker; halves are
    12 rounds; overtime alternates every round. Rounds that cannot be worked out stay None."""
    n = len(planter_teams)
    out: List[Optional[str]] = [None] * n

    def other(t):
        rest = [x for x in team_ids if x != t]
        return rest[0] if rest else None

    for lo, hi in ((0, 12), (12, 24)):
        votes = Counter(t for t in planter_teams[lo:hi] if t)
        if votes:
            att = votes.most_common(1)[0][0]
            for i in range(lo, min(hi, n)):
                out[i] = att
    if n > 0 and out[0] and n > 12 and not out[12]:
        for i in range(12, min(24, n)):
            out[i] = other(out[0])
    if n > 12 and out[12] and not out[0]:
        for i in range(0, 12):
            out[i] = other(out[12])
    for i in range(24, n):
        out[i] = planter_teams[i]
    for i in range(25, n):                       # overtime: alternate
        if out[i] is None and out[i - 1] is not None:
            out[i] = other(out[i - 1])
    for i in range(n - 2, 23, -1):
        if out[i] is None and out[i + 1] is not None:
            out[i] = other(out[i + 1])
    return out


# --------------------------------------------------------------------------- KAST
def compute_kast(round_events: List[List[dict]], team_of: Dict[str, str], puuids: List[str]) -> Dict[str, float]:
    """KAST: share of rounds where the player got a Kill, Assist, Survived or was Traded (within 5 s)."""
    n_rounds = len(round_events)
    hits = {p: 0 for p in puuids}
    for evs in round_events:
        killed = {e["victim"] for e in evs}
        for p in puuids:
            ok = False
            for e in evs:
                if e["killer"] == p or p in (e.get("assistants") or []):
                    ok = True
                    break
            if not ok and p not in killed:
                ok = True                                   # survived
            if not ok:
                for e in evs:                               # traded?
                    if e["victim"] == p:
                        for e2 in evs:
                            if (e2["victim"] == e["killer"] and team_of.get(e2["killer"]) == team_of.get(p)
                                    and 0 <= e2["rt"] - e["rt"] <= 5000):
                                ok = True
                                break
                    if ok:
                        break
            if ok:
                hits[p] += 1
    return {p: (hits[p] / n_rounds if n_rounds else 0.0) for p in puuids}


# --------------------------------------------------------------------------- parsing
def parse_match(raw: dict, static: StaticData) -> dict:
    mi = raw.get("matchInfo") or {}
    players_raw = [p for p in (raw.get("players") or []) if not p.get("isObserver")]
    if not players_raw:
        raise ValueError("match has no players")
    map_url = mi.get("mapId") or ""
    map_info = static.map_by_url(map_url)
    rounds_raw = sorted(raw.get("roundResults") or [], key=lambda r: r.get("roundNum", 0))
    n_rounds = len(rounds_raw)

    team_of = {p["subject"]: p.get("teamId", "") for p in players_raw}
    agent_of = {p["subject"]: str(p.get("characterId", "")).lower() for p in players_raw}
    team_ids = sorted({t for t in team_of.values() if t})

    dmg_total: Dict[str, float] = {p: 0.0 for p in team_of}
    head: Dict[str, int] = {p: 0 for p in team_of}
    hits: Dict[str, int] = {p: 0 for p in team_of}
    score_sum: Dict[str, float] = {p: 0.0 for p in team_of}
    events: List[dict] = []
    round_events: List[List[dict]] = []
    rounds: List[dict] = []
    planter_teams: List[Optional[str]] = []

    for idx, rr in enumerate(rounds_raw):
        rn = idx + 1
        evs: List[dict] = []
        for ps in rr.get("playerStats") or []:
            subj = ps.get("subject")
            if subj in score_sum:
                score_sum[subj] += float(ps.get("score") or 0)
            for d in ps.get("damage") or []:
                if subj in dmg_total:
                    dmg_total[subj] += float(d.get("damage") or 0)
                    h, b, l = (int(d.get("headshots") or 0), int(d.get("bodyshots") or 0), int(d.get("legshots") or 0))
                    head[subj] += h
                    hits[subj] += h + b + l
            for k in ps.get("kills") or []:
                killer, victim = k.get("killer"), k.get("victim")
                if not killer or not victim:
                    continue
                fd = k.get("finishingDamage") or {}
                weapon, cat = static.damage_source(fd.get("damageType"), fd.get("damageItem"), agent_of.get(killer))
                kloc = None
                for pl in k.get("playerLocations") or []:
                    if pl.get("subject") == killer:
                        kloc = _xy(pl)
                        break
                evs.append({"r": rn, "rt": int(k.get("roundTime") or 0), "killer": killer, "victim": victim,
                            "assistants": [a.get("assistant") if isinstance(a, dict) else a
                                           for a in (k.get("assistants") or [])],
                            "kloc": kloc, "vloc": _xy(k.get("victimLocation")),
                            "weapon": weapon, "cat": cat})
        evs.sort(key=lambda e: e["rt"])
        for e in evs:
            e["assistants"] = [a for a in e["assistants"] if a]
        events.extend(evs)
        round_events.append(evs)

        planter = rr.get("bombPlanter")
        plant = None
        ploc = _xy(rr.get("plantLocation"))
        if planter and ploc:
            plant = {"site": rr.get("plantSite") or "", "loc": ploc,
                     "rt": int(rr.get("plantRoundTime") or 0), "by": planter}
        planter_teams.append(team_of.get(planter) if planter else None)
        rounds.append({"n": rn, "winner": rr.get("winningTeam"), "plant": plant,
                       "result": rr.get("roundResult") or ""})

    attackers = infer_attackers(planter_teams, team_ids)
    for r, a in zip(rounds, attackers):
        r["attacker"] = a

    kast = compute_kast(round_events, team_of, list(team_of))

    weapons: Dict[str, Dict[str, int]] = {p: {} for p in team_of}
    for e in events:
        w = weapons.setdefault(e["killer"], {})
        w[e["weapon"]] = w.get(e["weapon"], 0) + 1

    teams: Dict[str, dict] = {}
    for t in raw.get("teams") or []:
        teams[t.get("teamId")] = {"won": bool(t.get("won")), "rounds_won": int(t.get("roundsWon") or 0)}
    for t in team_ids:
        teams.setdefault(t, {"won": False, "rounds_won": sum(1 for r in rounds if r["winner"] == t)})

    players = []
    for p in players_raw:
        s = p.get("stats") or {}
        subj = p["subject"]
        rounds_played = int(s.get("roundsPlayed") or n_rounds or 0)
        score = float(s["score"]) if s.get("score") is not None else score_sum[subj]
        dmg = dmg_total[subj]
        if dmg == 0 and p.get("roundDamage"):
            dmg = float(sum(float(x.get("damage") or 0) for x in p["roundDamage"]))
        players.append({
            "puuid": subj, "name": p.get("gameName") or "", "tag": p.get("tagLine") or "",
            "team_id": p.get("teamId", ""), "party_id": p.get("partyId") or None,
            "agent_id": agent_of[subj], "agent": static.agent_name(agent_of[subj]),
            "role": static.agent_role(agent_of[subj]),
            "tier": int(p.get("competitiveTier") or 0), "level": p.get("accountLevel"),
            "rounds": rounds_played, "kills": int(s.get("kills") or 0), "deaths": int(s.get("deaths") or 0),
            "assists": int(s.get("assists") or 0),
            "acs": score / rounds_played if rounds_played else 0.0,
            "adr": dmg / rounds_played if rounds_played else 0.0,
            "hs": (head[subj] / hits[subj]) if hits[subj] else 0.0,
            "kast": kast.get(subj, 0.0), "weapons": weapons.get(subj, {}),
        })

    parsed = {
        "match_id": mi.get("matchId") or "", "started_ms": int(mi.get("gameStartMillis") or 0),
        "duration_ms": int(mi.get("gameLengthMillis") or 0), "queue": (mi.get("queueID") or "").lower(),
        "is_ranked": bool(mi.get("isRanked")), "completed": bool(mi.get("isCompleted", True)),
        "map_url": map_url, "map": map_info, "team_ids": team_ids, "teams": teams,
        "players": players, "rounds": rounds, "events": events,
    }
    _normalise(parsed, static)
    return parsed


def _normalise(parsed: dict, static: StaticData) -> None:
    """Add normalised 0..1 minimap positions (nx/ny) to events and plants."""
    m = parsed["map"]
    pts = []
    for e in parsed["events"]:
        pts += [p for p in (e["kloc"], e["vloc"]) if p]
    for r in parsed["rounds"]:
        if r["plant"]:
            pts.append(r["plant"]["loc"])
    use_map = bool(m and m.get("x_mult") is not None)
    if not use_map and pts:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

        def tf(x, y):
            nx = 0.05 + 0.9 * ((y - y0) / (y1 - y0) if y1 > y0 else 0.5)
            ny = 0.95 - 0.9 * ((x - x0) / (x1 - x0) if x1 > x0 else 0.5)
            return nx, ny
    else:
        def tf(x, y):
            return map_transform(m, x, y)

    def norm(loc):
        if not loc or (not use_map and not pts):
            return None, None
        v = tf(loc[0], loc[1])
        return (round(v[0], 4), round(v[1], 4)) if v else (None, None)

    for e in parsed["events"]:
        e["knx"], e["kny"] = norm(e["kloc"])
        e["vnx"], e["vny"] = norm(e["vloc"])
        e["kc"] = static.callout_at(m, e["knx"], e["kny"]) if e["knx"] is not None else None
        e["vc"] = static.callout_at(m, e["vnx"], e["vny"]) if e["vnx"] is not None else None
    for r in parsed["rounds"]:
        p = r["plant"]
        if p:
            p["nx"], p["ny"] = norm(p["loc"])


# --------------------------------------------------------------------------- outputs
def digest_rows(parsed: dict) -> List[dict]:
    """One row per player for the stats cache."""
    rows = []
    for p in parsed["players"]:
        t = parsed["teams"].get(p["team_id"], {})
        opp = [v for k, v in parsed["teams"].items() if k != p["team_id"]]
        rows.append({
            "match_id": parsed["match_id"], "puuid": p["puuid"], "started_ms": parsed["started_ms"],
            "queue": parsed["queue"], "map_url": parsed["map_url"], "agent_id": p["agent_id"],
            "agent": p["agent"], "team": p["team_id"], "won": 1 if t.get("won") else 0,
            "rounds": p["rounds"], "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
            "acs": p["acs"], "adr": p["adr"], "hs": p["hs"], "kast": p["kast"], "tier": p["tier"],
            "score_for": t.get("rounds_won", 0), "score_against": opp[0]["rounds_won"] if opp else 0,
            "weapons": p["weapons"],
        })
    return rows


def observe_baselines(parsed: dict, baselines, db) -> None:
    """Teach the baselines from every player in this match (once per match)."""
    if db is None or not parsed["match_id"] or db.baseline_done(parsed["match_id"]):
        return
    if parsed["queue"] not in ("competitive", "unrated", "swiftplay", "spikerush"):
        return
    for p in parsed["players"]:
        baselines.observe(p["agent"], bracket(p["tier"]), p["acs"], p["adr"])
    db.mark_baseline_done(parsed["match_id"])


def _party_letters(players: List[dict]) -> Dict[str, Optional[str]]:
    counts = Counter(p["party_id"] for p in players if p["party_id"])
    letters, out = {}, {}
    for p in players:
        pid = p["party_id"]
        if pid and counts[pid] >= 2:
            if pid not in letters:
                letters[pid] = chr(ord("A") + len(letters)) if len(letters) < 26 else "?"
            out[p["puuid"]] = letters[pid]
        else:
            out[p["puuid"]] = None
    return out


def build_match_data(parsed: dict, me: Optional[str] = None, baselines=None,
                     pre_win_prob: Optional[float] = None, source: str = "live") -> dict:
    team_ids = parsed["team_ids"]
    my_team = None
    for p in parsed["players"]:
        if me and p["puuid"] == me:
            my_team = p["team_id"]
    if my_team is None:
        my_team = team_ids[0] if team_ids else ""
    en_team = next((t for t in team_ids if t != my_team), "")

    def label(team_id):
        return "me" if team_id == my_team else "en"

    ordered = sorted(parsed["players"], key=lambda p: (label(p["team_id"]) != "me", -p["acs"]))
    letters = _party_letters(ordered)
    sizes = Counter(p["party_id"] for p in ordered if p["party_id"])

    players = []
    for p in ordered:
        rating = None
        if baselines is not None and p["acs"] > 0:
            base = baselines.get(p["agent"], p["role"], bracket(p["tier"]))
            rating = round(clamp(rating_model.game_score(p["acs"], p["adr"], base), 0.5, 1.9), 2)
        players.append({
            "puuid": p["puuid"], "name": p["name"], "tag": p["tag"], "team": label(p["team_id"]),
            "party": letters[p["puuid"]], "party_size": sizes[p["party_id"]] if letters[p["puuid"]] else 1,
            "agent": p["agent"], "agent_id": p["agent_id"], "role": p["role"], "tier": p["tier"], "level": p["level"],
            "acs": round(p["acs"], 1), "adr": round(p["adr"], 1), "hs": round(p["hs"], 3),
            "kast": round(p["kast"], 3), "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
            "rounds": p["rounds"], "rating": rating,
        })

    t_me = parsed["teams"].get(my_team, {})
    t_en = parsed["teams"].get(en_team, {})
    won_me = bool(t_me.get("won"))
    won_en = bool(t_en.get("won"))
    winners = [p for p in players if p["team"] == ("me" if won_me else "en")] if (won_me or won_en) else []
    losers = [p for p in players if p["team"] == ("en" if won_me else "me")] if (won_me or won_en) else []
    mvp = {"match": max(winners, key=lambda p: p["acs"])["puuid"] if winners else None,
           "team": max(losers, key=lambda p: p["acs"])["puuid"] if losers else None}

    rounds = []
    for r in parsed["rounds"]:
        plant = r["plant"]
        rounds.append({
            "n": r["n"], "winner": label(r["winner"]) if r["winner"] else None,
            "attacker": label(r["attacker"]) if r["attacker"] else None,
            "plant": ({"site": plant["site"], "nx": plant.get("nx"), "ny": plant.get("ny"),
                       "rt": plant["rt"], "by": plant["by"]} if plant and plant.get("nx") is not None else None),
        })

    events = [{"r": e["r"], "rt": e["rt"], "killer": e["killer"], "victim": e["victim"],
               "assists": e["assistants"], "kx": e["knx"], "ky": e["kny"], "vx": e["vnx"], "vy": e["vny"],
               "w": e["weapon"], "cat": e["cat"], "kc": e["kc"], "vc": e["vc"]} for e in parsed["events"]]

    m = parsed["map"] or {}
    return {
        "schema": SCHEMA, "version": VERSION, "app": f"ValConvex {__version__}",
        "match_id": parsed["match_id"], "started_ms": parsed["started_ms"], "duration_ms": parsed["duration_ms"],
        "queue": parsed["queue"], "is_ranked": parsed["is_ranked"], "source": source,
        "map": {"uuid": m.get("uuid", ""), "name": m.get("name") or "Unknown map", "url": parsed["map_url"],
                "x_mult": m.get("x_mult"), "y_mult": m.get("y_mult"), "x_add": m.get("x_add"), "y_add": m.get("y_add")},
        "me": me if any(p["puuid"] == me for p in players) else None,
        "result": {"won": won_me, "draw": not won_me and not won_en,
                   "score": [t_me.get("rounds_won", 0), t_en.get("rounds_won", 0)]},
        "mvp": mvp, "pre_win_prob": pre_win_prob, "players": players, "rounds": rounds, "events": events,
        "generated_ms": now_ms(),
    }


def process_raw_match(raw: dict, static: StaticData, me: Optional[str], baselines=None, db=None,
                      pre_win_prob: Optional[float] = None, source: str = "live") -> dict:
    """raw match-details -> MatchData, also filling the stats cache and baselines."""
    t0 = time.time()
    parsed = parse_match(raw, static)
    if db is not None:
        db.save_match_rows(parsed["match_id"], digest_rows(parsed))
        observe_baselines(parsed, baselines, db)
    data = build_match_data(parsed, me=me, baselines=baselines, pre_win_prob=pre_win_prob, source=source)
    data["process_seconds"] = round(time.time() - t0, 2)
    return data


def apply_lobby_meta(raw: dict, meta: Dict[str, dict], respect_incognito: bool = True) -> dict:
    """Fill names and party ids that Riot's match-details left blank, using what the live lobby showed.
    With respect_incognito=True, players flagged as Streamer Mode are never filled in."""
    for p in raw.get("players") or []:
        m = meta.get(p.get("subject"))
        if not m:
            continue
        if not p.get("gameName") and m.get("name") and not (respect_incognito and m.get("incognito")):
            p["gameName"], p["tagLine"] = m["name"], m.get("tag") or ""
        if not p.get("partyId") and m.get("party_id"):
            p["partyId"] = m["party_id"]
    return raw


def reperspective(match: dict, new_me: Optional[str]) -> dict:
    """The same match seen from another player's side: their team becomes 'your team'."""
    m = copy.deepcopy(match)
    pl = next((p for p in m["players"] if p["puuid"] == new_me), None)
    if pl is None:
        return m
    m["me"] = new_me
    if pl["team"] == "me":
        return m
    for p in m["players"]:
        p["team"] = "me" if p["team"] == "en" else "en"
    m["players"].sort(key=lambda p: (p["team"] != "me", -p["acs"]))
    flip = {"me": "en", "en": "me", None: None}
    for r in m["rounds"]:
        r["winner"], r["attacker"] = flip[r["winner"]], flip[r["attacker"]]
    res = m["result"]
    res["score"] = list(reversed(res["score"]))
    if not res["draw"]:
        res["won"] = not res["won"]
    m["pre_win_prob"] = None
    return m
