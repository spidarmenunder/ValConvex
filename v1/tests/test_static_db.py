import json
import tempfile
import time
import unittest
from pathlib import Path

from valtracker.db import Database
from valtracker.static import StaticData, map_transform

AGENTS = [{"uuid": "AAA-1", "displayName": "Phoenix", "role": {"displayName": "Duelist"},
           "abilities": [{"slot": "Ability1", "displayName": "Blaze"}, {"slot": "Grenade", "displayName": "Curveball"},
                         {"slot": "Ability2", "displayName": "Hot Hands"}, {"slot": "Ultimate", "displayName": "Run it Back"}]}]
MAPS = [{"uuid": "MAP-1", "displayName": "Ascent", "mapUrl": "/Game/Maps/Ascent/Ascent", "displayIcon": "http://x/i.png",
         "xMultiplier": 0.0001, "yMultiplier": -0.0001, "xScalarToAdd": 0.5, "yScalarToAdd": 0.6,
         "callouts": [{"regionName": "Site", "superRegionName": "A", "location": {"x": 1000.0, "y": 2000.0}},
                      {"regionName": "Mid", "superRegionName": "Mid", "location": {"x": -1000.0, "y": -2000.0}}]}]
WEAPONS = [{"uuid": "W-VANDAL", "displayName": "Vandal", "category": "EEquippableCategory::Rifle"},
           {"uuid": "W-KNIFE", "displayName": "Melee", "category": "EEquippableCategory::Melee"},
           {"uuid": "W-OP", "displayName": "Operator", "category": "EEquippableCategory::Sniper"}]
SEASONS = [
    {"uuid": "EP1", "displayName": "EPISODE 9", "startTime": "2024-01-01T00:00:00Z", "endTime": "2024-12-31T00:00:00Z", "parentUuid": None},
    {"uuid": "A1", "displayName": "ACT 1", "startTime": "2024-01-01T00:00:00Z", "endTime": "2024-03-01T00:00:00Z", "parentUuid": "EP1"},
    {"uuid": "A2", "displayName": "ACT 2", "startTime": "2024-03-01T00:00:00Z", "endTime": "2024-06-01T00:00:00Z", "parentUuid": "EP1"},
    {"uuid": "A3", "displayName": "ACT 3", "startTime": "2024-06-01T00:00:00Z", "endTime": "2024-09-01T00:00:00Z", "parentUuid": "EP1"},
    {"uuid": "A4", "displayName": "ACT 4", "startTime": "2024-09-01T00:00:00Z", "endTime": "2024-12-01T00:00:00Z", "parentUuid": "EP1"},
]


class FakeResp:
    def __init__(self, data):
        self._d = data
        self.content = b"PNGDATA"

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": self._d}


class FakeHttp:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if self.fail:
            raise ConnectionError("offline")
        table = {"/agents": AGENTS, "/maps": MAPS, "/weapons": WEAPONS, "/seasons": SEASONS}
        for k, v in table.items():
            if url.endswith(k):
                return FakeResp(v)
        return FakeResp([])


