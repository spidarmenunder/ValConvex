"""Static game data (agents, maps, weapons, seasons) from the community valorant-api.com.

Downloaded once, cached on disk for a few days, and reused if the network is down.
Everything degrades gracefully: unknown ids just show up as generic names.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

log = logging.getLogger("valtracker")

API = "https://valorant-api.com/v1"
TTL_SECONDS = 3 * 24 * 3600

CATEGORY_LABELS = {
    "Rifle": "Rifles", "Sniper": "Snipers", "SMG": "SMGs", "Shotgun": "Shotguns",
    "Heavy": "Heavy", "Sidearm": "Sidearms", "Melee": "Melee",
}
# what match-details calls the slot -> what valorant-api calls it
ABILITY_SLOTS = {"Ability1": "Ability1", "Ability2": "Ability2", "GrenadeAbility": "Grenade",
                 "Grenade": "Grenade", "Ultimate": "Ultimate"}
CALLOUT_MAX_DIST = 0.10


def _iso_ms(text) -> Optional[int]:
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def map_transform(m: Optional[dict], x: float, y: float) -> Optional[Tuple[float, float]]:
    """Game coordinates -> normalised (0..1) minimap position. Note x and y swap."""
    if not m or m.get("x_mult") is None or m.get("y_mult") is None:
        return None
    nx = y * m["x_mult"] + (m.get("x_add") or 0.0)
    ny = x * m["y_mult"] + (m.get("y_add") or 0.0)
    return nx, ny


class StaticData:
    def __init__(self, agents=None, maps=None, weapons=None, seasons=None, cache_dir=None, http=None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.http = http or requests
        self._agents: Dict[str, dict] = {}
        self._maps: Dict[str, dict] = {}
        self._maps_by_url: Dict[str, dict] = {}
        self._weapons: Dict[str, Tuple[str, str]] = {}
        self._seasons: Dict[str, dict] = {}
        self.load_raw(agents or [], maps or [], weapons or [], seasons or [])

    # ---- loading ---------------------------------------------------------
    @classmethod
    def fetch(cls, cache_dir, http=None, force=False) -> "StaticData":
        sd = cls(cache_dir=cache_dir, http=http)
        sd.reload(force)
        return sd

    def reload(self, force: bool = False) -> None:
        """(Re)download or re-read the cached game data. Safe to call again if the first try was offline."""
        agents = self._endpoint("agents", "/agents", {"isPlayableCharacter": "true"}, force)
        maps = self._endpoint("maps", "/maps", None, force)
        weapons = self._endpoint("weapons", "/weapons", None, force)
        seasons = self._endpoint("seasons", "/seasons", None, force)
        self.load_raw(agents, maps, weapons, seasons)

    def _endpoint(self, name, path, params, force) -> list:
        file = (self.cache_dir / f"static_{name}.json") if self.cache_dir else None
        if file and file.exists() and not force and time.time() - file.stat().st_mtime < TTL_SECONDS:
            try:
                return json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        try:
            r = self.http.get(API + path, params=params, timeout=15)
            r.raise_for_status()
            data = r.json().get("data") or []
            if file:
                try:
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text(json.dumps(data), encoding="utf-8")
                except OSError:
                    pass
            return data
        except Exception as exc:   # offline, rate limited, ...
            log.warning("static data '%s' download failed: %s", name, exc)
            if file and file.exists():
                try:
                    return json.loads(file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            return []

    def load_raw(self, agents, maps, weapons, seasons) -> None:
        self._agents, self._maps, self._maps_by_url = {}, {}, {}
        self._agent_by_name = {}
        self._weapons, self._seasons = {}, {}
        for a in agents:
            uuid = str(a.get("uuid", "")).lower()
            if not uuid:
                continue
            abilities = {ab.get("slot"): ab.get("displayName") for ab in (a.get("abilities") or [])
                         if ab.get("slot") and ab.get("displayName")}
            self._agents[uuid] = {"name": a.get("displayName") or "Agent",
                                  "icon": a.get("displayIcon") or a.get("displayIconSmall") or "",
                                  "icon_small": a.get("displayIconSmall") or a.get("displayIcon") or "",
                                  "role": (a.get("role") or {}).get("displayName") or "",
                                  "abilities": abilities}
            self._agent_by_name[(a.get("displayName") or "").strip().lower()] = uuid
        for m in maps:
            uuid = str(m.get("uuid", "")).lower()
            if not uuid:
                continue
            info = {"uuid": uuid, "name": m.get("displayName") or "Map", "url": m.get("mapUrl") or "",
                    "icon": m.get("displayIcon") or m.get("listViewIcon") or "",
                    "x_mult": m.get("xMultiplier"), "y_mult": m.get("yMultiplier"),
                    "x_add": m.get("xScalarToAdd"), "y_add": m.get("yScalarToAdd"), "callouts": []}
            for c in (m.get("callouts") or []):
                loc = c.get("location") or {}
                if "x" in loc and "y" in loc:
                    pos = map_transform(info, float(loc["x"]), float(loc["y"]))
                    if pos:
                        info["callouts"].append({"name": c.get("regionName") or "", "super": c.get("superRegionName") or "",
                                                 "nx": pos[0], "ny": pos[1]})
            self._maps[uuid] = info
            if info["url"]:
                self._maps_by_url[info["url"].lower()] = info
        for w in weapons:
            uuid = str(w.get("uuid", "")).lower()
            cat = str(w.get("category") or "").split("::")[-1]
            self._weapons[uuid] = (w.get("displayName") or "Weapon", CATEGORY_LABELS.get(cat, "Other"))
        for s in seasons:
            uuid = str(s.get("uuid", "")).lower()
            if uuid:
                self._seasons[uuid] = {"name": s.get("displayName") or "", "start": _iso_ms(s.get("startTime")),
                                       "end": _iso_ms(s.get("endTime")),
                                       "parent": (str(s.get("parentUuid")).lower() if s.get("parentUuid") else None)}

    @property
    def has_maps(self) -> bool:
        return bool(self._maps)

    # ---- agents ----------------------------------------------------------
    def agent(self, uuid: Optional[str]) -> dict:
        return self._agents.get(str(uuid or "").lower()) or {"name": "Agent", "icon": "", "icon_small": "", "role": "", "abilities": {}}

    def agent_name(self, uuid) -> str:
        return self.agent(uuid)["name"]

    def agent_role(self, uuid) -> str:
        return self.agent(uuid)["role"]

    def agent_uuid_by_name(self, name) -> Optional[str]:
        """Agent id from its display name, for matches saved before ids were stored."""
        return self._agent_by_name.get(str(name or "").strip().lower())

    def agent_icon_path(self, uuid) -> Optional[Path]:
        """Cached icon file (no network). The image is verified once, not on every redraw."""
        if not self.cache_dir:
            return None
        aid = str(uuid or "").strip().lower()
        if not aid:
            return None
        p = self.cache_dir / f"agent_{aid}.png"
        if not p.exists():
            return None
        try:
            stamp = p.stat().st_mtime
        except OSError:
            return None
        ok = self.__dict__.setdefault("_icon_ok", {})
        if ok.get(str(p)) == stamp:
            return p
        try:
            from PIL import Image
            with Image.open(p) as im:
                im.verify()
            ok[str(p)] = stamp
            return p
        except Exception:
            try:
                p.unlink()
            except OSError:
                pass
            return None

    def rank_icon_path(self, tier: int, size: str = "small") -> Optional[Path]:
        """Cached current competitive rank icon for a numeric tier."""
        t = int(tier or 0)
        if t < 0 or t > 27 or not self.cache_dir:
            return None
        p = self.cache_dir / f"rank_{t}_{size}.png"
        return p if p.exists() else None

    def ensure_rank_icon(self, tier: int, size: str = "small") -> Optional[Path]:
        p = self.rank_icon_path(tier, size)
        if p:
            return p
        t = int(tier or 0)
        if t < 0 or t > 27 or not self.cache_dir:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Current competitive tier collection used by valorant-api.com.
        url = f"https://media.valorant-api.com/competitivetiers/564d8e28-c226-3180-6285-e48a390db8b1/{t}/{"largeicon" if size == "large" else "smallicon"}.png"
        try:
            r = self.http.get(url, timeout=15)
            r.raise_for_status()
            data = r.content
            if not data or len(data) < 64:
                return None
            from PIL import Image
            from io import BytesIO
            with Image.open(BytesIO(data)) as im:
                im.verify()
            p.write_bytes(data)
            return p
        except Exception:
            return None

    def ensure_agent_icon(self, uuid) -> Optional[Path]:
        """Return a real agent icon, downloading it if needed.

        Some versions of the static API omit displayIcon or return a URL that is
        temporarily unavailable. Fall back to the stable media endpoint and only
        cache a response that Pillow can actually decode as an image.
        """
        p = self.agent_icon_path(uuid)
        if p:
            return p
        aid = str(uuid or "").strip().lower()
        if not aid or not self.cache_dir:
            return None
        info = self.agent(aid)
        urls = [info.get("icon_small"), info.get("icon")]
        # Stable CDN fallback works even when the cached static JSON has no icon URL.
        urls.append(f"https://media.valorant-api.com/agents/{aid}/displayicon.png")
        seen = set()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"agent_{aid}.png"
        for url in urls:
            url = str(url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                r = self.http.get(url, timeout=15)
                r.raise_for_status()
                data = r.content
                if not data or len(data) < 64:
                    continue
                # Validate the bytes instead of trusting the extension/content-type.
                try:
                    from PIL import Image
                    from io import BytesIO
                    with Image.open(BytesIO(data)) as im:
                        im.verify()
                except Exception:
                    continue
                path.write_bytes(data)
                return path
            except Exception as exc:
                log.debug("agent icon source failed for %s (%s): %s", aid, url, exc)
        log.warning("agent icon download failed for %s", aid)
        return None

    def ability_name(self, agent_uuid, slot) -> str:
        abilities = self.agent(agent_uuid)["abilities"]
        return abilities.get(ABILITY_SLOTS.get(str(slot), str(slot))) or "Ability"

    # ---- maps ------------------------------------------------------------
    def map_by_url(self, url) -> Optional[dict]:
        return self._maps_by_url.get(str(url or "").lower())

    def map_by_uuid(self, uuid) -> Optional[dict]:
        return self._maps.get(str(uuid or "").lower())

    def map_name(self, url_or_uuid) -> str:
        m = self.map_by_url(url_or_uuid) or self.map_by_uuid(url_or_uuid)
        if m:
            return m["name"]
        tail = str(url_or_uuid or "").rstrip("/").split("/")[-1]
        return tail or "Unknown map"

    def callout_at(self, m: Optional[dict], nx: float, ny: float) -> Optional[str]:
        if not m:
            return None
        best, best_d = None, CALLOUT_MAX_DIST
        for c in m.get("callouts", []):
            d = ((c["nx"] - nx) ** 2 + (c["ny"] - ny) ** 2) ** 0.5
            if d < best_d:
                best, best_d = c, d
        if best is None:
            return None
        return f'{best["super"]} {best["name"]}'.strip() if best["super"] and best["super"] != best["name"] else best["name"]

    def map_image_path(self, m: Optional[dict]) -> Optional[Path]:
        if not m or not self.cache_dir:
            return None
        p = self.cache_dir / f"map_{m['uuid']}.png"
        return p if p.exists() else None

    def ensure_map_image(self, m: Optional[dict]) -> Optional[Path]:
        """Return the cached minimap file, downloading it the first time."""
        p = self.map_image_path(m)
        if p:
            return p
        if not m or not m.get("icon") or not self.cache_dir:
            return None
        try:
            r = self.http.get(m["icon"], timeout=20)
            r.raise_for_status()
            path = self.cache_dir / f"map_{m['uuid']}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return path
        except Exception as exc:
            log.warning("map image download failed: %s", exc)
            return None

    # ---- weapons ---------------------------------------------------------
    def damage_source(self, dtype, ditem, killer_agent_id=None) -> Tuple[str, str]:
        """(name, category) of what dealt the killing blow."""
        dtype = str(dtype or "")
        item = str(ditem or "").lower()
        if dtype == "Melee":
            return "Melee", "Melee"
        if dtype == "Ability":
            return self.ability_name(killer_agent_id, ditem), "Abilities"
        if dtype == "Bomb":
            return "Spike", "Other"
        w = self._weapons.get(item)
        if w:
            return w
        if dtype == "Weapon":
            return "Weapon", "Other"
        return (dtype or "Unknown"), "Other"

    # ---- seasons ---------------------------------------------------------
    def season_label(self, uuid) -> str:
        s = self._seasons.get(str(uuid or "").lower())
        if not s:
            return "Unknown act"
        act = s["name"].title()
        parent = self._seasons.get(s["parent"] or "")
        return f'{parent["name"].title()} · {act}' if parent else act

    def acts(self) -> List[str]:
        """Act season uuids, oldest first."""
        acts = [(u, s) for u, s in self._seasons.items() if s["parent"] and s["start"]]
        acts.sort(key=lambda x: x[1]["start"])
        return [u for u, _ in acts]

    def current_act(self, now_ms: Optional[int] = None) -> Optional[str]:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        current = None
        for u in self.acts():
            s = self._seasons[u]
            if s["start"] <= now and (s["end"] is None or now < s["end"]):
                return u
            if s["start"] <= now:
                current = u
        return current

    def acts_before(self, act_uuid: str, n: int = 3) -> List[str]:
        acts = self.acts()
        act_uuid = str(act_uuid or "").lower()
        if act_uuid not in acts:
            return []
        i = acts.index(act_uuid)
        return acts[max(0, i - n): i]
