"""Display-safe text: only characters the device font can draw.

The device font is a purpose-built subset (see docs/development/engineering/
lvgl-chinese-fonts.md in the repository). GB2312 level 1 is the 3755 most
common simplified characters; the firmware font must cover at least this set
plus the punctuation below, and the relay must never send anything else.
"""

from __future__ import annotations

import re
from functools import lru_cache

MAX_REPLY_CHARS = 200

_PUNCTUATION = "，。！？、；：“”‘’（）《》…—·～"
_MARKDOWN = re.compile(r"[*_#`>|~\[\]{}\\]")
_SPACES = re.compile(r"\s+")


@lru_cache(maxsize=1)
def allowed_chars() -> frozenset[str]:
    chars = {chr(c) for c in range(0x20, 0x7F)}
    chars.update(_PUNCTUATION)
    # GB2312 level 1: rows 16-55, i.e. lead bytes 0xB0-0xD7.
    for lead in range(0xB0, 0xD8):
        for trail in range(0xA1, 0xFF):
            try:
                chars.add(bytes((lead, trail)).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return frozenset(chars)


def sanitize(text: str, limit: int = MAX_REPLY_CHARS) -> str:
    """Strip markdown, drop undrawable characters and cap the length."""
    text = _MARKDOWN.sub("", text)
    allowed = allowed_chars()
    kept = "".join(ch if ch in allowed else " " if ch.isspace() else "" for ch in text)
    kept = _SPACES.sub(" ", kept).strip()
    if len(kept) > limit:
        kept = kept[: limit - 1].rstrip() + "…"
    return kept
