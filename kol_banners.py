"""Campaign banners the AI agent can attach to a KOL reply.

The agent writes a tag like [[BANNER:trading_comp]] anywhere in its reply.
We strip the tag from the text and send the matching image from
data/kol_banners/<key>.(png|jpg|jpeg|webp) right after the text — at most one
banner per reply and never the same banner twice in one chat.
"""
import os
import re

BANNER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "kol_banners")
_TAG = re.compile(r"\[\[\s*BANNER\s*:\s*([a-z0-9_\-]+)\s*\]\]", re.I)
_SENT_MARK = "[banner sent: {}]"
_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def banner_path(key: str) -> str | None:
    key = (key or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_\-]+", key):
        return None
    for ext in _EXTS:
        p = os.path.join(BANNER_DIR, key + ext)
        if os.path.isfile(p):
            return p
    return None


def extract(text: str) -> tuple[str, list[str]]:
    """Return (text without tags, banner keys in order)."""
    keys = [m.group(1).lower() for m in _TAG.finditer(text or "")]
    clean = _TAG.sub("", text or "")
    clean = re.sub(r"\[banner sent:[^\]]*\]", "", clean, flags=re.I)  # model echoing our history mark
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, keys


def already_sent(history: list, key: str) -> bool:
    mark = _SENT_MARK.format(key)
    return any(mark in str((h or {}).get("content", "")) for h in (history or []))


def sent_mark(key: str) -> str:
    return _SENT_MARK.format(key)
