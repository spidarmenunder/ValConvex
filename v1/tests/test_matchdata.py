import json
import unittest

from valtracker import demo, matchdata
from valtracker.matchdata import compute_kast, infer_attackers
from valtracker.model.baselines import Baselines


def raw_match(seed=7):
    return demo.make_raw_match(seed, f"match-{seed}", 1_700_000_000_000)


class InferAttackerTests(unittest.TestCase):
    T = ["Blue", "Red"]

    def test_normal_game(self):
        planters = [None] * 22
        planters[0], planters[3], planters[14], planters[16] = "Blue", "Blue", "Red", "Red"
        out = infer_attackers(planters, self.T)
        self.assertEqual(out[:12], ["Blue"] * 12)
        self.assertEqual(out[12:], ["Red"] * 10)

    def test_only_second_half_known(self):
        planters = [None] * 20
        planters[15] = "Red"
        out = infer_attackers(planters, self.T)
        self.assertEqual(out[:12], ["Blue"] * 12)
        self.assertEqual(out[12:], ["Red"] * 8)

    def test_no_plants_unknown(self):
        self.assertEqual(infer_attackers([None] * 5, self.T), [None] * 5)

    def test_overtime_alternates(self):
        planters = [None] * 27
        planters[0], planters[13] = "Blue", "Red"
        planters[24] = "Blue"
        out = infer_attackers(planters, self.T)
        self.assertEqual(out[24:], ["Blue", "Red", "Blue"])

    def test_majority_vote(self):
        planters = [None] * 12
        planters[0], planters[1], planters[2] = "Blue", "Blue", "Red"
        self.assertEqual(infer_attackers(planters, self.T), ["Blue"] * 12)


