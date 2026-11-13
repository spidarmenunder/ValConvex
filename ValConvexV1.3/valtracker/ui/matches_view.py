"""Tracker search: simple Riot ID search with recent searches and local match history."""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import List

from ..ranks import tier_name
from . import theme
from .widgets import Tile, RoundedFrame, make_tree, sync_tree

COLUMNS = [("when", "When", 140, "w"), ("map", "Map", 110, "w"), ("queue", "Queue", 90, "w"),
           ("result", "Result", 70, "w"), ("score", "Score", 60, "center"), ("src", "Source", 90, "w")]
RESULT_COLUMNS = [("res", "", 34, "center"), ("score", "Score", 70, "center"), ("map", "Map", 100, "w"),
                  ("agent", "Agent", 90, "w"), ("kda", "K/D/A", 72, "center"), ("acs", "ACS", 55, "e"),
                  ("rr", "RR", 55, "e"), ("ago", "When", 85, "e")]


class MatchesView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.search_active = False
        self.recent_searches: List[str] = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        head = tk.Frame(self, bg=theme.BG)
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 8))
        tk.Label(head, text="TRACKER", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_HERO).pack(anchor="w")
        tk.Label(head, text="Search a Riot ID or open a match from your history.", bg=theme.BG,
                 fg=theme.MUTED, font=theme.FONT).pack(anchor="w", pady=(3, 0))

        search = RoundedFrame(self, fill=theme.PANEL, radius=16)
        search.grid(row=1, column=0, sticky="ew", padx=28, pady=(8, 10))
        search.columnconfigure(0, weight=1)
        self.v_search = tk.StringVar()
        self.entry = ttk.Entry(search, textvariable=self.v_search, font=("Segoe UI", 12))
        self.entry.grid(row=0, column=0, sticky="ew", padx=14, pady=14, ipady=7)
        self.entry.bind("<Return>", lambda e: self.search())
        ttk.Button(search, text="Search", style="Accent.TButton", command=self.search).grid(row=0, column=1, padx=(0, 14), pady=14, ipady=3)

        recent = tk.Frame(self, bg=theme.BG)
        recent.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 12))
        tk.Label(recent, text="RECENT SEARCHES", bg=theme.BG, fg=theme.SUBTLE, font=theme.FONT_DISPLAY).pack(anchor="w")
        self.recent_box = tk.Frame(recent, bg=theme.BG)
        self.recent_box.pack(fill="x", pady=(7, 0))
        self.msg = tk.Label(recent, text="", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w")
        self.msg.pack(fill="x", pady=(7, 0))

        body = tk.Frame(self, bg=theme.BG)
        body.grid(row=3, column=0, sticky="nsew", padx=28, pady=(0, 24))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self.result = RoundedFrame(body, fill=theme.PANEL, radius=16)
        self.result.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.result.columnconfigure(0, weight=1)
        self.result_title = tk.Label(self.result, text="Search for a player", bg=theme.PANEL, fg=theme.TEXT,
                                     font=theme.FONT_BIG, anchor="w")
        self.result_title.grid(row=0, column=0, sticky="w", padx=18, pady=(15, 2))
        self.result_sub = tk.Label(self.result, text="Enter a Riot ID like Name#TAG", bg=theme.PANEL, fg=theme.MUTED,
                                   font=theme.FONT_SMALL, anchor="w")
        self.result_sub.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 14))
        self.tiles = {}
        tile_row = tk.Frame(self.result, bg=theme.PANEL)
        tile_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 14))
        for i, key in enumerate(["Current rank", "Peak", "Win rate", "K/D", "ACS", "ADR"]):
            tile_row.columnconfigure(i, weight=1)
            t = Tile(tile_row, key)
            t.grid(row=0, column=i, padx=6, sticky="ew")
            self.tiles[key] = t

        history_box = RoundedFrame(body, fill=theme.PANEL, radius=16)
        history_box.grid(row=1, column=0, sticky="nsew")
        history_box.rowconfigure(1, weight=1)
        history_box.columnconfigure(0, weight=1)
        tk.Label(history_box, text="MATCH HISTORY", bg=theme.PANEL, fg=theme.TEXT,
                 font=theme.FONT_TITLE, anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(13, 8))
        self.tree = make_tree(history_box, RESULT_COLUMNS, height=9)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self.tree.bind("<Double-1>", lambda e: self._open())
        self.tree.bind("<Return>", lambda e: self._open())
        self.history_tree = self.tree

    def search(self) -> None:
        text = self.v_search.get().strip()
        if not text:
            return
        self.search_active = True
        self._remember(text)
        self.msg.configure(text=f"Searching for {text}...", fg=theme.MUTED)
        self.app.search_profile(text)

    def _remember(self, label: str) -> None:
        if label in self.recent_searches:
            self.recent_searches.remove(label)
        self.recent_searches.insert(0, label)
        del self.recent_searches[6:]
        for w in self.recent_box.winfo_children():
            w.destroy()
        for item in self.recent_searches:
            ttk.Button(self.recent_box, text=item, command=lambda x=item: self._search_item(x)).pack(side="left", padx=(0, 7))

    def _search_item(self, text: str) -> None:
        self.v_search.set(text)
        self.search()

    def show_search_result(self, prof: dict) -> None:
        self.search_active = True
        mmr, st = prof["mmr"], prof["stats"]
        label = f'{prof["name"]}#{prof["tag"]}' if prof.get("name") else "Player"
        self.result_title.configure(text=label)
        self.result_sub.configure(text=f'{mmr.get("current_label") or "Unranked"}  ·  {st["games"]} competitive games loaded', fg=theme.MUTED)
        self.msg.configure(text="Player found. Double-click a match below to open the full leaderboard and heatmap.", fg=theme.GREEN)
        self.tiles["Current rank"].set(tier_name(mmr["tier"]) if mmr["tier"] else "Unranked", f'{mmr["rr"]} RR' if mmr["tier"] else "")
        self.tiles["Peak"].set(tier_name(mmr["peak_tier"]) if mmr["peak_tier"] else "-", mmr["peak_label"])
        self.tiles["Win rate"].set(theme.fmt_pct(mmr["wr"]) if mmr["wr"] is not None else theme.fmt_pct(st.get("wr")))
        self.tiles["K/D"].set(theme.fmt_num(st.get("kd"), 2))
        self.tiles["ACS"].set(theme.fmt_num(st.get("acs")))
        self.tiles["ADR"].set(theme.fmt_num(st.get("adr")))
        rows = []
        for g in prof["matches"]:
            kda = f'{g["k"]}/{g["d"]}/{g["a"]}' if g["k"] is not None else "-"
            rows.append((str(g["match_id"]), (g["result"], g["score"], g["map"], g["agent"], kda,
                                                f'{g["acs"]:.0f}' if g["acs"] is not None else "-",
                                                theme.signed(g["rr"]), time.strftime("%Y-%m-%d", time.localtime(g["started_ms"] / 1000)) if g["started_ms"] else "-"),
                         ("win",) if g["result"] == "W" else ("loss",) if g["result"] == "L" else ("norm",)))
        sync_tree(self.tree, rows)
        self._search_puuid = prof["puuid"]

    def refresh(self) -> None:
        # Local saved matches now live in the dedicated Match History view.
        # Tracker's table is reserved for the searched player's match history.
        if not self.search_active:
            self._search_puuid = None
            sync_tree(self.tree, [])
            self.result_title.configure(text="Search for a player")
            self.result_sub.configure(text="Enter a Riot ID like Name#TAG")
            self.msg.configure(text="Your saved matches are in Match History.", fg=theme.MUTED)

    def _open(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        if getattr(self, "_search_puuid", None):
            self.app.open_history_match(sel[0], self._search_puuid)
        else:
            self.app.open_match(sel[0])

    def import_file(self) -> None:
        path = filedialog.askopenfilename(title="Import a ValConvex match",
                                          filetypes=[("ValConvex match", "*.vtrack"), ("All files", "*.*")])
        if path:
            self.app.import_match(path)
