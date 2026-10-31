#!/usr/bin/env python3
"""ValConvex launcher.

    python run.py            real mode: detects VALORANT on its own, nothing to type or configure
    python run.py --demo     fake players and matches, no game needed
    python run.py --set-key  save a HenrikDev key (only for the optional Riot ID search box)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from valtracker import __version__
from valtracker.config import Config, default_root
from valtracker.util import setup_logging

NO_TK = ("ValConvex needs Python's tkinter (the window toolkit). The normal Windows installer from python.org "
         "includes it: re-run the installer, choose Modify, and tick 'tcl/tk and IDLE'.")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="ValConvex - a light, automatic VALORANT tracker")
    ap.add_argument("--demo", action="store_true", help="run on fake data (no VALORANT needed)")
    ap.add_argument("--speed", type=float, default=1.0, help="demo only: speed multiplier for the fake match cycle")
    ap.add_argument("--set-key", metavar="KEY", help="save your HenrikDev API key (only used by the Riot ID search)")
    ap.add_argument("--home", metavar="DIR", help="data folder (default: Documents/ValConvex)")
    ap.add_argument("--version", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"ValConvex {__version__}")
        return 0
    home = args.home or (str(default_root() / "demo") if args.demo else "")
    cfg = Config.load(home)
    cfg.ensure_dirs()
    if args.set_key is not None:
        key = args.set_key.strip()
        if not key:
            print("That key is empty.")
            return 1
        real = Config.load(args.home or "")
        real.henrik_api_key = key
        real.save()
        print(f"Saved. It is stored in {real.config_path} (keep that file private).")
        return 0

    setup_logging(cfg.log_path)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(NO_TK)
        return 1

    from valtracker.db import Database
    db = Database(cfg.db_path)
    if args.demo:
        from valtracker import demo
        from valtracker.demo_source import DemoSource
        static = demo.demo_static(cache_dir=cfg.cache_dir)
        try:
            demo.write_demo_agent_icons(cfg.cache_dir)
            img = cfg.cache_dir / f"map_{demo.MAP_UUID}.png"
            if not img.exists():
                demo.write_demo_minimap(img)
        except Exception as exc:                          # Pillow missing: the heatmap falls back to a grid
            print("Could not draw the demo minimap:", exc)
        source = DemoSource(static=static, speed=max(0.1, args.speed))
        factory = lambda: source
    else:
        from valtracker.riot.source import RiotSource
        from valtracker.static import StaticData
        print("Loading game data (maps, agents, weapons)...")
        static = StaticData.fetch(cfg.cache_dir)
        factory = lambda: RiotSource.connect(cfg.region_override)

    from valtracker.single_instance import DEMO_PORT, PORT, SingleInstance
    holder = {}
    inst = SingleInstance(DEMO_PORT if args.demo else PORT)
    if not inst.acquire(lambda: holder["app"].q.put(("tray", "open")) if "app" in holder else None):
        print("ValConvex is already running; brought it to the front.")
        return 0
    from valtracker.ui.app import App
    app = App(cfg, db, static, factory, demo=args.demo)
    holder["app"] = app
    try:
        app.mainloop()
    except Exception:
        import traceback
        traceback.print_exc()
        raise
    finally:
        inst.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