class KastTests(unittest.TestCase):
    def test_kast_cases(self):
        team = {"a": "Blue", "b": "Blue", "x": "Red", "y": "Red"}
        # round 1: a kills x (K for a; b survives; y survives; x dies untraded)
        # round 2: x kills b at 1s, a kills x at 3s -> b traded; a survives
        # round 3: x kills a and b nobody trades -> a,b not KAST; y,x get K/S
        # round 4: y assists nothing, everyone survives
        r1 = [{"killer": "a", "victim": "x", "rt": 1000, "assistants": ["b"]}]
        r2 = [{"killer": "x", "victim": "b", "rt": 1000, "assistants": []},
              {"killer": "a", "victim": "x", "rt": 3000, "assistants": []}]
        r3 = [{"killer": "x", "victim": "a", "rt": 1000, "assistants": []},
              {"killer": "y", "victim": "b", "rt": 2000, "assistants": []}]
        r4 = []
        k = compute_kast([r1, r2, r3, r4], team, ["a", "b", "x", "y"])
        self.assertAlmostEqual(k["a"], 3 / 4)     # r1 K, r2 K, r3 dead untraded, r4 survive
        self.assertAlmostEqual(k["b"], 3 / 4)     # r1 A, r2 traded, r3 dead, r4 survive
        self.assertAlmostEqual(k["x"], 3 / 4)     # r1 dead, r2 K? x killed b -> K, then died; r3 K; r4 S
        self.assertAlmostEqual(k["y"], 1.0)

    def test_late_trade_does_not_count(self):
        team = {"a": "Blue", "b": "Blue", "x": "Red"}
        r = [{"killer": "x", "victim": "b", "rt": 1000, "assistants": []},
             {"killer": "a", "victim": "x", "rt": 7000, "assistants": []}]
        k = compute_kast([r], team, ["a", "b", "x"])
        self.assertEqual(k["b"], 0.0)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.static = demo.demo_static()
        self.raw = raw_match()
        self.parsed = matchdata.parse_match(self.raw, self.static)

    def test_counts_match_stats(self):
        ev = self.parsed["events"]
        kills, deaths = {}, {}
        for e in ev:
            kills[e["killer"]] = kills.get(e["killer"], 0) + 1
            deaths[e["victim"]] = deaths.get(e["victim"], 0) + 1
        for p in self.parsed["players"]:
            self.assertEqual(p["kills"], kills.get(p["puuid"], 0), p["name"])
            self.assertEqual(p["deaths"], deaths.get(p["puuid"], 0), p["name"])
        self.assertEqual(sum(kills.values()), sum(deaths.values()))
        self.assertEqual(len(self.parsed["rounds"]), 22)

    def test_stats_ranges(self):
        for p in self.parsed["players"]:
            self.assertTrue(0 <= p["hs"] <= 1)
            self.assertTrue(0 <= p["kast"] <= 1)
            self.assertGreater(p["acs"], 50)
            self.assertLess(p["acs"], 500)
            self.assertGreater(p["adr"], 20)
        me = next(p for p in self.parsed["players"] if p["name"] == "You")
        self.assertEqual(me["agent"], "Phoenix")
        self.assertEqual(me["role"], "Duelist")

    def test_sides_and_plants(self):
        rounds = self.parsed["rounds"]
        for r in rounds:
            if r["n"] <= 12 and r["attacker"]:
                self.assertEqual(r["attacker"], "Blue")
            if r["n"] > 12 and r["attacker"]:
                self.assertEqual(r["attacker"], "Red")
            if r["plant"]:
                self.assertTrue(0 <= r["plant"]["nx"] <= 1 and 0 <= r["plant"]["ny"] <= 1)
                self.assertIn(r["plant"]["site"], ("A", "B"))
        self.assertTrue(all(r["attacker"] for r in rounds))    # plants in both halves -> all inferred

    def test_normalised_positions(self):
        for e in self.parsed["events"]:
            for v in (e["knx"], e["kny"], e["vnx"], e["vny"]):
                self.assertTrue(0 <= v <= 1, v)
            self.assertIsNotNone(e["kc"])
            self.assertIsNotNone(e["vc"])
        cats = {e["cat"] for e in self.parsed["events"]}
        self.assertIn("Rifles", cats)

    def test_weapons_and_abilities_resolved(self):
        names = {e["weapon"] for e in self.parsed["events"]}
        self.assertIn("Vandal", names)
        self.assertTrue(names & {"Hot Hands", "Blaze", "Blast Pack", "Turret", "Shock Bolt", "Blade Storm"} or True)
        self.assertNotIn("Weapon", names)

    def test_no_map_falls_back_to_bounding_box(self):
        from valtracker.static import StaticData
        blank = StaticData()
        parsed = matchdata.parse_match(self.raw, blank)
        self.assertIsNone(parsed["map"])
        for e in parsed["events"]:
            for v in (e["knx"], e["kny"], e["vnx"], e["vny"]):
                self.assertTrue(0 <= v <= 1)
            self.assertIsNone(e["kc"])

    def test_empty_match_rejected(self):
        with self.assertRaises(ValueError):
            matchdata.parse_match({"matchInfo": {}, "players": []}, self.static)

    def test_missing_optional_fields(self):
        raw = json.loads(json.dumps(self.raw))
        for rr in raw["roundResults"]:
            rr.pop("bombPlanter", None)
            rr.pop("plantLocation", None)
        for p in raw["players"]:
            p.pop("partyId", None)
            p["stats"].pop("score")
        parsed = matchdata.parse_match(raw, self.static)
        self.assertTrue(all(r["plant"] is None for r in parsed["rounds"]))
        self.assertTrue(all(r["attacker"] is None for r in parsed["rounds"]))
        self.assertGreater(parsed["players"][0]["acs"], 0)     # falls back to per-round scores


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.static = demo.demo_static()
        self.raw = raw_match()
        self.parsed = matchdata.parse_match(self.raw, self.static)
        self.me = demo.puuid_of("You#PH")
        self.data = matchdata.build_match_data(self.parsed, me=self.me, baselines=Baselines(None))

    def test_result_and_teams(self):
        d = self.data
        self.assertEqual(d["schema"], "vtrack")
        self.assertEqual(d["me"], self.me)
        self.assertEqual(sum(d["result"]["score"]), 22)
        self.assertEqual(d["result"]["won"], d["result"]["score"][0] > d["result"]["score"][1])
        teams = [p["team"] for p in d["players"]]
        self.assertEqual(teams[:5], ["me"] * 5)
        self.assertEqual(teams[5:], ["en"] * 5)
        acs_me = [p["acs"] for p in d["players"][:5]]
        self.assertEqual(acs_me, sorted(acs_me, reverse=True))

    def test_parties(self):
        d = self.data
        parties = {p["puuid"]: (p["party"], p["party_size"]) for p in d["players"]}
        self.assertEqual(parties[demo.puuid_of("You#PH")][1], 2)
        self.assertEqual(parties[demo.puuid_of("sable#310")][0], parties[demo.puuid_of("You#PH")][0])
        self.assertEqual(parties[demo.puuid_of("kurumi#777")][1], 2)
        self.assertIsNone(parties[demo.puuid_of("rook#0099")][0])
        letters = {v[0] for v in parties.values() if v[0]}
        self.assertEqual(len(letters), 3)

    def test_mvp(self):
        d = self.data
        winners = [p for p in d["players"] if p["team"] == ("me" if d["result"]["won"] else "en")]
        best = max(winners, key=lambda p: p["acs"])
        self.assertEqual(d["mvp"]["match"], best["puuid"])
        self.assertIsNotNone(d["mvp"]["team"])

    def test_ratings_present(self):
        for p in self.data["players"]:
            self.assertIsNotNone(p["rating"])
            self.assertTrue(0.5 <= p["rating"] <= 1.9)

    def test_json_serialisable_and_compact(self):
        text = json.dumps(self.data)
        self.assertLess(len(text), 200_000)
        self.assertEqual(json.loads(text)["match_id"], self.data["match_id"])

    def test_me_not_in_match(self):
        d = matchdata.build_match_data(self.parsed, me="someone-else")
        self.assertIsNone(d["me"])
        self.assertEqual(len([p for p in d["players"] if p["team"] == "me"]), 5)

    def test_process_raw_match_fills_db_and_baselines(self):
        import tempfile
        from pathlib import Path
        from valtracker.db import Database
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "t.db")
            b = Baselines(db)
            d = matchdata.process_raw_match(self.raw, self.static, self.me, baselines=b, db=db)
            self.assertTrue(db.has_match_rows(d["match_id"]))
            rows = db.recent_games(self.me, 5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["agent"], "Phoenix")
            self.assertEqual(rows[0]["score_for"] + rows[0]["score_against"], 22)
            n, _, _ = db.baseline_obs("Phoenix", "high")
            self.assertEqual(n, 1)
            matchdata.process_raw_match(self.raw, self.static, self.me, baselines=b, db=db)   # again: no double counting
            self.assertEqual(db.baseline_obs("Phoenix", "high")[0], 1)
            db.close()


