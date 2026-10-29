import tempfile
import time
import unittest
from pathlib import Path

from valtracker import demo
from valtracker.db import Database
from valtracker.demo_source import DemoSource
from valtracker.model.baselines import Baselines
from valtracker.services.stats import StatsService


class Counting:
    """Wraps a source and counts calls, to prove caching works."""

    def __init__(self, src):
        self.src, self.counts = src, {}

    def __getattr__(self, name):
        fn = getattr(self.src, name)
        if not callable(fn):
            return fn

        def call(*a, **k):
            self.counts[name] = self.counts.get(name, 0) + 1
            return fn(*a, **k)
        return call


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "s.db")
        self.static = demo.demo_static()
        self.src = Counting(DemoSource(static=self.static))
        self.st = StatsService(self.src, self.static, self.db, Baselines(self.db))
        self.me = demo.puuid_of("You#PH")
        self.owl = demo.puuid_of("nightowl#PH1")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_mmr_peak_and_acts(self):
        info = self.st.mmr_info(self.owl)
        self.assertEqual((info["tier"], info["rr"]), (20, 78))
        self.assertGreaterEqual(info["peak_tier"], info["tier"])
        self.assertEqual(info["current_season"], "demo-act5")
        self.assertEqual([a["season"] for a in info["acts"]], ["demo-act2", "demo-act3", "demo-act4"])
        self.assertIsNone(info["acts"][1]["tier"])                    # the act with no games
        self.assertEqual(info["acts"][1]["games"], 0)
        self.assertEqual(info["act_games"], 46)
        self.assertAlmostEqual(info["wr"], 23 / 46)
        self.assertEqual(info["current_label"], "Episode 1 · Act 5")
        # peak comes from the earliest act that reached the highest tier
        vex = self.st.mmr_info(demo.puuid_of("Vex#0042"))
        self.assertEqual(vex["peak_season"], "demo-act2")
        self.assertEqual(vex["peak_label"], "Episode 1 · Act 2")

    def test_peak_is_current_when_highest(self):
        raw = self.src.mmr(self.owl)
        for s in raw["QueueSkills"]["competitive"]["SeasonalInfoBySeasonID"].values():
            s["CompetitiveTier"], s["WinsByTier"] = 5, None
        info = self.st.parse_mmr(raw)
        self.assertEqual(info["peak_tier"], 20)
        self.assertEqual(info["peak_season"], "demo-act5")
        self.assertEqual(info["peak_rr"], 78)

    def test_parse_handles_empty(self):
        info = self.st.parse_mmr(None)
        self.assertEqual(info["tier"], 0)
        info = self.st.parse_mmr({"QueueSkills": None})
        self.assertEqual(info["peak_tier"], 0)
        self.assertEqual(len(info["acts"]), 3)                        # three acts, all "no games"
        self.assertTrue(all(a["tier"] is None and a["games"] == 0 for a in info["acts"]))
        self.assertEqual(self.st.parse_mmr(None)["acts"], [])

    def test_summary(self):
        s = self.st.summary(self.me)
        self.assertEqual(s["games"], 3)
        self.assertTrue(0.7 <= s["rating"] <= 1.5)
        self.assertGreater(s["acs"], 50)
        self.assertTrue(0 <= s["hs"] <= 1 and 0 <= s["kast"] <= 1)
        self.assertGreater(s["kd"], 0)
        self.assertIsNotNone(s["last_played_ms"])
        # cached: asking again does not re-download the matches
        before = self.src.counts.get("match_details", 0)
        self.st.summary(self.me)
        self.assertEqual(self.src.counts.get("match_details", 0), before)

    def test_matches_shared_between_players(self):
        self.st.summary(self.me)
        n = self.src.counts["match_details"]
        self.st.summary(self.owl)                       # same three matches, already cached
        self.assertEqual(self.src.counts["match_details"], n)

    def test_player_info_skill(self):
        info = self.st.player_info(self.owl)
        self.assertIsNotNone(info["skill"])
        self.assertGreater(info["skill"], 1600)

    def test_detail(self):
        d = self.st.detail(self.me, 5)
        self.assertEqual(len(d["games"]), 5)
        self.assertEqual(d["net_rr"], sum(g["rr"] for g in d["games"]))
        self.assertEqual(d["rr_series"], [g["rr"] for g in reversed(d["games"])])
        g = d["games"][0]
        self.assertIn(g["result"], ("W", "L"))
        self.assertEqual(g["map"], "Demo Map")
        self.assertRegex(g["score"], r"^\d+–\d+$")
        for g in d["games"]:
            self.assertEqual(g["result"] == "W", g["rr"] > 0)     # demo: wins gain RR, losses lose it

    def test_profile(self):
        seen = []
        p = self.st.profile(self.me, depth=6, progress=lambda i, n: seen.append((i, n)))
        self.assertEqual(seen[-1], (6, 6))
        self.assertEqual(p["stats"]["games"], 6)
        self.assertEqual(sum(a["games"] for a in p["agents"]), 6)
        self.assertEqual(p["agents"], sorted(p["agents"], key=lambda a: (-a["games"], a["agent"])))
        self.assertTrue(p["weapons"] and abs(sum(w["share"] for w in p["weapons"]) - sum(w["share"] for w in p["weapons"])) < 1e-9)
        self.assertLessEqual(len(p["weapons"]), 3)
        self.assertEqual(len(p["matches"]), 6)
        self.assertTrue(0 <= p["stats"]["wr"] <= 1)

    def test_failed_match_does_not_crash(self):
        class Broken(DemoSource):
            def match_details(self, mid):
                raise RuntimeError("boom")
        st = StatsService(Broken(static=self.static), self.static, self.db, Baselines(self.db))
        s = st.summary(self.me)
        self.assertEqual(s["games"], 0)
        self.assertIsNone(s["rating"])
        d = st.detail(self.me)
        self.assertEqual(d["games"][0]["agent"], "?")


if __name__ == "__main__":
    unittest.main()
