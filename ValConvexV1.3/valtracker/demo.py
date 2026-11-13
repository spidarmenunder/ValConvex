"""Demo data: a fake VALORANT world in the same JSON shape Riot uses.

Used by `python run.py --demo` (try every screen without the game running) and by the tests,
which push it through the exact same parsing code the real match data goes through.
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Dict, List, Optional

from .static import StaticData

MAP_URL = "/Game/Maps/Demo/Demo"
MAP_UUID = "demo-map"
X_MULT, Y_MULT, X_ADD, Y_ADD = 0.0001, -0.0001, 0.5, 0.5

# zone rectangles as fractions of the minimap: (x1, y1, x2, y2), matching the design mockups
ZONES = {
    "A Site": (195, 30, 275, 105), "B Site": (25, 30, 105, 95), "Mid": (125, 90, 175, 200),
    "A Main": (210, 120, 250, 210), "B Main": (35, 110, 70, 210), "Attack lane": (40, 208, 270, 226),
    "Defense": (125, 20, 175, 60),
}
ZONE_WEIGHTS = [("A Site", .2), ("B Site", .18), ("Mid", .26), ("A Main", .1), ("B Main", .1),
                ("Attack lane", .1), ("Defense", .06)]
ADJ = {"A Site": ["A Main", "Mid", "Defense"], "B Site": ["B Main", "Mid", "Defense"],
       "Mid": ["Defense", "Attack lane", "A Site", "B Site"], "A Main": ["Attack lane", "A Site"],
       "B Main": ["Attack lane", "B Site"], "Attack lane": ["A Main", "B Main", "Mid"],
       "Defense": ["A Site", "B Site", "Mid"]}

AGENT_DEFS = {
    "phoenix": ("Phoenix", "Duelist", {"Ability1": "Blaze", "Grenade": "Curveball", "Ability2": "Hot Hands", "Ultimate": "Run it Back"}, ["Ability1", "Ability2"]),
    "raze": ("Raze", "Duelist", {"Ability1": "Boom Bot", "Grenade": "Blast Pack", "Ability2": "Paint Shells", "Ultimate": "Showstopper"}, ["Ability1", "GrenadeAbility"]),
    "jett": ("Jett", "Duelist", {"Ability1": "Updraft", "Grenade": "Cloudburst", "Ability2": "Tailwind", "Ultimate": "Blade Storm"}, ["Ultimate"]),
    "reyna": ("Reyna", "Duelist", {"Ability1": "Devour", "Grenade": "Leer", "Ability2": "Dismiss", "Ultimate": "Empress"}, []),
    "sova": ("Sova", "Initiator", {"Ability1": "Owl Drone", "Grenade": "Shock Bolt", "Ability2": "Recon Bolt", "Ultimate": "Hunter's Fury"}, ["GrenadeAbility"]),
    "sage": ("Sage", "Sentinel", {"Ability1": "Slow Orb", "Grenade": "Barrier Orb", "Ability2": "Healing Orb", "Ultimate": "Resurrection"}, []),
    "killjoy": ("Killjoy", "Sentinel", {"Ability1": "Turret", "Grenade": "Alarmbot", "Ability2": "Nanoswarm", "Ultimate": "Lockdown"}, ["Ability1"]),
    "brimstone": ("Brimstone", "Controller", {"Ability1": "Stim Beacon", "Grenade": "Incendiary", "Ability2": "Sky Smoke", "Ultimate": "Orbital Strike"}, ["GrenadeAbility"]),
    "omen": ("Omen", "Controller", {"Ability1": "Shrouded Step", "Grenade": "Paranoia", "Ability2": "Dark Cover", "Ultimate": "From the Shadows"}, []),
}
WEAPON_DEFS = {"vandal": ("Vandal", "Rifle"), "phantom": ("Phantom", "Rifle"), "operator": ("Operator", "Sniper"),
               "sheriff": ("Sheriff", "Sidearm"), "ghost": ("Ghost", "Sidearm"), "melee": ("Melee", "Melee")}

# name, agent, team, party, tier, rr
ROSTER = [
    ("You#PH", "phoenix", "Blue", "party-b", 19, 33), ("echo#1212", "raze", "Blue", None, 18, 25),
    ("sable#310", "sage", "Blue", "party-b", 18, 60), ("rook#0099", "brimstone", "Blue", None, 17, 8),
    ("lumen#5", "sova", "Blue", None, 16, 70),
    ("Vex#0042", "jett", "Red", "party-a", 22, 41), ("nightowl#PH1", "sova", "Red", "party-a", 20, 78),
    ("kurumi#777", "omen", "Red", "party-c", 18, 12), ("zeroPing#1", "reyna", "Red", "party-c", 16, 55),
    ("mango#8821", "killjoy", "Red", None, 15, 90),
]
WIN_ROUNDS = {1, 2, 3, 5, 6, 8, 9, 10, 12, 14, 16, 19, 20}      # 13 rounds won by Blue (22-round game)


def puuid_of(name: str) -> str:
    return "puuid-" + name.split("#")[0].lower()


def demo_roster() -> List[dict]:
    out = []
    for name, agent, team, party, tier, rr in ROSTER:
        n, tag = name.split("#")
        out.append({"puuid": puuid_of(name), "name": n, "tag": tag, "agent": agent, "team": team,
                    "party": party, "tier": tier, "rr": rr})
    return out


def _game_from_norm(nx: float, ny: float):
    """Normalised minimap position -> the game coordinates Riot would report."""
    return (ny - Y_ADD) / Y_MULT, (nx - X_ADD) / X_MULT


def demo_static(now_ms: Optional[int] = None, cache_dir=None) -> StaticData:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    day = 86400 * 1000
    iso = lambda ms: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))
    agents = [{"uuid": f"demo-agent-{k}", "displayName": v[0], "role": {"displayName": v[1]},
               "abilities": [{"slot": s, "displayName": n} for s, n in v[2].items()]} for k, v in AGENT_DEFS.items()]
    callouts = []
    for zone, (x1, y1, x2, y2) in ZONES.items():          # a grid of callout points inside each zone
        cols, rows = max(1, -(-(x2 - x1) // 40)), max(1, -(-(y2 - y1) // 40))
        for i in range(int(cols)):
            for j in range(int(rows)):
                nx = (x1 + (i + .5) * (x2 - x1) / cols) / 300
                ny = (y1 + (j + .5) * (y2 - y1) / rows) / 300
                gx, gy = _game_from_norm(nx, ny)
                callouts.append({"regionName": zone, "superRegionName": zone, "location": {"x": gx, "y": gy}})
    maps = [{"uuid": MAP_UUID, "displayName": "Demo Map", "mapUrl": MAP_URL, "displayIcon": "",
             "xMultiplier": X_MULT, "yMultiplier": Y_MULT, "xScalarToAdd": X_ADD, "yScalarToAdd": Y_ADD,
             "callouts": callouts}]
    weapons = [{"uuid": f"demo-weapon-{k}", "displayName": v[0], "category": f"EEquippableCategory::{v[1]}"}
               for k, v in WEAPON_DEFS.items()]
    seasons = [{"uuid": "demo-ep1", "displayName": "EPISODE 1", "startTime": iso(now - 400 * day),
                "endTime": iso(now + 200 * day), "parentUuid": None}]
    for i in range(5):                       # 4 finished acts + the current one
        start = now - (240 - i * 60 + 30) * day if i < 4 else now - 30 * day
        seasons.append({"uuid": f"demo-act{i + 1}", "displayName": f"ACT {i + 1}", "startTime": iso(start),
                        "endTime": iso(start + 60 * day), "parentUuid": "demo-ep1"})
    return StaticData(agents, maps, weapons, seasons, cache_dir=cache_dir)


def write_demo_agent_icons(cache_dir) -> None:
    """Little colored discs with the agent's initial, so icons can be seen in demo mode (needs Pillow)."""
    from PIL import Image, ImageDraw
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    for i, (key, (name, *_rest)) in enumerate(AGENT_DEFS.items()):
        path = cache / f"agent_demo-agent-{key}.png"
        if path.exists():
            continue
        img = Image.new("RGBA", (44, 44), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        hue = [(76, 201, 240), (247, 37, 133), (181, 228, 140), (255, 183, 3), (157, 78, 221),
               (239, 71, 111), (6, 214, 160), (255, 159, 28), (72, 149, 239)][i % 9]
        d.ellipse([2, 2, 41, 41], fill=hue + (255,), outline=(20, 22, 26, 255), width=2)
        d.text((17, 14), name[0], fill=(20, 22, 26, 255))
        img.save(path)


def write_demo_minimap(path) -> Path:
    """A schematic minimap PNG for the demo map (needs Pillow)."""
    from PIL import Image, ImageDraw
    size = 1024
    img = Image.new("RGB", (size, size), (30, 34, 40))
    d = ImageDraw.Draw(img)
    for zone, (x1, y1, x2, y2) in ZONES.items():
        r = [x1 / 300 * size, y1 / 300 * size, x2 / 300 * size, y2 / 300 * size]
        d.rounded_rectangle(r, radius=18, fill=(52, 58, 68), outline=(90, 98, 110), width=3)
        d.text(((r[0] + r[2]) / 2 - 8 * len(zone) / 2, (r[1] + r[3]) / 2), zone, fill=(150, 158, 170))
    r = [95 / 300 * size, 228 / 300 * size, 205 / 300 * size, 280 / 300 * size]
    d.rounded_rectangle(r, radius=18, fill=(52, 58, 68), outline=(90, 98, 110), width=3)
    d.text((r[0] + 30, (r[1] + r[3]) / 2), "Attack spawn", fill=(150, 158, 170))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


# --------------------------------------------------------------------------- raw match generator
def _pick_zone(rng: random.Random) -> str:
    x, s = rng.random(), 0.0
    for name, w in ZONE_WEIGHTS:
        s += w
        if x < s:
            return name
    return "Mid"


def _point(rng: random.Random, zone: str):
    x1, y1, x2, y2 = ZONES[zone]
    return (x1 + rng.random() * (x2 - x1)) / 300, (y1 + rng.random() * (y2 - y1)) / 300


def make_raw_match(seed: int, match_id: str, started_ms: int, roster: Optional[List[dict]] = None,
                   win_rounds=WIN_ROUNDS, n_rounds: int = 22, my_team: str = "Blue",
                   queue: str = "competitive", season_id: str = "demo-act5") -> dict:
    """A raw match-details document shaped like Riot's, with kills, damage, plants and positions."""
    rng = random.Random(seed)
    roster = roster or demo_roster()
    teams = {"Blue": [p for p in roster if p["team"] == "Blue"], "Red": [p for p in roster if p["team"] == "Red"]}
    by_id = {p["puuid"]: p for p in roster}
    stats = {p["puuid"]: {"kills": 0, "deaths": 0, "assists": 0, "damage": 0.0, "score": 0.0} for p in roster}
    round_results = []
    t_ms = 0

    for r in range(1, n_rounds + 1):
        blue_wins = r in win_rounds
        winners = teams["Blue"] if blue_wins else teams["Red"]
        losers = teams["Red"] if blue_wins else teams["Blue"]
        x = rng.random()
        wl = 0 if x < .15 else 1 if x < .45 else 2 if x < .75 else 3 if x < .95 else 4
        seq = ["L"] * 5 + ["W"] * wl
        rng.shuffle(seq)
        if seq[-1] != "L":
            li = len(seq) - 1 - seq[::-1].index("L")
            seq[li], seq[-1] = seq[-1], seq[li]
        alive_w, alive_l = list(winners), list(losers)
        times = sorted(rng.randint(12000, 95000) for _ in seq)
        per_player = {p["puuid"]: {"kills": [], "damage": {}, "score": 0.0} for p in roster}
        for tok, rt in zip(seq, times):
            v_team, k_team = (alive_l, alive_w) if tok == "L" else (alive_w, alive_l)
            victim = v_team.pop(rng.randrange(len(v_team)))
            killer = rng.choice(k_team)
            vz = _pick_zone(rng)
            kz = vz if rng.random() < .4 else rng.choice(ADJ[vz])
            vx, vy = _point(rng, vz)
            kx, ky = _point(rng, kz)
            vgx, vgy = _game_from_norm(vx, vy)
            kgx, kgy = _game_from_norm(kx, ky)
            agent = killer["agent"]
            x1, x2, x3 = rng.random(), rng.random(), rng.random()
            abil = AGENT_DEFS[agent][3]
            if x1 < .09 and abil:
                fd = {"damageType": "Ability", "damageItem": rng.choice(abil), "isSecondaryFireMode": False}
            elif x3 < .012:
                fd = {"damageType": "Melee", "damageItem": "demo-weapon-melee", "isSecondaryFireMode": False}
            else:
                name = "vandal" if x2 < .58 else "phantom" if x2 < .82 else "sheriff" if x2 < .89 else "operator" if x2 < .95 else "ghost"
                fd = {"damageType": "Weapon", "damageItem": f"demo-weapon-{name}", "isSecondaryFireMode": False}
            mates = [p["puuid"] for p in (winners if killer in winners else losers) if p is not killer]
            assist = [rng.choice(mates)] if rng.random() < .3 and mates else []
            heads = 1 if rng.random() < .27 else 0
            per_player[killer["puuid"]]["kills"].append({
                "gameTime": t_ms + rt, "roundTime": rt, "killer": killer["puuid"], "victim": victim["puuid"],
                "victimLocation": {"x": vgx, "y": vgy}, "assistants": assist,
                "playerLocations": [{"subject": killer["puuid"], "viewRadians": 1.0, "location": {"x": kgx, "y": kgy}},
                                    {"subject": victim["puuid"], "viewRadians": 2.0, "location": {"x": vgx, "y": vgy}}],
                "finishingDamage": fd})
            dmg = per_player[killer["puuid"]]["damage"].setdefault(victim["puuid"], {"receiver": victim["puuid"], "damage": 0, "legshots": 0, "bodyshots": 0, "headshots": 0})
            dmg["damage"] += 150
            dmg["headshots"] += heads
            dmg["bodyshots"] += 1 - heads if rng.random() < .8 else 2
            stats[killer["puuid"]]["kills"] += 1
            stats[killer["puuid"]]["damage"] += 150
            stats[victim["puuid"]]["deaths"] += 1
            for a in assist:
                stats[a]["assists"] += 1
            per_player[killer["puuid"]]["score"] += 150
        for p in roster:                                         # some non-lethal damage too
            if rng.random() < .55:
                enemy = rng.choice(teams["Red" if p["team"] == "Blue" else "Blue"])["puuid"]
                extra = rng.randint(20, 90)
                d = per_player[p["puuid"]]["damage"].setdefault(enemy, {"receiver": enemy, "damage": 0, "legshots": 0, "bodyshots": 0, "headshots": 0})
                d["damage"] += extra
                d["bodyshots"] += 1
                stats[p["puuid"]]["damage"] += extra
                per_player[p["puuid"]]["score"] += extra * .4

        attackers = "Blue" if r <= 12 else "Red"
        rr = {"roundNum": r - 1, "roundResult": "Elimination", "winningTeam": "Blue" if blue_wins else "Red",
              "bombPlanter": None, "playerStats": []}
        if rng.random() < .62:
            planter = rng.choice(teams[attackers])
            site = "A" if rng.random() < .5 else "B"
            zone = "A Site" if site == "A" else "B Site"
            px, py = _point(rng, zone)
            pgx, pgy = _game_from_norm(px, py)
            rr.update({"bombPlanter": planter["puuid"], "plantSite": site, "plantRoundTime": rng.randint(35000, 85000),
                       "plantLocation": {"x": pgx, "y": pgy}})
        for p in roster:
            pp = per_player[p["puuid"]]
            stats[p["puuid"]]["score"] += pp["score"]
            rr["playerStats"].append({"subject": p["puuid"], "kills": pp["kills"], "damage": list(pp["damage"].values()),
                                      "score": pp["score"]})
        round_results.append(rr)
        t_ms += 100000

    blue_won = sum(1 for r in round_results if r["winningTeam"] == "Blue")
    players = []
    for p in roster:
        s = stats[p["puuid"]]
        players.append({"subject": p["puuid"], "gameName": p["name"], "tagLine": p["tag"], "teamId": p["team"],
                        "partyId": p["party"] or f"solo-{p['puuid']}", "characterId": f"demo-agent-{p['agent']}",
                        "competitiveTier": p["tier"], "accountLevel": 100 + len(p["name"]),
                        "stats": {"score": int(s["score"]), "roundsPlayed": n_rounds, "kills": s["kills"],
                                  "deaths": s["deaths"], "assists": s["assists"]},
                        "roundDamage": []})
    return {
        "matchInfo": {"matchId": match_id, "mapId": MAP_URL, "gameStartMillis": started_ms,
                      "gameLengthMillis": n_rounds * 100000, "queueID": queue, "isCompleted": True,
                      "isRanked": queue == "competitive", "seasonId": season_id},
        "players": players,
        "teams": [{"teamId": "Blue", "won": blue_won > n_rounds - blue_won, "roundsPlayed": n_rounds, "roundsWon": blue_won},
                  {"teamId": "Red", "won": blue_won < n_rounds - blue_won, "roundsPlayed": n_rounds, "roundsWon": n_rounds - blue_won}],
        "roundResults": round_results,
    }