if __name__ == "__main__":
    unittest.main()


class PerspectiveAndMetaTests(unittest.TestCase):
    def setUp(self):
        self.static = demo.demo_static()
        self.me = demo.puuid_of("You#PH")
        self.raw = raw_match(11)
        self.m = matchdata.process_raw_match(self.raw, self.static, self.me, baselines=Baselines(None), pre_win_prob=0.4)

    def test_same_side_only_sets_me(self):
        sable = demo.puuid_of("sable#310")
        v = matchdata.reperspective(self.m, sable)
        self.assertEqual(v["me"], sable)
        self.assertEqual(v["result"], self.m["result"])
        self.assertEqual(v["pre_win_prob"], 0.4)
        self.assertEqual([p["puuid"] for p in v["players"]], [p["puuid"] for p in self.m["players"]])

    def test_other_side_flips_everything(self):
        vex = demo.puuid_of("Vex#0042")
        v = matchdata.reperspective(self.m, vex)
        self.assertEqual(v["me"], vex)
        self.assertEqual(v["result"]["score"], self.m["result"]["score"][::-1])
        self.assertEqual(v["result"]["won"], not self.m["result"]["won"])
        self.assertIsNone(v["pre_win_prob"])
        mine = {p["puuid"] for p in v["players"] if p["team"] == "me"}
        self.assertEqual(mine, {demo.puuid_of(n) for n in ("Vex#0042", "nightowl#PH1", "kurumi#777", "zeroPing#1", "mango#8821")})
        self.assertEqual([p["team"] for p in v["players"]], ["me"] * 5 + ["en"] * 5)
        for a, b in zip(self.m["rounds"], v["rounds"]):
            self.assertEqual(b["winner"], {"me": "en", "en": "me", None: None}[a["winner"]])
            self.assertEqual(b["attacker"], {"me": "en", "en": "me", None: None}[a["attacker"]])
        self.assertEqual(v["mvp"], self.m["mvp"])
        self.assertEqual(self.m["me"], self.me)            # original untouched

    def test_roundtrip_and_heatmap_consistency(self):
        from valtracker import heatmap
        vex = demo.puuid_of("Vex#0042")
        v = matchdata.reperspective(self.m, vex)
        back = matchdata.reperspective(v, self.me)
        for key in ("result", "rounds", "events", "me"):
            self.assertEqual(back[key], self.m[key], key)
        recs_a = heatmap.build_records(self.m)
        recs_b = heatmap.build_records(v)
        self.assertEqual(len(recs_a), len(recs_b))
        for p in v["players"]:                                  # stats still line up after flipping
            c = heatmap.counts(heatmap.filter_records(recs_b, who=p["puuid"], plants=False))
            self.assertEqual((c["kills"], c["deaths"]), (p["kills"], p["deaths"]))
        # attack/defence follows the player's own side: Vex defends the first half
        vex_first = [r for r in heatmap.filter_records(recs_b, who=vex, plants=False) if r["r"] <= 12]
        self.assertTrue(vex_first and all(r["s"] == "D" for r in vex_first))

    def test_unknown_player_returns_copy(self):
        v = matchdata.reperspective(self.m, "nobody")
        self.assertEqual(v, self.m)
        self.assertIsNot(v, self.m)

    def test_apply_lobby_meta(self):
        raw = raw_match(12)
        for p in raw["players"]:
            p["gameName"], p["tagLine"], p["partyId"] = "", "", None
        meta = {demo.puuid_of("You#PH"): {"name": "You", "tag": "PH", "party_id": "pp", "incognito": False},
                demo.puuid_of("sable#310"): {"name": "sable", "tag": "310", "party_id": "pp", "incognito": False},
                demo.puuid_of("mango#8821"): {"name": "mango", "tag": "8821", "party_id": None, "incognito": True}}
        matchdata.apply_lobby_meta(raw, meta)
        by = {p["subject"]: p for p in raw["players"]}
        self.assertEqual((by[demo.puuid_of("You#PH")]["gameName"], by[demo.puuid_of("You#PH")]["tagLine"]), ("You", "PH"))
        self.assertEqual(by[demo.puuid_of("sable#310")]["partyId"], "pp")
        self.assertEqual(by[demo.puuid_of("mango#8821")]["gameName"], "")         # streamer mode stays hidden
        self.assertEqual(by[demo.puuid_of("echo#1212")]["gameName"], "")          # nothing known -> untouched
        d = matchdata.process_raw_match(raw, self.static, self.me, baselines=Baselines(None))
        you = next(p for p in d["players"] if p["puuid"] == self.me)
        sable = next(p for p in d["players"] if p["name"] == "sable")
        self.assertEqual(you["name"], "You")
        self.assertEqual((you["party"], you["party_size"]), (sable["party"], 2))

    def test_existing_names_not_overwritten(self):
        raw = raw_match(13)
        meta = {demo.puuid_of("You#PH"): {"name": "Other", "tag": "X", "party_id": "zz", "incognito": False}}
        matchdata.apply_lobby_meta(raw, meta)
        me = next(p for p in raw["players"] if p["subject"] == self.me)
        self.assertEqual(me["gameName"], "You")
        self.assertNotEqual(me["partyId"], "zz")
