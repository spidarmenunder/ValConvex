"""System tray icon so closing the window keeps ValConvex running. Needs the optional 'pystray' package."""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("valtracker")


def available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def make_icon(size: int = 64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=12, fill=(28, 31, 37, 255), outline=(62, 207, 142, 255), width=3)
    d.polygon([(14, 16), (24, 16), (32, 40), (40, 16), (50, 16), (36, 50), (28, 50)], fill=(62, 207, 142, 255))
    return img


class Tray:
    """post(action) is called from the tray thread with 'open' or 'quit'."""

    def __init__(self, post: Callable[[str], None]):
        self.post = post
        self.icon = None

    def start(self) -> bool:
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem("Open ValConvex", lambda icon, item: self.post("open"), default=True),
                pystray.MenuItem("Quit", lambda icon, item: self.post("quit")))
            self.icon = pystray.Icon("ValConvex", make_icon(), "ValConvex", menu)
            self.icon.run_detached()
            return True
        except Exception as exc:
            log.warning("tray icon unavailable: %s", exc)
            self.icon = None
            return False

    def notify(self, text: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(text, "ValConvex")
            except Exception:
                pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
