"""Saving and sharing a finished match.

  .vtrack  - portable JSON; another ValConvex imports it and gets the same leaderboard + heatmap
  .html    - one self-contained page (data + viewer, optional minimap) that opens in any browser
  .png     - a picture of the current heatmap view plus the result

"Hide other players' names" swaps names, ids and the match id for anonymous ones before writing.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Optional, Union

from . import heatmap
from .matchdata import SCHEMA, VERSION
from .util import slug

VIEWER = Path(__file__).parent / "web" / "viewer.html"
DATA_TOKEN = "/*__VT_DATA__*/null"


def default_filename(match: dict, ext: str) -> str:
    ts = match.get("started_ms")
    date = time.strftime("%Y-%m-%d_%H%M", time.localtime(ts / 1000)) if ts else "unknown-date"
    score = "-".join(str(s) for s in match["result"]["score"])
    return f"{date}_{slug(match['map']['name'])}_{score}_{slug(match['match_id'])[:8]}.{ext.lstrip('.')}"


# --------------------------------------------------------------------------- validation
def validate(m) -> None:
    if not isinstance(m, dict) or m.get("schema") != SCHEMA:
        raise ValueError("This is not a ValConvex match file.")
    v = m.get("version")
    if not isinstance(v, int) or v < 1:
        raise ValueError("Match file has no valid version.")
    if v > VERSION:
        raise ValueError(f"This file was made by a newer ValConvex (format {v}). Update the app to open it.")
    for key in ("match_id", "map", "result", "players", "rounds", "events"):
        if key not in m:
            raise ValueError(f"Match file is missing '{key}'.")


# --------------------------------------------------------------------------- anonymise
def anonymize_match(match: dict) -> dict:
    """Copy of the match with every other player's name, tag, id and level (and the match id) removed."""
    m = copy.deepcopy(match)
    me = m.get("me")
    mapping = {p["puuid"]: f"p{i + 1}" for i, p in enumerate(m["players"])}
    seen: dict = {}
    for p in m["players"]:
        old = p["puuid"]
        p["puuid"] = mapping[old]
        p.pop("level", None)
        if old == me:
            continue
        base = f'{p["agent"]} ({"ally" if p["team"] == "me" else "enemy"})'
        seen[base] = seen.get(base, 0) + 1
        p["name"] = base if seen[base] == 1 else f"{base} {seen[base]}"
        p["tag"] = ""
    m["me"] = mapping.get(me) if me else None
    for e in m["events"]:
        e["killer"] = mapping.get(e["killer"], "p?")
        e["victim"] = mapping.get(e["victim"], "p?")
        e["assists"] = [mapping.get(a, "p?") for a in e.get("assists", [])]
    for r in m["rounds"]:
        if r.get("plant"):
            r["plant"]["by"] = mapping.get(r["plant"]["by"], "p?")
    m["mvp"] = {k: (mapping.get(v) if v else None) for k, v in m.get("mvp", {}).items()}
    m["match_id"] = "anon-" + hashlib.sha1(str(match["match_id"]).encode()).hexdigest()[:16]
    m["anonymized"] = True
    return m


