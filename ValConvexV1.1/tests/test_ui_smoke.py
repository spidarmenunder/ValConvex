"""Runs the real UI code on a fake tkinter, fed by the demo source, and checks what ends up on screen."""
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import faketk                                   # noqa: E402

faketk.install()

from valtracker import demo, export              # noqa: E402
from valtracker.config import Config             # noqa: E402
from valtracker.db import Database               # noqa: E402
from valtracker.demo_source import DemoSource    # noqa: E402
from valtracker.ui import app as app_mod, live_view, postmatch_view, theme   # noqa: E402
from valtracker.riot.errors import HenrikError   # noqa: E402


class FakeHenrik:
    def __init__(self, key):
        pass

    def account(self, name, tag):
        return {"puuid": demo.puuid_of(f"{name}#{tag}"), "region": "ap", "name": name, "tag": tag, "level": 1}


def pump(app, seconds, until=None):
    end = time.time() + seconds
    seen = []
    while time.time() < end:
        try:
            kind, payload = app.q.get(timeout=0.05)
        except queue.Empty:
            continue
        app._handle(kind, payload)              # exceptions propagate so the test fails loudly
        seen.append(kind)
        if until and until(seen):
            break
    return seen


class UiSmokeTests(unittest.TestCase):
    def test_full_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(_root=tmp)
            cfg.ensure_dirs()
            cfg.poll_seconds = cfg.idle_poll_seconds = 0.05
            db = Database(cfg.db_path)
            static = demo.demo_static(cache_dir=cfg.cache_dir)
            demo.write_demo_minimap(cfg.cache_dir / f"map_{demo.MAP_UUID}.png")
            src = DemoSource(static=static, phases=[("menu", 0.3), ("pregame", 0.8), ("ingame", 2.5), ("menu", 60)],
                             details_delay=0.3)
            app = app_mod.App(cfg, db, static, lambda: src, demo=True)
            app.service.henrik_factory = lambda key: FakeHenrik(key)
            try:
                seen = pump(app, 30, until=lambda s: "postmatch" in s)
                self.assertIn("postmatch", seen)
                self.assertIn("lobby", seen)

                # live tab: rows were filled for both teams
                lv = app.live
                self.assertEqual(len(lv.ally_tree.rows), 5)
                self.assertEqual(len(lv.enemy_tree.rows), 5)
                me_row = lv.ally_tree.rows[demo.puuid_of("You#PH")]
                self.assertIn("(you)", me_row["values"][2])
                hidden = lv.enemy_tree.rows[demo.puuid_of("mango#8821")]
                self.assertEqual(hidden["values"][2], "Killjoy (hidden)")

                # clicking a player loads the detail panel
                target = demo.puuid_of("Vex#0042")
                lv.enemy_tree.selection_set([target])
                lv._on_select(lv.enemy_tree)
                pump(app, 10, until=lambda s: "detail" in s)
                self.assertEqual(len(lv.games_tree.rows), 5)
                self.assertIn("net", lv.d_net.opts["text"])
                lv._toggle_watch()
                self.assertTrue(cfg.in_watchlist(target))
                lv._open_profile()                        # jumps to the Profile tab and loads it
                pump(app, 10, until=lambda s: "profile" in s)
                self.assertEqual(app.profile.title.opts["text"], "Vex#0042")
                self.assertGreater(len(app.profile.agent_tree.rows), 0)
                self.assertGreater(len(app.profile.match_tree.rows), 0)

                # post-match tab shows the finished game and the heatmap is ready
                post = app.post
                self.assertIsNotNone(app.current_match)
                self.assertIn(post.result.opts["text"], ("Victory", "Defeat"))
                heat = post.heat
                self.assertTrue(heat.records)
                self.assertIn("kills", heat.count.opts["text"])
                cur = heat.current_filters()
                self.assertEqual(cur["who"], demo.puuid_of("You#PH"))         # defaults to you
                # change filters like a user would
                heat.v_type.set("k")
                heat.v_side.set("A")
                heat.v_round.set("Round 3")
                heat.v_cat.set("Rifles")
                heat._cat_changed()
                f = heat.current_filters()
                self.assertEqual((f["type"], f["side"], f["rnd"], f["cat"]), ("k", "A", 3, "Rifles"))
                heat.v_who.set("Enemy team (all five)")
                heat._changed()
                self.assertEqual(heat.current_filters()["who"], "en")
                # click on a marker
                heat.v_round.set("All rounds")
                heat.v_cat.set("All weapon types")
                heat.v_side.set("all")
                heat.v_type.set("both")
                heat._cat_changed()
                heat.v_who.set(next(k for k in heat.who_keys if k.startswith("You")))
                heat._changed()
                first = heat.vis[0]
                heat._on_click(type("E", (), {"x": first["x"] * 640, "y": first["y"] * 640})())
                self.assertIsNotNone(heat.sel)
                self.assertTrue(heat.card_head.opts["text"])
                heat._on_click(type("E", (), {"x": -500, "y": -500})())
                self.assertIsNone(heat.sel)

                # Match History lists the cached game; reopening it works
                app.history.refresh()
                self.assertEqual(len(app.db.list_matches()), 1)
                mid = app.current_match["match_id"]
                app.db.rename_match(mid, "Scrim Test")
                self.assertEqual(app.db.get_match_title(mid), "Scrim Test")
                app.history.refresh()
                app.current_match = None
                app.open_match(mid)
                self.assertEqual(app.current_match["match_id"], mid)

                # exports from the post-match view (no dialogs: call the writers directly)
                out = Path(tmp) / "x"
                export.save_vtrack(app.current_match, out / "a.vtrack")
                export.export_html(app.current_match, out / "a.html", minimap=app.minimap_path(app.current_match))
                export.export_png(app.current_match, out / "a.png", filters=heat.current_filters(),
                                  minimap_path=app.minimap_path(app.current_match))
                app.import_match(str(out / "a.vtrack"))
                self.assertEqual(app.current_match["match_id"], mid)
                app.import_match(str(out / "a.html"))           # not a vtrack: shows an error, keeps running
                # prefs toggles persist
                post.v_anon.set(True)
                post._prefs()
                self.assertTrue(Config.load(tmp).anonymize_exports)
            finally:
                app.service.stop()
                app.service.join(timeout=3)
                db.close()

    def test_row_formatting_before_and_after_loading(self):
        e = {"puuid": "p", "team": "enemy", "agent": "Jett", "name": "", "tag": "", "incognito": False,
             "party": "A", "party_size": 2, "tier": 0, "rr": 0, "peak_tier": 0, "rating": None, "skill": None,
             "acs": None, "adr": None, "hs": None, "wr": None, "flags": ["Watchlist"], "loaded": False, "is_me": False}
        v = live_view.row_values(e)
        self.assertEqual(v[1], "A·2")
        self.assertEqual(v[3], "...")
        self.assertEqual(v[5], "...")
        e.update({"loaded": True, "tier": 20, "rr": 78, "peak_tier": 21, "rating": 1.09, "skill": 1820.0,
                  "acs": 221.4, "adr": 142.2, "hs": 0.21, "wr": 0.51, "name": "nightowl", "tag": "PH1"})
        v = live_view.row_values(e)
        self.assertEqual(v[2], "nightowl#PH1")
        self.assertEqual(v[3], "Diamond 3 · 78")
        self.assertEqual(v[4], "Asc 1")
        self.assertEqual(v[5], "1.09")
        self.assertIn("1,820", v[6])
        self.assertIn("Asc 1 · 20 RR", v[6])
        self.assertEqual(v[7], "221 / 142")
        self.assertEqual(v[8], "21%")
        self.assertEqual(v[10], "Watchlist")
        self.assertEqual(live_view.row_tags(e), ("hi",))

    def test_theme_helpers(self):
        self.assertEqual(theme.signed(5), "+5")
        self.assertEqual(theme.signed(-3), "−3")
        self.assertEqual(theme.signed(0), "0")
        self.assertEqual(theme.fmt_rating(None), "...")
        self.assertEqual(theme.fmt_pct(None), "-")
        self.assertEqual(theme.fmt_rank(0), "Unranked")
        self.assertEqual(theme.rating_tag(1.2), "hi")
        self.assertEqual(theme.rating_tag(0.9), "lo")
        self.assertEqual(theme.rating_tag(1.0), "norm")
        self.assertEqual(theme.fmt_skill(1820), "1,820 ≈ Asc 1 · 20 RR")


