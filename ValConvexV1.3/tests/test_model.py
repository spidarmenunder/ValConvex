import math
import unittest

from valtracker import ranks
from valtracker.model import baselines, rating, winprob


class RankTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ranks.tier_name(3), "Iron 1")
        self.assertEqual(ranks.tier_name(20), "Diamond 3")
        self.assertEqual(ranks.tier_name(21), "Ascendant 1")
        self.assertEqual(ranks.tier_name(24), "Immortal 1")
        self.assertEqual(ranks.tier_name(27), "Radiant")
        self.assertEqual(ranks.tier_name(0), "Unranked")

    def test_points_roundtrip(self):
        self.assertEqual(ranks.tier_points(20, 78), 1778)
        self.assertEqual(ranks.tier_points(3, 0), 0)
        self.assertIsNone(ranks.tier_points(0, 50))
        self.assertEqual(ranks.rank_from_points(1820), (21, 20))   # Ascendant 1 20 RR
        self.assertEqual(ranks.rank_from_points(1778), (20, 78))
        self.assertEqual(ranks.rank_from_points(0), (3, 0))

    def test_bracket(self):
        self.assertEqual(ranks.bracket(3), "low")
        self.assertEqual(ranks.bracket(12), "mid")
        self.assertEqual(ranks.bracket(20), "high")
        self.assertEqual(ranks.bracket(21), "top")
        self.assertEqual(ranks.bracket(0), "unranked")


class RatingTests(unittest.TestCase):
    def test_average_player_is_one(self):
        base = (200.0, 130.0)
        s = rating.game_score(200, 130, base)
        self.assertAlmostEqual(s, 1.0)
        self.assertAlmostEqual(rating.player_rating([s, s, s]), 1.0)

    def test_mock_example(self):
        # matches the design mockup: 1.13 / 1.125 / 1.02 -> weighted 1.1065 -> shrunk ~1.085
        r = rating.player_rating([1.13, 1.125, 1.02])
        self.assertAlmostEqual(r, 1 + 0.1065 * 0.8, places=3)

    def test_weights_newest_first(self):
        raw, n = rating.combine([1.2, 1.0, 1.0])
        self.assertEqual(n, 3)
        self.assertAlmostEqual(raw, 1.1)

    def test_fewer_games_renormalise_and_shrink_more(self):
        raw, n = rating.combine([1.2])
        self.assertEqual((raw, n), (1.2, 1))
        self.assertLess(rating.player_rating([1.2]), rating.player_rating([1.2, 1.2, 1.2]))
        self.assertIsNone(rating.player_rating([]))

    def test_clamped(self):
        self.assertEqual(rating.player_rating([5, 5, 5]), rating.RATING_MAX)
        self.assertEqual(rating.player_rating([0.01, 0.01, 0.01]), rating.RATING_MIN)

    def test_agent_relative(self):
        # a 0.9 K/D style Sage vs a Jett: same relative performance -> same rating
        sage = rating.game_score(190, 125, (180, 118))
        jett = rating.game_score(235, 155, (222, 146))
        self.assertAlmostEqual(sage, jett, delta=0.02)

    def test_baselines_defaults_and_learning(self):
        b = baselines.Baselines(None)
        acs, adr = b.get("Jett", "Duelist", "high")
        self.assertAlmostEqual(acs, 222 * 0.99)

        class FakeDB:
            def baseline_obs(self, a, br):
                return 40, 40 * 260.0, 40 * 160.0

        b2 = baselines.Baselines(FakeDB())
        acs2, _ = b2.get("Jett", "Duelist", "mid")
        self.assertAlmostEqual(acs2, (40 * 222 + 40 * 260) / 80)


class WinProbTests(unittest.TestCase):
    def test_effective_skill_matches_mockup(self):
        # Diamond 3 78 RR, peak Ascendant 1, rating 1.09 -> ~1,820
        v = winprob.effective_skill(20, 78, 21, 0, 1.09)
        self.assertAlmostEqual(v, 1778 + 0.25 * (1800 - 1778) + 36, places=6)
        self.assertEqual(round(v), 1820)
        self.assertEqual(ranks.rank_from_points(v), (21, 20))

    def test_adj_cap(self):
        v = winprob.effective_skill(20, 0, 20, 0, 1.5)
        self.assertEqual(v, 1700 + 150)

    def test_unranked(self):
        self.assertIsNone(winprob.effective_skill(0, 0, 0, 0, 1.0))

    def test_party_bonus(self):
        self.assertEqual(winprob.party_bonus(["A", "A", "C", "C", "C"]), 5 + 12)
        self.assertEqual(winprob.party_bonus([None] * 5), 0)
        self.assertEqual(winprob.party_bonus(["A"] * 5), 25)

    def test_equal_teams_50_50(self):
        team = [{"skill": 1500, "party": None, "games": 3}] * 5
        r = winprob.win_probability(team, team)
        self.assertAlmostEqual(r["p_me"], 0.5)
        self.assertEqual(r["confidence"], "high")

    def test_mockup_lobby(self):
        mine = [1666, 1564, 1443, 1362, 1568]
        theirs = [2029, 1820, 1522, 1406, 1272]
        m = [{"skill": v, "party": p, "games": 3} for v, p in zip(mine, ["B", "B", None, None, None])]
        t = [{"skill": v, "party": p, "games": 3} for v, p in zip(theirs, ["A", "A", "C", "C", "C"])]
        r = winprob.win_probability(m, t)
        self.assertAlmostEqual(r["my_avg"], sum(mine) / 5)
        self.assertEqual(r["en_bonus"], 17)
        self.assertEqual(r["my_bonus"], 5)
        expected = 1 / (1 + math.exp(((sum(theirs) + 17 * 5) / 5 - (sum(mine) + 5 * 5) / 5) / 250))
        # bonus is applied to the team average (not per player)
        expected = 1 / (1 + math.exp(-((sum(mine) / 5 + 5) - (sum(theirs) / 5 + 17)) / 250))
        self.assertAlmostEqual(r["p_me"], expected)
        self.assertLess(r["p_me"], 0.5)

    def test_unknown_uses_lobby_average_and_lowers_confidence(self):
        mine = [{"skill": 1500, "party": None, "games": 3}] * 4 + [{"skill": None, "party": None, "games": 0}]
        theirs = [{"skill": 1500, "party": None, "games": 3}] * 5
        r = winprob.win_probability(mine, theirs)
        self.assertAlmostEqual(r["p_me"], 0.5)
        self.assertEqual(r["confidence"], "medium")
        all_unknown = [{"skill": None, "party": None, "games": 0}] * 5
        r2 = winprob.win_probability(all_unknown, all_unknown)
        self.assertAlmostEqual(r2["p_me"], 0.5)
        self.assertEqual(r2["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
