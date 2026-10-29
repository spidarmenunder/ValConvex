import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path

from valtracker import demo, export
from valtracker.config import Config
from valtracker.db import Database
from valtracker.demo_source import DemoSource
from valtracker.model.baselines import Baselines
from valtracker.riot.errors import ClientNotRunning, HenrikError
from valtracker.services import live
from valtracker.services.profile import parse_riot_id


def collect(q, seconds, stop_when=None):
    end = time.time() + seconds
    events = []
    while time.time() < end:
        try:
            ev = q.get(timeout=0.05)
        except queue.Empty:
            continue
        events.append(ev)
        if stop_when and stop_when(events):
            break
    return events


class ParseIdTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_riot_id(" Name #TAG "), ("Name", "TAG"))
        self.assertEqual(parse_riot_id("a b#c"), ("a b", "c"))
        for bad in ("", "nohash", "#tag", "name#", "  #  "):
            with self.assertRaises(ValueError):
                parse_riot_id(bad)


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(_root=self.tmp.name)
        self.cfg.ensure_dirs()
        self.cfg.poll_seconds = self.cfg.idle_poll_seconds = 0.05
        self.db = Database(self.cfg.db_path)
        self.static = demo.demo_static(cache_dir=self.cfg.cache_dir)
        self.q = queue.Queue()

    def tearDown(self):
        if getattr(self, "svc", None):
            self.svc.stop()
            self.svc.join(timeout=3)
        self.db.close()
        self.tmp.cleanup()

    def make(self, source):
        self.svc = live.LiveService(lambda: source, self.static, self.db, Baselines(self.db), self.cfg, self.q,
                                    henrik_factory=lambda key: FakeHenrik(key))
        return self.svc

    def test_full_cycle_with_demo_source(self):
        src = DemoSource(static=self.static, phases=[("menu", 0.3), ("pregame", 0.8), ("ingame", 2.5), ("menu", 30)],
                         details_delay=0.3)
        self.make(src).start()
        events = collect(self.q, 25, stop_when=lambda ev: any(k == "postmatch" for k, _ in ev))
        kinds = [k for k, _ in events]
        self.assertIn("connected", kinds)
        self.assertIn("postmatch", kinds, [e for e in events if e[0] in ("status", "error")])

        lobbies = [p for k, p in events if k == "lobby"]
        pre = [l for l in lobbies if l["phase"] == "pregame"]
        ing = [l for l in lobbies if l["phase"] == "ingame"]
        self.assertTrue(pre and ing)
        self.assertEqual(len(pre[-1]["players"]), 5)
        final = [l for l in ing if l["final"]][-1]
        self.assertEqual(len(final["players"]), 10)
        self.assertEqual(final["loaded"], 10)
        self.assertIsNotNone(final["win"])
        self.assertTrue(0 < final["win"]["p_me"] < 1)
        by_id = {p["puuid"]: p for p in final["players"]}
        me = by_id[demo.puuid_of("You#PH")]
        self.assertTrue(me["is_me"])
        self.assertEqual(final["players"][0]["puuid"], me["puuid"])          # me sorted first
        self.assertEqual(me["name"], "You")
        self.assertIsNotNone(me["rating"])
        self.assertIsNotNone(me["skill"])
        # parties: duo B (You + sable), duo A, duo C
        self.assertEqual(me["party_size"], 2)
        self.assertEqual(by_id[demo.puuid_of("sable#310")]["party"], me["party"])
        self.assertIsNone(by_id[demo.puuid_of("rook#0099")]["party"])
        # the streamer-mode player keeps a hidden name but still shows stats
        hidden = by_id[demo.puuid_of("mango#8821")]
        self.assertTrue(hidden["incognito"])
        self.assertEqual(hidden["name"], "")
        self.assertEqual(live.display_name(hidden), "Killjoy (hidden)")
        self.assertTrue(hidden["loaded"] and hidden["tier"] > 0)
        # progressive loading happened
        self.assertTrue(any(l["loaded"] < l["total"] for l in ing))

        pm = [p for k, p in events if k == "postmatch"][0]
        self.assertEqual(pm["match_id"], "demo-live-0")
        self.assertEqual(pm["me"], demo.puuid_of("You#PH"))
        self.assertAlmostEqual(pm["pre_win_prob"], final["win"]["p_me"])
        self.assertEqual(sum(pm["result"]["score"]), 22)
        # cached + autosaved
        self.assertIsNotNone(self.db.get_match("demo-live-0"))
        files = list(self.cfg.matches_dir.glob("*.vtrack"))
        self.assertEqual(len(files), 1)
        self.assertEqual(export.load_vtrack(files[0])["match_id"], "demo-live-0")
        listed = self.db.list_matches()
        self.assertEqual(listed[0]["saved_path"], str(files[0]))
        # sightings recorded for the repeat-player flag
        self.assertEqual(self.db.repeat_count(demo.puuid_of("Vex#0042"), "other", 10), 1)

    def test_autosave_off_and_anonymize(self):
        self.cfg.autosave = False
        src = DemoSource(static=self.static, phases=[("menu", 0.1), ("ingame", 0.6), ("menu", 30)], details_delay=0.1)
        self.make(src).start()
        collect(self.q, 15, stop_when=lambda ev: any(k == "postmatch" for k, _ in ev))
        self.assertEqual(list(self.cfg.matches_dir.glob("*.vtrack")), [])
        self.assertIsNotNone(self.db.get_match("demo-live-0"))

        self.cfg.autosave, self.cfg.anonymize_exports = True, True
        data = self.db.get_match("demo-live-0")
        path = self.svc.store_match(data)
        text = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("Vex", text)
        self.assertNotIn("demo-live-0", text)

    def test_client_not_running_then_recovers(self):
        state = {"n": 0}
        src = DemoSource(static=self.static, phases=[("menu", 30)])

        def factory():
            state["n"] += 1
            if state["n"] < 3:
                raise ClientNotRunning("Riot Client is not running (no lockfile found). Start VALORANT and try again.")
            return src
        self.svc = live.LiveService(factory, self.static, self.db, Baselines(self.db), self.cfg, self.q)
        self.svc.start()
        events = collect(self.q, 5, stop_when=lambda ev: any(k == "connected" for k, _ in ev))
        kinds = [k for k, _ in events]
        self.assertIn("connected", kinds)
        self.assertTrue(any(k == "status" and "not running" in p for k, p in events))
        self.assertEqual(state["n"], 3)

    def test_detail_profile_search_jobs(self):
        src = DemoSource(static=self.static, phases=[("menu", 60)])
        svc = self.make(src)
        svc.start()
        collect(self.q, 5, stop_when=lambda ev: any(k == "connected" for k, _ in ev))
        me = demo.puuid_of("You#PH")
        svc.request_detail(me)
        ev = collect(self.q, 10, stop_when=lambda ev: any(k == "detail" for k, _ in ev))
        d = [p for k, p in ev if k == "detail"][0]
        self.assertEqual(len(d["games"]), 5)

        svc.load_profile(me, "You", "PH", depth=4)
        ev = collect(self.q, 10, stop_when=lambda ev: any(k == "profile" for k, _ in ev))
        prof = [p for k, p in ev if k == "profile"][0]
        self.assertEqual(prof["name"], "You")
        self.assertEqual(prof["stats"]["games"], 4)
        self.assertIn(("profile_progress", (4, 4)), ev)

        svc.search_profile("You#PH", depth=3)
        ev = collect(self.q, 10, stop_when=lambda ev: any(k in ("profile", "error") for k, _ in ev))
        self.assertEqual([k for k, _ in ev if k in ("profile", "error")], ["profile"])
        self.assertEqual([p for k, p in ev if k == "profile"][0]["tag"], "PH")

        svc.search_profile("nohash")
        ev = collect(self.q, 3, stop_when=lambda ev: any(k == "error" for k, _ in ev))
        self.assertIn("name#tag", [p for k, p in ev if k == "error"][0])

        FakeHenrik.fail = True
        svc.search_profile("x#y")
        ev = collect(self.q, 3, stop_when=lambda ev: any(k == "error" for k, _ in ev))
        FakeHenrik.fail = False
        self.assertIn("rejected", [p for k, p in ev if k == "error"][0])

    def test_fetch_latest(self):
        src = DemoSource(static=self.static, phases=[("menu", 60)])
        svc = self.make(src)
        svc.start()
        collect(self.q, 5, stop_when=lambda ev: any(k == "connected" for k, _ in ev))
        svc.fetch_latest()
        ev = collect(self.q, 10, stop_when=lambda ev: any(k in ("postmatch", "error") for k, _ in ev))
        pm = [p for k, p in ev if k == "postmatch"]
        self.assertEqual(len(pm), 1)
        self.assertEqual(pm[0]["match_id"], "demo-past-0")

    def test_jobs_before_connect_report_error(self):
        svc = live.LiveService(lambda: (_ for _ in ()).throw(ClientNotRunning("no")), self.static, self.db,
                               Baselines(self.db), self.cfg, self.q)
        svc.request_detail("x")
        svc.fetch_latest()
        svc.load_profile("x")
        errs = [p for k, p in collect(self.q, 0.3) if k == "error"]
        self.assertEqual(len(errs), 3)

    def test_watchlist_and_repeat_flags(self):
        src = DemoSource(static=self.static, phases=[("menu", 0.1), ("ingame", 1.5), ("menu", 30)], details_delay=0.1)
        vex = demo.puuid_of("Vex#0042")
        self.cfg.toggle_watchlist(vex, "Vex#0042")
        for i in range(3):                               # earlier matches where Vex was also present
            self.db.record_seen(f"old{i}", 1000 + i, [(vex, "Vex#0042"), (src.puuid, "You#PH")])
        self.make(src).start()
        events = collect(self.q, 15, stop_when=lambda ev: any(k == "lobby" and p["final"] and p["phase"] == "ingame" for k, p in ev))
        final = [p for k, p in events if k == "lobby" and p["final"] and p["phase"] == "ingame"][-1]
        v = next(p for p in final["players"] if p["puuid"] == vex)
        self.assertIn("Watchlist", v["flags"])
        self.assertIn("Seen 3x", v["flags"])
        me = next(p for p in final["players"] if p["is_me"])
        self.assertEqual(me["flags"], [])