class StaticTests(unittest.TestCase):
    def setUp(self):
        self.sd = StaticData(AGENTS, MAPS, WEAPONS, SEASONS)

    def test_agents(self):
        self.assertEqual(self.sd.agent_name("aaa-1"), "Phoenix")
        self.assertEqual(self.sd.agent_role("AAA-1"), "Duelist")
        self.assertEqual(self.sd.agent_name("nope"), "Agent")
        self.assertEqual(self.sd.ability_name("AAA-1", "GrenadeAbility"), "Curveball")
        self.assertEqual(self.sd.ability_name("AAA-1", "Ability1"), "Blaze")
        self.assertEqual(self.sd.ability_name("AAA-1", "Ultimate"), "Run it Back")
        self.assertEqual(self.sd.ability_name("nope", "Ability1"), "Ability")

    def test_damage_source(self):
        self.assertEqual(self.sd.damage_source("Weapon", "W-VANDAL"), ("Vandal", "Rifles"))
        self.assertEqual(self.sd.damage_source("Weapon", "w-op"), ("Operator", "Snipers"))
        self.assertEqual(self.sd.damage_source("Melee", "W-KNIFE"), ("Melee", "Melee"))
        self.assertEqual(self.sd.damage_source("Weapon", "W-KNIFE"), ("Melee", "Melee"))
        self.assertEqual(self.sd.damage_source("Ability", "Ability2", "AAA-1"), ("Hot Hands", "Abilities"))
        self.assertEqual(self.sd.damage_source("Bomb", ""), ("Spike", "Other"))
        self.assertEqual(self.sd.damage_source("Weapon", "unknown"), ("Weapon", "Other"))

    def test_maps_and_transform(self):
        m = self.sd.map_by_url("/game/maps/ascent/ascent")
        self.assertEqual(m["name"], "Ascent")
        self.assertIs(self.sd.map_by_uuid("MAP-1"), m)
        nx, ny = map_transform(m, 1000.0, 2000.0)
        self.assertAlmostEqual(nx, 2000.0 * 0.0001 + 0.5)     # x comes from game y
        self.assertAlmostEqual(ny, 1000.0 * -0.0001 + 0.6)    # y comes from game x
        self.assertIsNone(map_transform(None, 1, 1))
        self.assertEqual(self.sd.map_name("/Game/Maps/Foo/Bar"), "Bar")

    def test_callouts(self):
        m = self.sd.map_by_uuid("MAP-1")
        nx, ny = map_transform(m, 1000.0, 2000.0)
        self.assertEqual(self.sd.callout_at(m, nx + 0.01, ny), "A Site")
        self.assertIsNone(self.sd.callout_at(m, 0.99, 0.99))

    def test_seasons(self):
        self.assertEqual(self.sd.season_label("A2"), "Episode 9 · Act 2")
        self.assertEqual(self.sd.acts(), ["a1", "a2", "a3", "a4"])
        now = int(time.mktime((2024, 7, 1, 0, 0, 0, 0, 0, 0)) * 1000)
        self.assertEqual(self.sd.current_act(now), "a3")
        self.assertEqual(self.sd.acts_before("a4", 3), ["a1", "a2", "a3"])
        self.assertEqual(self.sd.acts_before("a2", 3), ["a1"])
        self.assertEqual(self.sd.acts_before("zzz", 3), [])
        self.assertEqual(self.sd.season_label("nope"), "Unknown act")

    def test_fetch_caches_and_survives_offline(self):
        with tempfile.TemporaryDirectory() as d:
            http = FakeHttp()
            sd = StaticData.fetch(d, http=http)
            self.assertEqual(sd.agent_name("aaa-1"), "Phoenix")
            n = len(http.calls)
            sd2 = StaticData.fetch(d, http=http)            # fresh cache -> no new calls
            self.assertEqual(len(http.calls), n)
            self.assertEqual(sd2.agent_name("aaa-1"), "Phoenix")
            sd3 = StaticData.fetch(d, http=FakeHttp(fail=True), force=True)   # offline -> stale cache
            self.assertEqual(sd3.agent_name("aaa-1"), "Phoenix")
        with tempfile.TemporaryDirectory() as d:
            sd4 = StaticData.fetch(d, http=FakeHttp(fail=True))                # offline, no cache
            self.assertEqual(sd4.agent_name("aaa-1"), "Agent")
            self.assertFalse(sd4.has_maps)

    def test_reload_after_offline_start(self):
        with tempfile.TemporaryDirectory() as d:
            http = FakeHttp(fail=True)
            sd = StaticData.fetch(d, http=http)
            self.assertFalse(sd.has_maps)
            http.fail = False
            sd.reload()
            self.assertTrue(sd.has_maps)
            self.assertEqual(sd.agent_name("aaa-1"), "Phoenix")

    def test_map_image_download(self):
        with tempfile.TemporaryDirectory() as d:
            sd = StaticData(AGENTS, MAPS, WEAPONS, SEASONS, cache_dir=d, http=FakeHttp())
            m = sd.map_by_uuid("MAP-1")
            self.assertIsNone(sd.map_image_path(m))
            p = sd.ensure_map_image(m)
            self.assertTrue(p.exists())
            self.assertEqual(sd.map_image_path(m), p)


