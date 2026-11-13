"""Talks to the Riot Client running on THIS computer (https://127.0.0.1:<port>)."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

from ..util import decode_b64_json, get
from .errors import ClientNotRunning, RiotError

log = logging.getLogger("valtracker")

try:                                    # the local client uses a self-signed certificate
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:                       # pragma: no cover
    pass


@dataclass
class LockInfo:
    name: str
    pid: str
    port: int
    password: str
    protocol: str


def _localappdata() -> Optional[Path]:
    v = os.environ.get("LOCALAPPDATA")
    return Path(v) if v else None


def lockfile_path() -> Optional[Path]:
    env = os.environ.get("VALCONVEX_LOCKFILE")
    if env:
        return Path(env)
    base = _localappdata()
    return base / "Riot Games" / "Riot Client" / "Config" / "lockfile" if base else None


def parse_lockfile(text: str) -> LockInfo:
    head = text.strip().split(":", 3)                      # name : pid : port : <password>:<protocol>
    if len(head) != 4 or ":" not in head[3]:
        raise ClientNotRunning("The Riot lockfile is malformed.")
    password, protocol = head[3].rsplit(":", 1)             # protocol is last, so a ':' inside the password is safe
    try:
        port = int(head[2])
    except ValueError as exc:
        raise ClientNotRunning("The Riot lockfile is malformed.") from exc
    return LockInfo(head[0], head[1], port, password, protocol)


def read_lockfile(path: Optional[Path] = None) -> LockInfo:
    path = path or lockfile_path()
    if not path or not Path(path).exists():
        raise ClientNotRunning("Riot Client is not running (no lockfile found). Start VALORANT and try again.")
    try:
        return parse_lockfile(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClientNotRunning(f"Could not read the Riot lockfile: {exc}") from exc


def client_platform_header() -> str:
    payload = {"platformType": "PC", "platformOS": "Windows",
               "platformOSVersion": "10.0.19042.1.256.64bit", "platformChipset": "Unknown"}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _shooter_log() -> Optional[Path]:
    base = _localappdata()
    return base / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log" if base else None


def region_to_shard(region: str) -> str:
    return "na" if region in ("na", "latam", "br") else region


def read_log_region() -> Optional[Tuple[str, str]]:
    """(region, shard) from the game log, if the game has been started recently."""
    path = _shooter_log()
    if not path or not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = None
    for m in re.finditer(r"glz-([a-z]+)-1\.([a-z]+)\.a\.pvp\.net", text):
        pass
    return (m.group(1), m.group(2)) if m else None


def read_log_version() -> Optional[str]:
    path = _shooter_log()
    if not path or not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    hits = re.findall(r"CI server version: (\S+)", text)
    if not hits:
        return None
    parts = hits[-1].split("-")
    parts.insert(2, "shipping")
    return "-".join(parts)


def fetch_client_version(http=requests) -> Optional[str]:
    try:
        r = http.get("https://valorant-api.com/v1/version", timeout=10)
        r.raise_for_status()
        return get(r.json(), "data", "riotClientVersion")
    except Exception as exc:
        log.warning("client version lookup failed: %s", exc)
        return None


class LocalClient:
    def __init__(self, lock: LockInfo, http=requests):
        self.lock = lock
        self.http = http
        self.base = f"{lock.protocol}://127.0.0.1:{lock.port}"

    def get(self, path: str, timeout: float = 5):
        try:
            r = self.http.get(self.base + path, auth=("riot", self.lock.password), verify=False, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            raise ClientNotRunning("Could not reach the Riot Client. Is VALORANT running?") from exc
        if r.status_code != 200:
            raise RiotError(r.status_code, path)
        return r.json()

    def entitlements(self) -> Tuple[str, str, str]:
        """(access token, entitlements token, my puuid)"""
        d = self.get("/entitlements/v1/token")
        if not d.get("accessToken") or not d.get("token") or not d.get("subject"):
            raise RiotError(200, "/entitlements/v1/token", "Riot Client did not return tokens yet (still logging in?)")
        return d["accessToken"], d["token"], d["subject"]

    def region_shard(self) -> Optional[Tuple[str, str]]:
        try:
            sessions = self.get("/product-session/v1/external-sessions")
        except (RiotError, ClientNotRunning):
            sessions = {}
        for s in (sessions or {}).values():
            if s.get("productId") == "valorant":
                for arg in get(s, "launchConfiguration", "arguments", default=[]):
                    if str(arg).startswith("-ares-deployment="):
                        region = str(arg).split("=", 1)[1]
                        return region, region_to_shard(region)
        return read_log_region()

    def presences(self) -> Dict[str, dict]:
        """puuid -> {'party_id': ..., 'name': ..., 'tag': ...} for everyone the chat service knows about."""
        try:
            d = self.get("/chat/v4/presences")
        except (RiotError, ClientNotRunning):
            return {}
        out = {}
        for p in d.get("presences", []):
            priv = decode_b64_json(p.get("private"))
            party = priv.get("partyId") or get(priv, "matchPresenceData", "partyId")
            if p.get("puuid"):
                out[p["puuid"]] = {"party_id": party, "name": p.get("game_name") or "", "tag": p.get("game_tag") or ""}
        return out

    def self_name(self) -> Tuple[str, str]:
        d = self.get("/chat/v1/session")
        return d.get("game_name") or "", d.get("game_tag") or ""
