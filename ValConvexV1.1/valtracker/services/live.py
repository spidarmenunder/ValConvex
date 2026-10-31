"""Background service: watches the game, loads lobbies, builds the post-match view.

The UI never talks to the network. It reads events from `out` (a queue.Queue) as (kind, payload):
  status  str                  connected / waiting / error messages for the status bar
  lobby   dict                 the live roster (sent repeatedly as stats arrive; final=True at the end)
  idle    None                 no match in progress
  postmatch dict               a finished match (MatchData) ready to show
  detail  dict                 stats for one clicked player
  profile dict / profile_progress (done, total)
  error   str
"""
from __future__ import annotations

import copy
import logging
import re
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

from .. import export, matchdata
from ..model.baselines import Baselines
from ..model.winprob import win_probability
from ..ranks import rank_from_points
from ..riot.errors import ClientNotRunning, HenrikError, RiotError
from ..riot.henrik import HenrikClient
from ..util import now_ms
from .profile import parse_riot_id
from .stats import StatsService

log = logging.getLogger("valtracker")

LOAD_WORKERS = 5
FINALIZE_ATTEMPTS = 25
FINALIZE_DELAY = 3.0


def display_name(entry: dict) -> str:
    if entry.get("name"):
        return f'{entry["name"]}#{entry["tag"]}' if entry.get("tag") else entry["name"]
    return f'{entry.get("agent") or "Player"} (hidden)'


