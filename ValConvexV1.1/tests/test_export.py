import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from valtracker import demo, export, heatmap, matchdata
from valtracker.model.baselines import Baselines

ROOT = Path(__file__).resolve().parent.parent
ME = demo.puuid_of("You#PH")


def make_match():
    static = demo.demo_static()
    raw = demo.make_raw_match(7, "match-abc-12345", 1_700_000_000_000)
    return matchdata.process_raw_match(raw, static, ME, baselines=Baselines(None), pre_win_prob=0.4)


class HeatmapTests(unittest.TestCase):
    def setUp(self):
        self.m = make_match()
        self.recs = heatmap.build_records(self.m)

    def test_record_counts_match_events(self):
        ev = self.m["events"]
        c = heatmap.counts(self.recs)
        self.assertEqual(c["kills"], len(ev))
        self.assertEqual(c["deaths"], len(ev))
        self.assertEqual(c["plants"], sum(1 for r in self.m["rounds"] if r["plant"]))

    def test_player_filter_matches_scoreboard(self):
        for p in self.m["players"]:
            c = heatmap.counts(heatmap.filter_records(self.recs, who=p["puuid"], plants=False))
            self.assertEqual(c["kills"], p["kills"], p["name"])
            self.assertEqual(c["deaths"], p["deaths"], p["name"])

    def test_team_filters_partition(self):
        me = heatmap.counts(heatmap.filter_records(self.recs, who="me", plants=False))
        en = heatmap.counts(heatmap.filter_records(self.recs, who="en", plants=False))
        allc = heatmap.counts(heatmap.filter_records(self.recs, plants=False))
        self.assertEqual(me["kills"] + en["kills"], allc["kills"])
        self.assertEqual(me["deaths"] + en["deaths"], allc["deaths"])

    def test_round_side_type_filters(self):
        r5 = heatmap.filter_records(self.recs, rnd=5)
        self.assertTrue(r5 and all(r["r"] == 5 for r in r5))
        att = heatmap.filter_records(self.recs, who=ME, side="A", plants=False)
        dfn = heatmap.filter_records(self.recs, who=ME, side="D", plants=False)
        self.assertTrue(all(r["r"] <= 12 for r in att))      # my team attacks the first half
        self.assertTrue(all(r["r"] > 12 for r in dfn))
        both = heatmap.filter_records(self.recs, who=ME, plants=False)
        self.assertEqual(len(att) + len(dfn), len(both))
        kills = heatmap.filter_records(self.recs, who=ME, type="k", plants=False)
        self.assertTrue(all(r["t"] == "k" for r in kills))
        self.assertEqual(len(kills), next(p for p in self.m["players"] if p["puuid"] == ME)["kills"])

    def test_weapon_filters(self):
        cats = heatmap.categories_present(self.recs)
        self.assertIn("Rifles", cats)
        vandal = heatmap.filter_records(self.recs, wpn="Vandal", plants=False)
        self.assertTrue(vandal and all(r["w"] == "Vandal" for r in vandal))
        rifles = heatmap.filter_records(self.recs, cat="Rifles", plants=False)
        self.assertTrue(len(rifles) >= len(vandal))
        self.assertIn("Vandal", heatmap.weapons_in(self.recs, "Rifles"))
        self.assertNotIn("Vandal", heatmap.weapons_in(self.recs, "Snipers"))

    def test_plants_toggle(self):
        with_p = heatmap.filter_records(self.recs, plants=True)
        no_p = heatmap.filter_records(self.recs, plants=False)
        self.assertGreater(len(with_p), len(no_p))
        self.assertTrue(all(r["t"] != "p" for r in no_p))
        self.assertTrue(all(r["s"] == "A" for r in with_p if r["t"] == "p"))

    def test_describe(self):
        k = next(r for r in self.recs if r["t"] == "k")
        d = heatmap.describe(k, self.m)
        self.assertEqual(d["kind"], "Kill")
        labels = [x[0] for x in d["rows"]]
        self.assertIn("Killed", labels)
        p = next(r for r in self.recs if r["t"] == "p")
        self.assertEqual(heatmap.describe(p, self.m)["kind"], "Spike plant")
        dd = heatmap.describe(next(r for r in self.recs if r["t"] == "d"), self.m)
        self.assertEqual(dd["rows"][1][0], "Killed by")

    def test_hidden_name_uses_agent(self):
        self.assertEqual(heatmap.player_name({"name": "", "tag": "", "agent": "Jett"}), "Jett (hidden)")
        self.assertEqual(heatmap.player_name({"name": "A", "tag": "B", "agent": "Jett"}), "A#B")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.m = make_match()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_filename(self):
        n = export.default_filename(self.m, "vtrack")
        self.assertTrue(n.endswith(".vtrack"))
        self.assertIn("Demo-Map", n)
        self.assertIn(f'{self.m["result"]["score"][0]}-{self.m["result"]["score"][1]}', n)

    def test_vtrack_roundtrip(self):
        p = export.save_vtrack(self.m, self.dir / "a.vtrack")
        back = export.load_vtrack(p)
        self.assertEqual(back, json.loads(json.dumps(self.m)))
        self.assertEqual(heatmap.build_records(back), heatmap.build_records(self.m))

    def test_load_rejects_bad_files(self):
        bad = self.dir / "bad.vtrack"
        bad.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            export.load_vtrack(bad)
        bad.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            export.load_vtrack(bad)
        newer = dict(self.m, version=99)
        bad.write_text(json.dumps(newer), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "newer"):
            export.load_vtrack(bad)
        missing = {k: v for k, v in self.m.items() if k != "events"}
        bad.write_text(json.dumps(missing), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "events"):
            export.load_vtrack(bad)
        with self.assertRaises(ValueError):
            export.load_vtrack(self.dir / "does-not-exist.vtrack")

    def test_anonymize_removes_everything_identifying(self):
        anon = export.anonymize_match(self.m)
        text = json.dumps(anon)
        for p in self.m["players"]:
            if p["puuid"] == ME:
                continue
            self.assertNotIn(p["puuid"], text)
            self.assertNotIn(p["name"] + "#", text)
            self.assertNotIn(f'"{p["name"]}"', text)
        self.assertNotIn(self.m["match_id"], text)
        self.assertNotIn(ME, text)
        me = next(p for p in anon["players"] if p["puuid"] == anon["me"])
        self.assertEqual(me["name"], "You")                 # the sharer keeps their own name
        names = [p["name"] for p in anon["players"]]
        self.assertEqual(len(set(names)), len(names))       # unique
        self.assertTrue(anon["anonymized"])
        # still fully usable
        recs = heatmap.build_records(anon)
        self.assertEqual(heatmap.counts(recs), heatmap.counts(heatmap.build_records(self.m)))
        for p in anon["players"]:
            c = heatmap.counts(heatmap.filter_records(recs, who=p["puuid"], plants=False))
            self.assertEqual((c["kills"], c["deaths"]), (p["kills"], p["deaths"]))
        self.assertNotIn("level", json.dumps(anon["players"]))
        # original untouched
        self.assertIn(ME, json.dumps(self.m))

    def test_anonymize_when_me_missing(self):
        m = dict(self.m, me=None)
        anon = export.anonymize_match(m)
        self.assertIsNone(anon["me"])
        self.assertNotIn("You", [p["name"] for p in anon["players"]])

    def test_html_embeds_data_and_is_safe(self):
        m = json.loads(json.dumps(self.m))
        m["players"][1]["name"] = "</script><b>x"
        out = export.export_html(m, self.dir / "a.html", minimap=b"\x89PNGfake")
        html = out.read_text(encoding="utf-8")
        self.assertNotIn("/*__VT_DATA__*/null", html)
        self.assertEqual(html.count("</script>"), 2)                 # only the two real script tags
        self.assertIn("data:image/png;base64,", html)
        self.assertIn(m["match_id"], html)

    def test_html_anonymized_has_no_names(self):
        out = export.export_html(self.m, self.dir / "b.html", anonymize=True)
        html = out.read_text(encoding="utf-8")
        self.assertNotIn("Vex", html)
        self.assertNotIn(self.m["match_id"], html)

    def test_png(self):
        from PIL import Image
        mm = demo.write_demo_minimap(self.dir / "mm.png")
        out = export.export_png(self.m, self.dir / "a.png", minimap_path=mm)
        im = Image.open(out)
        self.assertEqual(im.width, 900)
        self.assertGreater(im.height, 900)
        out2 = export.export_png(self.m, self.dir / "b.png", filters={"who": "all", "type": "d", "side": "D", "rnd": 0,
                                                                        "cat": "Rifles"}, anonymize=True)
        self.assertTrue(out2.exists())
        out3 = export.export_png(self.m, self.dir / "c.png", filters={"who": demo.puuid_of("Vex#0042")}, anonymize=True)
        self.assertTrue(out3.exists())
        self.assertIn("Everyone", export.describe_filters(self.m, {"who": "all"}))
        self.assertIn("round 3", export.describe_filters(self.m, {"who": "me", "rnd": 3}))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class JsParityTests(unittest.TestCase):
    """The standalone page has its own copy of the filter logic; make sure it agrees with Python."""

    def setUp(self):
        self.m = make_match()
        self.recs = heatmap.build_records(self.m)
        self.html = ROOT / "valtracker" / "web" / "viewer.html"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _script(self, ident):
        text = self.html.read_text(encoding="utf-8")
        return re.search(rf'<script id="{ident}">(.*?)</script>', text, re.S).group(1)

    def test_syntax_of_both_scripts(self):
        for ident in ("core", "ui"):
            f = Path(self.tmp.name) / f"{ident}.js"
            f.write_text(self._script(ident), encoding="utf-8")
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_filter_parity(self):
        cases = [
            {"who": "all", "type": "both", "side": "all", "rnd": 0, "cat": "all", "wpn": "any", "plants": True},
            {"who": "me", "type": "k", "side": "A", "rnd": 0, "cat": "all", "wpn": "any", "plants": False},
            {"who": "en", "type": "d", "side": "D", "rnd": 0, "cat": "Rifles", "wpn": "any", "plants": True},
            {"who": ME, "type": "both", "side": "all", "rnd": 5, "cat": "all", "wpn": "any", "plants": True},
            {"who": demo.puuid_of("Vex#0042"), "type": "both", "side": "all", "rnd": 0, "cat": "all", "wpn": "Vandal", "plants": False},
            {"who": "all", "type": "both", "side": "A", "rnd": 14, "cat": "all", "wpn": "any", "plants": True},
        ]
        script = self._script("core") + """
        var input = JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));
        var out = input.cases.map(function(f){var v=filterRecords(input.recs,f);var c=countRecords(v);
          return {ids:v.map(function(r){return r.id;}),c:c};});
        out.push({cats:categoriesPresent(input.recs),wpns:weaponsIn(input.recs,'Rifles')});
        console.log(JSON.stringify(out));
        """
        js = Path(self.tmp.name) / "run.js"
        js.write_text(script, encoding="utf-8")
        data = Path(self.tmp.name) / "in.json"
        data.write_text(json.dumps({"recs": self.recs, "cases": cases}), encoding="utf-8")
        r = subprocess.run(["node", str(js), str(data)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        for f, got in zip(cases, out):
            py = heatmap.filter_records(self.recs, **f)
            self.assertEqual(got["ids"], [x["id"] for x in py], f)
            self.assertEqual(got["c"], heatmap.counts(py))
        self.assertEqual(out[-1]["cats"], heatmap.categories_present(self.recs))
        self.assertEqual(out[-1]["wpns"], heatmap.weapons_in(self.recs, "Rifles"))

    def test_ui_script_runs_against_stub_dom(self):
        """Run the page script with a permissive fake DOM to catch runtime errors."""
        out = export.export_html(self.m, Path(self.tmp.name) / "p.html", minimap=b"x")
        html = out.read_text(encoding="utf-8")
        core = re.search(r'<script id="core">(.*?)</script>', html, re.S).group(1)
        ui = re.search(r'<script id="ui">(.*?)</script>', html, re.S).group(1)
        harness = """
        var handlers = {};
        function el(id){var o={id:id,innerHTML:'',textContent:'',value:'',className:'',style:{},checked:true,
          width:800,height:800,
          addEventListener:function(t,f){handlers[id+':'+t]=f;},
          querySelectorAll:function(){return [];},
          getBoundingClientRect:function(){return {left:0,top:0,width:640,height:640};},
          getContext:function(){return new Proxy({}, {get:function(t,k){ if(k in t) return t[k]; return function(){}; }, set:function(t,k,v){t[k]=v;return true;}});},
          getAttribute:function(){return 'k';}};return o;}
        var els = {};
        var document = {getElementById:function(id){return els[id]||(els[id]=el(id));}};
        function Image(){}
        """
        js = Path(self.tmp.name) / "ui.js"
        js.write_text(harness + core + "\n" + ui + """
        var cv = document.getElementById('cv');
        handlers['cv:click']({clientX: 100, clientY: 100});
        handlers['fWho:change']; els['fWho'].value = 'me'; handlers['fWho:change']();
        els['fRound'].value = '3'; handlers['fRound:change']();
        els['fCat'].value = 'Rifles'; handlers['fCat:change']();
        els['fPlants'].checked = false; handlers['fPlants:change']({target: els['fPlants']});
        console.log('ok', els['cnt'].textContent);
        """, encoding="utf-8")
        r = subprocess.run(["node", str(js)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)
        self.assertIn("kills", r.stdout)


if __name__ == "__main__":
    unittest.main()


class ColorAndLegendTests(unittest.TestCase):
    def setUp(self):
        self.m = make_match()

    def test_player_colors_unique_and_stable(self):
        c = heatmap.player_colors(self.m)
        self.assertEqual(len(set(c.values())), 10)
        flipped = matchdata.reperspective(self.m, demo.puuid_of("Vex#0042"))
        self.assertEqual(heatmap.player_colors(flipped), c)         # does not change with perspective
        self.assertEqual(heatmap.short_name({"name": "Bob", "agent": "Jett"}), "Bob")
        self.assertEqual(heatmap.short_name({"name": "", "agent": "Jett"}), "Jett")

    def test_png_player_colors_and_legend_is_taller(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            a = export.export_png(self.m, Path(d) / "a.png", filters={"who": "all", "color": "result"})
            b = export.export_png(self.m, Path(d) / "b.png", filters={"who": "all", "color": "player"})
            c = export.export_png(self.m, Path(d) / "c.png", filters={"who": demo.puuid_of("Vex#0042"), "color": "player"},
                                  anonymize=True)
            ia, ib = Image.open(a), Image.open(b)
            self.assertGreater(ib.height, ia.height)                # names legend adds rows
            self.assertTrue(c.exists())
            self.assertNotEqual(list(ia.convert("RGB").resize((8, 8)).getdata()), list(ib.convert("RGB").resize((8, 8)).getdata()))
            ia.close(), ib.close()

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_palette_matches_python_and_legend_lists_names(self):
        html = (ROOT / "valtracker" / "web" / "viewer.html").read_text(encoding="utf-8")
        js_pal = re.search(r'var PALETTE=(\[.*?\]);', html).group(1)
        self.assertEqual(json.loads(js_pal), heatmap.PALETTE)
        with tempfile.TemporaryDirectory() as d:
            out = export.export_html(self.m, Path(d) / "p.html", minimap=b"x")
            page = out.read_text(encoding="utf-8")
            core = re.search(r'<script id="core">(.*?)</script>', page, re.S).group(1)
            ui = re.search(r'<script id="ui">(.*?)</script>', page, re.S).group(1)
            harness = """
            var handlers = {};
            function el(id){var o={id:id,innerHTML:'',textContent:'',value:'',className:'',style:{},checked:true,width:800,height:800,
              addEventListener:function(t,f){handlers[id+':'+t]=f;},querySelectorAll:function(){return [];},
              getBoundingClientRect:function(){return {left:0,top:0,width:640,height:640};},
              getContext:function(){return new Proxy({}, {get:function(t,k){ if(k in t) return t[k]; return function(){}; }, set:function(t,k,v){t[k]=v;return true;}});},
              getAttribute:function(){return 'k';}};return o;}
            var els = {};
            var document = {getElementById:function(id){return els[id]||(els[id]=el(id));}};
            function Image(){}
            """
            js = Path(d) / "u.js"
            js.write_text(harness + core + "\n" + ui + """
            var leg = els['legend'].innerHTML;
            console.log(JSON.stringify({names:(leg.match(/class="sw"/g)||[]).length, hasVex:leg.indexOf('Vex#0042')>-1, hasParty:leg.indexOf('·2')>-1}));
            els['fColor'].value='result'; handlers['fColor:change']();
            els['fWho'].value='""" + demo.puuid_of("Vex#0042") + """'; handlers['fWho:change']();
            console.log(els['fColor'].value);
            """, encoding="utf-8")
            r = subprocess.run(["node", str(js)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            first, second = r.stdout.strip().splitlines()
            info = json.loads(first)
            self.assertEqual(info["names"], 10)
            self.assertTrue(info["hasVex"] and info["hasParty"])
            self.assertEqual(second, "result")                        # picking one player colors by result
