"""Post-match tab: readable player cards, rank/agent imagery, heatmap, and sharing."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .. import export, heatmap
from ..ranks import tier_name
from . import theme
from .heatmap_view import HeatmapView
from .widgets import make_tree, sync_tree

_ICON_PHOTOS = {}


def _photo(path, size):
    if not path:
        return None
    key = (str(path), int(size))
    if key in _ICON_PHOTOS:
        return _ICON_PHOTOS[key]
    try:
        from PIL import Image, ImageTk
        with Image.open(path) as source:
            resized = source.convert("RGBA").resize((size, size))
        photo = ImageTk.PhotoImage(resized)
        _ICON_PHOTOS[key] = photo
        return photo
    except Exception:
        return None


_LEADERBOARD_PHOTOS = {}


def _leaderboard_badge(app, player, size=34):
    try:
        from PIL import Image, ImageTk
        aid = str(player.get("agent_id") or "").lower()
        if not aid and player.get("agent"):
            aid = str(app.static.agent_uuid_by_name(player.get("agent")) or "").lower()
        tier = int(player.get("tier") or 0)
        ap = app.static.agent_icon_path(aid) if aid else None
        rp = app.static.rank_icon_path(tier, "small") if tier >= 3 else None
        if not ap and not rp:
            return None
        key = (str(ap or ""), str(rp or ""), size)
        if key in _LEADERBOARD_PHOTOS:
            return _LEADERBOARD_PHOTOS[key]
        out = Image.new("RGBA", (size * 2 + 6, size), (0, 0, 0, 0))
        if ap:
            with Image.open(ap) as source:
                im = source.convert("RGBA").resize((size, size))
            out.alpha_composite(im, (0, 0))
        if rp:
            with Image.open(rp) as source:
                im = source.convert("RGBA").resize((size, size))
            out.alpha_composite(im, (size + 6, 0))
        ph = ImageTk.PhotoImage(out)
        _LEADERBOARD_PHOTOS[key] = ph
        return ph
    except Exception:
        return None



class PostMatchView(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app; self.match: Optional[dict] = None
        self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1)
        head = tk.Frame(self, bg=theme.BG); head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 7))
        head.columnconfigure(1, weight=1)
        self.result = tk.Label(head, text="No match yet", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_BIG, anchor="w"); self.result.grid(row=0, column=0, sticky="w")
        self.meta = tk.Label(head, text="Finish a game (or import a .vtrack file) and it appears here.", bg=theme.BG, fg=theme.MUTED, font=theme.FONT, anchor="w", justify="left"); self.meta.grid(row=1, column=0, sticky="w")
        self.ready = tk.Label(head, text="", bg=theme.BG, fg=theme.GREEN, font=theme.FONT_SMALL); self.ready.grid(row=0, column=1, sticky="e", padx=8)
        self.v_anon = tk.BooleanVar(value=bool(app.cfg.anonymize_exports)); self.v_auto = tk.BooleanVar(value=bool(app.cfg.autosave))
        self.save_btn = ttk.Menubutton(head, text="Save / share")
        menu = tk.Menu(self.save_btn, tearoff=0)
        menu.add_command(label="Match file (.vtrack)  - opens in ValConvex", command=self._save_vtrack)
        menu.add_command(label="Standalone page (.html)  - opens in any browser", command=self._save_html)
        menu.add_command(label="Image (.png)  - current heatmap view", command=self._save_png)
        menu.add_separator(); menu.add_checkbutton(label="Hide other players' names", variable=self.v_anon, command=self._prefs); menu.add_checkbutton(label="Autosave every finished match", variable=self.v_auto, command=self._prefs)
        menu.add_separator(); menu.add_command(label="Open matches folder", command=self.app.open_matches_folder)
        self.save_btn["menu"] = menu; self.save_btn.grid(row=0, column=3, rowspan=2, sticky="e")

        tabs = ttk.Notebook(self); tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.tabs = tabs

        # Restored full leaderboard tab. Agent + rank icons live in the compact badge column;
        # rank text still spells out the individual tier and RR (e.g. Immortal 2 · 74 RR).
        leaderboard = ttk.Frame(tabs)
        leaderboard.rowconfigure(0, weight=1); leaderboard.columnconfigure(0, weight=1)
        columns = [("player", "Player", 260, "w"), ("party", "Party", 65, "center"),
                   ("rank", "Rank", 170, "w"), ("kda", "K/D/A", 90, "center"),
                   ("acs", "ACS", 70, "e"), ("adr", "ADR", 70, "e"),
                   ("hs", "HS%", 70, "e"), ("kast", "KAST", 70, "e"),
                   ("rating", "Rating", 78, "e")]
        self.leaderboard_tree = make_tree(leaderboard, columns, height=12, image_column=True)
        self.leaderboard_tree.grid(row=0, column=0, sticky="nsew")
        self.leaderboard_tree.bind("<Double-1>", self._open_leaderboard_player)
        self.leaderboard_tree.bind("<Button-3>", self._leaderboard_menu)
        tabs.add(leaderboard, text="Leaderboard")

        # Post-match intentionally keeps the focused views to Leaderboard + Heatmap.
        # Teams was redundant with the leaderboard and is no longer exposed here.
        self.heat = HeatmapView(tabs, app); tabs.add(self.heat, text="Heatmap")

    def _selected_leaderboard_player(self):
        sel = self.leaderboard_tree.selection()
        if not sel or not self.match:
            return None
        return next((p for p in self.match.get("players", []) if p.get("puuid") == sel[0]), None)

    def _open_leaderboard_player(self, _event=None):
        p = self._selected_leaderboard_player()
        if p and p.get("puuid"):
            self.app.open_profile(p["puuid"], p.get("name") or "", p.get("tag") or "")

    def _leaderboard_menu(self, event):
        iid = self.leaderboard_tree.identify_row(event.y)
        if iid:
            self.leaderboard_tree.selection_set(iid)
        p = self._selected_leaderboard_player()
        if not p:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Open player tracker", command=lambda: self.app.open_profile(p["puuid"], p.get("name") or "", p.get("tag") or ""))
        menu.add_command(label="Copy Riot ID", command=lambda: self.clipboard_append(f'{p.get("name", "")}#{p.get("tag", "")}'))
        menu.tk_popup(event.x_root, event.y_root)

    def show(self, match: dict, minimap_path=None) -> None:
        same_match = self.match is not None and self.match.get("match_id") == match.get("match_id")
        current_tab = self.tabs.index(self.tabs.select()) if same_match and self.tabs.select() else 0
        self.match = match; res = match["result"]
        text, color = (("Draw", theme.MUTED) if res["draw"] else ("Victory", theme.GREEN) if res["won"] else ("Defeat", theme.RED))
        self.result.configure(text=text, fg=color)
        import time
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(match["started_ms"] / 1000)) if match.get("started_ms") else ""
        lines = f'{match["map"]["name"]} · {match["queue"] or "custom"} · {res["score"][0]}-{res["score"][1]}' + (f" · {when}" if when else "")
        if match.get("pre_win_prob") is not None: lines += f'\nPre-match estimate was {match["pre_win_prob"] * 100:.0f}%'
        mine, persp = self.app.my_puuid, match.get("me")
        persp_p = next((p for p in match["players"] if p["puuid"] == persp), None)
        if persp and persp != mine and persp_p:
            lines += f'\nViewing from {heatmap.player_name(persp_p)}\'s side'
        self.meta.configure(text=lines); secs = match.get("process_seconds"); self.ready.configure(text=f"Ready in {secs:.1f} s" if secs is not None else "Loaded from saved file")
        lb_rows = []
        self._leaderboard_photos = []
        for p in sorted(match.get("players", []), key=lambda x: (-(float(x.get("rating") or 0)), -(int(x.get("kills") or 0)))):
            name = heatmap.player_name(p)
            if mine and p.get("puuid") == mine:
                name += "  (you)"
            rank_text = tier_name(p.get("tier")) if p.get("tier") else "Unranked"
            if p.get("rr") is not None and p.get("tier"):
                rank_text += f" · {int(p['rr'])} RR"
            badge = _leaderboard_badge(self.app, p, 30)
            if badge:
                self._leaderboard_photos.append(badge)
            lb_rows.append((p["puuid"], (name, f'{p.get("party") or ""}·{p.get("party_size") or ""}' if p.get("party") else "", rank_text,
                             f'{p.get("kills",0)}/{p.get("deaths",0)}/{p.get("assists",0)}',
                             f'{p.get("acs",0):.0f}' if p.get("acs") is not None else "-",
                             f'{p.get("adr",0):.0f}' if p.get("adr") is not None else "-", theme.fmt_pct(p.get("hs")),
                             theme.fmt_pct(p.get("kast")), theme.fmt_rating(p.get("rating")) if p.get("rating") is not None else "-"),
                             (theme.rating_tag(p.get("rating")), "team_me" if p.get("team") == "me" else "team_en") + (("me",) if p.get("puuid") == match.get("me") else ())))
        sync_tree(self.leaderboard_tree, lb_rows)
        for p in match.get("players", []):
            badge = _leaderboard_badge(self.app, p, 30)
            if badge:
                try: self.leaderboard_tree.item(p["puuid"], image=badge)
                except Exception: pass
        self.heat.set_match(match, minimap_path)
        # show() can be called again when the minimap finishes downloading. Preserve
        # the user's current tab on same-match refreshes instead of jumping to Leaderboard.
        if same_match:
            self.tabs.select(current_tab)
        else:
            self.tabs.select(0)
        self._load_images_async(match)

    def _load_images_async(self, match):
        def job():
            try:
                for p in match.get("players", []):
                    aid = p.get("agent_id") or self.app.static.agent_uuid_by_name(p.get("agent"))
                    if aid: self.app.static.ensure_agent_icon(aid)
                    if p.get("tier"): self.app.static.ensure_rank_icon(p.get("tier"), "small")
                self.app.q.put(("post_icons", None))
            except Exception: pass
        threading.Thread(target=job, daemon=True, name="postmatch-icons").start()

    def redraw_icons(self):
        """Attach newly downloaded icons without rebuilding the page or restarting the loader.

        Calling show() here used to start another _load_images_async() worker, which
        queued another post_icons event. That created an endless redraw/download loop
        and could make the whole Tk application unresponsive after a match.
        """
        if not self.match:
            return
        photos = []
        for player in self.match.get("players", []):
            puuid = player.get("puuid")
            if not puuid:
                continue
            badge = _leaderboard_badge(self.app, player, 30)
            if badge:
                photos.append(badge)
                try:
                    self.leaderboard_tree.item(puuid, image=badge)
                except Exception:
                    pass
        self._leaderboard_photos = photos

    def _prefs(self) -> None:
        self.app.cfg.anonymize_exports = bool(self.v_anon.get()); self.app.cfg.autosave = bool(self.v_auto.get())
        try: self.app.cfg.save()
        except OSError: pass

    def _save(self, ext: str, label: str, writer) -> None:
        if not self.match: messagebox.showinfo("Nothing to save", "There is no finished match to save yet."); return
        path = filedialog.asksaveasfilename(initialdir=str(self.app.cfg.matches_dir), initialfile=export.default_filename(self.match, ext), defaultextension="." + ext, filetypes=[(label, "*." + ext)])
        if not path: return
        try: writer(path); self.app.set_status(f"Saved {path}")
        except Exception as exc: messagebox.showerror("Could not save", str(exc))

    def _save_vtrack(self): self._save("vtrack", "ValConvex match", lambda p: export.save_vtrack(self.match, p, anonymize=bool(self.v_anon.get())))
    def _save_html(self): self._save("html", "Web page", lambda p: export.export_html(self.match, p, minimap=self.app.minimap_path(self.match), anonymize=bool(self.v_anon.get())))
    def _save_png(self): self._save("png", "PNG image", lambda p: export.export_png(self.match, p, filters=self.heat.current_filters(), minimap_path=self.app.minimap_path(self.match), anonymize=bool(self.v_anon.get())))
