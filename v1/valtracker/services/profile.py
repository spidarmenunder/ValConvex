from __future__ import annotations

from typing import Tuple


def parse_riot_id(text: str) -> Tuple[str, str]:
    """'name#tag' -> ('name', 'tag'). Raises ValueError with a friendly message."""
    text = (text or "").strip()
    if "#" not in text:
        raise ValueError("Enter a Riot ID like  name#tag")
    name, tag = text.rsplit("#", 1)
    name, tag = name.strip(), tag.strip()
    if not name or not tag:
        raise ValueError("Enter a Riot ID like  name#tag")
    return name, tag
