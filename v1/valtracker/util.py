from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any, Optional


def now_ms() -> int:
    return int(time.time() * 1000)


def ago(ms: Optional[int], now: Optional[int] = None) -> str:
    """Human 'time since' text: '3 h ago', 'Yesterday', '2 d ago'."""
    if not ms:
        return "unknown"
    diff = ((now if now is not None else now_ms()) - int(ms)) // 1000
    if diff < 0:
        diff = 0
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{diff // 60} min ago"
    if diff < 86400:
        return f"{diff // 3600} h ago"
    if diff < 172800:
        return "Yesterday"
    days = diff // 86400
    if days < 30:
        return f"{days} d ago"
    return f"{days // 30} mo ago"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def decode_b64_json(text: Optional[str]) -> dict:
    """Decode a base64 encoded JSON blob (used by Riot presence data)."""
    if not text:
        return {}
    try:
        pad = "=" * (-len(text) % 4)
        out = json.loads(base64.b64decode(text + pad).decode("utf-8", "replace"))
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-") or "x"


def setup_logging(path=None) -> logging.Logger:
    log = logging.getLogger("valtracker")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if path:
        try:
            fh = logging.FileHandler(path, encoding="utf-8")
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            pass
    return log


def get(d: Any, *keys, default=None):
    """Safe nested dict access: get(d, 'a', 'b') -> d['a']['b'] or default."""
    cur = d
    for k in keys:
        if isinstance(cur, dict) and k in cur and cur[k] is not None:
            cur = cur[k]
        else:
            return default
    return cur