class FakeHenrik:
    fail = False

    def __init__(self, key):
        self.key = key

    def account(self, name, tag):
        if FakeHenrik.fail:
            raise HenrikError("HenrikDev rejected the API key. Check it, or generate a new one.")
        return {"puuid": demo.puuid_of(f"{name}#{tag}"), "region": "ap", "name": name, "tag": tag, "level": 1}


if __name__ == "__main__":
    unittest.main()


class HistoryAndMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(_root=self.tmp.name)
        self.cfg.ensure_dirs()
        self.cfg.poll_seconds = self.cfg.idle_poll_seconds = 0.05
        self.db = Database(self.cfg.db_path)
        self.static = demo.demo_static(cache_dir=self.cfg.cache_dir)
        self.q = queue.Queue()
        self.svc = None

    def tearDown(self):
        if self.svc:
            self.svc.stop()
            self.svc.join(timeout=3)
        self.db.close()
        self.tmp.cleanup()

    def start(self, src):
        self.svc = live.LiveService(lambda: src, self.static, self.db, Baselines(self.db), self.cfg, self.q)
        self.svc.start()
        collect(self.q, 5, stop_when=lambda ev: any(k == "connected" for k, _ in ev))
        return self.svc

    def test_open_history_match_from_another_players_side(self):
        src = DemoSource(static=self.static, phases=[("menu", 60)])
        svc = self.start(src)
        vex = demo.puuid_of("Vex#0042")
        svc.open_match("demo-past-0", vex)
        ev = collect(self.q, 10, stop_when=lambda ev: any(k in ("open_match", "error") for k, _ in ev))
        m = [p for k, p in ev if k == "open_match"][0]
        self.assertEqual(m["me"], vex)
        self.assertEqual([p["team"] for p in m["players"]][:5], ["me"] * 5)
        self.assertEqual(m["players"][0]["team"], "me")
        self.assertIn(vex, [p["puuid"] for p in m["players"][:5]])
        self.assertEqual(m["source"], "history")
        self.assertEqual(list(self.cfg.matches_dir.glob("*.vtrack")), [])      # history views are not autosaved
        stored = self.db.get_match("demo-past-0")
        self.assertIsNotNone(stored)
        # second time comes straight from the cache, from yet another side, with no network call
        calls = {"n": 0}
        orig = src.match_details
        src.match_details = lambda mid: (calls.__setitem__("n", calls["n"] + 1), orig(mid))[1]
        svc.open_match("demo-past-0", demo.puuid_of("You#PH"))
        ev = collect(self.q, 10, stop_when=lambda ev: any(k in ("open_match", "error") for k, _ in ev))
        m2 = [p for k, p in ev if k == "open_match"][0]
        self.assertEqual(calls["n"], 0)
        self.assertEqual(m2["me"], demo.puuid_of("You#PH"))
        self.assertEqual(m2["result"]["won"], not m["result"]["won"])

    def test_open_unknown_match_reports_error(self):
        svc = self.start(DemoSource(static=self.static, phases=[("menu", 60)]))
        svc.open_match("does-not-exist", None)
        ev = collect(self.q, 5, stop_when=lambda ev: any(k in ("open_match", "error") for k, _ in ev))
        self.assertEqual([k for k, _ in ev if k in ("open_match", "error")], ["error"])
        self.assertIn("no longer available", [p for k, p in ev if k == "error"][0])

    def test_names_and_parties_filled_from_the_live_lobby(self):
        """Riot's finished-match data came back without names or parties: the live lobby supplies them."""
        src = DemoSource(static=self.static, phases=[("menu", 0.1), ("ingame", 1.2), ("menu", 60)],
                         details_delay=0.1, blank_raw=True)
        self.svc = live.LiveService(lambda: src, self.static, self.db, Baselines(self.db), self.cfg, self.q)
        self.svc.start()
        ev = collect(self.q, 20, stop_when=lambda ev: any(k == "postmatch" for k, _ in ev))
        pm = [p for k, p in ev if k == "postmatch"][0]
        by = {p["puuid"]: p for p in pm["players"]}
        you = by[demo.puuid_of("You#PH")]
        self.assertEqual((you["name"], you["tag"]), ("You", "PH"))                 # even your own name
        self.assertEqual(by[demo.puuid_of("Vex#0042")]["name"], "Vex")
        self.assertEqual(you["party_size"], 2)
        self.assertEqual(you["party"], by[demo.puuid_of("sable#310")]["party"])
        self.assertEqual(by[demo.puuid_of("kurumi#777")]["party_size"], 2)
        self.assertIsNone(by[demo.puuid_of("rook#0099")]["party"])
        hidden = by[demo.puuid_of("mango#8821")]
        self.assertEqual((hidden["name"], hidden["tag"]), ("mango", "8821"))       # shown after the match by default
        self.assertIsNotNone(hidden["rating"])

    def test_pregame_has_only_allies_and_says_how_many_enemies(self):
        src = DemoSource(static=self.static, phases=[("menu", 0.1), ("pregame", 3), ("menu", 60)])
        self.svc = live.LiveService(lambda: src, self.static, self.db, Baselines(self.db), self.cfg, self.q)
        self.svc.start()
        ev = collect(self.q, 15, stop_when=lambda ev: any(k == "lobby" and p["final"] for k, p in ev))
        final = [p for k, p in ev if k == "lobby" and p["final"]][-1]
        self.assertEqual(final["phase"], "pregame")
        self.assertEqual({p["team"] for p in final["players"]}, {"ally"})          # VALORANT sends no enemies yet
        self.assertEqual(final["enemy_size"], 5)
        self.assertIsNone(final["win"])
        self.assertIsNotNone(final["ally_avg"])
        self.assertEqual(final["loaded"], 5)

    def test_enemies_are_loaded_first(self):
        order = []
        src = DemoSource(static=self.static, phases=[("menu", 0.1), ("ingame", 3), ("menu", 60)])
        svc = live.LiveService(lambda: src, self.static, self.db, Baselines(self.db), self.cfg, self.q)
        svc.source = src
        svc.stats = type("S", (), {"player_info": lambda self_, puuid: (order.append(puuid), {"mmr": {
            "tier": 18, "rr": 1, "peak_tier": 18, "peak_rr": 0, "peak_label": "", "wr": None},
            "summary": {"rating": 1.0, "acs": 1, "adr": 1, "hs": 0, "kd": 1, "kast": 0, "games": 3, "last_played_ms": 1},
            "skill": 1500.0})[1]})()
        loader = live.LobbyLoader(svc, src.lobby("ingame", "x"))
        loader.run()
        enemy_ids = {p["puuid"] for p in src.lobby("ingame", "x")["players"] if p["team"] == "enemy"}
        self.assertEqual(set(order[:5]), enemy_ids)