if __name__ == "__main__":
    unittest.main()


class FakeTray:
    def __init__(self, post):
        self.post, self.notes, self.started, self.stopped = post, [], False, False

    def start(self):
        self.started = True
        return True

    def notify(self, text):
        self.notes.append(text)

    def stop(self):
        self.stopped = True


class UiFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(_root=self.tmp.name)
        self.cfg.ensure_dirs()
        self.cfg.poll_seconds = self.cfg.idle_poll_seconds = 0.05
        self.db = Database(self.cfg.db_path)
        self.static = demo.demo_static(cache_dir=self.cfg.cache_dir)
        self.src = DemoSource(static=self.static, phases=[("menu", 60)])
        self.trays = []

    def tearDown(self):
        self.app.service.stop()
        self.app.service.join(timeout=3)
        self.db.close()
        self.tmp.cleanup()

    def make(self, tray=True):
        def factory(post):
            if not tray:
                return None
            t = FakeTray(post)
            self.trays.append(t)
            return t
        self.app = app_mod.App(self.cfg, self.db, self.static, lambda: self.src, demo=True, tray_factory=factory)
        pump(self.app, 5, until=lambda s: "connected" in s)
        return self.app

    def test_layout_settings_are_saved_and_applied(self):
        app = self.make(tray=False)
        app.cfg.layout_mode = "compact"
        app.cfg.interface_scale = 110
        app.cfg.table_density = "comfortable"
        app.cfg.sidebar_mode = "collapsed"
        app._settings_dialog()
        top = next(child for child in app.children if isinstance(child, app_mod.tk.Toplevel))
        pending = list(top.children)
        save_button = None
        while pending:
            widget = pending.pop()
            pending.extend(getattr(widget, "children", []))
            if getattr(widget, "opts", {}).get("text") == "Save & Apply":
                save_button = widget
                break
        self.assertIsNotNone(save_button)
        save_button.opts["command"]()
        saved = Config.load(self.tmp.name)
        self.assertEqual(saved.layout_mode, "compact")
        self.assertEqual(saved.interface_scale, 110)
        self.assertEqual(saved.table_density, "comfortable")
        self.assertEqual(saved.sidebar_mode, "collapsed")

    def test_history_match_opens_from_profile_and_live_detail(self):
        app = self.make()
        self.assertEqual(app.my_puuid, demo.puuid_of("You#PH"))
        vex = demo.puuid_of("Vex#0042")
        app.open_profile(vex, "Vex", "0042")
        pump(app, 10, until=lambda s: "profile" in s)
        prof = app.profile
        self.assertEqual(prof.puuid, vex)
        mid = next(iter(prof.match_tree.rows))
        self.assertTrue(mid.startswith("demo-past-"))                       # rows are keyed by match id
        prof.match_tree.selection_set([mid])
        prof._open_match()
        pump(app, 10, until=lambda s: "open_match" in s)
        m = app.current_match
        self.assertEqual(m["match_id"], mid)
        self.assertEqual(m["me"], vex)
        post = app.post
        self.assertIn("Viewing from", post.meta.opts["text"])
        # the live detail list opens games too
        lv = app.live
        lv.selected = vex
        lv.games_tree.selection_set([mid])
        lv._open_game()
        pump(app, 10, until=lambda s: "open_match" in s)
        self.assertEqual(app.current_match["me"], vex)

    def test_postmatch_refresh_preserves_selected_heatmap_tab(self):
        app = self.make()
        app.service.open_match("demo-past-1", app.my_puuid)
        pump(app, 10, until=lambda s: "open_match" in s)
        post = app.post

        # Heatmap is the second notebook tab.
        post.tabs.select(1)
        self.assertEqual(post.tabs.select(), 1)

        # A background refresh (e.g. minimap/icon completion) calls show() again.
        post.show(app.current_match, app.minimap_path(app.current_match))
        self.assertEqual(post.tabs.select(), 1)

        # A genuinely new match still opens on Leaderboard.
        new_match = dict(app.current_match)
        new_match["match_id"] = "new-postmatch-id"
        post.show(new_match, app.minimap_path(new_match))
        self.assertEqual(post.tabs.select(), 0)

    def test_heatmap_shows_names_colors_and_labels(self):
        app = self.make()
        app.service.open_match("demo-past-1", app.my_puuid)
        pump(app, 10, until=lambda s: "open_match" in s)
        heat = app.post.heat
        legend_text = [w.opts.get("text", "") for row in heat.legend.children for w in row.children] + \
                      [w.opts.get("text", "") for w in heat.legend.children]
        joined = " ".join(legend_text)
        for name in ("You#PH", "Vex#0042", "sable#310"):
            self.assertIn(name, joined)
        self.assertIn("(you)", joined)
        self.assertIn("B·2", joined)                                         # your party is visible
        self.assertIn("Your team", joined)
        picker = list(heat.who_keys)
        self.assertTrue(any(l.startswith("You#PH (you)") and "·" in l and "K" in l for l in picker))
        self.assertTrue(any("B·2" in l for l in picker))
        # default: just you, colored by result; legend click switches to that player
        self.assertEqual(heat.v_color.get(), "result")
        heat.v_who.set("Everyone")
        heat._who_changed()
        self.assertEqual(heat.v_color.get(), "player")
        self.assertEqual(heat.current_filters()["color"], "player")
        vex = demo.puuid_of("Vex#0042")
        heat._pick_player(vex)
        self.assertEqual(heat.current_filters()["who"], vex)
        self.assertEqual(heat.v_color.get(), "result")
        self.assertTrue(heat.vis and all(r["who"] == vex for r in heat.vis if r["t"] != "p" or True))
        heat.v_labels.set(True)
        heat._changed()                                                      # draws name labels without error
        self.assertNotEqual(heat._color({"t": "k", "who": vex}), heat._color({"t": "d", "who": vex}))
        heat.v_color.set("player")
        self.assertEqual(heat._color({"t": "k", "who": vex}), heat._color({"t": "d", "who": vex}))
        self.assertEqual(heat._color({"t": "p", "who": vex}), "#f5a524")

    def test_closing_the_window_keeps_running_in_the_tray(self):
        app = self.make()
        tray = self.trays[0]
        self.assertTrue(tray.started)
        app.close_window()
        self.assertTrue(app.hidden)
        self.assertEqual(len(tray.notes), 1)
        self.assertFalse(app.service._halt.is_set())                         # still tracking
        app.close_window()
        self.assertEqual(len(tray.notes), 1)                                 # only told once
        app._handle("tray", "open")
        self.assertFalse(app.hidden)
        app.close_window()
        # a finished match while hidden: notification, no popup by default
        m = demo_match(self.static, app.my_puuid)
        app._handle("postmatch", m)
        self.assertTrue(app.hidden)
        self.assertIn(("Victory" if m["result"]["won"] else "Defeat"), tray.notes[-1])
        self.cfg.popup_on_match_end = True
        app._handle("postmatch", m)
        self.assertFalse(app.hidden)
        app._handle("tray", "quit")
        self.assertTrue(app.service._halt.is_set())
        self.assertTrue(tray.stopped)

    def test_no_tray_minimises_instead(self):
        app = self.make(tray=False)
        self.assertIsNone(app.tray)
        app.close_window()
        self.assertFalse(app.hidden)
        self.assertFalse(app.service._halt.is_set())

    def test_background_off_quits_on_close(self):
        self.cfg.run_in_background = False
        app = self.make()
        self.assertEqual(self.trays, [])
        app.close_window()
        self.assertTrue(app.service._halt.is_set())

    def test_agent_select_enemy_slot_text(self):
        app = self.make()
        lobby = {"phase": "pregame", "match_id": "x", "map": "Demo Map", "queue": "competitive", "final": True,
                 "loaded": 5, "total": 5, "win": None, "enemy_size": 5, "ally_avg": 1520.0, "players": []}
        app.live.update_lobby(lobby)
        self.assertIn("5 players", app.live.enemy_lbl.opts["text"])
        self.assertIn("when the match loads", app.live.enemy_lbl.opts["text"])
        self.assertIn("1,520", app.live.conf.opts["text"])


