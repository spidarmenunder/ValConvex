import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from valtracker.riot import local, remote
from valtracker.riot.errors import ClientNotRunning, HenrikError, RiotError
from valtracker.riot.henrik import HenrikClient
from valtracker.riot.source import RiotSource
from valtracker.util import decode_b64_json


class Resp:
    def __init__(self, status=200, data=None, headers=None, text=""):
        self.status_code, self._d, self.headers, self.text = status, data, headers or {}, text

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeRemoteHttp:
    def __init__(self, table):
        self.table, self.calls = table, []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((method, url, dict(headers or {}), json))
        v = self.table.get((method, url))
        if callable(v):
            v = v()
        if isinstance(v, list):
            v = v.pop(0)
        return v if v is not None else Resp(404)


def make_remote(table, tokens=None):
    seq = tokens or [("acc1", "ent1", "me")]
    state = {"i": 0}

    def provider():
        t = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        return t

    sleeps = []
    r = remote.RiotRemote(provider, "release-09.05-shipping-6-2578911", "ap", "ap",
                          http=FakeRemoteHttp(table), sleep=sleeps.append, min_interval=0)
    r.sleeps = sleeps
    return r


class LockfileTests(unittest.TestCase):
    def test_parse(self):
        li = local.parse_lockfile("Riot Client:1234:5555:pa:ss:https")
        self.assertEqual((li.name, li.pid, li.port, li.password, li.protocol),
                         ("Riot Client", "1234", 5555, "pa:ss", "https"))          # password may contain ':'

    def test_malformed(self):
        with self.assertRaises(ClientNotRunning):
            local.parse_lockfile("nonsense")

    def test_missing(self):
        with mock.patch.dict("os.environ", {"VALCONVEX_LOCKFILE": "/nonexistent/lockfile"}):
            with self.assertRaises(ClientNotRunning):
                local.read_lockfile()

    def test_read_from_env_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lockfile"
            p.write_text("Riot Client:9:1111:secret:https", encoding="utf-8")
            with mock.patch.dict("os.environ", {"VALCONVEX_LOCKFILE": str(p)}):
                self.assertEqual(local.read_lockfile().port, 1111)

    def test_platform_header_is_base64_json(self):
        d = json.loads(base64.b64decode(local.client_platform_header()))
        self.assertEqual(d["platformType"], "PC")

    def test_region_shard(self):
        self.assertEqual(local.region_to_shard("latam"), "na")
        self.assertEqual(local.region_to_shard("br"), "na")
        self.assertEqual(local.region_to_shard("ap"), "ap")
        self.assertEqual(local.region_to_shard("eu"), "eu")


class LocalClientTests(unittest.TestCase):
    def make(self, table):
        class H:
            def get(self, url, auth=None, verify=None, timeout=None):
                self.last = (url, auth, verify)
                r = table.get(url.replace("https://127.0.0.1:5555", ""))
                if isinstance(r, Exception):
                    raise r
                return r or Resp(404)
        h = H()
        return local.LocalClient(local.LockInfo("Riot Client", "1", 5555, "pw", "https"), http=h), h

    def test_entitlements_and_auth(self):
        c, h = self.make({"/entitlements/v1/token": Resp(200, {"accessToken": "A", "token": "T", "subject": "P"})})
        self.assertEqual(c.entitlements(), ("A", "T", "P"))
        self.assertEqual(h.last[1], ("riot", "pw"))
        self.assertFalse(h.last[2])

    def test_entitlements_not_ready(self):
        c, _ = self.make({"/entitlements/v1/token": Resp(200, {"accessToken": "", "token": "", "subject": ""})})
        with self.assertRaises(RiotError):
            c.entitlements()

    def test_unreachable(self):
        c, _ = self.make({"/entitlements/v1/token": requests.exceptions.ConnectionError("x")})
        with self.assertRaises(ClientNotRunning):
            c.entitlements()

    def test_region_from_session(self):
        sess = {"abc": {"productId": "valorant", "launchConfiguration": {"arguments": ["-x", "-ares-deployment=latam"]}},
                "def": {"productId": "league_of_legends"}}
        c, _ = self.make({"/product-session/v1/external-sessions": Resp(200, sess)})
        self.assertEqual(c.region_shard(), ("latam", "na"))

    def test_presences_decode(self):
        priv = base64.b64encode(json.dumps({"partyId": "party-1"}).encode()).decode()
        c, _ = self.make({"/chat/v4/presences": Resp(200, {"presences": [
            {"puuid": "p1", "private": priv, "game_name": "A", "game_tag": "1"},
            {"puuid": "p2", "private": None},
            {"puuid": "p3", "private": "%%%notbase64"}]})})
        out = c.presences()
        self.assertEqual(out["p1"]["party_id"], "party-1")
        self.assertIsNone(out["p2"]["party_id"])
        self.assertIsNone(out["p3"]["party_id"])
        self.assertEqual(decode_b64_json("!!"), {})


