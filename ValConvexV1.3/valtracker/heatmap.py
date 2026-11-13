"""Heatmap data: turn a MatchData dict into plottable records and filter them.

A record is one marker:
  kill  ('k'): where the killer stood        death ('d'): where the player died
  plant ('p'): where the spike went down
Fields: id, t, r (round), who (puuid of the player it belongs to), team ('me'|'en'), other (the
other puuid), s (side of `who`: 'A' attack, 'D' defense or None), x/y and ox/oy (0..1 minimap),
w (weapon or ability), cat, c/oc (callouts), rt (round time ms).
The same filter logic is mirrored in web/viewer.html (tests keep the two in step).
"""
from __future__ import annotations

from typing import Dict, List, Optional

PALETTE = ["#4cc9f0", "#f72585", "#b5e48c", "#ffb703", "#9d4edd", "#ef476f", "#06d6a0", "#ff9f1c", "#4895ef", "#e5989b"]


def player_colors(match: dict) -> Dict[str, str]:
    """A stable colour per player (ordered by id, so it does not change with perspective)."""
    ids = sorted(p["puuid"] for p in match["players"])
    return {u: PALETTE[i % len(PALETTE)] for i, u in enumerate(ids)}


def short_name(p: Optional[dict]) -> str:
    """Name without the tag (or the agent for hidden players) - used for labels."""
    if not p:
        return "?"
    return p["name"] if p.get("name") else (p.get("agent") or "?")


CATEGORY_ORDER = ["Rifles", "SMGs", "Shotguns", "Heavy", "Snipers", "Sidearms", "Abilities", "Melee", "Other"]


def _side(team: Optional[str], attacker: Optional[str]) -> Optional[str]:
    if not team or not attacker:
        return None
    return "A" if team == attacker else "D"


def player_name(p: Optional[dict]) -> str:
    """Riot ID, or the agent name for players whose name is hidden."""
    if not p:
        return "Unknown"
    if p.get("name"):
        return f'{p["name"]}#{p["tag"]}' if p.get("tag") else p["name"]
    return f'{p.get("agent") or "Player"} (hidden)'


def player_map(match: dict) -> Dict[str, dict]:
    return {p["puuid"]: p for p in match["players"]}


def build_records(match: dict) -> List[dict]:
    team = {p["puuid"]: p["team"] for p in match["players"]}
    attacker = {r["n"]: r.get("attacker") for r in match["rounds"]}
    recs: List[dict] = []
    for i, e in enumerate(match["events"]):
        rn = e["r"]
        kt, vt = team.get(e["killer"], "en"), team.get(e["victim"], "en")
        if e.get("kx") is not None and e.get("ky") is not None:
            recs.append({"id": f"k{i}", "t": "k", "r": rn, "who": e["killer"], "team": kt, "other": e["victim"],
                         "s": _side(kt, attacker.get(rn)), "x": e["kx"], "y": e["ky"], "ox": e.get("vx"),
                         "oy": e.get("vy"), "w": e["w"], "cat": e["cat"], "c": e.get("kc"), "oc": e.get("vc"),
                         "rt": e["rt"]})
        if e.get("vx") is not None and e.get("vy") is not None:
            recs.append({"id": f"d{i}", "t": "d", "r": rn, "who": e["victim"], "team": vt, "other": e["killer"],
                         "s": _side(vt, attacker.get(rn)), "x": e["vx"], "y": e["vy"], "ox": e.get("kx"),
                         "oy": e.get("ky"), "w": e["w"], "cat": e["cat"], "c": e.get("vc"), "oc": e.get("kc"),
                         "rt": e["rt"]})
    for r in match["rounds"]:
        pl = r.get("plant")
        if pl and pl.get("nx") is not None:
            site = pl.get("site") or ""
            recs.append({"id": f'p{r["n"]}', "t": "p", "r": r["n"], "who": pl["by"], "team": team.get(pl["by"], "en"),
                         "other": None, "s": "A", "x": pl["nx"], "y": pl["ny"], "ox": None, "oy": None,
                         "w": "", "cat": "", "c": f"{site} Site" if site else "Site", "oc": None, "rt": pl["rt"],
                         "site": site})
    return recs


def is_visible(r: dict, who: str = "all", type: str = "both", side: str = "all", rnd: int = 0,
               cat: str = "all", wpn: str = "any", plants: bool = True) -> bool:
    if who == "me":
        if r["team"] != "me":
            return False
    elif who == "en":
        if r["team"] != "en":
            return False
    elif who != "all" and r["who"] != who:
        return False
    if side != "all" and r["s"] != side:
        return False
    if rnd and r["r"] != rnd:
        return False
    if r["t"] == "p":
        return bool(plants)
    if type != "both" and r["t"] != type:
        return False
    if cat != "all" and r["cat"] != cat:
        return False
    if wpn != "any" and r["w"] != wpn:
        return False
    return True


def filter_records(records: List[dict], **flt) -> List[dict]:
    return [r for r in records if is_visible(r, **flt)]


def counts(records: List[dict]) -> Dict[str, int]:
    return {"kills": sum(1 for r in records if r["t"] == "k"),
            "deaths": sum(1 for r in records if r["t"] == "d"),
            "plants": sum(1 for r in records if r["t"] == "p")}


def categories_present(records: List[dict]) -> List[str]:
    have = {r["cat"] for r in records if r["t"] != "p" and r["cat"]}
    return [c for c in CATEGORY_ORDER if c in have] + sorted(have - set(CATEGORY_ORDER))


def weapons_in(records: List[dict], cat: str = "all") -> List[str]:
    return sorted({r["w"] for r in records if r["t"] != "p" and r["w"] and (cat == "all" or r["cat"] == cat)})


def describe(rec: dict, match: dict) -> Dict[str, object]:
    """Text for the detail card when a marker is clicked."""
    pm = player_map(match)
    side = {"A": "Attack", "D": "Defense"}.get(rec["s"] or "", "Side unknown")
    head = f'Round {rec["r"]} · {side}'
    rt = rec.get("rt") or 0
    time_txt = f"{rt // 60000}:{(rt // 1000) % 60:02d}"
    if rec["t"] == "p":
        return {"kind": "Spike plant", "head": head, "rows": [
            ("Site", rec["c"] or "Site"), ("Planted by", f'{player_name(pm.get(rec["who"]))} · {pm.get(rec["who"], {}).get("agent", "")}'),
            ("Round time", time_txt)]}
    is_kill = rec["t"] == "k"
    who, other = pm.get(rec["who"]), pm.get(rec["other"])
    ability = rec["cat"] == "Abilities"
    return {"kind": "Kill" if is_kill else "Death", "head": head, "rows": [
        ("Player", f'{player_name(who)} · {(who or {}).get("agent", "")}'),
        ("Killed" if is_kill else "Killed by", f'{player_name(other)} · {(other or {}).get("agent", "")}'),
        ("Ability" if ability else "Weapon", rec["w"] or "Unknown"),
        ("Killer at · victim at" if is_kill else "Died at · killer at",
         f'{rec["c"] or "?"} · {rec["oc"] or "?"}'),
        ("Round time", time_txt)]}
