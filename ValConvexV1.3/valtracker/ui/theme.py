"""ValConvex visual system: dark, compact, competitive desktop UI."""
from __future__ import annotations

from typing import Optional

from ..ranks import rank_from_points, tier_family, tier_name, tier_short

BG = "#0e1117"                 # main workspace
SIDEBAR = "#10141c"            # navigation rail
PANEL = "#171c26"              # primary cards
PANEL2 = "#1e2532"             # elevated / selected surfaces
PANEL3 = "#283243"             # hover and nested surfaces
BORDER = "#2a3444"             # quiet structural borders
TEXT = "#eef2f8"
MUTED = "#a0aabd"
SUBTLE = "#707d91"
GREEN = "#35d39a"
RED = "#ff6478"
AMBER = "#f5bd62"
BLUE = "#72b7ff"
PURPLE = "#8b7bff"
PURPLE_DARK = "#7160e8"
PURPLE_SOFT = "#292644"
WHITE = "#ffffff"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_BIG = ("Segoe UI Semibold", 22)
FONT_HERO = ("Segoe UI Semibold", 27)
FONT_MID = ("Segoe UI Semibold", 14)
FONT_TITLE = ("Segoe UI Semibold", 15)
FONT_DISPLAY = ("Segoe UI Semibold", 9)



def rank_color(tier: Optional[int]) -> str:
    return RANK_COLORS.get(tier_family(tier), MUTED)


def fmt_rating(r: Optional[float]) -> str:
    return "..." if r is None else f"{r:.2f}"


def fmt_pct(x: Optional[float], digits: int = 0) -> str:
    return "-" if x is None else f"{x * 100:.{digits}f}%"


def fmt_num(x: Optional[float], digits: int = 0) -> str:
    return "-" if x is None else f"{x:,.{digits}f}"


def fmt_rank(tier: Optional[int], rr: Optional[int] = None) -> str:
    if int(tier or 0) < 3:
        return "Unranked"
    return tier_name(tier) if rr is None else f"{tier_name(tier)} · {int(rr)}"


def fmt_skill(skill: Optional[float]) -> str:
    if skill is None:
        return "-"
    tier, rr = rank_from_points(skill)
    return f"{skill:,.0f} ≈ {tier_short(tier)} · {rr} RR"


def rating_tag(r: Optional[float]) -> str:
    if r is None:
        return "norm"
    if r >= 1.05:
        return "hi"
    if r < 0.98:
        return "lo"
    return "norm"


def signed(n: Optional[int]) -> str:
    if n is None:
        return "-"
    return f"+{n}" if n > 0 else (f"−{abs(n)}" if n < 0 else "0")


def open_folder(path) -> None:
    """Open a folder in the native file manager.

    Windows gets an explicit Explorer launch so this also works reliably from
    a PyInstaller --windowed/--onefile build where there is no console.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    folder = Path(path)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        folder = folder.resolve()
        if sys.platform.startswith("win"):
            # explorer.exe is more reliable than relying on shell association
            # from a windowed PyInstaller executable.
            subprocess.Popen(["explorer.exe", str(folder)], close_fds=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", str(folder)], close_fds=True)
    except Exception:
        # Last-resort Windows shell association.
        if sys.platform.startswith("win"):
            try:
                os.startfile(str(folder))  # type: ignore[attr-defined]
            except Exception:
                pass
