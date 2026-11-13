import unittest

from valtracker import demo
from valtracker.demo_source import DemoSource


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class DemoSourceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.src = DemoSource(clock=self.clock, phases=[("menu", 2), ("pregame", 3), ("ingame", 10), ("menu", 5)],
                              details_delay=2.0)

    def test_phase_cycle(self):
        seq = []
        for t in (0, 1.9, 2, 4.9, 5, 14.9, 15, 19.9, 20, 22, 25):
            self.clock.t = 1_000_000.0 + t
            seq.append(self.src.state())
        self.assertEqual([s[0] for s in seq],
                         ["menu", "menu", "pregame", "pregame", "ingame", "ingame", "menu", "menu", "menu", "pregame", "ingame"])
        self.assertEqual(seq[2][1], "demo-live-0")
        self.assertEqual(seq[9][1], "demo-live-1")
        self.assertIsNone(seq[0][1])

    def test_lobby_pregame_has_only_allies(self):
        pre = self.src.lobby("pregame", "x")
        self.assertEqual(len(pre["players"]), 5)
        self.assertTrue(all(p["team"] == "ally" for p in pre["players"]))
        ing = self.src.lobby("ingame", "x")
        self.assertEqual(len(ing["players"]), 10)
        self.assertEqual(sum(1 for p in ing["players"] if p["team"] == "enemy"), 5)
        self.assertEqual([p["puuid"] for p in ing["players"] if p["incognito"]], [demo.puuid_of("mango#8821")])

    def test_names_like_the_real_name_service(self):
        names = self.src.names([p["puuid"] for p in self.src.roster])
        self.assertEqual(len(names), 10)
        self.assertEqual(names[demo.puuid_of("mango#8821")], ("mango", "8821"))
        self.assertEqual(self.src.names(["stranger"]), {})

    def test_updates_chain_is_consistent(self):
        ups = self.src.updates(self.src.puuid, 0, 5)
        self.assertEqual(len(ups), 5)
        for newer, older in zip(ups, ups[1:]):
            self.assertEqual(newer["RankedRatingBeforeUpdate"], older["RankedRatingAfterUpdate"])
            self.assertEqual(newer["TierBeforeUpdate"], older["TierAfterUpdate"])
        for u in ups:
            self.assertEqual(u["RankedRatingAfterUpdate"] + 100 * u["TierAfterUpdate"],
                             u["RankedRatingBeforeUpdate"] + 100 * u["TierBeforeUpdate"] + u["RankedRatingEarned"])
        self.assertEqual(len(self.src.updates(self.src.puuid, 1, 3)), 2)

    def test_mmr_shape(self):
        d = self.src.mmr(demo.puuid_of("nightowl#PH1"))
        seasons = d["QueueSkills"]["competitive"]["SeasonalInfoBySeasonID"]
        self.assertEqual(set(seasons), {"demo-act1", "demo-act2", "demo-act3", "demo-act4", "demo-act5"})
        self.assertEqual(seasons["demo-act3"]["NumberOfGames"], 0)
        self.assertIsNone(self.src.mmr("nobody"))

    def test_match_details_delay_and_hidden(self):
        self.clock.t = 1_000_000.0 + 16            # match ended at t=15, delay 2 s
        self.assertIsNone(self.src.match_details("demo-live-0"))
        self.clock.t = 1_000_000.0 + 18
        raw = self.src.match_details("demo-live-0")
        self.assertIsNotNone(raw)
        names = {p["subject"]: p["gameName"] for p in raw["players"]}
        self.assertEqual(names[demo.puuid_of("mango#8821")], "")
        self.assertEqual(raw["matchInfo"]["matchId"], "demo-live-0")
        again = self.src.match_details("demo-live-0")
        self.assertEqual(again["matchInfo"]["gameStartMillis"], raw["matchInfo"]["gameStartMillis"])
        self.assertIsNone(self.src.match_details("bogus"))

    def test_history_lists_past_and_live(self):
        h = self.src.history(self.src.puuid, 0, 3)
        self.assertEqual(len(h), 3)
        self.assertTrue(h[0]["GameStartTime"] > h[1]["GameStartTime"])
        self.assertIsNotNone(self.src.match_details(h[0]["MatchID"]))


if __name__ == "__main__":
    unittest.main()
