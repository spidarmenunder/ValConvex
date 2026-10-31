"""HenrikDev API (unofficial, free key) - used only to turn a Riot ID (name#tag) into a puuid."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import requests

from .errors import HenrikError

BASE = "https://api.henrikdev.xyz"


class HenrikClient:
    def __init__(self, api_key: str, http=None):
        self.key = (api_key or "").strip()
        self.http = http or requests

    def account(self, name: str, tag: str) -> dict:
        """{'puuid','region','name','tag','level'} or raises HenrikError with a readable message."""
        if not self.key:
            raise HenrikError("No HenrikDev API key set. Run:  python run.py --set-key YOUR_KEY")
        name, tag = name.strip(), tag.strip().lstrip("#")
        if not name or not tag:
            raise HenrikError("Enter a Riot ID like  name#tag")
        problem: Optional[str] = None
        for path in (f"/valorant/v2/account/{quote(name, safe='')}/{quote(tag, safe='')}",
                     f"/valorant/v1/account/{quote(name, safe='')}/{quote(tag, safe='')}"):
            try:
                r = self.http.get(BASE + path, headers={"Authorization": self.key, "Accept": "application/json"},
                                  timeout=12)
            except requests.exceptions.RequestException as exc:
                raise HenrikError(f"Could not reach HenrikDev: {exc.__class__.__name__}") from exc
            if r.status_code == 200:
                data = (r.json() or {}).get("data") or {}
                if data.get("puuid"):
                    return {"puuid": data["puuid"], "region": data.get("region") or "",
                            "name": data.get("name") or name, "tag": data.get("tag") or tag,
                            "level": data.get("account_level")}
                problem = "HenrikDev answered without a player id."
                continue
            if r.status_code in (401, 403):
                raise HenrikError("HenrikDev rejected the API key. Check it, or generate a new one.")
            if r.status_code == 429:
                raise HenrikError("HenrikDev rate limit reached. Wait a minute and try again.")
            if r.status_code == 404:
                problem = f"No player found for {name}#{tag}."
                continue
            problem = f"HenrikDev returned HTTP {r.status_code}."
        raise HenrikError(problem or "Lookup failed.")