class RemoteTests(unittest.TestCase):
    URL = "https://pd.ap.a.pvp.net/mmr/v1/players/x"

    def test_headers(self):
        r = make_remote({("GET", self.URL): Resp(200, {"ok": 1})})
        self.assertEqual(r.get(self.URL), {"ok": 1})
        h = r.http.calls[0][2]
        self.assertEqual(h["Authorization"], "Bearer acc1")
        self.assertEqual(h["X-Riot-Entitlements-JWT"], "ent1")
        self.assertEqual(h["X-Riot-ClientVersion"], "release-09.05-shipping-6-2578911")
        self.assertIn("X-Riot-ClientPlatform", h)

    def test_404_is_none(self):
        r = make_remote({})
        self.assertIsNone(r.get(self.URL))

    def test_token_refresh_on_403(self):
        r = make_remote({("GET", self.URL): [Resp(403), Resp(200, {"ok": 1})]},
                        tokens=[("acc1", "ent1", "me"), ("acc2", "ent2", "me")])
        self.assertEqual(r.get(self.URL), {"ok": 1})
        self.assertEqual(r.http.calls[1][2]["Authorization"], "Bearer acc2")

    def test_403_twice_raises(self):
        r = make_remote({("GET", self.URL): [Resp(403), Resp(403)]}, tokens=[("a", "e", "m"), ("b", "f", "m")])
        with self.assertRaises(RiotError) as cm:
            r.get(self.URL)
        self.assertEqual(cm.exception.status, 403)

    def test_429_waits_then_succeeds(self):
        r = make_remote({("GET", self.URL): [Resp(429, headers={"Retry-After": "3"}), Resp(200, {"ok": 2})]})
        self.assertEqual(r.get(self.URL), {"ok": 2})
        self.assertIn(3.0, r.sleeps)

    def test_500_retries_then_gives_up(self):
        r = make_remote({("GET", self.URL): [Resp(500)] * 10})
        with self.assertRaises(RiotError):
            r.get(self.URL)
        self.assertEqual(len(r.http.calls), 5)

    def test_other_status_raises(self):
        r = make_remote({("GET", self.URL): Resp(418)})
        with self.assertRaises(RiotError) as cm:
            r.get(self.URL)
        self.assertEqual(cm.exception.status, 418)

    def test_endpoints(self):
        base_pd, base_glz = "https://pd.ap.a.pvp.net", "https://glz-ap-1.ap.a.pvp.net"
        table = {
            ("GET", f"{base_glz}/core-game/v1/players/me"): Resp(200, {"MatchID": "cg1"}),
            ("GET", f"{base_glz}/pregame/v1/players/me"): Resp(404),
            ("GET", f"{base_pd}/mmr/v1/players/me/competitiveupdates?startIndex=0&endIndex=5&queue=competitive"): Resp(200, {"Matches": []}),
            ("GET", f"{base_pd}/match-history/v1/history/me?startIndex=0&endIndex=3"): Resp(200, {"History": []}),
            ("GET", f"{base_pd}/match-history/v1/history/me?startIndex=0&endIndex=3&queue=competitive"): Resp(200, {"History": [1]}),
            ("PUT", f"{base_pd}/name-service/v2/players"): Resp(200, [{"Subject": "p1", "GameName": "N", "TagLine": "T"}]),
        }
        r = make_remote(table)
        self.assertEqual(r.coregame_id("me"), "cg1")
        self.assertIsNone(r.pregame_id("me"))
        self.assertEqual(r.updates("me"), {"Matches": []})
        self.assertEqual(r.history("me", 0, 3), {"History": []})
        self.assertEqual(r.history("me", 0, 3, "competitive"), {"History": [1]})
        self.assertEqual(r.names(["p1"])[0]["GameName"], "N")
        self.assertEqual(r.http.calls[-1][3], ["p1"])
        self.assertEqual(r.names([]), [])