class PostMatchNamesTests(unittest.TestCase):
    """After a game every name Riot provides is shown; hiding Streamer Mode players is an opt-in setting."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(_root=self.tmp.name)
        self.cfg.ensure_dirs()
        self.cfg.poll_seconds = self.cfg.idle_poll_seconds = 0.05
        self.db = Database(self.cfg.db_path)
        self.static = demo.demo_static(cache_dir=self.cfg.cache_dir)
        self.q = queue.Queue()
        self.svc = None

    def tearDown(self):
        if self.svc:
            self.svc.stop()
            self.svc.join(timeout=3)
        self.db.close()
        self.tmp.cleanup()

    def play(self, hidden):
        names = ["You#PH", "echo#1212", "sable#310", "rook#0099", "lumen#5", "Vex#0042", "nightowl#PH1",
                 "kurumi#777", "zeroPing#1", "mango#8821"]
        src = DemoSource(static=self.static, phases=[("menu", 0.1), ("ingame", 1.2), ("menu", 60)], details_delay=0.1,
                         blank_raw=True, hidden=[n for n in names if n in hidden] if hidden != "all" else names)
        self.svc = live.LiveService(lambda: src, self.static, self.db, Baselines(self.db), self.cfg, self.q)
        self.svc.start()
        ev = collect(self.q, 20, stop_when=lambda ev: any(k == "postmatch" for k, _ in ev))
        return ([p for k, p in ev if k == "postmatch"][0], [p for k, p in ev if k == "lobby" and p["final"]][-1])

    def test_everyone_flagged_still_gets_names_after_the_match(self):
        pm, lobby = self.play("all")
        self.assertTrue(all(p["incognito"] for p in lobby["players"]))             # the game flagged everyone
        self.assertTrue(all(not p["name"] for p in lobby["players"]))              # live roster still honours the flag
        self.assertEqual(sorted(p["name"] for p in pm["players"] if p["name"]),
                         sorted(["You", "echo", "sable", "rook", "lumen", "Vex", "nightowl", "kurumi", "zeroPing", "mango"]))
        by = {p["puuid"]: p for p in pm["players"]}
        self.assertEqual((by[demo.puuid_of("You#PH")]["name"], by[demo.puuid_of("You#PH")]["tag"]), ("You", "PH"))

    def test_option_keeps_streamer_mode_players_hidden(self):
        self.cfg.hide_streamer_names_after_match = True
        pm, _ = self.play(["mango#8821", "Vex#0042"])
        by = {p["puuid"]: p for p in pm["players"]}
        self.assertEqual(by[demo.puuid_of("mango#8821")]["name"], "")
        self.assertEqual(by[demo.puuid_of("Vex#0042")]["name"], "")
        self.assertEqual(by[demo.puuid_of("echo#1212")]["name"], "echo")           # everyone else still shown

    def test_names_are_summarised_in_the_log(self):
        with self.assertLogs("valtracker", level="INFO") as cm:
            self.play("all")
        line = next(l for l in cm.output if "post-match names" in l)
        self.assertIn("10 players", line)
        self.assertIn("10 flagged Streamer Mode in the lobby, hiding them: False", line)
        self.assertIn("0 still blank", line)

    def test_history_matches_show_names_too(self):
        src = DemoSource(static=self.static, phases=[("menu", 60)], hidden=["mango#8821"])
        self.svc = live.LiveService(lambda: src, self.static, self.db, Baselines(self.db), self.cfg, self.q)
        self.svc.start()
        collect(self.q, 5, stop_when=lambda ev: any(k == "connected" for k, _ in ev))
        self.svc.open_match("demo-past-1", demo.puuid_of("You#PH"))
        ev = collect(self.q, 10, stop_when=lambda ev: any(k in ("open_match", "error") for k, _ in ev))
        m = [p for k, p in ev if k == "open_match"][0]
        self.assertEqual({p["name"] for p in m["players"]}, {"You", "echo", "sable", "rook", "lumen", "Vex", "nightowl",
                                                              "kurumi", "zeroPing", "mango"})