def row(match_id, puuid, started, queue="competitive", agent="Jett", acs=200.0, adr=130.0):
    return {"match_id": match_id, "puuid": puuid, "started_ms": started, "queue": queue, "map_url": "/m",
            "agent_id": "a", "agent": agent, "team": "Blue", "won": 1, "rounds": 22, "kills": 20,
            "deaths": 10, "assists": 3, "acs": acs, "adr": adr, "hs": 0.2, "kast": 0.7, "tier": 20,
            "score_for": 13, "score_against": 9, "weapons": {"Vandal": 15}}


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_rows_and_recent(self):
        self.assertFalse(self.db.has_match_rows("m1"))
        self.db.save_match_rows("m1", [row("m1", "p1", 1000), row("m1", "p2", 1000)])
        self.db.save_match_rows("m2", [row("m2", "p1", 2000, queue="custom")])
        self.db.save_match_rows("m3", [row("m3", "p1", 3000)])
        self.assertTrue(self.db.has_match_rows("m1"))
        rec = self.db.recent_games("p1", 5)
        self.assertEqual([r["match_id"] for r in rec], ["m3", "m1"])     # newest first, competitive only
        self.assertEqual(rec[0]["weapons"], {"Vandal": 15})
        allq = self.db.recent_games("p1", 5, queue=None)
        self.assertEqual(len(allq), 3)
        got = self.db.games_by_id("p1", ["m1", "m3", "zzz"])
        self.assertEqual(set(got), {"m1", "m3"})
        self.assertEqual(self.db.games_by_id("p1", []), {})

    def test_baseline_obs(self):
        self.assertEqual(self.db.baseline_obs("Jett", "high"), (0, 0.0, 0.0))
        self.db.add_baseline_obs("Jett", "high", 250.0, 150.0)
        self.db.add_baseline_obs("Jett", "high", 200.0, 130.0)
        n, a, d = self.db.baseline_obs("Jett", "high")
        self.assertEqual((n, a, d), (2, 450.0, 280.0))
        self.assertFalse(self.db.baseline_done("m1"))
        self.db.mark_baseline_done("m1")
        self.assertTrue(self.db.baseline_done("m1"))

    def test_matches_roundtrip(self):
        m = {"match_id": "m1", "started_ms": 5, "map": {"name": "Ascent"}, "queue": "competitive",
             "result": {"won": True, "score": [13, 9]}, "x": [1, 2, 3]}
        self.db.save_match(m)
        self.assertEqual(self.db.get_match("m1"), m)
        lst = self.db.list_matches()
        self.assertEqual(lst[0]["score"], "13-9")
        self.assertEqual(lst[0]["result"], "Victory")
        self.db.set_saved_path("m1", "/tmp/x.vtrack")
        self.assertEqual(self.db.list_matches()[0]["saved_path"], "/tmp/x.vtrack")
        self.assertIsNone(self.db.get_match("nope"))

    def test_repeat_count(self):
        for i in range(1, 6):
            self.db.record_seen(f"m{i}", i * 1000, [("me", "Me"), ("sniper", "S")] if i in (1, 3) else [("me", "Me")])
        # current match m5: look back over m1..m4
        self.assertEqual(self.db.repeat_count("sniper", "m5", 10), 2)
        self.assertEqual(self.db.repeat_count("nobody", "m5", 10), 0)
        self.assertEqual(self.db.repeat_count("sniper", "m5", 1), 0)       # only m4 considered
        self.assertEqual(Database(Path(self.tmp.name) / "e.db").repeat_count("x"), 0)

    def test_json_cache(self):
        self.db.cache_put("k", {"a": 1})
        self.assertEqual(self.db.cache_get("k", 60), {"a": 1})
        self.assertIsNone(self.db.cache_get("k", -1))
        self.assertIsNone(self.db.cache_get("zz", 60))


if __name__ == "__main__":
    unittest.main()


class LobbyMetaTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "m.db")
            db.save_lobby_meta("m1", [{"puuid": "a", "name": "A", "tag": "1", "party_id": "P", "incognito": False},
                                      {"puuid": "b", "name": "", "tag": "", "party_id": None, "incognito": True}])
            got = db.get_lobby_meta("m1")
            self.assertEqual(got["a"], {"name": "A", "tag": "1", "party_id": "P", "incognito": False})
            self.assertTrue(got["b"]["incognito"])
            db.save_lobby_meta("m1", [{"puuid": "a", "name": "A2", "tag": "1", "party_id": "P", "incognito": False}])
            self.assertEqual(db.get_lobby_meta("m1")["a"]["name"], "A2")
            self.assertEqual(db.get_lobby_meta("zzz"), {})
            db.close()