class LobbyLoader(threading.Thread):
    """Builds and progressively fills in the roster for one lobby."""

    def __init__(self, svc: "LiveService", lobby: dict):
        super().__init__(daemon=True, name="lobby-loader")
        self.svc, self.lobby = svc, lobby
        self.cancelled = threading.Event()
        self.entries: List[dict] = []
        self.party_ids: Dict[str, str] = {}

    def cancel(self) -> None:
        self.cancelled.set()

    # ---- helpers ---------------------------------------------------------
    def _base_entries(self) -> List[dict]:
        svc = self.svc
        out = []
        for p in self.lobby["players"]:
            aid = p.get("agent_id") or ""
            out.append({
                "puuid": p["puuid"], "team": p["team"], "agent_id": aid,
                "agent": svc.static.agent_name(aid) if aid else "Picking...",
                "agent_role": svc.static.agent_role(aid) if aid else "",
                "incognito": bool(p.get("incognito")), "name": "", "tag": "", "party": None, "party_size": 1,
                "tier": int(p.get("tier") or 0), "rr": 0, "peak_tier": 0, "peak_rr": 0, "peak_label": "",
                "rating": None, "skill": None, "skill_rank": None, "acs": None, "adr": None, "hs": None,
                "kd": None, "kast": None, "wr": None, "games": 0, "last_played_ms": None,
                "flags": [], "loaded": False, "is_me": p["puuid"] == svc.source.puuid,
            })
        out.sort(key=lambda e: (e["team"] != "ally", not e["is_me"]))
        return out

    def _apply_names(self) -> None:
        svc = self.svc
        if not svc.cfg.show_live_names:
            return
        ask = [e["puuid"] for e in self.entries if not e["incognito"]]
        try:
            names = svc.source.names(ask) if ask else {}
        except Exception as exc:
            log.warning("name lookup failed: %s", exc)
            names = {}
        for e in self.entries:
            if e["puuid"] in names:
                e["name"], e["tag"] = names[e["puuid"]]
        me = next((e for e in self.entries if e["is_me"]), None)
        if me and not me["name"] and not me["incognito"]:
            try:
                me["name"], me["tag"] = svc.source.my_name()
            except Exception:
                pass

    def _apply_parties(self) -> None:
        try:
            pm = self.svc.source.parties()
        except Exception as exc:
            log.warning("party lookup failed: %s", exc)
            return
        self.party_ids = pm or {}
        ids = {}
        for e in self.entries:
            pid = pm.get(e["puuid"])
            if pid:
                ids.setdefault(pid, []).append(e)
        letter = 0
        for pid, members in ids.items():
            if len(members) < 2:
                continue
            for m in members:
                m["party"], m["party_size"] = chr(ord("A") + letter), len(members)
            letter += 1

    def _save_meta(self) -> None:
        """Remember names, parties and streamer-mode flags so the post-match screen can fill gaps in Riot's data."""
        try:
            self.svc.db.save_lobby_meta(self.lobby["match_id"], [
                {"puuid": e["puuid"], "name": e["name"], "tag": e["tag"], "party_id": self.party_ids.get(e["puuid"]),
                 "incognito": e["incognito"]} for e in self.entries])
        except Exception as exc:
            log.warning("could not save lobby details: %s", exc)

    def _apply_flags(self) -> None:
        svc = self.svc
        for e in self.entries:
            e["flags"] = []
            if e["is_me"]:
                continue
            if svc.cfg.in_watchlist(e["puuid"]):
                e["flags"].append("Watchlist")
            try:
                n = svc.db.repeat_count(e["puuid"], self.lobby["match_id"], 10)
            except Exception:
                n = 0
            if n >= 2:
                e["flags"].append(f"Seen {n}x")

    def _fill(self, e: dict, info: dict) -> None:
        mmr, s = info["mmr"], info["summary"]
        e.update({"tier": mmr["tier"] or e["tier"], "rr": mmr["rr"], "peak_tier": mmr["peak_tier"],
                  "peak_rr": mmr["peak_rr"], "peak_label": mmr["peak_label"], "wr": mmr["wr"],
                  "rating": s["rating"], "acs": s["acs"], "adr": s["adr"], "hs": s["hs"], "kd": s["kd"],
                  "kast": s["kast"], "games": s["games"], "last_played_ms": s["last_played_ms"],
                  "skill": info["skill"], "skill_rank": rank_from_points(info["skill"]) if info["skill"] is not None else None,
                  "skill_mode": info.get("skill_mode", s.get("skill_mode", "fallback")),
                  "fallback_matches": info.get("fallback_matches", s.get("fallback_matches", [])),
                  "loaded": True})

    def _win(self) -> Optional[dict]:
        enemies = [e for e in self.entries if e["team"] == "enemy"]
        if not enemies:
            return None
        row = lambda e: {"skill": e["skill"], "party": e["party"], "games": e["games"]}
        return win_probability([row(e) for e in self.entries if e["team"] == "ally"], [row(e) for e in enemies])

    def _emit(self, final: bool = False) -> None:
        svc = self.svc
        win = self._win()
        if final:
            svc.last_prob = win["p_me"] if win else None
        allies = [e["skill"] for e in self.entries if e["team"] == "ally" and e["skill"] is not None]
        svc.emit("lobby", {
            "phase": self.lobby["phase"], "match_id": self.lobby["match_id"],
            "enemy_size": self.lobby.get("enemy_size"), "ally_avg": (sum(allies) / len(allies)) if allies else None,
            "map": svc.static.map_name(self.lobby["map_url"]), "queue": self.lobby["queue"],
            "players": copy.deepcopy(self.entries), "win": win,
            "loaded": sum(1 for e in self.entries if e["loaded"]), "total": len(self.entries), "final": final})

    # ---- thread ------------------------------------------------------------
    def run(self) -> None:
        svc = self.svc
        try:
            self.entries = self._base_entries()
            self._apply_names()
            self._apply_parties()
            self._save_meta()
            self._apply_flags()
            if self.cancelled.is_set():
                return
            self._emit()
            # Submit enemies as the first batch so the first five stats requests
            # are always the enemy team; this also keeps agent-select loading
            # deterministic for the win-probability panel.
            with ThreadPoolExecutor(max_workers=LOAD_WORKERS) as pool:
                enemies = [e for e in self.entries if e["team"] == "enemy"]
                allies = [e for e in self.entries if e["team"] != "enemy"]
                futures = {}
                for e in enemies:
                    futures[pool.submit(svc.stats.player_info, e["puuid"])] = e
                # Do not enqueue allies until the enemy batch has completed.
                for fut in as_completed(list(futures)):
                    if self.cancelled.is_set():
                        for f in futures:
                            f.cancel()
                        return
                    e = futures[fut]
                    try:
                        self._fill(e, fut.result())
                    except Exception as exc:
                        log.warning("stats for %s failed: %s", e["puuid"], exc)
                        e["loaded"] = True
                    self._emit()
                if not self.cancelled.is_set() and allies:
                    futures = {pool.submit(svc.stats.player_info, e["puuid"]): e for e in allies}
                    for fut in as_completed(futures):
                        if self.cancelled.is_set():
                            for f in futures:
                                f.cancel()
                            return
                        e = futures[fut]
                        try:
                            self._fill(e, fut.result())
                        except Exception as exc:
                            log.warning("stats for %s failed: %s", e["puuid"], exc)
                            e["loaded"] = True
                        self._emit()
            if self.cancelled.is_set():
                return
            if self.lobby["phase"] == "ingame":
                try:
                    svc.db.record_seen(self.lobby["match_id"], now_ms(),
                                       [(e["puuid"], display_name(e)) for e in self.entries])
                except Exception as exc:
                    log.warning("could not record sightings: %s", exc)
            self._emit(final=True)
        except Exception as exc:
            log.exception("lobby loader crashed")
            svc.emit("error", f"Could not load the lobby: {exc}")