class SourceTests(unittest.TestCase):
    def make(self, table):
        r = make_remote(table)

        class L:
            def presences(self):
                return {"a": {"party_id": "P1"}, "b": {"party_id": None}}

            def self_name(self):
                return "Me", "PH"
        return RiotSource(L(), r)

    def test_state_and_lobbies(self):
        glz, pd = "https://glz-ap-1.ap.a.pvp.net", "https://pd.ap.a.pvp.net"
        core = {"MatchID": "m1", "MapID": "/Game/Maps/Ascent/Ascent", "MatchmakingData": {"QueueID": "Competitive"},
                "Players": [
                    {"Subject": "me", "TeamID": "Blue", "CharacterID": "AGENT-1", "PlayerIdentity": {"Incognito": False, "AccountLevel": 50}},
                    {"Subject": "x", "TeamID": "Red", "CharacterID": "AGENT-2", "PlayerIdentity": {"Incognito": True}}]}
        pre = {"MapID": "/Game/Maps/Bind/Bind", "QueueID": "competitive",
               "AllyTeam": {"Players": [{"Subject": "me", "CharacterID": "", "CompetitiveTier": 20,
                                         "PlayerIdentity": {"Incognito": False}}]}}
        src = self.make({("GET", f"{glz}/core-game/v1/players/me"): Resp(200, {"MatchID": "m1"}),
                         ("GET", f"{glz}/core-game/v1/matches/m1"): Resp(200, core),
                         ("GET", f"{glz}/pregame/v1/players/me"): Resp(200, {"MatchID": "m1"}),
                         ("GET", f"{glz}/pregame/v1/matches/m1"): Resp(200, pre)})
        self.assertEqual(src.state(), ("ingame", "m1"))
        lob = src.lobby("ingame", "m1")
        self.assertEqual(lob["queue"], "competitive")
        self.assertEqual([(p["puuid"], p["team"], p["incognito"]) for p in lob["players"]],
                         [("me", "ally", False), ("x", "enemy", True)])
        self.assertEqual(lob["players"][0]["agent_id"], "agent-1")
        pl = src.lobby("pregame", "m1")
        self.assertEqual(pl["players"][0]["tier"], 20)
        self.assertEqual(pl["players"][0]["agent_id"], "")
        self.assertEqual(src.parties(), {"a": "P1"})
        self.assertEqual(src.my_name(), ("Me", "PH"))

    def test_menu_and_missing(self):
        src = self.make({})
        self.assertEqual(src.state(), ("menu", None))
        self.assertIsNone(src.lobby("ingame", "gone"))
        self.assertEqual(src.updates("me"), [])
        self.assertIsNone(src.match_details("zzz"))


class HenrikTests(unittest.TestCase):
    class H:
        def __init__(self, table):
            self.table, self.calls = table, []

        def get(self, url, headers=None, timeout=None):
            self.calls.append((url, headers))
            for k, v in self.table.items():
                if url.endswith(k):
                    return v
            return Resp(404)

    def test_ok_v2(self):
        h = self.H({"/valorant/v2/account/Name/TAG": Resp(200, {"status": 200, "data": {
            "puuid": "P", "region": "ap", "name": "Name", "tag": "TAG", "account_level": 100}})})
        r = HenrikClient("KEY", http=h).account("Name", "#TAG")
        self.assertEqual(r["puuid"], "P")
        self.assertEqual(h.calls[0][1]["Authorization"], "KEY")

    def test_falls_back_to_v1_and_encodes(self):
        h = self.H({"/valorant/v1/account/A%20B/T1": Resp(200, {"data": {"puuid": "P2"}})})
        r = HenrikClient("K", http=h).account("A B", "T1")
        self.assertEqual(r["puuid"], "P2")
        self.assertEqual(len(h.calls), 2)

    def test_errors(self):
        with self.assertRaisesRegex(HenrikError, "No HenrikDev API key"):
            HenrikClient("").account("a", "b")
        with self.assertRaisesRegex(HenrikError, "name#tag"):
            HenrikClient("k").account("", "")
        for status, text in ((401, "rejected"), (403, "rejected"), (429, "rate limit")):
            h = self.H({"": Resp(status)})
            with self.assertRaisesRegex(HenrikError, text):
                HenrikClient("k", http=h).account("a", "b")
        with self.assertRaisesRegex(HenrikError, "No player found"):
            HenrikClient("k", http=self.H({})).account("a", "b")

    def test_network_error(self):
        class Boom:
            def get(self, *a, **k):
                raise requests.exceptions.ConnectionError("x")
        with self.assertRaisesRegex(HenrikError, "Could not reach"):
            HenrikClient("k", http=Boom()).account("a", "b")


if __name__ == "__main__":
    unittest.main()
