"""Live dashboard: agent select, win probability, team rosters and focused player profile."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional

from ..ranks import tier_name, tier_short
from ..services.live import display_name
from ..util import ago
from . import theme
from .widgets import RRBars, Tile, WinBar, RoundedFrame, AppleScrollbar, make_tree, sync_tree

COLUMNS = [("agent", "Agent", 90, "w"), ("party", "Party", 55, "center"), ("name", "Player", 180, "w"),
           ("rank", "Rank", 125, "w"), ("peak", "Peak", 72, "w"), ("rating", "Rating", 62, "e"),
           ("skill", "Effective skill", 150, "w"), ("acs", "ACS / ADR", 90, "e"), ("hs", "HS%", 50, "e"),
           ("wr", "WR", 50, "e"), ("flags", "Flags", 95, "w")]
# Keep every Focused Player history column visible in the narrower side panel.
GAME_COLUMNS = [("res", "", 24, "center"), ("score", "Score", 43, "center"), ("map", "Map", 52, "w"),
                ("agent", "Agent", 48, "w"), ("kda", "K/D/A", 50, "center"), ("acs", "ACS", 35, "e"),
                ("rr", "RR", 34, "e"), ("ago", "When", 48, "e")]


def row_values(e: dict) -> tuple:
    loaded = e["loaded"]
    party = f'{e["party"]}·{e["party_size"]}' if e["party"] else ""
    name = display_name(e) + ("  (you)" if e.get("is_me") else "")
    rank = theme.fmt_rank(e["tier"], e["rr"]) if (loaded or e["tier"]) else "..."
    peak = tier_short(e["peak_tier"]) if e["peak_tier"] else ("-" if loaded else "...")
    acs = f'{e["acs"]:.0f} / {e["adr"]:.0f}' if e["acs"] is not None and e["adr"] is not None else ("-" if loaded else "...")
    return (e["agent"], party, name, rank, peak, theme.fmt_rating(e["rating"]) if loaded else "...",
            theme.fmt_skill(e["skill"]) if loaded else "...", acs, theme.fmt_pct(e["hs"]) if loaded else "...",
            theme.fmt_pct(e["wr"]), ", ".join(e["flags"]))


def row_tags(e: dict) -> tuple:
    tags = [theme.rating_tag(e["rating"])]
    if e.get("is_me"):
        tags.append("me")
    return tuple(tags)


class LiveView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.entries: Dict[str, dict] = {}
        self.selected: Optional[str] = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # Header
        head = tk.Frame(self, bg=theme.BG)
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 10))
        tk.Label(head, text="LIVE MATCH", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_HERO).pack(side="left")
        self.dot = tk.Label(head, text="●", bg=theme.BG, fg=theme.MUTED, font=("Segoe UI", 12))
        self.dot.pack(side="left", padx=(12, 5))
        self.status = tk.Label(head, text="Waiting for VALORANT...", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_BOLD)
        self.status.pack(side="left")
        self.meta = tk.Label(head, text="", bg=theme.BG, fg=theme.MUTED, font=theme.FONT)
        self.meta.pack(side="left", padx=10)
        self.progress = tk.Label(head, text="", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL)
        self.progress.pack(side="right")

        # Win probability block
        winbox = RoundedFrame(self, fill=theme.PANEL, radius=16)
        winbox.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 8))
        tk.Label(winbox, text="WIN PROBABILITY", bg=theme.PANEL, fg=theme.TEXT,
                 font=theme.FONT_DISPLAY).pack(anchor="w", padx=18, pady=(12, 2))
        self.winbar = WinBar(winbox, height=38)
        self.winbar.pack(fill="x", padx=14, pady=(2, 10))
        self.conf = tk.Label(winbox, text="", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w")
        self.conf.pack(fill="x", padx=18, pady=(0, 10))

        # Dashboard columns
        dash = tk.Frame(self, bg=theme.BG)
        dash.grid(row=3, column=0, sticky="nsew", padx=28, pady=(0, 24))
        self.dash = dash
        dash.columnconfigure(0, weight=6)
        dash.columnconfigure(1, weight=5)
        dash.rowconfigure(0, weight=1)

        teams = tk.Frame(dash, bg=theme.BG)
        teams.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        teams.columnconfigure(0, weight=1)
        teams.rowconfigure(0, weight=1)
        teams.rowconfigure(1, weight=1)

        self.ally_card, self.ally_lbl, self.ally_tree = self._team_card(teams, "ALLY TEAM", theme.GREEN, 0)
        self.enemy_card, self.enemy_lbl, self.enemy_tree = self._team_card(teams, "ENEMY TEAM", theme.RED, 1)
        for tree in (self.enemy_tree, self.ally_tree):
            tree.bind("<<TreeviewSelect>>", lambda e, t=tree: self._on_select(t))
            try:
                tree.bind("<Configure>", lambda e, t=tree: self._fit_roster_columns(t), add="+")
            except TypeError:
                tree.bind("<Configure>", lambda e, t=tree: self._fit_roster_columns(t))
            tree.bind("<Motion>", lambda e, t=tree: self._roster_skill_motion(e, t))
            tree.bind("<Leave>", self._roster_skill_leave)

        self._build_detail(dash)

    def _fit_roster_columns(self, tree) -> None:
        """Fit roster columns to the actual table width instead of clipping the right side."""
        available = max(360, int(tree.winfo_width()) - 8)
        desired = [90, 55, 180, 125, 72, 62, 150, 90, 50, 50, 95]
        minimum = [32, 24, 58, 46, 28, 34, 58, 46, 26, 26, 32]
        keys = [c[0] for c in COLUMNS]
        min_total = sum(minimum)
        if available <= min_total:
            widths = minimum
        else:
            extra = available - min_total
            weights = [max(0, want - low) for want, low in zip(desired, minimum)]
            weight_total = sum(weights) or 1
            widths = [low + int(extra * weight / weight_total) for low, weight in zip(minimum, weights)]
        for key, width, low in zip(keys, widths, minimum):
            try:
                tree.column(key, width=width, minwidth=low, stretch=False)
            except (tk.TclError, TypeError):
                pass

    def _team_card(self, parent, title: str, accent: str, row: int):
        card = RoundedFrame(parent, fill=theme.PANEL, radius=16)
        card.grid(row=row, column=0, sticky="nsew", pady=(0 if row == 0 else 10, 0))
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)
        head = tk.Frame(card, bg=theme.PANEL)
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 7))
        lbl = tk.Label(head, text=title, bg=theme.PANEL, fg=accent, font=theme.FONT_TITLE, anchor="w")
        lbl.pack(side="left")
        tree = make_tree(card, COLUMNS, height=5)
        self._fit_roster_columns(tree)
        tree.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        return card, lbl, tree

    def _build_detail(self, parent) -> None:
        # Keep the focused-player panel compact while allowing the full account
        # (rank history + recent games + actions) to be scrolled vertically.
        box = RoundedFrame(parent, fill=theme.PANEL, radius=16)
        box.grid(row=0, column=1, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)

        head = tk.Frame(box, bg=theme.PANEL)
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="FOCUSED PLAYER", bg=theme.PANEL, fg=theme.SUBTLE,
                 font=theme.FONT_DISPLAY).grid(row=0, column=0, sticky="w")
        self.d_title = tk.Label(head, text="Select a player", bg=theme.PANEL, fg=theme.TEXT,
                                font=theme.FONT_BIG, anchor="w")
        self.d_title.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.d_sub = tk.Label(head, text="Click a roster row to inspect recent performance", bg=theme.PANEL,
                              fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w")
        self.d_sub.grid(row=2, column=0, sticky="ew")

        body = tk.Frame(box, bg=theme.PANEL)
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        canvas = tk.Canvas(body, bg=theme.PANEL, highlightthickness=0, bd=0)
        scrollbar = AppleScrollbar(body, command=canvas.yview_moveto)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        lower = tk.Frame(canvas, bg=theme.PANEL)
        window_id = canvas.create_window((0, 0), window=lower, anchor="nw")

        def _resize(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window_id, width=canvas.winfo_width())
        lower.bind("<Configure>", _resize)
        canvas.bind("<Configure>", _resize)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        lower.columnconfigure(0, weight=1)
        tiles = RoundedFrame(lower, fill=theme.PANEL, radius=14)
        tiles.grid(row=0, column=0, sticky="ew", padx=16, pady=(2, 4))
        for c in range(2):
            tiles.columnconfigure(c, weight=1)
        self.tiles: Dict[str, Tile] = {}
        for i, key in enumerate(["Rating", "Effective skill", "ACS", "ADR", "K/D", "HS%", "KAST", "Win rate"]):
            t = Tile(tiles, key)
            t.grid(row=i // 2, column=i % 2, padx=(0 if i % 2 == 0 else 8, 8 if i % 2 == 0 else 0), pady=4, sticky="ew")
            self.tiles[key] = t
        self._skill_mode = "session"
        self._fallback_matches = []
        self._skill_tip_after = None
        self._skill_tip_window = None
        self._roster_hover_key = None
        skill_value = self.tiles["Effective skill"].value_label
        try:
            skill_value.bind("<Enter>", self._schedule_skill_tooltip, add="+")
            skill_value.bind("<Leave>", self._hide_skill_tooltip, add="+")
        except TypeError:  # headless test double does not accept Tk's additive bind flag
            skill_value.bind("<Enter>", self._schedule_skill_tooltip)
            skill_value.bind("<Leave>", self._hide_skill_tooltip)

        account = RoundedFrame(lower, fill=theme.PANEL, radius=14)
        account.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 12))
        account.columnconfigure(0, weight=1)
        self.d_rank = tk.Label(account, text="", bg=theme.PANEL, fg=theme.TEXT, font=theme.FONT_SMALL,
                               anchor="w", justify="left")
        self.d_rank.grid(row=0, column=0, sticky="ew")
        self.rrbars = RRBars(account, height=62)
        self.rrbars.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self.d_net = tk.Label(account, text="", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w")
        self.d_net.grid(row=2, column=0, sticky="ew", pady=(2, 8))
        self.games_tree = make_tree(account, GAME_COLUMNS, height=5)
        # Fixed compact widths prevent the right edge of the history table being clipped.
        for key, _heading, width, _anchor in GAME_COLUMNS:
            self.games_tree.column(key, width=width, minwidth=max(22, min(width, 28)), stretch=False)
        self.games_tree.grid(row=3, column=0, sticky="ew")
        self.games_tree.bind("<Double-1>", lambda e: self._open_game())
        self.games_tree.bind("<Return>", lambda e: self._open_game())
        btns = tk.Frame(account, bg=theme.PANEL)
        btns.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.btn_profile = ttk.Button(btns, text="Open profile", style="Accent.TButton", command=self._open_profile)
        self.btn_profile.pack(side="left")
        self.btn_watch = ttk.Button(btns, text="Watchlist", command=self._toggle_watch)
        self.btn_watch.pack(side="left", padx=8)
        self.btn_profile.state(["disabled"])
        self.btn_watch.state(["disabled"])

    def _roster_skill_motion(self, event, tree) -> None:
        """Show fallback details only when the pointer is over a player's Effective Skill cell."""
        try:
            if tree.identify_region(event.x, event.y) != "cell" or tree.identify_column(event.x) != "#7":
                self._roster_skill_leave()
                return
            puuid = tree.identify_row(event.y)
        except (tk.TclError, AttributeError):
            return
        if not puuid:
            self._roster_skill_leave()
            return
        key = (id(tree), puuid)
        if key == self._roster_hover_key:
            return
        self._roster_hover_key = key
        player = self.entries.get(puuid, {})
        self._hide_skill_tooltip()
        if player.get("skill_mode") == "fallback" and player.get("fallback_matches"):
            self._schedule_skill_tooltip(event, player.get("fallback_matches"))

    def _roster_skill_leave(self, _event=None) -> None:
        self._roster_hover_key = None
        self._hide_skill_tooltip()

    def _schedule_skill_tooltip(self, event=None, matches=None) -> None:
        matches = self._fallback_matches if matches is None else matches
        if not matches:
            return
        if self._skill_tip_after:
            try:
                self.after_cancel(self._skill_tip_after)
            except tk.TclError:
                pass
        self._skill_tip_after = self.after(250, lambda e=event, m=list(matches): self._show_skill_tooltip(e, m))

    def _show_skill_tooltip(self, event=None, matches=None) -> None:
        self._skill_tip_after = None
        matches = self._fallback_matches if matches is None else matches
        if not matches:
            return
        self._hide_skill_tooltip()
        win = tk.Toplevel(self)
        self._skill_tip_window = win
        win.wm_overrideredirect(True)
        win.configure(bg=theme.BORDER)
        try:
            x = (event.x_root + 14) if event else self.winfo_pointerx() + 14
            y = (event.y_root + 12) if event else self.winfo_pointery() + 12
            win.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
        panel = tk.Frame(win, bg=theme.PANEL, padx=12, pady=10, highlightbackground=theme.BORDER, highlightthickness=1)
        panel.pack(padx=1, pady=1)
        tk.Label(panel, text="EFFECTIVE SKILL · FALLBACK", bg=theme.PANEL, fg=theme.AMBER,
                 font=theme.FONT_BOLD, anchor="w").pack(fill="x")
        tk.Label(panel, text="No matches found within the last 5 hours. Using the previous three-match formula.",
                 bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL, justify="left", wraplength=270).pack(
            anchor="w", pady=(3, 8))
        for match in matches[:3]:
            row = tk.Frame(panel, bg=theme.PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=str(match.get("score") or "Score unavailable"), bg=theme.PANEL, fg=theme.TEXT,
                     font=theme.FONT_BOLD, width=16, anchor="w").pack(side="left")
            rr = int(match.get("rr") or 0)
            rr_color = theme.GREEN if rr > 0 else theme.RED if rr < 0 else theme.MUTED
            tk.Label(row, text=f"RR {theme.signed(rr)}", bg=theme.PANEL, fg=rr_color,
                     font=theme.FONT_SMALL, width=10, anchor="e").pack(side="left", padx=(2, 8))
            stamp = match.get("started_ms")
            tk.Label(row, text=ago(stamp) if stamp else "time unknown", bg=theme.PANEL, fg=theme.MUTED,
                     font=theme.FONT_SMALL, anchor="e").pack(side="right")

    def _hide_skill_tooltip(self, event=None) -> None:
        if self._skill_tip_after:
            try:
                self.after_cancel(self._skill_tip_after)
            except tk.TclError:
                pass
            self._skill_tip_after = None
        if self._skill_tip_window is not None:
            try:
                self._skill_tip_window.destroy()
            except tk.TclError:
                pass
            self._skill_tip_window = None

    def show_idle(self, text: str = "Waiting for a match") -> None:
        self.dot.configure(fg=theme.MUTED)
        self.status.configure(text=text)
        self.meta.configure(text="")
        self.progress.configure(text="")

    def update_lobby(self, lobby: dict) -> None:
        ingame = lobby["phase"] == "ingame"
        self.dot.configure(fg=theme.GREEN if ingame else theme.AMBER)
        self.status.configure(text="IN MATCH" if ingame else "AGENT SELECT")
        self.meta.configure(text=f'{lobby["map"]}' + (f'  ·  {lobby["queue"]}' if lobby.get("queue") else ""))
        self.progress.configure(text="" if lobby["final"] else f'Loading stats {lobby["loaded"]}/{lobby["total"]}')
        self.entries = {p["puuid"]: p for p in lobby["players"]}
        allies = [p for p in lobby["players"] if p["team"] == "ally"]
        enemies = [p for p in lobby["players"] if p["team"] == "enemy"]
        sync_tree(self.ally_tree, [(p["puuid"], row_values(p), row_tags(p)) for p in allies])
        sync_tree(self.enemy_tree, [(p["puuid"], row_values(p), row_tags(p)) for p in enemies])
        if enemies:
            self.enemy_lbl.configure(text="ENEMY TEAM")
        else:
            n = lobby.get("enemy_size")
            who = f"{n} players, " if n else ""
            self.enemy_lbl.configure(text=f"ENEMY TEAM  ({who}VALORANT only reveals them when the match loads)")
        win = lobby.get("win")
        if win:
            self.winbar.set(win)
            party = ""
            if win["my_bonus"] or win["en_bonus"]:
                party = f'  ·  party bonus: you +{win["my_bonus"]}, enemy +{win["en_bonus"]}'
            thin = f'  ·  {win["thin"]} player(s) with thin recent data' if win["thin"] else ""
            self.conf.configure(text=f'Estimate  ·  {win["confidence"]} confidence{thin}  ·  avg skill '
                                     f'{win["my_avg"]:,.0f} vs {win["en_avg"]:,.0f}{party}')
        else:
            self.winbar.set(None, "Win estimate appears once enough player data is available" if not enemies else "")
            avg = lobby.get("ally_avg")
            self.conf.configure(text=f"Your team's average skill so far: {avg:,.0f}" if (avg and not enemies) else "")

    def show_detail(self, d: dict) -> None:
        if d["puuid"] != self.selected:
            return
        e = self.entries.get(d["puuid"], {})
        mmr, s = d["mmr"], d["summary"]
        self.d_title.configure(text=f'{display_name(e) if e else d["puuid"]}  ·  {e.get("agent", "") or "No agent"}')
        last = ago(d["last_played_ms"]) if d.get("last_played_ms") else "unknown"
        self.d_sub.configure(text=f'{theme.fmt_rank(mmr["tier"], mmr["rr"])}  ·  last game {last}')
        self.tiles["Rating"].set(theme.fmt_rating(s["rating"]) if s["rating"] is not None else "-", "recent",
                                  theme.GREEN if (s["rating"] or 0) >= 1.05 else None)
        self._skill_mode = d.get("skill_mode", s.get("skill_mode", "fallback"))
        self._fallback_matches = d.get("fallback_matches", s.get("fallback_matches", [])) or []
        skill_sub = (theme.fmt_skill(d["skill"]).split("≈")[-1].strip() if d["skill"] is not None else "")
        if self._skill_mode == "fallback":
            skill_sub = (skill_sub + " · " if skill_sub else "") + "Fallback · last 3"
        else:
            skill_sub = (skill_sub + " · " if skill_sub else "") + f'Session · {s.get("games", 0)} games'
        skill_color = theme.GREEN if (s["rating"] or 0) >= 1.05 else (theme.AMBER if self._skill_mode == "fallback" else None)
        self.tiles["Effective skill"].set(f'{d["skill"]:,.0f}' if d["skill"] is not None else "-",
                                          skill_sub, skill_color)
        self._hide_skill_tooltip()
        self.tiles["ACS"].set(theme.fmt_num(s["acs"]))
        self.tiles["ADR"].set(theme.fmt_num(s["adr"]))
        self.tiles["K/D"].set(theme.fmt_num(s["kd"], 2))
        self.tiles["HS%"].set(theme.fmt_pct(s["hs"]))
        self.tiles["KAST"].set(theme.fmt_pct(s["kast"]))
        self.tiles["Win rate"].set(theme.fmt_pct(mmr["wr"]), f'{mmr["act_games"]} games this act' if mmr["act_games"] else "")
        peak = tier_name(mmr["peak_tier"]) if mmr["peak_tier"] else "-"
        lines = [f'Peak: {peak}' + (f'  ·  {mmr["peak_label"]}' if mmr["peak_label"] else "")]
        acts = [f'{a["label"]}: {tier_name(a["tier"]) if a["tier"] else "No games"}' for a in mmr["acts"]]
        if acts:
            lines.append("Last 3 acts:  " + "   |   ".join(acts))
        self.d_rank.configure(text="\n".join(lines))
        self.rrbars.set(d["rr_series"])
        self.d_net.configure(text=f'RR change, last {len(d["games"])} games  ·  net {theme.signed(d["net_rr"])}')
        rows = []
        for g in d["games"]:
            kda = f'{g["k"]}/{g["d"]}/{g["a"]}' if g["k"] is not None else "-"
            rows.append((str(g["match_id"]), (g["result"], g["score"], g["map"], g["agent"], kda,
                                                   f'{g["acs"]:.0f}' if g["acs"] is not None else "-",
                                                   theme.signed(g["rr"]), ago(g["started_ms"])),
                         ("win",) if g["result"] == "W" else ("loss",) if g["result"] == "L" else ("norm",)))
        sync_tree(self.games_tree, rows)

    def _on_select(self, tree) -> None:
        sel = tree.selection()
        if not sel:
            return
        puuid = sel[0]
        other = self.ally_tree if tree is self.enemy_tree else self.enemy_tree
        if other.selection():
            other.selection_remove(*other.selection())
        if puuid == self.selected:
            return
        self.selected = puuid
        e = self.entries.get(puuid, {})
        self.d_title.configure(text=f'{display_name(e) if e else puuid}  ·  loading...')
        self.btn_profile.state(["!disabled"])
        self.btn_watch.state(["!disabled"])
        self.btn_watch.configure(text="Remove from watchlist" if self.app.cfg.in_watchlist(puuid) else "Add to watchlist")
        self.app.request_detail(puuid)

    def _open_game(self) -> None:
        sel = self.games_tree.selection()
        if sel and self.selected:
            self.app.open_history_match(sel[0], self.selected)

    def _open_profile(self) -> None:
        if self.selected:
            e = self.entries.get(self.selected, {})
            self.app.open_profile(self.selected, e.get("name", ""), e.get("tag", ""))

    def _toggle_watch(self) -> None:
        if not self.selected:
            return
        e = self.entries.get(self.selected, {})
        on = self.app.cfg.toggle_watchlist(self.selected, display_name(e) if e else "")
        try:
            self.app.cfg.save()
        except OSError:
            pass
        self.btn_watch.configure(text="Remove from watchlist" if on else "Watchlist")
        self.app.set_status("Added to watchlist. It will be flagged in future lobbies." if on else "Removed from watchlist.")
