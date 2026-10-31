"""Profile tab: search any Riot ID and see rank, act history, stats, agents, weapons and match history."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional

from ..ranks import tier_name
from ..util import ago
from . import theme
from .widgets import Tile, RoundedFrame, make_tree, sync_tree

AGENT_COLUMNS = [("agent", "Agent", 120, "w"), ("games", "Games", 60, "e"), ("wr", "WR", 60, "e"),
                 ("acs", "ACS", 60, "e"), ("rating", "Rating", 70, "e")]
MATCH_COLUMNS = [("res", "", 30, "center"), ("score", "Score", 70, "center"), ("map", "Map", 110, "w"),
                 ("agent", "Agent", 100, "w"), ("kda", "K/D/A", 80, "center"), ("acs", "ACS", 55, "e"),
                 ("adr", "ADR", 55, "e"), ("rr", "RR", 55, "e"), ("ago", "When", 90, "e")]


class ProfileView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.columnconfigure(0, weight=1)

        bar = tk.Frame(self, bg=theme.BG)
        bar.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 8))
        tk.Label(bar, text="PROFILE", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_HERO).pack(anchor="w")
        tk.Label(bar, text="Your VALORANT profile. It is loaded automatically from the Riot Client.",
                 bg=theme.BG, fg=theme.MUTED, font=theme.FONT).pack(anchor="w", pady=(3, 0))
        ttk.Button(bar, text="Refresh", style="Accent.TButton", command=self.app.open_own_profile).pack(anchor="e", pady=(8, 0))
        self.msg = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w")
        self.msg.grid(row=1, column=0, sticky="ew", padx=28)

        self.title = tk.Label(self, text="", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BIG, anchor="w")
        self.title.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 0))
        self.subtitle = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, font=theme.FONT, anchor="w")
        self.subtitle.grid(row=3, column=0, sticky="ew", padx=12)

        tiles = ttk.Frame(self, style="Panel.TFrame")
        tiles.grid(row=4, column=0, sticky="ew", padx=12, pady=8)
        self.tiles: Dict[str, Tile] = {}
        for i, key in enumerate(["Current rank", "Peak", "This act", "Win rate", "K/D", "ACS", "ADR", "HS%", "KAST"]):
            t = Tile(tiles, key)
            t.grid(row=0, column=i, padx=10, pady=8, sticky="w")
            self.tiles[key] = t

        self.acts_lbl = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w")
        self.acts_lbl.grid(row=5, column=0, sticky="ew", padx=12)

        mid = ttk.Frame(self)
        mid.grid(row=6, column=0, sticky="ew", padx=12, pady=8)
        mid.columnconfigure(0, weight=1)
        self.agent_tree = make_tree(mid, AGENT_COLUMNS, height=5)
        self.agent_tree.grid(row=0, column=0, sticky="ew")
        self.weapons = tk.Label(mid, text="", bg=theme.BG, fg=theme.TEXT, font=theme.FONT, anchor="w", justify="left")
        self.weapons.grid(row=0, column=1, sticky="nw", padx=16)

        self.match_tree = make_tree(self, MATCH_COLUMNS, height=8)
        self.match_tree.grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.recent: list = []
        self.puuid = None
        self.match_tree.bind("<Double-1>", lambda e: self._open_match())
        self.match_tree.bind("<Return>", lambda e: self._open_match())
        tk.Label(self, text="Double-click a match to open its leaderboard and heatmap", bg=theme.BG, fg=theme.MUTED,
                 font=theme.FONT_SMALL, anchor="w").grid(row=8, column=0, sticky="ew", padx=12)

    # ---- search ----------------------------------------------------------
    def load(self, puuid: str, name: str, tag: str) -> None:
        """Open a profile by puuid (e.g. from the live roster)."""
        self.msg.configure(text="Loading your profile...", fg=theme.MUTED)
        self.app.load_profile(puuid, name, tag)

    def show_error(self, text: str) -> None:
        self.msg.configure(text=text, fg=theme.RED)

    def show_progress(self, done: int, total: int) -> None:
        self.msg.configure(text=f"Loading match history {done}/{total}...", fg=theme.MUTED)

    def _open_match(self) -> None:
        sel = self.match_tree.selection()
        if sel and self.puuid:
            self.app.open_history_match(sel[0], self.puuid)

    def show(self, prof: dict) -> None:
        self.puuid = prof["puuid"]
        mmr, st = prof["mmr"], prof["stats"]
        label = f'{prof["name"]}#{prof["tag"]}' if prof.get("name") else "Player"
        self.title.configure(text=label)
        self.subtitle.configure(text=f'{mmr.get("current_label") or ""}   ·   last {st["games"]} competitive games loaded')
        self.msg.configure(text="", fg=theme.MUTED)
        self.tiles["Current rank"].set(tier_name(mmr["tier"]) if mmr["tier"] else "Unranked",
                                       f'{mmr["rr"]} RR' if mmr["tier"] else "")
        self.tiles["Peak"].set(tier_name(mmr["peak_tier"]) if mmr["peak_tier"] else "-", mmr["peak_label"])
        self.tiles["This act"].set(str(mmr["act_games"]), f'{mmr["act_wins"]}W {mmr["act_games"] - mmr["act_wins"]}L')
        self.tiles["Win rate"].set(theme.fmt_pct(mmr["wr"]) if mmr["wr"] is not None else theme.fmt_pct(st.get("wr")), "act" if mmr["wr"] is not None else "recent")
        self.tiles["K/D"].set(theme.fmt_num(st.get("kd"), 2))
        self.tiles["ACS"].set(theme.fmt_num(st.get("acs")))
        self.tiles["ADR"].set(theme.fmt_num(st.get("adr")))
        self.tiles["HS%"].set(theme.fmt_pct(st.get("hs")))
        self.tiles["KAST"].set(theme.fmt_pct(st.get("kast")))
        acts = [f'{a["label"]}: {tier_name(a["tier"]) if a["tier"] else "No games"}' for a in mmr["acts"]]
        self.acts_lbl.configure(text=("Last 3 acts:  " + "   |   ".join(acts)) if acts else "")
        sync_tree(self.agent_tree, [(a["agent"], (a["agent"], a["games"], theme.fmt_pct(a["wr"]), f'{a["acs"]:.0f}',
                                                    theme.fmt_rating(a["rating"])), (theme.rating_tag(a["rating"]),))
                                    for a in prof["agents"]])
        self.weapons.configure(text="Weapons (share of kills)\n" + ("\n".join(
            f'{w["weapon"]}   {w["share"] * 100:.0f}%' for w in prof["weapons"]) if prof["weapons"] else "-"))
        rows = []
        for i, g in enumerate(prof["matches"]):
            kda = f'{g["k"]}/{g["d"]}/{g["a"]}' if g["k"] is not None else "-"
            rows.append((str(g["match_id"]), (g["result"], g["score"], g["map"], g["agent"], kda,
                                                   f'{g["acs"]:.0f}' if g["acs"] is not None else "-",
                                                   f'{g["adr"]:.0f}' if g["adr"] is not None else "-",
                                                   theme.signed(g["rr"]), ago(g["started_ms"])),
                         ("win",) if g["result"] == "W" else ("loss",) if g["result"] == "L" else ("norm",)))
        sync_tree(self.match_tree, rows)
