"""SQLite cache: processed matches, per-player match rows, baselines, sightings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches(
  match_id TEXT PRIMARY KEY, started_ms INTEGER, map_name TEXT, queue TEXT,
  result TEXT, score TEXT, imported INTEGER DEFAULT 0, saved_path TEXT, title TEXT, folder TEXT DEFAULT 'Unsorted', json TEXT);
CREATE TABLE IF NOT EXISTS player_match(
  match_id TEXT, puuid TEXT, started_ms INTEGER, queue TEXT, map_url TEXT,
  agent_id TEXT, agent TEXT, team TEXT, won INTEGER, rounds INTEGER,
  kills INTEGER, deaths INTEGER, assists INTEGER, acs REAL, adr REAL, hs REAL, kast REAL,
  tier INTEGER, score_for INTEGER, score_against INTEGER, weapons TEXT,
  PRIMARY KEY(match_id, puuid));
CREATE TABLE IF NOT EXISTS match_seen(match_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS seen(
  puuid TEXT, match_id TEXT, started_ms INTEGER, name TEXT, PRIMARY KEY(puuid, match_id));
CREATE TABLE IF NOT EXISTS baseline_obs(
  agent TEXT, bracket TEXT, n INTEGER, sum_acs REAL, sum_adr REAL, PRIMARY KEY(agent, bracket));
CREATE TABLE IF NOT EXISTS baseline_done(match_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS json_cache(key TEXT PRIMARY KEY, fetched_ms INTEGER, json TEXT);
CREATE TABLE IF NOT EXISTS lobby_meta(
  match_id TEXT, puuid TEXT, name TEXT, tag TEXT, party_id TEXT, incognito INTEGER,
  PRIMARY KEY(match_id, puuid));
CREATE INDEX IF NOT EXISTS idx_pm_puuid ON player_match(puuid, started_ms DESC);
"""

ROW_FIELDS = ["match_id", "puuid", "started_ms", "queue", "map_url", "agent_id", "agent", "team", "won",
              "rounds", "kills", "deaths", "assists", "acs", "adr", "hs", "kast", "tier",
              "score_for", "score_against", "weapons"]


