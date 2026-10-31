"""Riot's remote game endpoints (pd = player data, glz = live game), using tokens from the local client."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional, Tuple

import requests

from .errors import RiotError
from .local import client_platform_header

log = logging.getLogger("valtracker")


class RiotRemote:
    def __init__(self, token_provider: Callable[[], Tuple[str, str, str]], client_version: str,
                 region: str, shard: str, http=None, sleep: Callable[[float], None] = time.sleep,
                 min_interval: float = 0.05):
        self.token_provider = token_provider
        self.version = client_version
        self.region, self.shard = region, shard
        self.http = http or requests
        self.sleep = sleep
        self.min_interval = min_interval
        self.access, self.ent, self.puuid = token_provider()
        self._lock = threading.Lock()
        self._last = 0.0
        self.pd = f"https://pd.{shard}.a.pvp.net"
        self.glz = f"https://glz-{region}-1.{shard}.a.pvp.net"

    # ---- plumbing --------------------------------------------------------
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access}", "X-Riot-Entitlements-JWT": self.ent,
                "X-Riot-ClientPlatform": client_platform_header(), "X-Riot-ClientVersion": self.version,
                "User-Agent": "ValConvex/1.0"}

    def refresh(self) -> None:
        self.access, self.ent, self.puuid = self.token_provider()

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()

    def request(self, method: str, url: str, body=None):
        """JSON of a 200 response; None for 404 (not in a game / no such match)."""
        refreshed = False
        last_status = 0
        for attempt in range(5):
            self._throttle()
            r = self.http.request(method, url, headers=self.headers(), json=body, timeout=12)
            last_status = r.status_code
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            expired = r.status_code in (401, 403) or (r.status_code == 400 and "BAD_CLAIMS" in (r.text or ""))
            if expired and not refreshed:
                refreshed = True
                self.refresh()
                continue
            if r.status_code == 429:
                try:
                    delay = float(r.headers.get("Retry-After", 0)) or 2.0 * (attempt + 1)
                except (TypeError, ValueError):
                    delay = 2.0 * (attempt + 1)
                log.info("rate limited, waiting %.1fs", delay)
                self.sleep(delay)
                continue
            if r.status_code >= 500:
                self.sleep(1.0)
                continue
            raise RiotError(r.status_code, url)
        raise RiotError(last_status, url, f"gave up after retries (last status {last_status})")

    def get(self, url: str):
        return self.request("GET", url)

    # ---- live game -------------------------------------------------------
    def coregame_id(self, puuid: str) -> Optional[str]:
        d = self.get(f"{self.glz}/core-game/v1/players/{puuid}")
        return d.get("MatchID") if d else None

    def coregame(self, match_id: str) -> Optional[dict]:
        return self.get(f"{self.glz}/core-game/v1/matches/{match_id}")

    def pregame_id(self, puuid: str) -> Optional[str]:
        d = self.get(f"{self.glz}/pregame/v1/players/{puuid}")
        return d.get("MatchID") if d else None

    def pregame(self, match_id: str) -> Optional[dict]:
        return self.get(f"{self.glz}/pregame/v1/matches/{match_id}")

    # ---- player data -----------------------------------------------------
    def mmr(self, puuid: str) -> Optional[dict]:
        return self.get(f"{self.pd}/mmr/v1/players/{puuid}")

    def updates(self, puuid: str, start: int = 0, end: int = 5, queue: str = "competitive") -> Optional[dict]:
        return self.get(f"{self.pd}/mmr/v1/players/{puuid}/competitiveupdates?startIndex={start}&endIndex={end}&queue={queue}")

    def history(self, puuid: str, start: int = 0, end: int = 10, queue: Optional[str] = None) -> Optional[dict]:
        q = f"&queue={queue}" if queue else ""
        return self.get(f"{self.pd}/match-history/v1/history/{puuid}?startIndex={start}&endIndex={end}{q}")

    def match_details(self, match_id: str) -> Optional[dict]:
        return self.get(f"{self.pd}/match-details/v1/matches/{match_id}")

    def names(self, puuids: List[str]) -> List[dict]:
        if not puuids:
            return []
        return self.request("PUT", f"{self.pd}/name-service/v2/players", list(puuids)) or []