class _PngHttp:
    """Pretends to be the icon CDN: returns a real PNG, and counts requests."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def get(self, url, timeout=None, **kw):
        self.calls.append(url)
        if self.fail:
            raise ConnectionError("offline")
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGBA", (40, 40), (200, 50, 50, 255)).save(buf, "PNG")
        return type("R", (), {"content": buf.getvalue(), "raise_for_status": lambda self: None})()


def _luminance(hex_color):
    def chan(i):
        v = int(hex_color[i:i + 2], 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * chan(1) + 0.7152 * chan(3) + 0.0722 * chan(5)


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class ReportedFixesTests(unittest.TestCase):
    """Match History icons, heatmap marker size, and readable dropdowns."""

    def setUp(self):
        from valtracker.ui import history_view
        self.hv = history_view
        for coll in (history_view._INFLIGHT, history_view._FAILED, history_view._PHOTOS):
            coll.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(_root=self.tmp.name)
        self.cfg.ensure_dirs()
        self.cfg.poll_seconds = self.cfg.idle_poll_seconds = 0.05
        self.db = Database(self.cfg.db_path)
        self.static = demo.demo_static(cache_dir=self.cfg.cache_dir)
        self.src = DemoSource(static=self.static, phases=[("menu", 60)])
        self.app = app_mod.App(self.cfg, self.db, self.static, lambda: self.src, demo=True, tray_factory=lambda post: None)
        pump(self.app, 5, until=lambda s: "connected" in s)
        self.orig_photo = history_view._photo

    def tearDown(self):
        self.hv._photo = self.orig_photo
        self.app.service.stop()
        self.app.service.join(timeout=3)
        self.db.close()
        self.tmp.cleanup()

    def _save(self, mid, strip_agent_ids=False):
        from valtracker import matchdata
        data = matchdata.process_raw_match(demo.make_raw_match(41, mid, 1_700_000_000_000), self.static, self.app.my_puuid)
        if strip_agent_ids:                                   # how matches saved by older versions look
            for p in data["players"]:
                p.pop("agent_id", None)
        self.db.save_match(data)
        return data

    def _draw_images(self, card):
        calls = []
        self.hv._photo = lambda path, size: ("PHOTO", Path(path).name)
        card.canvas.create_image = lambda *a, **k: calls.append(k.get("image"))
        card._draw(object())                                  # a <Configure> event is passed in: must not raise
        return calls

    def test_postmatch_icon_redraw_does_not_rebuild_or_restart_loading(self):
        view = self.app.post
        view.match = {"match_id": "icons-only", "players": [{"puuid": "player-1"}]}
        old_badge = postmatch_view._leaderboard_badge
        old_show = view.show
        try:
            postmatch_view._leaderboard_badge = lambda app, player, size: ("badge", player["puuid"])
            view.show = lambda *a, **k: (_ for _ in ()).throw(AssertionError("redraw must not call show()"))
            view.redraw_icons()
            self.assertEqual(view._leaderboard_photos, [("badge", "player-1")])
        finally:
            postmatch_view._leaderboard_badge = old_badge
            view.show = old_show

    def test_new_matches_store_each_players_agent_id(self):
        data = self._save("new-1")
        self.assertTrue(all(p.get("agent_id") for p in data["players"]))
        self.assertEqual(self.db.get_match("new-1")["players"][0]["agent_id"], data["players"][0]["agent_id"])

    def test_history_shows_icons_even_for_matches_saved_without_agent_ids(self):
        self._save("old-1", strip_agent_ids=True)
        demo.write_demo_agent_icons(self.cfg.cache_dir)
        self.app.history.refresh()
        calls = self._draw_images(self.app.history._cards["old-1"])
        self.assertEqual(len(calls), 10)                                          # 5 allies + 5 enemies
        self.assertTrue(all(c[0] == "PHOTO" for c in calls))
        self.assertIn("agent_demo-agent-phoenix.png", {c[1] for c in calls})
        self.assertEqual(self.static.agent_uuid_by_name("Phoenix"), "demo-agent-phoenix")
        self.assertIsNone(self.static.agent_uuid_by_name("Nobody"))

    def test_drawing_never_downloads_and_missing_icons_arrive_in_the_background(self):
        http = _PngHttp()
        self.static.http = http
        self._save("dl-1")
        self.app.history.refresh()                                                # no icons cached yet
        card = self.app.history._cards["dl-1"]
        before = len(http.calls)
        card._draw()
        card._draw(object())
        self.assertEqual(len(http.calls), before)                                  # drawing itself makes no requests
        self.assertEqual(len(self._draw_images(card)), 0)                          # placeholders until the files exist
        end = time.time() + 5
        got = []
        while time.time() < end and not got:
            try:
                kind, payload = self.app.q.get(timeout=0.1)
            except queue.Empty:
                continue
            if kind == "history_icons":
                got.append(kind)
        self.assertEqual(got, ["history_icons"])                                    # worker asks the Tk thread to redraw
        self.assertTrue(self.static.agent_icon_path("demo-agent-phoenix"))
        self.app._handle("history_icons", None)
        self.assertEqual(len(self._draw_images(card)), 10)
        n = len(http.calls)
        self.app.history.refresh()                                                  # a second refresh does not re-download
        time.sleep(0.2)
        self.assertEqual(len(http.calls), n)

    def test_failed_downloads_are_not_retried_every_refresh(self):
        http = _PngHttp(fail=True)
        self.static.http = http
        self._save("fail-1")
        self.app.history.refresh()
        time.sleep(0.5)
        n = len(http.calls)
        self.assertGreater(n, 0)
        self.assertTrue(self.hv._FAILED)
        self.app.history.refresh()
        time.sleep(0.3)
        self.assertEqual(len(http.calls), n)
        self.assertTrue(self.app.q.empty() or all(k != "history_icons" for k, _ in list(self.app.q.queue)))

    def test_heatmap_markers_are_small_and_the_size_is_adjustable(self):
        self._save("hm-1")
        self.app.service.open_match("hm-1", self.app.my_puuid)
        pump(self.app, 10, until=lambda s: "open_match" in s)
        heat = self.app.post.heat
        heat.v_who.set("Everyone")
        heat._who_changed()
        ovals = []
        heat.canvas.create_oval = lambda *a, **k: ovals.append((a, k))
        heat.redraw()

        def sizes():
            markers = [a[2] - a[0] for a, k in ovals if k.get("outline") == "black"]
            glows = [a[2] - a[0] for a, k in ovals if k.get("stipple")]
            return max(markers), max(glows)
        marker, glow = sizes()
        self.assertLessEqual(marker, 12)                                           # was 16 (10 on busy maps)
        self.assertLessEqual(glow, 26)                                             # was 52
        ovals.clear()
        heat.v_size.set(2.0)
        heat.redraw()
        big, _ = sizes()
        self.assertAlmostEqual(big, marker * 2, delta=1)
        heat.v_size.set(99)
        self.assertEqual(heat._scale(), 2.5)
        heat.v_size.set(0.01)
        self.assertEqual(heat._scale(), 0.4)
        heat.v_size.set("bad")
        self.assertEqual(heat._scale(), 1.0)

    def test_dropdowns_and_selectors_are_readable(self):
        from valtracker.ui import widgets
        ttk = sys.modules["tkinter.ttk"]
        rec = {"conf": {}, "map": {}}

        class Style:
            def __init__(self, root=None):
                pass

            def theme_use(self, name):
                pass

            def configure(self, name, **kw):
                rec["conf"].setdefault(name, {}).update(kw)

            def map(self, name, **kw):
                rec["map"].setdefault(name, {}).update(kw)
        old = ttk.Style
        ttk.Style = Style
        try:
            widgets.apply_style(faketk.Widget())
        finally:
            ttk.Style = old
        m = rec["map"]["TCombobox"]
        state = lambda key, st: dict(m[key])[st]
        self.assertEqual(state("fieldbackground", "readonly"), theme.PANEL2)        # dark, not light grey
        self.assertEqual(state("foreground", "readonly"), theme.TEXT)
        self.assertGreaterEqual(_contrast(state("foreground", "readonly"), state("fieldbackground", "readonly")), 7)
        self.assertGreaterEqual(_contrast(state("foreground", "disabled"), state("fieldbackground", "disabled")), 3.5)
        self.assertEqual(state("selectbackground", "readonly"), state("fieldbackground", "readonly"))   # no blue block
        self.assertEqual(state("selectforeground", "readonly"), theme.TEXT)
        for name in ("TCheckbutton", "TRadiobutton"):
            ind = dict(rec["map"][name]["indicatorcolor"])
            self.assertEqual(ind["selected"], theme.PURPLE)
            self.assertNotEqual(ind["!selected"], ind["selected"])


def demo_match(static, me):
    from valtracker import matchdata
    from valtracker.model.baselines import Baselines as B
    return matchdata.process_raw_match(demo.make_raw_match(3, "m-x", 1_700_000_000_000), static, me, baselines=B(None))
