"""Local Match History: large rounded match cards with team agent lineups."""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
from pathlib import Path

from .. import heatmap
from . import theme
from .widgets import AppleScrollbar

# Icons are decoded once and shared by every card; downloads are claimed once so ten cards do not ask for the same file.
_PHOTOS = {}
_INFLIGHT = set()
_FAILED = set()
_LOCK = threading.Lock()


def _photo(path, size):
    """A PhotoImage for the icon file, or None. Cached by file, size and modification time."""
    try:
        key = (str(path), size, Path(path).stat().st_mtime)
    except OSError:
        return None
    if key in _PHOTOS:
        return _PHOTOS[key]
    try:
        from PIL import Image, ImageTk
        with Image.open(path) as source:
            resized = source.convert("RGB").resize((size, size))
        photo = ImageTk.PhotoImage(resized)
    except Exception:
        return None
    _PHOTOS[key] = photo
    return photo


class MatchHistoryView(tk.Frame):
    CARD_H = 178

    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self._cards = {}
        self._images = []
        self._refresh_generation = 0
        self._refresh_after = None
        self._refresh_matches = []
        self._refresh_index = 0
        self._refresh_page_end = 0
        self._page_size = 24
        self._batch_size = 4
        self._load_more_button = None
        self.folder_var = tk.StringVar(value="All")
        self.player_search = tk.StringVar()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        head = tk.Frame(self, bg=theme.BG)
        head.grid(row=0, column=0, sticky="ew", padx=30, pady=(28, 8))
        tk.Label(head, text="MATCH HISTORY", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_HERO).pack(anchor="w")
        tk.Label(head, text="Your locally saved matches. Open, rename, or delete a game.",
                 bg=theme.BG, fg=theme.MUTED, font=theme.FONT).pack(anchor="w", pady=(3, 0))

        toolbar = tk.Frame(self, bg=theme.BG)
        toolbar.grid(row=1, column=0, sticky="ew", padx=30, pady=(8, 12))
        tk.Button(toolbar, text="↻  Refresh", command=self.refresh, bd=0, relief="flat",
                  bg=theme.PANEL2, fg=theme.TEXT, activebackground=theme.PANEL3,
                  activeforeground=theme.TEXT, font=theme.FONT_BOLD, padx=14, pady=7).pack(side="left")
        self.folder_box = ttk.Combobox(toolbar, textvariable=self.folder_var, state="readonly", width=16, values=("All", "Ranked", "Scrims", "Unsorted"), style="TCombobox")
        self.folder_box.pack(side="left", padx=(8, 4))
        ttk.Button(toolbar, text="New folder", command=self.create_folder).pack(side="left")
        ttk.Button(toolbar, text="Import match", command=self.import_file).pack(side="left", padx=6)
        self.folder_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        search = ttk.Entry(toolbar, textvariable=self.player_search, width=24)
        search.pack(side="right", padx=(6, 0)); search.bind("<Return>", lambda e: self.search_player())
        ttk.Button(toolbar, text="Find player", command=self.search_player).pack(side="right")
        self.count = tk.Label(toolbar, text="", bg=theme.BG, fg=theme.SUBTLE, font=theme.FONT_SMALL)
        self.count.pack(side="left", padx=12)

        wrap = tk.Frame(self, bg=theme.BG)
        wrap.grid(row=2, column=0, sticky="nsew", padx=30, pady=(0, 24))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(wrap, bg=theme.BG, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = AppleScrollbar(wrap, command=self.canvas.yview_moveto)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.inner = tk.Frame(self.canvas, bg=theme.BG)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")

    def _on_inner(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _wheel(self, event):
        try:
            if self.winfo_ismapped():
                self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def refresh(self):
        """Refresh history incrementally so large libraries never monopolize Tk."""
        self.app._history_loaded = True
        self._refresh_generation += 1
        generation = self._refresh_generation
        if self._refresh_after is not None:
            try:
                self.after_cancel(self._refresh_after)
            except Exception:
                pass
            self._refresh_after = None

        self.app.cfg.ensure_dirs()
        folders = {"Ranked", "Scrims", "Unrated", "Unsorted"} | set(self.app.db.list_folders())
        try:
            folders |= {p.name for p in self.app.cfg.matches_dir.iterdir() if p.is_dir()}
        except OSError:
            pass
        self.folder_box["values"] = ["All"] + sorted(folders, key=str.lower)
        if self.folder_var.get() not in self.folder_box["values"]:
            self.folder_var.set("All")

        # Keep the number of live Tk widgets bounded. A long match history is
        # paged in small chunks instead of constructing up to 200 canvases at once.
        for w in self.inner.winfo_children():
            w.destroy()
        self._load_more_button = None
        self._cards.clear()
        self._images.clear()
        self._refresh_matches = self.app.db.list_matches(limit=200, folder=self.folder_var.get())
        self._refresh_index = 0
        self._refresh_page_end = min(self._page_size, len(self._refresh_matches))
        self.count.configure(text=f"{len(self._refresh_matches)} saved matches")
        if not self._refresh_matches:
            tk.Label(self.inner, text="No saved matches yet.", bg=theme.BG, fg=theme.MUTED,
                     font=theme.FONT_BIG).pack(pady=80)
            return
        self._render_next_batch(generation)

    def _load_more(self) -> None:
        if self._refresh_index >= len(self._refresh_matches):
            return
        if self._load_more_button is not None:
            try:
                self._load_more_button.destroy()
            except Exception:
                pass
            self._load_more_button = None
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._refresh_page_end = min(self._refresh_index + self._page_size, len(self._refresh_matches))
        self._render_next_batch(generation)

    def _render_next_batch(self, generation: int) -> None:
        if generation != self._refresh_generation:
            return
        self._refresh_after = None
        stop = min(self._refresh_index + self._batch_size, self._refresh_page_end)
        while self._refresh_index < stop:
            meta = self._refresh_matches[self._refresh_index]
            self._refresh_index += 1
            try:
                self._make_card(meta)
            except Exception:
                import logging
                logging.getLogger("valtracker").exception(
                    "Could not render history card for match %s", meta.get("match_id"))
        if self._refresh_index < self._refresh_page_end:
            self._refresh_after = self.after(
                8, lambda g=generation: self._render_next_batch(g))
        elif self._refresh_index < len(self._refresh_matches):
            self._load_more_button = ttk.Button(
                self.inner,
                text=f"Load more matches ({self._refresh_index} of {len(self._refresh_matches)})",
                command=self._load_more,
            )
            self._load_more_button.pack(pady=(0, 16))

    def _make_card(self, meta: dict):
        match = self.app.db.get_match(meta["match_id"])
        if not match:
            return
        card = MatchCard(self.inner, self.app, meta, match, self)
        card.pack(fill="x", pady=(0, 14))
        self._cards[meta["match_id"]] = card

    def create_folder(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("New match folder", "Folder name:", parent=self)
        if not name: return
        try:
            self.app.cfg.match_folder(name)
            vals = ["All"] + sorted(set(self.app.db.list_folders()) | {name.strip()}, key=str.lower)
            self.folder_box["values"] = vals
            self.folder_var.set(name.strip())
            self.refresh()
        except ValueError as exc:
            messagebox.showerror("Invalid folder", str(exc), parent=self)

    def import_file(self):
        path = filedialog.askopenfilename(title="Import a ValConvex match", filetypes=[("ValConvex match", "*.vtrack"), ("All files", "*.*")], parent=self)
        if not path: return
        folder = self.folder_var.get() if self.folder_var.get() not in {"All", ""} else "Unsorted"
        self.app.import_match(path, folder=folder)

    def search_player(self):
        q = self.player_search.get().strip()
        if q: self.app.track_player(q)

    def redraw_icons(self):
        for card in list(self._cards.values()):
            card._redraw_icons()

    def open_match(self, match_id):
        self.app.open_match(match_id)

    def rename_match(self, match_id):
        current = self.app.db.get_match_title(match_id) or ""
        from tkinter import simpledialog
        value = simpledialog.askstring("Rename game", "Name this match:", initialvalue=current,
                                       parent=self)
        if value is None:
            return
        value = value.strip()
        if not value:
            messagebox.showinfo("Rename game", "Enter a name or press Cancel.", parent=self)
            return
        self.app.db.rename_match(match_id, value)
        self.refresh()

    def move_match(self, match_id):
        current = self.app.db.get_match_folder(match_id) or "Unsorted"
        folders = {"Ranked", "Scrims", "Unrated", "Unsorted"} | set(self.app.db.list_folders())
        try:
            folders |= {p.name for p in self.app.cfg.matches_dir.iterdir() if p.is_dir()}
        except OSError:
            pass
        values = sorted(folders, key=str.lower)

        # Use a read-only folder picker instead of asking the user to type a
        # folder name. This prevents typos from creating duplicate folders.
        dialog = tk.Toplevel(self)
        dialog.title("Move match")
        dialog.configure(bg=theme.BG)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.resizable(False, False)

        tk.Label(dialog, text="Move match to folder", bg=theme.BG, fg=theme.TEXT,
                 font=theme.FONT_BOLD).pack(anchor="w", padx=22, pady=(18, 8))
        tk.Label(dialog, text="Choose an existing match-history folder:", bg=theme.BG, fg=theme.MUTED,
                 font=theme.FONT_SMALL).pack(anchor="w", padx=22, pady=(0, 8))
        var = tk.StringVar(value=current if current in values else (values[0] if values else "Unsorted"))
        box = ttk.Combobox(dialog, textvariable=var, values=values, state="readonly", width=28, style="TCombobox")
        box.pack(fill="x", padx=22, pady=(0, 16))
        box.focus_set()

        buttons = tk.Frame(dialog, bg=theme.BG)
        buttons.pack(fill="x", padx=22, pady=(0, 18))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")

        def do_move():
            name = var.get().strip()
            if not name:
                return
            try:
                folder = self.app.cfg.match_folder(name)
                meta = next((m for m in self.app.db.list_matches(200) if m["match_id"] == match_id), None)
                old = meta.get("saved_path") if meta else None
                new = old
                if old and Path(old).exists():
                    dest = folder / Path(old).name
                    if Path(old).resolve() != dest.resolve():
                        import shutil
                        shutil.move(str(old), str(dest)); new = str(dest)
                self.app.db.set_match_folder(match_id, name, new)
                dialog.destroy()
                self.refresh()
            except Exception as exc:
                messagebox.showerror("Move match", str(exc), parent=dialog)

        ttk.Button(buttons, text="Move", style="Accent.TButton", command=do_move).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _e: do_move())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

    def delete_match(self, match_id):
        answer = messagebox.askyesno("Delete saved match", "Delete this match from ValConvex's local history?\n\n"
                                      "This removes it from the app's local database. Exported .vtrack files are not deleted.",
                                      parent=self)
        if not answer:
            return
        self.app.db.delete_match(match_id)
        if self.app.current_match and self.app.current_match.get("match_id") == match_id:
            self.app.current_match = None
        self.refresh()
        self.app.set_status("Match deleted from local history.")


class MatchCard(tk.Frame):
    def __init__(self, parent, app, meta, match, owner):
        super().__init__(parent, bg=theme.BG, height=MatchHistoryView.CARD_H)
        self.app, self.meta, self.match, self.owner = app, meta, match, owner
        self.pack_propagate(False)
        self.canvas = tk.Canvas(self, height=MatchHistoryView.CARD_H, bg=theme.BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._draw)
        self.canvas.bind("<Double-Button-1>", self._double_click)
        self.canvas.bind("<Button-3>", self._menu)
        self._photos = []
        self._icon_jobs = set()
        self._draw()
        self._load_icons_async()

    def _player_at(self, x, y):
        players = [p for p in self.match.get("players", []) if p.get("team") in {"me", "en"}]
        team = "me" if 78 <= y <= 122 else "en" if 124 <= y <= 168 else None
        if not team: return None
        group = [p for p in players if p.get("team") == team][:5]
        idx = int(max(0, x - 85) // 40)
        return group[idx] if 0 <= idx < len(group) else None

    def _double_click(self, event):
        p = self._player_at(event.x, event.y)
        if p: self.app.open_player_tracker(p.get("puuid"), heatmap.player_name(p))
        else: self.owner.open_match(self.meta["match_id"])

    def _rounded(self, x1, y1, x2, y2, r=16, **kwargs):
        pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
               x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
        return self.canvas.create_polygon(pts, smooth=True, splinesteps=12, **kwargs)

    def _draw(self, _event=None):
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 700)
        h = MatchHistoryView.CARD_H
        result = self.meta.get("result") or ""
        accent = theme.GREEN if result == "Victory" else theme.RED if result == "Defeat" else theme.MUTED
        self._rounded(3, 3, w-3, h-3, 16, fill=theme.PANEL, outline=theme.BORDER, width=1)
        c.create_rectangle(3, 3, 8, h-3, fill=accent, outline=accent)

        title = self.meta.get("title") or ""
        map_name = self.meta.get("map_name") or self.match.get("map", {}).get("name") or "Unknown map"
        queue = self.meta.get("queue") or self.match.get("queue") or "custom"
        when = time.strftime("%b %d, %Y  ·  %I:%M %p", time.localtime((self.meta.get("started_ms") or 0) / 1000)) if self.meta.get("started_ms") else "Unknown time"
        score = self.meta.get("score") or "-"
        label = title if title else f"{map_name}  ·  {queue}"
        c.create_text(28, 28, text=label, anchor="w", fill=theme.TEXT, font=theme.FONT_TITLE)
        c.create_text(28, 51, text=f"{when}   ·   {map_name}   ·   {queue}", anchor="w", fill=theme.MUTED, font=theme.FONT_SMALL)
        c.create_text(w-28, 34, text=score, anchor="e", fill=accent, font=("Segoe UI Semibold", 20))
        c.create_text(w-28, 57, text=result.upper(), anchor="e", fill=accent, font=theme.FONT_SMALL)

        players = self.match.get("players") or []
        ally = [p for p in players if p.get("team") == "me"][:5]
        enemy = [p for p in players if p.get("team") == "en"][:5]
        self._team_row(ally, 84, "ALLY")
        self._team_row(enemy, 130, "ENEMY")
        c.create_text(w/2, 107, text="VS", fill=theme.SUBTLE, font=("Segoe UI Semibold", 10))

        c.create_text(w-28, h-24, text="Double-click to open  ·  Right-click for options", anchor="e",
                      fill=theme.SUBTLE, font=("Segoe UI", 8))

    def _team_row(self, players, y, label):
        c = self.canvas
        w = max(c.winfo_width(), 700)
        c.create_text(28, y+12, text=label, anchor="w", fill=theme.SUBTLE, font=("Segoe UI Semibold", 8))
        start = 90
        size = 32
        gap = 8
        for i in range(5):
            x = start + i*(size+gap)
            p = players[i] if i < len(players) else None
            self._agent_box(p, x, y-4, size)

    def _agent_id(self, p):
        """The player's agent id. Matches saved before ids were stored are looked up by agent name."""
        p = p or {}
        aid = str(p.get("agent_id") or "").strip().lower()
        if not aid and p.get("agent"):
            aid = str(self.app.static.agent_uuid_by_name(p.get("agent")) or "").lower()
        return aid

    def _agent_box(self, p, x, y, size):
        c = self.canvas
        name = (p or {}).get("agent") or "?"
        photo = None
        aid = self._agent_id(p)
        if aid:
            try:
                path = self.app.static.agent_icon_path(aid)       # cached file only: never touch the network while drawing
                photo = _photo(path, size) if path else None
            except Exception:
                photo = None
        if photo is not None:
            self._photos.append(photo)
            c.create_image(x + size / 2, y + size / 2, image=photo)
            return
        c.create_rectangle(x, y, x + size, y + size, fill=theme.PANEL3, outline=theme.BORDER)
        initials = "".join(word[0] for word in str(name).split()[:2]).upper()[:2] or "?"
        c.create_text(x + size / 2, y + size / 2, text=initials, fill=theme.TEXT, font=("Segoe UI Semibold", 9))

    def _load_icons_async(self):
        """Download icons that are missing, in the background, then ask the app to redraw the cards."""
        static = self.app.static
        wanted = {self._agent_id(p) for p in (self.match.get("players") or [])}
        with _LOCK:
            todo = [a for a in wanted if a and a not in _INFLIGHT and a not in _FAILED and static.agent_icon_path(a) is None]
            _INFLIGHT.update(todo)
        if not todo:
            return

        def worker():
            # Let the initial paint finish before background downloads begin.
            time.sleep(0.08)
            changed = False
            for aid in todo:
                try:
                    ok = static.ensure_agent_icon(aid) is not None
                except Exception:
                    ok = False
                with _LOCK:
                    _INFLIGHT.discard(aid)
                    if not ok:
                        _FAILED.add(aid)
                changed = changed or ok
            if changed:
                try:
                    self.app.q.put(("history_icons", None))      # the Tk thread redraws; never touch Tk from here
                except Exception:
                    pass
        threading.Thread(target=worker, daemon=True, name="history-icons").start()

    def _redraw_icons(self):
        self._photos.clear()
        self._draw()

    def _menu(self, event):
        player = self._player_at(event.x, event.y)
        if player:
            self.app.player_menu(event, player.get("puuid"), heatmap.player_name(player)); return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Open match", command=lambda: self.owner.open_match(self.meta["match_id"]))
        menu.add_command(label="Rename", command=lambda: self.owner.rename_match(self.meta["match_id"]))
        menu.add_command(label="Move to folder", command=lambda: self.owner.move_match(self.meta["match_id"]))
        menu.add_separator()
        menu.add_command(label="Delete", command=lambda: self.owner.delete_match(self.meta["match_id"]))
        menu.tk_popup(event.x_root, event.y_root)
