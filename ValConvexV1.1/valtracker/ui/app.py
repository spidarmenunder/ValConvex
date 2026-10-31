"""Main window: tabs, the event loop that receives updates from the background service, and menus."""
from __future__ import annotations

import logging
import queue
from collections import deque
import threading
import shutil
import sys
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from .. import export
from ..config import Config
from ..db import Database
from ..model.baselines import Baselines
from ..services.live import LiveService
from . import theme, tray as tray_mod
from .live_view import LiveView
from .matches_view import MatchesView
from .history_view import MatchHistoryView
from .postmatch_view import PostMatchView
from .profile_view import ProfileView
from .widgets import apply_style

log = logging.getLogger("valtracker")
POLL_MS = 150


class App(tk.Tk):
    def __init__(self, cfg: Config, db: Database, static, source_factory, demo: bool = False, tray_factory=None):
        super().__init__()
        self.cfg, self.db, self.static, self.demo = cfg, db, static, demo
        self.title("ValConvex" + ("  (demo mode)" if demo else ""))
        self.geometry("1280x800")
        self.minsize(850, 600)
        try:
            self._base_tk_scaling = float(self.tk.call("tk", "scaling"))
        except (tk.TclError, TypeError, ValueError, AttributeError):
            self._base_tk_scaling = 1.0
        apply_style(self)
        # Use the dedicated Windows ICO for the application/window icon.
        # The PNG is kept as a fallback for environments where Tk cannot load ICOs.
        self._logo_photo = None
        try:
            root_dir = Path(__file__).resolve().parents[2]
            icon_path = root_dir / "valconvex.ico"
            if icon_path.exists():
                self.iconbitmap(default=str(icon_path))
        except Exception:
            pass
        try:
            from PIL import Image, ImageTk
            logo_path = Path(__file__).resolve().parents[2] / "valtracker_logo.png"
            self._logo_photo = ImageTk.PhotoImage(Image.open(logo_path).convert("RGBA").resize((64, 64)))
            # Keep the image fallback for platforms that do not support ICO loading.
            if sys.platform != "win32":
                self.iconphoto(True, self._logo_photo)
        except Exception:
            self._logo_photo = None
        self.q: "queue.Queue" = queue.Queue()
        self._pending_events = deque()
        self.service = LiveService(source_factory, static, db, Baselines(db), cfg, self.q)
        self.current_match: Optional[dict] = None
        self._history_loaded = False
        self._profile_loaded_puuid: Optional[str] = None
        self._lobby_key = None
        self.my_puuid: Optional[str] = None
        self.hidden = False
        self.tray = None
        self._told_tray = False

        self._menu()
        self._build_shell()

        self.live = LiveView(self.content, self)
        self.post = PostMatchView(self.content, self)
        self.matches = MatchesView(self.content, self)
        self.history = MatchHistoryView(self.content, self)
        self.profile = ProfileView(self.content, self)
        for view in (self.live, self.post, self.matches, self.history, self.profile):
            view.grid(row=0, column=0, sticky="nsew")
        self._views = {"Live": self.live, "Tracker": self.matches, "Match History": self.history, "Profile": self.profile, "Post-match": self.post}
        self._nav_buttons = {}
        self._nav_meta = {}
        self._sidebar_layout_applied = None
        self._add_nav_button("Live", "◉", self.live)
        self._add_nav_button("Tracker", "⌕", self.matches)
        self._add_nav_button("Match History", "▦", self.history)
        self._add_nav_button("Profile", "◌", self.profile)
        self._add_nav_button("Post-match", "▤", self.post)
        self._add_nav_button("Settings", "⚙", None)

        account = tk.Frame(self.sidebar, bg=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
        account.pack(side="bottom", fill="x", padx=12, pady=14)
        tk.Label(account, text="VALCONVEX", bg=theme.PANEL, fg=theme.TEXT,
                 font=theme.FONT_BOLD).pack(anchor="w", padx=12, pady=(10, 1))
        tk.Label(account, text="Riot Client connected", bg=theme.PANEL, fg=theme.GREEN,
                 font=theme.FONT_SMALL).pack(anchor="w", padx=12, pady=(0, 10))
        self._account_panel = account

        self._select_view("Live")

        self.status = tk.Label(self, text="Starting...", bg=theme.SIDEBAR, fg=theme.MUTED, font=theme.FONT_SMALL,
                               anchor="w", padx=18, pady=5)
        self.status.grid(row=1, column=1, sticky="ew")
        self.matches.refresh()
        # Match History is intentionally lazy-loaded. Building cards during startup
        # competes with first paint and live-match detection on lower-powered PCs.
        self.protocol("WM_DELETE_WINDOW", self.close_window)
        if cfg.run_in_background:
            factory = tray_factory or (lambda post: tray_mod.Tray(post) if tray_mod.available() else None)
            t = factory(lambda action: self.q.put(("tray", action)))
            if t is not None and t.start():
                self.tray = t
        self.service.start()
        self.after(POLL_MS, self._pump)
        try:
            self.bind("<Configure>", self._on_root_configure, add="+")
        except TypeError:  # compatibility with the headless UI test double
            self.bind("<Configure>", self._on_root_configure)
        self.apply_layout_preferences()
        if not self.static.has_maps and not demo:
            self.set_status("Game data (maps, agents) could not be downloaded yet. Check your internet connection.")

    def _build_shell(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.sidebar = tk.Frame(self, bg=theme.SIDEBAR, width=250)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.content = tk.Frame(self, bg=theme.BG)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        brand = tk.Frame(self.sidebar, bg=theme.SIDEBAR)
        brand.pack(fill="x", padx=22, pady=(22, 26))
        self._brand_frame = brand
        self._brand_logo = None
        if getattr(self, "_logo_photo", None):
            self._brand_logo = tk.Label(brand, image=self._logo_photo, bg=theme.SIDEBAR)
            self._brand_logo.pack(anchor="w", pady=(0, 7))
        self._brand_title = tk.Label(brand, text="VALCONVEX", bg=theme.SIDEBAR, fg=theme.TEXT,
                                     font=("Segoe UI Semibold", 18))
        self._brand_title.pack(anchor="w")
        self._brand_subtitle = tk.Label(brand, text="COMPANION", bg=theme.SIDEBAR, fg=theme.MUTED,
                                        font=("Segoe UI", 8))
        self._brand_subtitle.pack(anchor="w", pady=(1, 0))

        self._sidebar_section_label = tk.Label(self.sidebar, text="COMPANION", bg=theme.SIDEBAR, fg=theme.SUBTLE,
                                               font=("Segoe UI Semibold", 8))
        self._sidebar_section_label.pack(anchor="w", padx=22, pady=(0, 7))

    def _add_nav_button(self, label: str, icon: str, view) -> None:
        btn = tk.Button(self.sidebar, text=f"  {icon}   {label}", anchor="w", bd=0, relief="flat",
                        bg=theme.SIDEBAR, fg=theme.MUTED, activebackground=theme.PANEL2,
                        activeforeground=theme.TEXT, font=theme.FONT_BOLD, padx=18, pady=9,
                        cursor="hand2", command=lambda n=label: self._select_view(n))
        btn.pack(fill="x", padx=6, pady=1)
        self._nav_buttons[label] = btn
        self._nav_meta[label] = icon

    def _on_root_configure(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if getattr(self.cfg, "sidebar_mode", "auto") == "auto":
            self._apply_sidebar_layout()

    def _apply_sidebar_layout(self) -> None:
        mode = getattr(self.cfg, "sidebar_mode", "auto")
        collapsed = mode == "collapsed" or (mode == "auto" and self.winfo_width() < 1120)
        state = "collapsed" if collapsed else "expanded"
        if state == self._sidebar_layout_applied:
            return
        self._sidebar_layout_applied = state
        width = 68 if collapsed else 220
        self.sidebar.configure(width=width)
        self.grid_columnconfigure(0, minsize=width, weight=0)
        for label, button in self._nav_buttons.items():
            icon = self._nav_meta.get(label, "")
            button.configure(text=f" {icon}" if collapsed else f"  {icon}   {label}",
                             anchor="center" if collapsed else "w", padx=4 if collapsed else 12)
        if collapsed:
            self._account_panel.pack_forget()
            self._brand_frame.pack_configure(padx=0, pady=(10, 10))
            self._sidebar_section_label.configure(text="•", anchor="center")
            self._sidebar_section_label.pack_configure(anchor="center", padx=0, pady=(0, 4))
            if self._brand_title.winfo_manager():
                self._brand_title.pack_forget()
            if self._brand_subtitle.winfo_manager():
                self._brand_subtitle.pack_forget()
            if self._brand_logo is not None:
                self._brand_logo.pack_configure(anchor="center", pady=(0, 4))
        else:
            self._account_panel.pack(side="bottom", fill="x", padx=12, pady=14)
            self._brand_frame.pack_configure(padx=22, pady=(22, 26))
            self._sidebar_section_label.configure(text="COMPANION", anchor="w")
            self._sidebar_section_label.pack_configure(anchor="w", padx=22, pady=(0, 7))
            self._brand_title.pack_configure(anchor="w")
            self._brand_subtitle.pack_configure(anchor="w", pady=(1, 0))
            if self._brand_logo is not None:
                self._brand_logo.pack_configure(anchor="w", pady=(0, 7))

    def apply_layout_preferences(self) -> None:
        """Apply saved layout settings without recreating views or adding dependencies."""
        mode = getattr(self.cfg, "layout_mode", "balanced")
        scale = max(80, min(120, int(getattr(self.cfg, "interface_scale", 100) or 100)))
        density = getattr(self.cfg, "table_density", "compact")
        try:
            self.tk.call("tk", "scaling", self._base_tk_scaling * scale / 100.0)
        except (tk.TclError, AttributeError, TypeError):
            pass
        try:
            ttk.Style(self).configure("Treeview", rowheight=21 if density == "compact" else 29,
                                     font=theme.FONT_SMALL if density == "compact" else theme.FONT)
        except tk.TclError:
            pass
        # All modes remain window-friendly; they change minimum size and density, not features.
        min_sizes = {"compact": (950, 620), "balanced": (1000, 650), "comfortable": (1080, 700)}
        self.minsize(*min_sizes.get(mode, min_sizes["balanced"]))
        self._sidebar_layout_applied = None
        self._apply_sidebar_layout()

    def _select_view(self, name: str) -> None:
        view = self._views.get(name)
        if view is not None:
            started = __import__("time").perf_counter()
            view.tkraise()
            for key, btn in self._nav_buttons.items():
                btn.configure(bg=theme.PANEL2 if key == name else theme.SIDEBAR,
                              fg=theme.TEXT if key == name else theme.MUTED)
            if name == "Profile":
                self.open_own_profile()
            elif name == "Match History" and not self._history_loaded:
                self._history_loaded = True
                self.history.refresh()
            elapsed = (__import__("time").perf_counter() - started) * 1000
            if elapsed >= 100:
                log.warning("Navigation to %s took %.1f ms", name, elapsed)
        elif name == "Settings":
            self._settings_dialog()
        else:
            self.set_status(f"{name} is coming soon.")

    # ---- menu ----------------------------------------------------------------
    def _menu(self) -> None:
        bar = tk.Menu(self)
        file = tk.Menu(bar, tearoff=0)
        file.add_command(label="Import .vtrack file...", command=lambda: self.matches.import_file())
        file.add_command(label="Open matches folder", command=self.open_matches_folder)
        file.add_separator()
        file.add_command(label="Quit ValConvex", command=self.quit_app)
        bar.add_cascade(label="File", menu=file)
        help_ = tk.Menu(bar, tearoff=0)
        help_.add_command(label="About", command=lambda: messagebox.showinfo(
            "ValConvex", "A light, personal VALORANT tracker.\n\nNot endorsed by Riot Games. Uses the unofficial "
                          "local client API and the community valorant-api.com and HenrikDev services.\n\n"
                          f"Data folder:\n{self.cfg.root}"))
        bar.add_cascade(label="Help", menu=help_)
        self.configure(menu=bar)

    # ---- actions called by views --------------------------------------------------
    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def request_detail(self, puuid: str) -> None:
        self.service.request_detail(puuid)

    def open_own_profile(self) -> None:
        """Open the locally logged-in player's profile; no Riot ID search is needed."""
        puuid = self.my_puuid or (getattr(self.service, "source", None).puuid if getattr(self.service, "source", None) else None)
        if not puuid:
            self.set_status("Connect to VALORANT to load your profile.")
            return
        if self._profile_loaded_puuid == puuid and getattr(self.profile, "puuid", None) == puuid:
            return
        self.service.load_own_profile()

    def open_profile(self, puuid: str, name: str = "", tag: str = "") -> None:
        # Used by a focused player or a history row. The sidebar Profile remains the user's own profile.
        self.profile.tkraise()
        for key, btn in self._nav_buttons.items():
            btn.configure(bg=theme.PANEL2 if key == "Profile" else theme.SIDEBAR,
                          fg=theme.TEXT if key == "Profile" else theme.MUTED)
        self.profile.load(puuid, name, tag)

    def load_profile(self, puuid: str, name: str = "", tag: str = "") -> None:
        self.service.load_profile(puuid, name, tag)

    def search_profile(self, text: str) -> None:
        self.service.search_profile(text)

    def _settings_dialog(self) -> None:
        win = tk.Toplevel(self)
        win.title("ValConvex Settings")
        win.configure(bg=theme.BG)
        win.transient(self)
        win.geometry("570x560")
        win.minsize(520, 500)
        win.columnconfigure(0, weight=1)
        win.grab_set()
        tk.Label(win, text="SETTINGS", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_TITLE).grid(
            row=0, column=0, sticky="w", padx=24, pady=(20, 6))
        tk.Label(win, text="Layout preferences", bg=theme.BG, fg=theme.TEXT, font=theme.FONT_BOLD).grid(
            row=1, column=0, sticky="w", padx=24, pady=(8, 3))
        controls = tk.Frame(win, bg=theme.BG)
        controls.grid(row=2, column=0, sticky="ew", padx=24)
        controls.columnconfigure(1, weight=1)

        layout_var = tk.StringVar(value=getattr(self.cfg, "layout_mode", "balanced"))
        scale_var = tk.IntVar(value=max(80, min(120, int(getattr(self.cfg, "interface_scale", 100) or 100))))
        density_var = tk.StringVar(value=getattr(self.cfg, "table_density", "compact"))
        sidebar_var = tk.StringVar(value=getattr(self.cfg, "sidebar_mode", "auto"))
        settings = [
            ("Layout mode", layout_var, ("compact", "balanced", "comfortable")),
            ("Table density", density_var, ("compact", "comfortable")),
            ("Sidebar", sidebar_var, ("auto", "expanded", "collapsed")),
        ]
        for row, (label, var, values) in enumerate(settings):
            tk.Label(controls, text=label, bg=theme.BG, fg=theme.TEXT, font=theme.FONT).grid(
                row=row, column=0, sticky="w", pady=7, padx=(0, 16))
            ttk.Combobox(controls, textvariable=var, values=values, state="readonly", width=22).grid(
                row=row, column=1, sticky="ew", pady=7)
        scale_row = len(settings)
        scale_label = tk.Label(controls, text=f"Interface scale: {scale_var.get()}%", bg=theme.BG,
                               fg=theme.TEXT, font=theme.FONT)
        scale_label.grid(row=scale_row, column=0, sticky="w", pady=7, padx=(0, 16))
        def scale_changed(value):
            scale_var.set(int(float(value)))
            scale_label.configure(text=f"Interface scale: {scale_var.get()}%")
        ttk.Scale(controls, from_=80, to=120, variable=scale_var, command=scale_changed).grid(
            row=scale_row, column=1, sticky="ew", pady=7)

        tk.Label(win, text="HenrikDev API key (optional)", bg=theme.BG, fg=theme.TEXT,
                 font=theme.FONT_BOLD).grid(row=3, column=0, sticky="w", padx=24, pady=(14, 4))
        key_var = tk.StringVar(value=self.cfg.henrik_api_key or "")
        ent = ttk.Entry(win, textvariable=key_var, show="•")
        ent.grid(row=4, column=0, sticky="ew", padx=24, ipady=5)
        tk.Label(win, text="Only needed when searching for a new Riot ID not saved locally. The key is stored in your local config.json.",
                 bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL, justify="left", wraplength=510).grid(
            row=5, column=0, sticky="w", padx=24, pady=(7, 8))
        btns = tk.Frame(win, bg=theme.BG)
        btns.grid(row=6, column=0, sticky="ew", padx=24, pady=(12, 20))
        def save():
            self.cfg.henrik_api_key = key_var.get().strip()
            self.cfg.layout_mode = layout_var.get() if layout_var.get() in ("compact", "balanced", "comfortable") else "balanced"
            self.cfg.interface_scale = max(80, min(120, int(scale_var.get())))
            self.cfg.table_density = density_var.get() if density_var.get() in ("compact", "comfortable") else "compact"
            self.cfg.sidebar_mode = sidebar_var.get() if sidebar_var.get() in ("auto", "expanded", "collapsed") else "auto"
            self.cfg.save()
            self.apply_layout_preferences()
            win.destroy()
            self.set_status("Settings saved and applied.")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Save & Apply", style="Accent.TButton", command=save).pack(side="right", padx=8)
        ent.focus_set()

    def open_player_tracker(self, puuid: str, label: str = "") -> None:
        if not puuid:
            return
        self.matches.search_active = True
        self.matches._search_puuid = puuid
        self.matches.v_search.set(label)
        self.matches.result_title.configure(text=label or "Player tracker")
        self._select_view("Tracker")
        self.set_status("Loading player tracker...")
        self.load_profile(puuid, *(label.split("#", 1) + [""])[:2])

    def track_player(self, text: str) -> None:
        text = (text or "").strip()
        if not text: return
        local = self.db.find_player(text)
        if local:
            label = f'{local.get("name") or "Player"}#{local.get("tag")}' if local.get("tag") else local.get("name") or "Player"
            self.open_player_tracker(local["puuid"], label)
            return
        self.matches.search_active = True
        self.matches.v_search.set(text)
        self._select_view("Tracker")
        self.search_profile(text)

    def player_menu(self, event, puuid: str, label: str) -> None:
        menu = tk.Menu(self, tearoff=0); menu.add_command(label="Player tracker", command=lambda: self.open_player_tracker(puuid, label)); menu.add_separator(); menu.add_command(label="Copy Riot ID", command=lambda: self.clipboard_append(label))
        menu.tk_popup(event.x_root, event.y_root)

    def fetch_latest(self) -> None:
        self.set_status("Loading the latest match...")
        self.service.fetch_latest()

    def open_matches_folder(self) -> None:
        """Open the persistent folder containing exported/autosaved matches."""
        self.cfg.ensure_dirs()
        theme.open_folder(self.cfg.matches_dir)
        self.set_status(f"Match results folder: {self.cfg.matches_dir}")

    def minimap_path(self, match: dict):
        return self.static.map_image_path(self.static.map_by_url(match["map"]["url"]))

    def show_match(self, match: dict, focus: bool = True, refresh_lists: bool = False) -> None:
        self.current_match = match
        self.post.show(match, self.minimap_path(match))
        # Opening an already-saved match should not rebuild the tracker and up to
        # 200 history cards. Refresh those lists only when data was actually added.
        if refresh_lists:
            self.matches.refresh()
            if self._history_loaded:
                self.history.refresh()
        if focus:
            self._select_view("Post-match")
        if self.minimap_path(match) is None:                # fetch the minimap in the background, then redraw
            m = self.static.map_by_url(match["map"]["url"])
            if m:
                threading.Thread(target=lambda: (self.static.ensure_map_image(m), self.q.put(("minimap", match["match_id"]))),
                                 daemon=True).start()

    def open_history_match(self, match_id: str, perspective: Optional[str] = None) -> None:
        """Open a match from a player's history (leaderboard + heatmap), seen from that player's side."""
        self.set_status("Opening match...")
        self.service.open_match(match_id, perspective)

    def open_match(self, match_id: str) -> None:
        m = self.db.get_match(match_id)
        if not m:
            messagebox.showerror("Not found", "That match is no longer in the cache.")
            return
        self.show_match(m)

    def import_match(self, path: str, folder: str = "Unsorted") -> None:
        try:
            m = export.load_vtrack(path)
            folder = folder or "Unsorted"
            dest_dir = self.cfg.match_folder(folder)
            dest = dest_dir / Path(path).name
            if Path(path).resolve() != dest.resolve():
                shutil.copy2(path, dest)
            m["folder"] = folder
            self.db.save_match(m, saved_path=str(dest), imported=True)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Could not import", str(exc))
            return
        self.set_status(f"Imported {m['map']['name']} {m['result']['score'][0]}-{m['result']['score'][1]} into {folder}")
        self.show_match(m, refresh_lists=True)

    # ---- events from the service ---------------------------------------------------
    def _pump(self) -> None:
        # Drain a bounded batch, coalescing superseded live/progress updates. The
        # service may emit one lobby snapshot per player; rendering every snapshot
        # wastes Tk time and can make navigation feel frozen during match startup.
        batch = []
        try:
            for _ in range(100):
                batch.append(self.q.get_nowait())
        except queue.Empty:
            pass

        compact = []
        latest_replaceable = {}
        barriers = {"idle", "postmatch", "open_match", "tray", "error"}
        replaceable = {"lobby", "profile_progress", "history_icons", "post_icons"}
        for kind, payload in batch:
            if kind in barriers:
                latest_replaceable.clear()
                compact.append((kind, payload))
            elif kind in replaceable:
                old = latest_replaceable.get(kind)
                if old is not None:
                    compact[old] = None
                latest_replaceable[kind] = len(compact)
                compact.append((kind, payload))
            else:
                compact.append((kind, payload))
        self._pending_events.extend(item for item in compact if item is not None)

        # Bound work per Tk turn. A single slow callback is logged, while a queue
        # backlog is spread across turns so input and repaint events still run.
        deadline = __import__("time").perf_counter() + 0.008
        handled = 0
        while self._pending_events and handled < 12:
            kind, payload = self._pending_events.popleft()
            started = __import__("time").perf_counter()
            try:
                self._handle(kind, payload)
            except Exception:
                log.exception("UI event %s failed", kind)
            elapsed = (__import__("time").perf_counter() - started) * 1000
            if elapsed >= 100:
                log.warning("Slow UI event %s took %.1f ms", kind, elapsed)
            handled += 1
            if __import__("time").perf_counter() >= deadline:
                break
        self.after(30 if self._pending_events else POLL_MS, self._pump)

    def _handle(self, kind: str, payload) -> None:
        if kind == "status":
            self.set_status(payload)
        elif kind == "lobby":
            key = (payload["phase"], payload["match_id"])
            if key != self._lobby_key:                      # a new lobby started: show it without any clicking
                self._lobby_key = key
                self._select_view("Live")
            self.live.update_lobby(payload)
        elif kind == "idle":
            self._lobby_key = None
            self.live.show_idle()
        elif kind == "connected":
            self.my_puuid = payload.get("puuid")
        elif kind == "open_match":
            self.show_match(payload)
            self.set_status("Match opened.")
        elif kind == "tray":
            if payload == "open":
                self.show_window()
            elif payload == "quit":
                self.quit_app()
        elif kind == "detail":
            self.live.show_detail(payload)
        elif kind == "profile":
            if payload.get("puuid"):
                self._profile_loaded_puuid = payload["puuid"]
            if getattr(self.matches, "search_active", False):
                self.matches.show_search_result(payload)
            else:
                self.profile.show(payload)
        elif kind == "profile_progress":
            self.profile.show_progress(*payload)
        elif kind == "postmatch":
            self.show_match(payload, focus=not self.hidden, refresh_lists=True)
            self.set_status("Match finished. Leaderboard and heatmap are ready.")
            if self.hidden:
                if self.cfg.popup_on_match_end:
                    self.show_window()
                elif self.tray:
                    res = payload["result"]
                    word = "Draw" if res["draw"] else ("Victory" if res["won"] else "Defeat")
                    self.tray.notify(f'{word} {res["score"][0]}-{res["score"][1]} on {payload["map"]["name"]}. '
                                     "Open ValConvex for the leaderboard and heatmap.")
        elif kind == "history_icons":
            self.history.redraw_icons()
        elif kind == "post_icons":
            self.post.redraw_icons()
        elif kind == "minimap":
            if self.current_match and self.current_match["match_id"] == payload:
                self.post.show(self.current_match, self.minimap_path(self.current_match))
        elif kind == "error":
            self.set_status(payload)
            self.profile.show_error(payload)

    # ---- window / background ---------------------------------------------------
    def close_window(self) -> None:
        """The X button: keep running in the tray (or minimised if there is no tray)."""
        if not self.cfg.run_in_background:
            self.quit_app()
            return
        if self.tray is not None:
            self.withdraw()
            self.hidden = True
            if not self._told_tray:
                self._told_tray = True
                self.tray.notify("ValConvex is still running. Click the tray icon to open it, right-click to quit.")
        else:
            self.iconify()

    def show_window(self) -> None:
        self.hidden = False
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:
            pass

    def quit_app(self) -> None:
        if self.tray is not None:
            self.tray.stop()
        self.service.stop()
        try:
            self.cfg.save()
        except OSError:
            pass
        self.destroy()

    on_close = quit_app