class LiveService(threading.Thread):
    def __init__(self, source_factory: Callable[[], object], static, db, baselines: Baselines, cfg,
                 out: "queue.Queue", henrik_factory: Optional[Callable[[str], HenrikClient]] = None):
        super().__init__(daemon=True, name="live-service")
        self.source_factory = source_factory
        self.static, self.db, self.baselines, self.cfg, self.out = static, db, baselines, cfg, out
        self.henrik_factory = henrik_factory or (lambda key: HenrikClient(key))
        self.source = None
        self.stats: Optional[StatsService] = None
        self._halt = threading.Event()
        self._key = None
        self.phase = "menu"
        self.match_id: Optional[str] = None
        self._missing = 0
        self._loader: Optional[LobbyLoader] = None
        self.last_prob: Optional[float] = None
        self._last_status: Optional[str] = None
        self._finalizing: set = set()
        self._own_profile_lock = threading.Lock()
        self._own_profile_loading = False

    # ---- helpers ---------------------------------------------------------
    def emit(self, kind: str, payload=None) -> None:
        self.out.put((kind, payload))

    def status(self, text: str) -> None:
        if text != self._last_status:
            self._last_status = text
            self.emit("status", text)

    def stop(self) -> None:
        self._halt.set()
        if self._loader:
            self._loader.cancel()

    def _reset(self) -> None:
        if self._loader:
            self._loader.cancel()
        self._loader, self._key, self.phase, self.match_id = None, None, "menu", None
        self._missing = 0

    # ---- main loop ---------------------------------------------------------
    def run(self) -> None:
        while not self._halt.is_set():
            interval = self.cfg.idle_poll_seconds
            try:
                if self.source is None:
                    self._connect()
                phase, mid = self.source.state()
                self._on_state(phase, mid)
                if phase != "menu" or self.phase != "menu":
                    interval = self.cfg.poll_seconds
            except ClientNotRunning as exc:
                self._disconnect(str(exc))
            except RiotError as exc:
                if exc.status in (401, 403):
                    self._disconnect("Riot Client session expired, reconnecting...")
                else:
                    self.status(f"Riot error: {exc}")
            except Exception as exc:
                log.exception("live loop error")
                self.status(f"Error: {exc}")
            self._halt.wait(interval)

    def _connect(self) -> None:
        self.source = self.source_factory()
        if not self.static.has_maps:                     # startup was offline: try the game data again
            try:
                self.static.reload()
            except Exception as exc:
                log.warning("game data reload failed: %s", exc)
        self.stats = StatsService(self.source, self.static, self.db, self.baselines)
        self.emit("connected", {"puuid": self.source.puuid})
        self.status("Connected. Waiting for a match.")

    def _disconnect(self, message: str) -> None:
        self.source, self.stats = None, None
        self._reset()
        self.status(message or "Waiting for VALORANT...")
        self.emit("idle", None)

    def _on_state(self, phase: str, mid: Optional[str]) -> None:
        if phase in ("pregame", "ingame"):
            self._missing = 0
            if (phase, mid) != self._key:
                self._start_lobby(phase, mid)
            return
        if self.phase == "menu":
            self.status("Connected. Waiting for a match.")
            return
        self._missing += 1
        if self._missing < 2:                     # ignore one blip
            return
        ended_id, was_ingame, prob = self.match_id, self.phase == "ingame", self.last_prob
        self._reset()
        self.emit("idle", None)
        if was_ingame and ended_id:
            self.status("Match ended. Loading results...")
            threading.Thread(target=self._finalize, args=(ended_id, prob), daemon=True, name="finalize").start()
        else:
            self.status("Connected. Waiting for a match.")

    def _start_lobby(self, phase: str, mid: str) -> None:
        lobby = self.source.lobby(phase, mid)
        if not lobby:
            return                                # try again on the next poll
        if self._loader:
            self._loader.cancel()
        self._key, self.phase, self.match_id = (phase, mid), phase, mid
        self.status("Agent select" if phase == "pregame" else "In match")
        self._loader = LobbyLoader(self, lobby)
        self._loader.start()

    # ---- post-match ------------------------------------------------------
    def _finalize(self, match_id: str, pre_prob: Optional[float], attempts: int = FINALIZE_ATTEMPTS,
                  delay: float = FINALIZE_DELAY) -> None:
        if match_id in self._finalizing:
            return
        self._finalizing.add(match_id)
        try:
            source = self.source
            raw = None
            for _ in range(attempts):
                if self._halt.is_set() or source is None:
                    return
                try:
                    raw = source.match_details(match_id)
                except Exception as exc:
                    log.info("match details not ready: %s", exc)
                    raw = None
                if raw and raw.get("players") and raw.get("roundResults"):
                    break
                raw = None
                self._halt.wait(delay)
            if raw is None:
                self.emit("error", "The finished match is not available yet. Use Matches > Load latest match to retry.")
                return
            matchdata.apply_lobby_meta(raw, self.db.get_lobby_meta(match_id), self.cfg.hide_streamer_names_after_match)
            self._apply_riot_ids_to_match(raw)
            data = matchdata.process_raw_match(raw, self.static, source.puuid, self.baselines, self.db,
                                               pre_win_prob=pre_prob, source="live")
            self.store_match(data)
            self.status("Match results ready.")
            self.emit("postmatch", data)
        except Exception as exc:
            log.exception("post-match failed")
            self.emit("error", f"Could not build the post-match view: {exc}")
        finally:
            self._finalizing.discard(match_id)

    def store_match(self, data: dict, autosave: bool = True) -> Optional[str]:
        """Cache the match, download its minimap, and autosave a .vtrack file. Returns the saved path."""
        try:
            self.static.ensure_map_image(self.static.map_by_url(data["map"]["url"]))
        except Exception as exc:
            log.warning("minimap not available: %s", exc)
        saved = None
        queue = str(data.get("queue") or "").lower()
        folder = data.get("folder") or ("Ranked" if queue in {"competitive", "ranked"} else "Scrims" if queue in {"custom", "tournament"} or "tournament" in queue else "Unsorted")
        data["folder"] = folder
        if autosave and self.cfg.autosave:
            try:
                path = self.cfg.match_folder(folder) / export.default_filename(data, "vtrack")
                export.save_vtrack(data, path, anonymize=self.cfg.anonymize_exports)
                saved = str(path)
            except OSError as exc:
                log.warning("autosave failed: %s", exc)
        self.db.save_match(data, saved_path=saved)
        return saved

    # ---- jobs the UI can start ------------------------------------------------
    def _need_source(self) -> bool:
        if self.source is None or self.stats is None:
            self.emit("error", "Not connected to VALORANT yet.")
            return False
        return True

    def _resolve_riot_ids(self, puuids: List[str], protected: Optional[set] = None) -> Dict[str, tuple]:
        """Resolve PUUIDs to Riot IDs through the connected VALORANT client.

        PUUIDs stay as internal identifiers, but every player-facing surface gets
        GameName#TagLine when Riot's name service provides it. Protected entries
        are streamer-mode players and are intentionally left hidden.
        """
        if self.source is None:
            return {}
        protected = protected or set()
        ask = [u for u in puuids if u and u not in protected]
        if not ask:
            return {}
        try:
            return self.source.names(ask)
        except Exception as exc:
            log.warning("could not resolve Riot IDs: %s", exc)
            return {}

    def _apply_riot_ids_to_match(self, raw: dict) -> None:
        players = raw.get("players") or []
        meta = self.db.get_lobby_meta((raw.get("matchInfo") or {}).get("matchId", ""))
        flagged = {u for u, m in meta.items() if m.get("incognito")}
        # By default every name Riot provides is shown once the match is over. Set
        # hide_streamer_names_after_match in config.json to keep flagged players hidden.
        protected = flagged if self.cfg.hide_streamer_names_after_match else set()
        have = sum(1 for p in players if p.get("gameName"))
        names = self._resolve_riot_ids([p.get("subject") for p in players], protected)
        looked_up = 0
        for p in players:
            subject = p.get("subject")
            if subject in protected:
                continue
            if not p.get("gameName") and subject in names:
                game, tag = names[subject]
                if game:
                    p["gameName"], p["tagLine"] = game, tag
                    looked_up += 1
        blank = sum(1 for p in players if not p.get("gameName"))
        log.info("post-match names: %d players, %d in Riot's match data, %d from the lobby/name lookup, %d still blank "
                 "(%d flagged Streamer Mode in the lobby, hiding them: %s)",
                 len(players), have, looked_up, blank, len(flagged), bool(protected))

    def request_detail(self, puuid: str) -> None:
        if not self._need_source():
            return

        def job():
            try:
                self.emit("detail", self.stats.detail(puuid))
            except Exception as exc:
                self.emit("error", f"Could not load that player: {exc}")
        threading.Thread(target=job, daemon=True, name="detail").start()

    def load_own_profile(self, depth: int = 15) -> None:
        """Load the current player's profile without making the Tk thread wait on Riot Client."""
        if not self._need_source():
            return
        with self._own_profile_lock:
            if self._own_profile_loading:
                return
            self._own_profile_loading = True

        source, stats = self.source, self.stats

        def job():
            try:
                # my_name() performs local-client API requests and must never run
                # synchronously from the tab click handler.
                puuid = source.puuid
                name, tag = source.my_name()
                prof = stats.profile(
                    puuid, depth,
                    progress=lambda i, n: self.emit("profile_progress", (i, n)))
                if not name:
                    resolved = self._resolve_riot_ids([puuid]).get(puuid)
                    if resolved:
                        name, tag = resolved
                prof["name"], prof["tag"] = name, tag
                self.emit("profile", prof)
            except Exception as exc:
                log.exception("own profile load failed")
                self.emit("error", f"Could not load your profile: {exc}")
            finally:
                with self._own_profile_lock:
                    self._own_profile_loading = False

        threading.Thread(target=job, daemon=True, name="own-profile").start()

    def load_profile(self, puuid: str, name: str = "", tag: str = "", depth: int = 15) -> None:
        if not self._need_source():
            return

        def job():
            try:
                display_game, display_tag = name, tag
                prof = self.stats.profile(puuid, depth, progress=lambda i, n: self.emit("profile_progress", (i, n)))
                if not display_game:
                    resolved = self._resolve_riot_ids([puuid]).get(puuid)
                    if resolved:
                        display_game, display_tag = resolved
                prof["name"], prof["tag"] = display_game, display_tag
                self.emit("profile", prof)
            except Exception as exc:
                self.emit("error", f"Could not load the profile: {exc}")
        threading.Thread(target=job, daemon=True, name="profile").start()

    def search_profile(self, text: str, depth: int = 15) -> None:
        try:
            name, tag = parse_riot_id(text)
        except ValueError as exc:
            self.emit("error", str(exc))
            return
        if not self._need_source():
            return

        def job():
            try:
                acct = self.henrik_factory(self.cfg.api_key).account(name, tag)
            except HenrikError as exc:
                self.emit("error", str(exc))
                return
            self.load_profile(acct["puuid"], acct["name"], acct["tag"], depth)
        threading.Thread(target=job, daemon=True, name="search").start()

    def open_match(self, match_id: str, perspective: Optional[str] = None) -> None:
        """Open any match (e.g. from someone's history) as a leaderboard + heatmap, from `perspective`'s side."""
        def job():
            try:
                m = self.db.get_match(match_id)
                if m is None:
                    if not self._need_source():
                        return
                    raw = self.source.match_details(match_id)
                    if not raw or not raw.get("players"):
                        self.emit("error", "That match is no longer available from Riot.")
                        return
                    matchdata.apply_lobby_meta(raw, self.db.get_lobby_meta(match_id), self.cfg.hide_streamer_names_after_match)
                    self._apply_riot_ids_to_match(raw)
                    m = matchdata.process_raw_match(raw, self.static, perspective or self.source.puuid,
                                                    self.baselines, self.db, source="history")
                    self.store_match(m, autosave=False)
                if perspective:
                    m = matchdata.reperspective(m, perspective)
                self.emit("open_match", m)
            except Exception as exc:
                log.exception("open_match failed")
                self.emit("error", f"Could not open that match: {exc}")
        threading.Thread(target=job, daemon=True, name="open-match").start()

    def fetch_latest(self) -> None:
        """Load the most recent finished match (any queue), for when auto-detect missed it."""
        if not self._need_source():
            return

        def job():
            try:
                hist = self.source.history(self.source.puuid, 0, 3)
                if not hist:
                    self.emit("error", "No recent matches found.")
                    return
                self._finalize(hist[0]["MatchID"], None, attempts=3, delay=2.0)
            except Exception as exc:
                self.emit("error", f"Could not load the latest match: {exc}")
        threading.Thread(target=job, daemon=True, name="latest").start()
