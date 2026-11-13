from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def default_root() -> Path:
    env = os.environ.get("VALCONVEX_HOME")
    if env:
        return Path(env)
    docs = Path.home() / "Documents"
    return (docs if docs.exists() else Path.home()) / "ValConvex"


@dataclass
class Config:
    henrik_api_key: str = ""
    autosave: bool = True
    anonymize_exports: bool = False
    poll_seconds: float = 2.0
    idle_poll_seconds: float = 5.0
    region_override: str = ""          # e.g. "ap"; leave empty to auto-detect
    run_in_background: bool = True     # closing the window keeps ValConvex running in the system tray
    popup_on_match_end: bool = False   # bring the window forward when a match finishes
    show_live_names: bool = True       # names shown for players who are not in Streamer Mode
    hide_streamer_names_after_match: bool = False   # True = keep Streamer Mode players hidden in post-match too
    # Window/layout preferences are stored in config.json and applied without extra dependencies.
    layout_mode: str = "balanced"                   # compact, balanced, comfortable
    interface_scale: int = 100                       # 80..120 percent
    table_density: str = "compact"                  # compact, comfortable
    sidebar_mode: str = "auto"                      # auto, expanded, collapsed
    watchlist: list = field(default_factory=list)   # [{"puuid": "...", "name": "..."}]
    _root: str = ""                    # not saved

    # ---- paths -----------------------------------------------------------
    @property
    def root(self) -> Path:
        return Path(self._root) if self._root else default_root()

    @property
    def matches_dir(self) -> Path:
        # Keep exported match files in a clearly named, user-accessible folder.
        # This lives outside the one-file EXE so it remains visible and persistent.
        return self.root / "Match Results"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def db_path(self) -> Path:
        return self.root / "valconvex.db"

    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def log_path(self) -> Path:
        return self.root / "valconvex.log"

    def ensure_dirs(self) -> None:
        for p in (self.root, self.matches_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)
        for name in ("Ranked", "Scrims", "Unrated", "Unsorted"):
            (self.matches_dir / name).mkdir(parents=True, exist_ok=True)

    def match_folder(self, name: str) -> Path:
        clean = " ".join(str(name or "Unsorted").strip().split()) or "Unsorted"
        if clean in {".", ".."} or any(ch in clean for ch in '/\\:*?"<>|'):
            raise ValueError("Invalid match folder name.")
        path = self.matches_dir / clean
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- api key -----------------------------------------------------------
    @property
    def api_key(self) -> str:
        return os.environ.get("HENRIK_API_KEY") or self.henrik_api_key or ""

    # ---- persistence -------------------------------------------------------
    @classmethod
    def load(cls, root: str = "") -> "Config":
        cfg = cls(_root=root or "")
        try:
            data = json.loads(cfg.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        names = {f.name for f in fields(cls) if not f.name.startswith("_")}
        for k, v in data.items():
            if k in names:
                setattr(cfg, k, v)
        return cfg

    def save(self) -> None:
        self.ensure_dirs()
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.config_path)

    # ---- watchlist ---------------------------------------------------------
    def in_watchlist(self, puuid: str) -> bool:
        return any(w.get("puuid") == puuid for w in self.watchlist)

    def toggle_watchlist(self, puuid: str, name: str = "") -> bool:
        """Add/remove a player. Returns True if now on the list."""
        if self.in_watchlist(puuid):
            self.watchlist = [w for w in self.watchlist if w.get("puuid") != puuid]
            return False
        self.watchlist.append({"puuid": puuid, "name": name})
        return True
