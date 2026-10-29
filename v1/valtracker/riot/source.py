"""One interface the services use, with two implementations: the real Riot client and a demo."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import requests

from ..util import get
from .errors import ClientNotRunning, RiotError
from .local import LocalClient, fetch_client_version, read_lockfile, read_log_version
from .remote import RiotRemote

log = logging.getLogger("valtracker")


class RiotSource:
    """Live data from the Riot Client on this PC.

    state()  -> ('menu'|'pregame'|'ingame', match_id or None)
    lobby()  -> {'phase','match_id','map_url','queue','players':[{puuid,team,agent_id,incognito,tier,level}]}
    """

    def __init__(self, local: LocalClient, remote: RiotRemote):
        self.local, self.remote = local, remote
        self._name_cache: Dict[str, Tuple[str, str]] = {}

    @property
    def puuid(self) -> str:
        return self.remote.puuid

    @classmethod
    def connect(cls, region_override: str = "", http=requests) -> "RiotSource":
        lock = read_lockfile()
        local = LocalClient(lock)
        _, _, puuid = local.entitlements()          # raises RiotError if the client is still logging in
        if region_override:
            region, shard = region_override, ("na" if region_override in ("na", "latam", "br") else region_override)
        else:
            found = local.region_shard()
            if not found:
                raise RiotError(0, "", "Could not work out your VALORANT region. Set region_override in config.json (e.g. \"ap\").")
            region, shard = found
        version = fetch_client_version(http) or read_log_version()
        if not version:
            raise RiotError(0, "", "Could not determine the VALORANT client version (offline?).")
        remote = RiotRemote(local.entitlements, version, region, shard)
        log.info("connected: region=%s shard=%s version=%s", region, shard, version)
        return cls(local, remote)

    # ---- state -----------------------------------------------------------
    def state(self) -> Tuple[str, Optional[str]]:
        mid = self.remote.coregame_id(self.puuid)
        if mid:
            return "ingame", mid
        mid = self.remote.pregame_id(self.puuid)
        if mid:
            return "pregame", mid
        return "menu", None

    def lobby(self, phase: str, match_id: str) -> Optional[dict]:
        def ident(p):
            pi = p.get("PlayerIdentity") or {}
            return {"puuid": p.get("Subject") or pi.get("Subject"), "agent_id": str(p.get("CharacterID") or "").lower(),
                    "incognito": bool(pi.get("Incognito")), "level": pi.get("AccountLevel"),
                    "tier": int(p.get("CompetitiveTier") or 0)}
        if phase == "pregame":
            d = self.remote.pregame(match_id)
            if not d:
                return None
            ally = get(d, "AllyTeam", "Players", default=[])
            players = [dict(ident(p), team="ally") for p in ally]
            return {"phase": phase, "match_id": match_id, "map_url": d.get("MapID") or "",
                    "queue": (d.get("QueueID") or "").lower(), "players": players,
                    "enemy_size": d.get("EnemyTeamSize")}
        d = self.remote.coregame(match_id)
        if not d:
            return None
        raw = d.get("Players") or []
        my_team = next((p.get("TeamID") for p in raw if p.get("Subject") == self.puuid), None)
        players = [dict(ident(p), team="ally" if p.get("TeamID") == my_team else "enemy") for p in raw]
        return {"phase": phase, "match_id": match_id, "map_url": d.get("MapID") or "",
                "queue": (get(d, "MatchmakingData", "QueueID", default="") or "").lower(), "players": players}

    # ---- lookups ---------------------------------------------------------
    def names(self, puuids: List[str]) -> Dict[str, Tuple[str, str]]:
        """Resolve PUUIDs to current Riot IDs and cache successful results.

        The name-service endpoint is the local VALORANT client-backed source for
        the current GameName + TagLine. PUUID remains the internal key; Riot ID is
        what the UI should display.
        """
        wanted = [str(u) for u in puuids if u]
        missing = [u for u in wanted if u not in self._name_cache]
        if missing:
            try:
                for row in self.remote.names(missing):
                    subject = row.get("Subject")
                    game = row.get("GameName") or ""
                    tag = row.get("TagLine") or ""
                    if subject and game:
                        self._name_cache[subject] = (game, tag)
            except Exception as exc:
                log.warning("Riot ID lookup failed: %s", exc)
        return {u: self._name_cache[u] for u in wanted if u in self._name_cache}

    def parties(self) -> Dict[str, str]:
        return {k: v["party_id"] for k, v in self.local.presences().items() if v.get("party_id")}

    def my_name(self) -> Tuple[str, str]:
        try:
            return self.local.self_name()
        except Exception:
            return "", ""

    def mmr(self, puuid: str) -> Optional[dict]:
        return self.remote.mmr(puuid)

    def updates(self, puuid: str, start: int = 0, end: int = 5) -> List[dict]:
        d = self.remote.updates(puuid, start, end)
        return (d or {}).get("Matches") or []

    def history(self, puuid: str, start: int = 0, end: int = 10, queue: Optional[str] = None) -> List[dict]:
        d = self.remote.history(puuid, start, end, queue)
        return (d or {}).get("History") or []

    def match_details(self, match_id: str) -> Optional[dict]:
        return self.remote.match_details(match_id)
