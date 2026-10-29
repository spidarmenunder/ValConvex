"""Small reusable widgets and helpers for the Tk windows."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Iterable, List, Optional, Sequence, Tuple

from . import theme


def apply_style(root) -> None:
    root.configure(bg=theme.BG)
    root.option_add("*TCombobox*Listbox.background", theme.PANEL)
    root.option_add("*TCombobox*Listbox.foreground", theme.TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", theme.PURPLE)
    root.option_add("*TCombobox*Listbox.selectForeground", theme.WHITE)
    root.option_add("*TCombobox*Listbox.font", theme.FONT)
    root.option_add("*Menu.background", theme.PANEL)
    root.option_add("*Menu.foreground", theme.TEXT)
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", background=theme.BG, foreground=theme.TEXT, fieldbackground=theme.PANEL, font=theme.FONT)
    st.configure("TFrame", background=theme.BG)
    st.configure("Sidebar.TFrame", background=theme.SIDEBAR)
    st.configure("Card.TFrame", background=theme.PANEL)
    st.configure("Soft.TFrame", background=theme.PANEL2)
    st.configure("Panel.TFrame", background=theme.PANEL)
    st.configure("Accent.TButton", background=theme.PURPLE, foreground=theme.WHITE, padding=(12, 7))
    st.map("Accent.TButton", background=[("active", theme.PURPLE_DARK)])
    st.configure("TLabel", background=theme.BG, foreground=theme.TEXT)
    st.configure("Panel.TLabel", background=theme.PANEL, foreground=theme.TEXT)
    st.configure("Muted.TLabel", background=theme.BG, foreground=theme.MUTED, font=theme.FONT_SMALL)
    st.configure("PanelMuted.TLabel", background=theme.PANEL, foreground=theme.MUTED, font=theme.FONT_SMALL)
    st.configure("TButton", background=theme.PANEL2, foreground=theme.TEXT, bordercolor=theme.BORDER, padding=(10, 6), relief="flat")
    st.map("TButton", background=[("active", theme.BORDER)])
    st.configure("TCheckbutton", background=theme.BG, foreground=theme.TEXT)
    st.configure("TRadiobutton", background=theme.BG, foreground=theme.TEXT)
    st.configure("TNotebook", background=theme.BG, bordercolor=theme.BG, tabmargins=0)
    st.configure("TNotebook.Tab", background=theme.PANEL, foreground=theme.MUTED, padding=(14, 6))
    st.map("TNotebook.Tab", background=[("selected", theme.PANEL2)], foreground=[("selected", theme.TEXT)])
    st.configure("Treeview", background=theme.PANEL, fieldbackground=theme.PANEL, foreground=theme.TEXT,
                 rowheight=26, borderwidth=0, font=theme.FONT)
    st.configure("Treeview.Heading", background=theme.BG, foreground=theme.MUTED, relief="flat", font=theme.FONT_SMALL)
    st.map("Treeview", background=[("selected", theme.PANEL2)], foreground=[("selected", theme.TEXT)])
    st.configure("Vertical.TScrollbar", background=theme.PANEL3, troughcolor=theme.PANEL, bordercolor=theme.PANEL, arrowcolor=theme.PANEL, relief="flat", width=7)
    st.map("Vertical.TScrollbar", background=[("active", theme.SUBTLE), ("pressed", theme.TEXT)])
    st.configure("TEntry", fieldbackground=theme.PANEL, foreground=theme.TEXT)
    # The clam theme paints read-only boxes light grey unless every state is mapped, which made the text unreadable.
    st.configure("TCombobox", fieldbackground=theme.PANEL2, background=theme.PANEL3, foreground=theme.TEXT,
                 arrowcolor=theme.TEXT, bordercolor=theme.BORDER, lightcolor=theme.PANEL2, darkcolor=theme.PANEL2,
                 selectbackground=theme.PANEL2, selectforeground=theme.TEXT, padding=(6, 4))
    st.map("TCombobox",
           fieldbackground=[("readonly", theme.PANEL2), ("disabled", theme.PANEL), ("active", theme.PANEL3)],
           foreground=[("readonly", theme.TEXT), ("disabled", theme.MUTED)],
           background=[("readonly", theme.PANEL3), ("active", theme.BORDER)],
           selectbackground=[("readonly", theme.PANEL2), ("focus", theme.PANEL2)],
           selectforeground=[("readonly", theme.TEXT), ("focus", theme.TEXT)],
           arrowcolor=[("disabled", theme.SUBTLE)],
           bordercolor=[("focus", theme.PURPLE)])
    for name in ("TCheckbutton", "TRadiobutton"):
        st.map(name, background=[("active", theme.BG)], foreground=[("active", theme.TEXT)],
               indicatorcolor=[("selected", theme.PURPLE), ("!selected", theme.PANEL2)])
    st.configure("TMenubutton", background=theme.PANEL2, foreground=theme.TEXT, padding=(10, 4))



class AppleScrollbar(tk.Frame):
    """Minimal dark scrollbar: thin, arrowless, low-contrast until hovered."""
    def __init__(self, parent, command=None, orient="vertical", **kwargs):
        super().__init__(parent, bg=theme.PANEL, width=7, highlightthickness=0, bd=0, **kwargs)
        self.command = command
        self.orient = orient
        self.canvas = tk.Canvas(self, width=7, bg=theme.PANEL, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._first, self._last = 0.0, 1.0
        self._hover = False
        self._drag_y = None
        self._drag_start = None
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.canvas.bind("<Enter>", self._enter)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)

    def set(self, first, last):
        try:
            self._first, self._last = float(first), float(last)
        except (TypeError, ValueError):
            self._first, self._last = 0.0, 1.0
        self._draw()

    def _enter(self, _=None):
        self._hover = True; self._draw()
    def _leave(self, _=None):
        self._hover = False; self._draw()

    def _draw(self):
        if not self.winfo_exists(): return
        self.canvas.delete("thumb")
        h = max(self.canvas.winfo_height(), 1)
        thumb_h = max(18, h * max(0.03, self._last - self._first))
        y = (h - thumb_h) * max(0.0, min(1.0, self._first))
        color = theme.SUBTLE if self._hover else theme.PANEL3
        self.canvas.create_round_rect = None
        r = 3
        self.canvas.create_oval(1, y, 7, y + 6, fill=color, outline="", tags="thumb")
        self.canvas.create_rectangle(1, y + r, 7, y + thumb_h - r, fill=color, outline="", tags="thumb")
        self.canvas.create_oval(1, y + thumb_h - 6, 7, y + thumb_h, fill=color, outline="", tags="thumb")

    def _press(self, event):
        h = max(self.canvas.winfo_height(), 1)
        thumb_h = max(18, h * max(0.03, self._last - self._first))
        y = (h - thumb_h) * max(0.0, min(1.0, self._first))
        if y <= event.y <= y + thumb_h:
            self._drag_y = event.y
            self._drag_start = self._first
        elif self.command:
            self.command((event.y / h) - (thumb_h / h) / 2, (event.y / h) + (thumb_h / h) / 2)

    def _drag(self, event):
        if self._drag_y is None or not self.command: return
        h = max(self.canvas.winfo_height(), 1)
        thumb_h = max(18, h * max(0.03, self._last - self._first))
        travel = max(1.0, h - thumb_h)
        delta = (event.y - self._drag_y) / travel
        self.command(max(0.0, min(1.0, self._drag_start + delta)))


class RoundedFrame(tk.Frame):
    """A lightweight rounded panel background for the desktop UI."""

    def __init__(self, parent, fill=None, outline=None, radius=14, **kwargs):
        self._fill = fill or theme.PANEL
        self._outline = outline or theme.BORDER
        self._radius = radius
        super().__init__(parent, bg=theme.BG, **kwargs)
        self._canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, bd=0)
        self._canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        c = self._canvas
        c.delete("panel")
        w, h = max(2, self.winfo_width()), max(2, self.winfo_height())
        r = min(self._radius, w // 2, h // 2)
        pts = [r,0, w-r,0, w,0, w,r, w,h-r, w,h, w-r,h, r,h, 0,h, 0,h-r, 0,r, 0,0]
        c.create_polygon(pts, smooth=True, splinesteps=16, fill=self._fill, outline=self._outline,
                         width=1, tags="panel")
        c.tag_lower("panel")

def make_tree(parent, columns: Sequence[Tuple[str, str, int, str]], height: int = 5, image_column: bool = False) -> ttk.Treeview:
    """columns: (key, heading, width, anchor); optionally reserve a compact image column."""
    tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show=("tree headings" if image_column else "headings"), height=height, selectmode="browse")
    if image_column:
        tree.heading("#0", text="")
        tree.column("#0", width=68, minwidth=68, stretch=False, anchor="center")
    for key, heading, width, anchor in columns:
        tree.heading(key, text=heading)
        tree.column(key, width=width, anchor=anchor, stretch=True)
    tree.tag_configure("hi", foreground=theme.GREEN)
    tree.tag_configure("lo", foreground=theme.MUTED)
    tree.tag_configure("norm", foreground=theme.TEXT)
    tree.tag_configure("me", background=theme.PANEL2)
    # Subtle team tints used by post-match leaderboard rows. These sit behind
    # the normal rating foreground colors so performance coloring remains readable.
    tree.tag_configure("team_me", background="#153027")
    tree.tag_configure("team_en", background="#301b20")
    tree.tag_configure("win", foreground=theme.GREEN)
    tree.tag_configure("loss", foreground=theme.RED)
    return tree


def sync_tree(tree: ttk.Treeview, rows: List[Tuple[str, tuple, tuple]]) -> None:
    """Update a tree in place when the rows are the same, so selection and scroll position survive."""
    iids = [r[0] for r in rows]
    existing = list(tree.get_children())
    selected = list(tree.selection())
    if existing == iids:
        for iid, values, tags in rows:
            tree.item(iid, values=values, tags=tags)
        return
    if existing:
        tree.delete(*existing)
    for iid, values, tags in rows:
        tree.insert("", "end", iid=iid, values=values, tags=tags)
    keep = [s for s in selected if s in iids]
    if keep:
        tree.selection_set(keep)


class Tile(ttk.Frame):
    """A label over a value, used for stat grids."""

    def __init__(self, parent, label: str, style_prefix: str = "Panel"):
        super().__init__(parent, style=f"{style_prefix}.TFrame")
        self.value = tk.StringVar(value="-")
        self.sub = tk.StringVar(value="")
        ttk.Label(self, text=label, style=f"{style_prefix}Muted.TLabel").pack(anchor="w")
        self.value_label = tk.Label(self, textvariable=self.value, bg=theme.PANEL, fg=theme.TEXT, font=theme.FONT_MID)
        self.value_label.pack(anchor="w")
        ttk.Label(self, textvariable=self.sub, style=f"{style_prefix}Muted.TLabel").pack(anchor="w")

    def set(self, value: str, sub: str = "", color: Optional[str] = None) -> None:
        self.value.set(value)
        self.sub.set(sub)
        self.value_label.configure(fg=color or theme.TEXT)


class WinBar(tk.Canvas):
    """Team win estimate: green share for your team, red for the enemy."""

    def __init__(self, parent, height: int = 28):
        super().__init__(parent, height=height, bg=theme.BG, highlightthickness=0)
        self.win: Optional[dict] = None
        self.text = "Win estimate appears once the match starts"
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, win: Optional[dict], text: str = "") -> None:
        self.win = win
        if text:
            self.text = text
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w = max(int(self.winfo_width()), 100)
        h = int(self["height"])
        if not self.win:
            self.create_rectangle(0, 0, w, h, fill=theme.PANEL, outline="")
            self.create_text(w // 2, h // 2, text=self.text, fill=theme.MUTED, font=theme.FONT_SMALL)
            return
        p = self.win["p_me"]
        split = int(w * p)
        self.create_rectangle(0, 0, split, h, fill="#1d9e75", outline="")
        self.create_rectangle(split, 0, w, h, fill="#d64545", outline="")
        self.create_text(8, h // 2, anchor="w", text=f"Your team {p * 100:.0f}%", fill="white", font=theme.FONT_BOLD)
        self.create_text(w - 8, h // 2, anchor="e", text=f"Enemy {(1 - p) * 100:.0f}%", fill="white", font=theme.FONT_BOLD)


class RRBars(tk.Canvas):
    """RR change per game, oldest to newest: green up for gains, red down for losses."""

    def __init__(self, parent, width: int = 220, height: int = 90):
        super().__init__(parent, width=width, height=height, bg=theme.PANEL, highlightthickness=0)
        self.series: List[int] = []
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, series: Iterable[int]) -> None:
        self.series = list(series)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w, h = max(int(self.winfo_width()), 120), int(self["height"])
        mid = h // 2
        self.create_line(0, mid, w, mid, fill=theme.BORDER)
        n = len(self.series)
        if not n:
            return
        top = max(max(abs(v) for v in self.series), 10)
        slot = w / n
        for i, v in enumerate(self.series):
            bar = (abs(v) / top) * (mid - 6)
            x0, x1 = i * slot + slot * 0.2, (i + 1) * slot - slot * 0.2
            if v >= 0:
                self.create_rectangle(x0, mid - bar, x1, mid, fill=theme.GREEN, outline="")
            else:
                self.create_rectangle(x0, mid, x1, mid + bar, fill=theme.RED, outline="")
