#!/usr/bin/env python3
"""ValConvex launcher.

    python run.py            real mode: detects VALORANT on its own, nothing to type or configure
    python run.py --demo     fake players and matches, no game needed
    python run.py --set-key  save a HenrikDev key (only for the optional Riot ID search box)
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from valtracker import __version__
from valtracker.config import Config, default_root
from valtracker.util import PERFORMANCE, setup_logging

NO_TK = ("ValConvex needs Python's tkinter (the window toolkit). The normal Windows installer from python.org "
         "includes it: re-run the installer, choose Modify, and tick 'tcl/tk and IDLE'.")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="ValConvex - a light, automatic VALORANT tracker")
    ap.add_argument("--demo", action="store_true", help="run on fake data (no VALORANT needed)")
    ap.add_argument("--speed", type=float, default=1.0, help="demo only: speed multiplier for the fake match cycle")
    ap.add_argument("--set-key", metavar="KEY", help="save a HenrikDev API key (only used by the Riot ID search)")
    ap.add_argument("--home", metavar="DIR", help="data folder (default: Documents/ValConvex)")
    ap.add_argument("--version", action="store_true")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"ValConvex {__version__}")
        return 0
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

    # Claim the instance port before touching the database or making network
    # requests. A second launch can bring the existing window forward promptly,
    # even if the first launch is still loading game data.
    from valtracker.single_instance import DEMO_PORT, PORT, SingleInstance

    holder = {}
    pending_show = threading.Event()

    def show_existing() -> None:
        app = holder.get("app")
        if app is None:
            pending_show.set()
        else:
            app.q.put(("tray", "open"))

    inst = SingleInstance(DEMO_PORT if args.demo else PORT)
    if not inst.acquire(show_existing):
        print("ValConvex is already running; brought it to the front.")
        return 0

    db = None
    app = None
    startup_t0 = time.perf_counter()
    try:
        home = args.home or (str(default_root() / "demo") if args.demo else "")
        phase_t0 = time.perf_counter()
        cfg = Config.load(home)
        cfg.ensure_dirs()
        setup_logging(cfg.log_path)
        PERFORMANCE.record("startup.config", time.perf_counter() - phase_t0)

        try:
            import tkinter  # noqa: F401
        except ImportError:
            print(NO_TK)
            return 1

        from valtracker.db import Database
        phase_t0 = time.perf_counter()
        db = Database(cfg.db_path)
        PERFORMANCE.record("startup.database", time.perf_counter() - phase_t0)

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
            phase_t0 = time.perf_counter()
            print("Loading game data (maps, agents, weapons)...")
            static = StaticData.fetch(cfg.cache_dir)
            PERFORMANCE.record("startup.static_data", time.perf_counter() - phase_t0)
            factory = lambda: RiotSource.connect(cfg.region_override)

        from valtracker.ui.app import App
        phase_t0 = time.perf_counter()
        app = App(cfg, db, static, factory, demo=args.demo)
        holder["app"] = app
        PERFORMANCE.record("startup.ui_init", time.perf_counter() - phase_t0)
        PERFORMANCE.record("startup.total_to_ui", time.perf_counter() - startup_t0)
        if pending_show.is_set():
            app.q.put(("tray", "open"))
        try:
            app.mainloop()
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        return 0
    finally:
        if app is not None:
            try:
                app.service.shutdown(wait=True)
            except Exception:
                import logging
                logging.getLogger("valtracker").exception("background shutdown failed")
        if db is not None:
            try:
                db.close()
            except Exception:
                import logging
                logging.getLogger("valtracker").exception("database shutdown failed")
        inst.release()


if __name__ == "__main__":
    sys.exit(main())
