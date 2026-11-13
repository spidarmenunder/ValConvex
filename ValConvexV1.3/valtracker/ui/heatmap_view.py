"""Heatmap tab: kills, deaths and spike plants drawn on the minimap, with filters, a player legend and click details."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

from .. import heatmap
from . import theme

try:
    from PIL import Image, ImageTk
except ImportError:                                  # the heatmap still works, just on a plain grid
    Image = ImageTk = None

CANVAS = 640
ALL_ROUNDS = "All rounds"
ALL_CATS = "All weapon types"
ANY_WEAPON = "Any weapon"
KILL_COLOR, DEATH_COLOR, PLANT_COLOR = "#2fbf8f", "#ef5350", "#f5a524"


class HeatmapView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.match: Optional[dict] = None
        self.records: List[dict] = []
        self.vis: List[dict] = []
        self.sel: Optional[str] = None
        self.photo = None
        self.who_keys: Dict[str, str] = {}
        self.pm: Dict[str, dict] = {}
        self.colors: Dict[str, str] = {}

        self.v_who = tk.StringVar()
        self.v_type = tk.StringVar(value="both")
        self.v_side = tk.StringVar(value="all")
        self.v_round = tk.StringVar(value=ALL_ROUNDS)
        self.v_cat = tk.StringVar(value=ALL_CATS)
        self.v_wpn = tk.StringVar(value=ANY_WEAPON)
        self.v_plants = tk.BooleanVar(value=True)
        self.v_color = tk.StringVar(value="result")
        self.v_labels = tk.BooleanVar(value=False)
        self.v_size = tk.DoubleVar(value=1.0)

        self.canvas = tk.Canvas(self, width=CANVAS, height=CANVAS, bg="#1e2228", highlightthickness=0)
        self.canvas.grid(row=0, column=0, padx=12, pady=12, sticky="nw")
        self.canvas.bind("<Button-1>", self._on_click)

        side = ttk.Frame(self)
        side.grid(row=0, column=1, sticky="nw", padx=(0, 12), pady=12)
        ttk.Label(side, text="Player", style="Muted.TLabel").pack(anchor="w")
        self.cb_who = ttk.Combobox(side, textvariable=self.v_who, state="readonly", width=44)
        self.cb_who.pack(anchor="w", pady=(0, 6))
        self.cb_who.bind("<<ComboboxSelected>>", lambda e: self._who_changed())

        row = ttk.Frame(side)
        row.pack(anchor="w")
        col1, col2 = ttk.Frame(row), ttk.Frame(row)
        col1.pack(side="left", padx=(0, 18))
        col2.pack(side="left")
        ttk.Label(col1, text="Show", style="Muted.TLabel").pack(anchor="w")
        self._radios(col1, self.v_type, [("Both", "both"), ("Kills", "k"), ("Deaths", "d")])
        ttk.Label(col2, text="Side (of the player shown)", style="Muted.TLabel").pack(anchor="w")
        self._radios(col2, self.v_side, [("All", "all"), ("Attack", "A"), ("Defense", "D")])

        ttk.Label(side, text="Color markers by", style="Muted.TLabel").pack(anchor="w")
        self._radios(side, self.v_color, [("Result (green / red)", "result"), ("Player", "player")])

        ttk.Label(side, text="Round", style="Muted.TLabel").pack(anchor="w")
        self.cb_round = ttk.Combobox(side, textvariable=self.v_round, state="readonly", width=44)
        self.cb_round.pack(anchor="w", pady=(0, 6))
        self.cb_round.bind("<<ComboboxSelected>>", lambda e: self._changed())

        ttk.Label(side, text="Weapon", style="Muted.TLabel").pack(anchor="w")
        self.cb_cat = ttk.Combobox(side, textvariable=self.v_cat, state="readonly", width=44)
        self.cb_cat.pack(anchor="w", pady=(0, 4))
        self.cb_cat.bind("<<ComboboxSelected>>", lambda e: self._cat_changed())
        self.cb_wpn = ttk.Combobox(side, textvariable=self.v_wpn, state="readonly", width=44)
        self.cb_wpn.pack(anchor="w", pady=(0, 6))
        self.cb_wpn.bind("<<ComboboxSelected>>", lambda e: self._changed())

        opts = ttk.Frame(side)
        opts.pack(anchor="w")
        ttk.Checkbutton(opts, text="Spike plants", variable=self.v_plants, command=self._changed).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opts, text="Name labels on markers", variable=self.v_labels, command=self._changed).pack(side="left")
        size_row = ttk.Frame(side)
        size_row.pack(anchor="w", pady=(6, 0))
        ttk.Label(size_row, text="Marker size", style="Muted.TLabel").pack(side="left")
        ttk.Scale(size_row, from_=0.5, to=2.5, variable=self.v_size, length=150,
                  command=lambda _v: self.redraw()).pack(side="left", padx=8)
        self.count = tk.Label(side, text="", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BOLD, anchor="w")
        self.count.pack(anchor="w", pady=(8, 2))

        self.legend_title = tk.Label(side, text="Players  (click a name to show only them)", bg=theme.BG,
                                     fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w")
        self.legend_title.pack(anchor="w")
        self.legend = ttk.Frame(side)
        self.legend.pack(anchor="w", fill="x")

        self.card = ttk.Frame(side, style="Panel.TFrame")
        self.card.pack(anchor="w", fill="x", pady=(10, 0))
        self.card_head = tk.Label(self.card, text="Click a marker for details", bg=theme.PANEL, fg=theme.TEXT,
                                  font=theme.FONT_BOLD, anchor="w", justify="left", wraplength=360)
        self.card_head.pack(anchor="w", padx=10, pady=(8, 2), fill="x")
        self.card_rows = tk.Label(self.card, text="", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT, anchor="w",
                                  justify="left", wraplength=360)
        self.card_rows.pack(anchor="w", padx=10, pady=(0, 8), fill="x")

    def _radios(self, parent, var, items) -> None:
        row = ttk.Frame(parent)
        row.pack(anchor="w", pady=(0, 6))
        for text, value in items:
            ttk.Radiobutton(row, text=text, variable=var, value=value, command=self._changed).pack(side="left", padx=(0, 10))

    # ---- data ------------------------------------------------------------
    def _label_for(self, p: dict) -> str:
        name = heatmap.player_name(p)
        if self.app.my_puuid and p["puuid"] == self.app.my_puuid:
            name += " (you)"
        party = f' · {p["party"]}·{p["party_size"]}' if p.get("party") else ""
        return f'{name} · {p["agent"]}{party} · {p["kills"]}K {p["deaths"]}D'

    def set_match(self, match: dict, minimap_path=None) -> None:
        self.match = match
        self.records = heatmap.build_records(match)
        self.pm = heatmap.player_map(match)
        self.colors = heatmap.player_colors(match)
        self.sel = None
        self.photo = None
        if minimap_path and Image is not None and ImageTk is not None:
            try:
                self.photo = ImageTk.PhotoImage(Image.open(minimap_path).convert("RGB").resize((CANVAS, CANVAS)))
            except Exception:
                self.photo = None
        items = [("Everyone", "all"), ("Your team (all five)", "me"), ("Enemy team (all five)", "en")]
        used = {label for label, _ in items}
        for p in match["players"]:
            label = self._label_for(p)
            while label in used:                       # two hidden players on the same agent would clash
                label += " ~"
            used.add(label)
            items.append((label, p["puuid"]))
        self.who_keys = {label: key for label, key in items}
        self.cb_who.configure(values=[label for label, _ in items])
        default = next((label for label, key in items if key == match.get("me")), items[0][0])
        self.v_who.set(default)
        self.v_color.set("result" if self.who_keys[default] not in ("all", "me", "en") else "player")
        self.cb_round.configure(values=[ALL_ROUNDS] + [f"Round {i}" for i in range(1, len(match["rounds"]) + 1)])
        self.cb_cat.configure(values=[ALL_CATS] + heatmap.categories_present(self.records))
        self.v_type.set("both")
        self.v_side.set("all")
        self.v_round.set(ALL_ROUNDS)
        self.v_cat.set(ALL_CATS)
        self.v_plants.set(True)
        self.v_labels.set(False)
        self._fill_weapons()
        self._build_legend()
        self.redraw()

    def _build_legend(self) -> None:
        for w in list(self.legend.winfo_children()):
            w.destroy()
        match = self.match
        if not match:
            return
        mine, persp = self.app.my_puuid, match.get("me")
        persp_p = self.pm.get(persp)
        own = persp is None or persp == mine
        titles = {"me": "Your team" if own or not persp_p else f"{heatmap.player_name(persp_p)}'s team",
                  "en": "Enemy team" if own or not persp_p else "Opponents"}
        for team in ("me", "en"):
            tk.Label(self.legend, text=titles[team], bg=theme.BG, fg=theme.GREEN if team == "me" else theme.RED,
                     font=theme.FONT_SMALL, anchor="w").pack(anchor="w", pady=(4, 0))
            for p in (p for p in match["players"] if p["team"] == team):
                row = tk.Frame(self.legend, bg=theme.BG)
                row.pack(anchor="w", fill="x")
                sw = tk.Label(row, text="  ", bg=self.colors.get(p["puuid"], "#888888"))
                sw.pack(side="left", padx=(0, 6))
                name = heatmap.player_name(p) + ("  (you)" if mine and p["puuid"] == mine else "")
                party = f'  {p["party"]}·{p["party_size"]}' if p.get("party") else ""
                txt = tk.Label(row, text=f'{name}  ·  {p["agent"]}{party}  ·  {p["kills"]}/{p["deaths"]}/{p["assists"]}',
                               bg=theme.BG, fg=theme.TEXT, font=theme.FONT_SMALL, anchor="w")
                txt.pack(side="left")
                for w in (row, sw, txt):
                    w.bind("<Button-1>", lambda e, u=p["puuid"]: self._pick_player(u))

    def _pick_player(self, puuid: str) -> None:
        label = next((l for l, k in self.who_keys.items() if k == puuid), None)
        if label:
            self.v_who.set(label)
            self._who_changed()

    def _filters(self) -> dict:
        rnd = self.v_round.get()
        cat, wpn = self.v_cat.get(), self.v_wpn.get()
        return {"who": self.who_keys.get(self.v_who.get(), "all"), "type": self.v_type.get(), "side": self.v_side.get(),
                "rnd": 0 if rnd == ALL_ROUNDS else int(rnd.split()[-1]), "cat": "all" if cat == ALL_CATS else cat,
                "wpn": "any" if wpn == ANY_WEAPON else wpn, "plants": bool(self.v_plants.get())}

    def current_filters(self) -> dict:
        f = self._filters()
        f["color"] = self.v_color.get()
        return f

    def _fill_weapons(self) -> None:
        cat = self._filters()["cat"]
        self.cb_wpn.configure(values=[ANY_WEAPON] + heatmap.weapons_in(self.records, cat))
        self.v_wpn.set(ANY_WEAPON)

    def _cat_changed(self) -> None:
        self._fill_weapons()
        self._changed()

    def _who_changed(self) -> None:
        # a whole team / everyone is easier to read colored by player; a single player by result
        self.v_color.set("result" if self._filters()["who"] not in ("all", "me", "en") else "player")
        self._changed()

    def _changed(self) -> None:
        self.sel = None
        self.redraw()

    # ---- drawing -----------------------------------------------------------
    def _scale(self) -> float:
        try:
            return max(0.4, min(2.5, float(self.v_size.get())))
        except (TypeError, ValueError):
            return 1.0

    def _color(self, r: dict) -> str:
        if r["t"] == "p":
            return PLANT_COLOR
        if self.v_color.get() == "player":
            return self.colors.get(r["who"], "#888888")
        return KILL_COLOR if r["t"] == "k" else DEATH_COLOR

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        if self.photo is not None:
            c.create_image(0, 0, image=self.photo, anchor="nw")
        else:
            for g in range(0, CANVAS + 1, CANVAS // 8):
                c.create_line(g, 0, g, CANVAS, fill="#2c313a")
                c.create_line(0, g, CANVAS, g, fill="#2c313a")
        if not self.match:
            return
        self.vis = heatmap.filter_records(self.records, **self._filters())
        dense = len(self.vis) > 90
        k = self._scale()
        glow, rad = (8 if dense else 12) * k, (3 if dense else 4.5) * k
        for r in self.vis:
            if r["t"] == "p":
                continue
            x, y = r["x"] * CANVAS, r["y"] * CANVAS
            c.create_oval(x - glow, y - glow, x + glow, y + glow, outline="", fill=self._color(r), stipple="gray25")
        sel = next((r for r in self.vis if r["id"] == self.sel), None)
        if sel and sel["t"] != "p" and sel.get("ox") is not None:
            ox, oy = sel["ox"] * CANVAS, sel["oy"] * CANVAS
            c.create_line(sel["x"] * CANVAS, sel["y"] * CANVAS, ox, oy, fill="white", dash=(5, 5))
            c.create_oval(ox - 5, oy - 5, ox + 5, oy + 5, outline="white", width=2)
        labels = bool(self.v_labels.get())
        for r in self.vis:
            x, y = r["x"] * CANVAS, r["y"] * CANVAS
            col = self._color(r)
            if r["t"] == "k":
                c.create_oval(x - rad, y - rad, x + rad, y + rad, fill=col, outline="black", width=1)
            elif r["t"] == "d":
                d = rad + 1.5
                c.create_polygon(x, y - d, x + d, y, x, y + d, x - d, y, fill=col, outline="black")
            else:
                d = rad + 2
                c.create_polygon(x, y - d, x + d, y + rad, x - d, y + rad, fill=col, outline="black")
            if labels:
                c.create_text(x + rad + 4, y - rad - 4, anchor="w", fill=self.colors.get(r["who"], "#dddddd"),
                              text=heatmap.short_name(self.pm.get(r["who"])), font=("Segoe UI", 8))
        if sel:
            x, y = sel["x"] * CANVAS, sel["y"] * CANVAS
            c.create_oval(x - rad - 6, y - rad - 6, x + rad + 6, y + rad + 6, outline="white", width=2)
        n = heatmap.counts(self.vis)
        self.count.configure(text=f'{n["kills"]} kills · {n["deaths"]} deaths · {n["plants"]} plants')
        self._show_card(sel)

    def _show_card(self, rec: Optional[dict]) -> None:
        if not rec or not self.match:
            self.card_head.configure(text="Click a marker for details")
            self.card_rows.configure(text="")
            return
        d = heatmap.describe(rec, self.match)
        self.card_head.configure(text=f'{d["kind"]} · {d["head"]}')
        self.card_rows.configure(text="\n".join(f"{k}:  {v}" for k, v in d["rows"]))

    def _on_click(self, event) -> None:
        best, best_d = None, 16 * 16
        for r in self.vis:
            dx, dy = r["x"] * CANVAS - event.x, r["y"] * CANVAS - event.y
            d = dx * dx + dy * dy
            if d < best_d:
                best, best_d = r, d
        self.sel = best["id"] if best else None
        self.redraw()