# --------------------------------------------------------------------------- .vtrack
def save_vtrack(match: dict, path, anonymize: bool = False) -> Path:
    data = anonymize_match(match) if anonymize else match
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load_vtrack(path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
        m = json.loads(text)
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Could not read the file: {exc}") from exc
    except ValueError as exc:
        raise ValueError("The file is not valid JSON, so it is not a ValConvex match file.") from exc
    validate(m)
    return m


# --------------------------------------------------------------------------- .html
def _minimap_uri(minimap: Optional[Union[str, Path, bytes]]) -> Optional[str]:
    if minimap is None:
        return None
    try:
        raw = minimap if isinstance(minimap, (bytes, bytearray)) else Path(minimap).read_bytes()
    except OSError:
        return None
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def export_html(match: dict, path, minimap=None, anonymize: bool = False) -> Path:
    data = anonymize_match(match) if anonymize else match
    payload = {"match": data, "records": heatmap.build_records(data), "minimap": _minimap_uri(minimap)}
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    text = text.replace("</", "<\\/").replace("<!--", "<\\!--").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = VIEWER.read_text(encoding="utf-8")
    if template.count(DATA_TOKEN) != 1:
        raise RuntimeError("viewer template is missing its data placeholder")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.replace(DATA_TOKEN, text), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- .png
def _font(size: int):
    from PIL import ImageFont
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def describe_filters(match: dict, f: dict) -> str:
    pm = heatmap.player_map(match)
    who = f.get("who", "all")
    parts = [{"all": "Everyone", "me": "Your team", "en": "Enemy team"}.get(who) or heatmap.player_name(pm.get(who))]
    parts.append({"both": "kills + deaths", "k": "kills", "d": "deaths"}[f.get("type", "both")])
    if f.get("side", "all") != "all":
        parts.append({"A": "attack", "D": "defense"}[f["side"]])
    parts.append(f'round {f["rnd"]}' if f.get("rnd") else "all rounds")
    if f.get("cat", "all") != "all":
        parts.append(f["cat"].lower())
    if f.get("wpn", "any") != "any":
        parts.append(f["wpn"])
    return " · ".join(parts)


def _rgb(hex_color: str):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def export_png(match: dict, path, filters: Optional[dict] = None, minimap_path=None,
               anonymize: bool = False, width: int = 900) -> Path:
    from PIL import Image, ImageDraw
    data = anonymize_match(match) if anonymize else match
    flt = {"who": data["me"] or "all", "type": "both", "side": "all", "rnd": 0, "cat": "all", "wpn": "any",
           "plants": True}
    color_by = "result"
    if filters:
        f = dict(filters)
        color_by = f.pop("color", "result")
        flt.update(f)
        if anonymize and f.get("who") not in (None, "all", "me", "en"):
            # the filter names an original puuid; translate to the anonymised id
            order = {p["puuid"]: f"p{i + 1}" for i, p in enumerate(match["players"])}
            flt["who"] = order.get(f["who"], "all")
    recs = heatmap.filter_records(heatmap.build_records(data), **flt)
    colors = heatmap.player_colors(data)

    def color(r):
        if r["t"] == "p":
            return (245, 165, 36)
        if color_by == "player":
            return _rgb(colors.get(r["who"], "#888888"))
        return (47, 191, 143) if r["t"] == "k" else (239, 83, 80)

    pad, head_h, foot_h = 24, 110, 64
    size = width - 2 * pad
    legend_rows = math.ceil(len(data["players"]) / 2) if color_by == "player" else 0
    legend_h = legend_rows * 24 + 10 if legend_rows else 0
    img = Image.new("RGB", (width, head_h + size + foot_h + legend_h + pad), (20, 22, 26))
    d = ImageDraw.Draw(img)
    won, draw_ = data["result"]["won"], data["result"]["draw"]
    d.text((pad, 16), "Draw" if draw_ else "Victory" if won else "Defeat", font=_font(34),
           fill=(120, 120, 120) if draw_ else (62, 207, 142) if won else (255, 107, 107))
    ts = data.get("started_ms")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts / 1000)) if ts else ""
    d.text((pad, 60), f'{data["map"]["name"]} · {data["result"]["score"][0]}-{data["result"]["score"][1]} · {when}',
           font=_font(18), fill=(200, 204, 210))
    d.text((pad, 84), describe_filters(data, flt), font=_font(14), fill=(138, 145, 156))

    top = head_h
    base = Image.new("RGB", (size, size), (30, 34, 40))
    if minimap_path and Path(minimap_path).exists():
        try:
            base = Image.open(minimap_path).convert("RGB").resize((size, size))
        except OSError:
            pass
    else:
        bd = ImageDraw.Draw(base)
        for g in range(0, size + 1, size // 8):
            bd.line([(g, 0), (g, size)], fill=(44, 49, 58))
            bd.line([(0, g), (size, g)], fill=(44, 49, 58))
    base = base.convert("RGBA")
    dense = len(recs) > 90
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gr = 16 if dense else 24
    for r in recs:
        if r["t"] == "p":
            continue
        x, y = r["x"] * size, r["y"] * size
        gd.ellipse([x - gr, y - gr, x + gr, y + gr], fill=color(r) + (34 if dense else 48,))
    base = Image.alpha_composite(base, glow)
    md = ImageDraw.Draw(base)
    rad = 5 if dense else 8
    for r in recs:
        x, y = r["x"] * size, r["y"] * size
        col = color(r) + (255,)
        if r["t"] == "k":
            md.ellipse([x - rad, y - rad, x + rad, y + rad], fill=col, outline=(0, 0, 0, 200), width=2)
        elif r["t"] == "d":
            md.polygon([(x, y - rad - 2), (x + rad + 2, y), (x, y + rad + 2), (x - rad - 2, y)],
                       fill=col, outline=(0, 0, 0, 200))
        else:
            md.polygon([(x, y - rad - 3), (x + rad + 3, y + rad), (x - rad - 3, y + rad)],
                       fill=col, outline=(0, 0, 0, 200))
    img.paste(base.convert("RGB"), (pad, top))
    c = heatmap.counts(recs)
    y0 = top + size + 14
    d.text((pad, y0), f'{c["kills"]} kills · {c["deaths"]} deaths · {c["plants"]} plants',
           font=_font(18), fill=(230, 232, 235))
    key = ("Colored by player · circle: kill · diamond: death · triangle: spike plant"
           if color_by == "player" else
           "Green circle: kill · Red diamond: death · Amber triangle: spike plant")
    d.text((pad, y0 + 24), key + " · ValConvex", font=_font(13), fill=(138, 145, 156))
    if legend_rows:
        ly = y0 + 54
        half = size // 2
        for i, p in enumerate(data["players"]):
            cx, cy = pad + (i % 2) * half, ly + (i // 2) * 24
            d.rectangle([cx, cy + 3, cx + 12, cy + 15], fill=_rgb(colors.get(p["puuid"], "#888888")))
            d.text((cx + 20, cy), f'{heatmap.player_name(p)} · {p["agent"]}', font=_font(14), fill=(220, 224, 230))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path