class Database:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            for column, definition in (("title", "TEXT"), ("folder", "TEXT DEFAULT 'Unsorted'")):
                try:
                    self.conn.execute(f"ALTER TABLE matches ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError:
                    pass
            self.conn.execute("UPDATE matches SET folder=COALESCE(NULLIF(folder, ''), 'Unsorted')")
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def _run(self, sql: str, args: Iterable = (), commit: bool = False):
        with self.lock:
            cur = self.conn.execute(sql, tuple(args))
            rows = cur.fetchall()
            if commit:
                self.conn.commit()
            return rows

    # ---- per-player match rows ------------------------------------------
    def has_match_rows(self, match_id: str) -> bool:
        return bool(self._run("SELECT 1 FROM match_seen WHERE match_id=?", (match_id,)))

    def save_match_rows(self, match_id: str, rows: List[dict]) -> None:
        with self.lock:
            for r in rows:
                vals = [json.dumps(r[f]) if f == "weapons" and not isinstance(r[f], str) else r[f]
                        for f in ROW_FIELDS]
                self.conn.execute(
                    f"INSERT OR REPLACE INTO player_match({','.join(ROW_FIELDS)}) "
                    f"VALUES({','.join('?' * len(ROW_FIELDS))})", vals)
            self.conn.execute("INSERT OR REPLACE INTO match_seen(match_id) VALUES(?)", (match_id,))
            self.conn.commit()

    def _row(self, r) -> dict:
        d = dict(r)
        try:
            d["weapons"] = json.loads(d.get("weapons") or "{}")
        except ValueError:
            d["weapons"] = {}
        return d

    def recent_games(self, puuid: str, limit: int = 5, queue: Optional[str] = "competitive") -> List[dict]:
        if queue:
            rows = self._run("SELECT * FROM player_match WHERE puuid=? AND queue=? "
                             "ORDER BY started_ms DESC LIMIT ?", (puuid, queue, limit))
        else:
            rows = self._run("SELECT * FROM player_match WHERE puuid=? ORDER BY started_ms DESC LIMIT ?",
                             (puuid, limit))
        return [self._row(r) for r in rows]

    def games_by_id(self, puuid: str, match_ids: List[str]) -> Dict[str, dict]:
        if not match_ids:
            return {}
        q = ",".join("?" * len(match_ids))
        rows = self._run(f"SELECT * FROM player_match WHERE puuid=? AND match_id IN ({q})",
                         [puuid] + list(match_ids))
        return {r["match_id"]: self._row(r) for r in rows}

    # ---- processed matches (heatmap / leaderboard bundles) -------------------
    def save_match(self, match: dict, saved_path: Optional[str] = None, imported: bool = False) -> None:
        score = match.get("result", {}).get("score") or []
        existing = self.get_match_title(match["match_id"])
        existing_folder = self.get_match_folder(match["match_id"])
        self._run(
            "INSERT OR REPLACE INTO matches(match_id, started_ms, map_name, queue, result, score, imported, saved_path, title, folder, json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (match["match_id"], match.get("started_ms"), match.get("map", {}).get("name"), match.get("queue"),
             "Victory" if match.get("result", {}).get("won") else "Defeat",
             "-".join(str(s) for s in score), 1 if imported else 0, saved_path, existing,
             match.get("folder") or existing_folder or "Unsorted", json.dumps(match, separators=(",", ":"))), commit=True)
        players = match.get("players") or []
        if players:
            self.save_lobby_meta(match["match_id"], [{"puuid": p.get("puuid"), "name": p.get("name") or "", "tag": p.get("tag") or "", "party_id": None, "incognito": False} for p in players if p.get("puuid")])
            self.record_seen(match["match_id"], int(match.get("started_ms") or 0), [(p.get("puuid"), (p.get("name") or "")) for p in players if p.get("puuid")])

    def get_match(self, match_id: str) -> Optional[dict]:
        rows = self._run("SELECT json FROM matches WHERE match_id=?", (match_id,))
        return json.loads(rows[0]["json"]) if rows else None

    def list_matches(self, limit: int = 200, folder: Optional[str] = None) -> List[dict]:
        if folder and folder != "All":
            rows = self._run("SELECT match_id, started_ms, map_name, queue, result, score, imported, saved_path, title, folder "
                             "FROM matches WHERE COALESCE(folder, 'Unsorted')=? ORDER BY started_ms DESC LIMIT ?", (folder, limit))
        else:
            rows = self._run("SELECT match_id, started_ms, map_name, queue, result, score, imported, saved_path, title, folder "
                             "FROM matches ORDER BY started_ms DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def set_saved_path(self, match_id: str, path: str) -> None:
        self._run("UPDATE matches SET saved_path=? WHERE match_id=?", (path, match_id), commit=True)

    def set_match_folder(self, match_id: str, folder: str, path: Optional[str] = None) -> None:
        if path is None:
            self._run("UPDATE matches SET folder=? WHERE match_id=?", (folder, match_id), commit=True)
        else:
            self._run("UPDATE matches SET folder=?, saved_path=? WHERE match_id=?", (folder, path, match_id), commit=True)

    def list_folders(self) -> List[str]:
        rows = self._run("SELECT DISTINCT COALESCE(NULLIF(folder, ''), 'Unsorted') AS folder FROM matches ORDER BY folder COLLATE NOCASE")
        return [r["folder"] for r in rows]

    def find_player(self, query: str) -> Optional[dict]:
        q = (query or "").strip().lower()
        if not q:
            return None
        rows = self._run("SELECT puuid, name, tag FROM lobby_meta WHERE lower(name || CASE WHEN tag != '' THEN '#' || tag ELSE '' END)=? ORDER BY match_id DESC LIMIT 1", (q,))
        if rows:
            r = rows[0]
            return {"puuid": r["puuid"], "name": r["name"], "tag": r["tag"]}
        rows = self._run("SELECT puuid, name, '' AS tag FROM lobby_meta WHERE lower(name)=? ORDER BY match_id DESC LIMIT 1", (q.split("#",1)[0],))
        if rows:
            r = rows[0]
            return {"puuid": r["puuid"], "name": r["name"], "tag": r["tag"]}
        rows = self._run("SELECT puuid, name, '' AS tag FROM seen WHERE lower(name)=? ORDER BY started_ms DESC LIMIT 1", (q.split("#",1)[0],))
        if rows:
            r = rows[0]
            return {"puuid": r["puuid"], "name": r["name"], "tag": r["tag"]}
        return None

    def get_match_title(self, match_id: str) -> Optional[str]:
        rows = self._run("SELECT title FROM matches WHERE match_id=?", (match_id,))
        return rows[0]["title"] if rows and rows[0]["title"] else None

    def rename_match(self, match_id: str, title: str) -> None:
        self._run("UPDATE matches SET title=? WHERE match_id=?", (title.strip() or None, match_id), commit=True)

    def get_match_folder(self, match_id: str) -> Optional[str]:
        rows = self._run("SELECT folder FROM matches WHERE match_id=?", (match_id,))
        return rows[0]["folder"] if rows and rows[0]["folder"] else None

    def delete_match(self, match_id: str) -> None:
        with self.lock:
            for table in ("matches", "player_match", "match_seen", "lobby_meta", "seen", "baseline_done"):
                self.conn.execute(f"DELETE FROM {table} WHERE match_id=?", (match_id,))
            self.conn.commit()

    # ---- baselines -------------------------------------------------------
    def baseline_obs(self, agent: str, bracket: str) -> Tuple[int, float, float]:
        rows = self._run("SELECT n, sum_acs, sum_adr FROM baseline_obs WHERE agent=? AND bracket=?",
                         (agent, bracket))
        return (rows[0]["n"], rows[0]["sum_acs"], rows[0]["sum_adr"]) if rows else (0, 0.0, 0.0)

    def add_baseline_obs(self, agent: str, bracket: str, acs: float, adr: float) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO baseline_obs(agent, bracket, n, sum_acs, sum_adr) VALUES(?,?,1,?,?) "
                "ON CONFLICT(agent, bracket) DO UPDATE SET n=n+1, sum_acs=sum_acs+excluded.sum_acs, "
                "sum_adr=sum_adr+excluded.sum_adr", (agent, bracket, acs, adr))
            self.conn.commit()

    def baseline_done(self, match_id: str) -> bool:
        return bool(self._run("SELECT 1 FROM baseline_done WHERE match_id=?", (match_id,)))

    def mark_baseline_done(self, match_id: str) -> None:
        self._run("INSERT OR IGNORE INTO baseline_done(match_id) VALUES(?)", (match_id,), commit=True)

    # ---- sightings (repeat-player flag) ------------------------------------
    def record_seen(self, match_id: str, started_ms: int, players: List[Tuple[str, str]]) -> None:
        with self.lock:
            for puuid, name in players:
                self.conn.execute("INSERT OR IGNORE INTO seen(puuid, match_id, started_ms, name) VALUES(?,?,?,?)",
                                  (puuid, match_id, started_ms, name))
            self.conn.commit()

    def repeat_count(self, puuid: str, exclude_match_id: str = "", last_n: int = 10) -> int:
        """In how many of my last N recorded matches (not counting the current one) was this player present?"""
        rows = self._run("SELECT match_id FROM seen WHERE match_id<>? GROUP BY match_id "
                         "ORDER BY MAX(started_ms) DESC LIMIT ?", (exclude_match_id, last_n))
        ids = [r["match_id"] for r in rows]
        if not ids:
            return 0
        q = ",".join("?" * len(ids))
        got = self._run(f"SELECT COUNT(DISTINCT match_id) AS c FROM seen WHERE puuid=? AND match_id IN ({q})",
                        [puuid] + ids)
        return int(got[0]["c"])

    # ---- what the live lobby told us (names, parties, streamer mode) -------------
    def save_lobby_meta(self, match_id: str, rows: List[dict]) -> None:
        with self.lock:
            for r in rows:
                self.conn.execute(
                    "INSERT OR REPLACE INTO lobby_meta(match_id, puuid, name, tag, party_id, incognito) VALUES(?,?,?,?,?,?)",
                    (match_id, r["puuid"], r.get("name") or "", r.get("tag") or "", r.get("party_id"),
                     1 if r.get("incognito") else 0))
            self.conn.commit()

    def get_lobby_meta(self, match_id: str) -> Dict[str, dict]:
        rows = self._run("SELECT puuid, name, tag, party_id, incognito FROM lobby_meta WHERE match_id=?", (match_id,))
        return {r["puuid"]: {"name": r["name"], "tag": r["tag"], "party_id": r["party_id"],
                             "incognito": bool(r["incognito"])} for r in rows}

    # ---- generic JSON cache ----------------------------------------------
    def cache_get(self, key: str, max_age_s: float) -> Optional[object]:
        rows = self._run("SELECT fetched_ms, json FROM json_cache WHERE key=?", (key,))
        if not rows:
            return None
        if time.time() * 1000 - rows[0]["fetched_ms"] > max_age_s * 1000:
            return None
        try:
            return json.loads(rows[0]["json"])
        except ValueError:
            return None

    def cache_put(self, key: str, obj: object) -> None:
        self._run("INSERT OR REPLACE INTO json_cache(key, fetched_ms, json) VALUES(?,?,?)",
                  (key, int(time.time() * 1000), json.dumps(obj)), commit=True)
