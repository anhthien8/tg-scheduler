"""Edit / delete / revoke one of OUR messages in a lead's DM from the app.

History rows don't keep Telegram message ids, so the real message is found by
text + send time among our recent outgoing messages in that DM.
"""
import html
import re
from datetime import datetime, timedelta, timezone

HUMAN_PREFIX = "[Human Admin]:"
WINDOW = timedelta(minutes=20)
_TAG = re.compile(r"<[^>]+>")
_BANNER_MARK = re.compile(r"\[banner sent:[^\]]*\]", re.I)


def norm(text: str) -> str:
    t = (text or "").strip()
    if t.startswith(HUMAN_PREFIX):
        t = t[len(HUMAN_PREFIX):]
    t = _BANNER_MARK.sub("", html.unescape(_TAG.sub("", t)))
    return re.sub(r"\s+", " ", t).strip().lower()


def _local_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().replace(tzinfo=None)


def find_matches(entry: dict, messages: list) -> list:
    """Our Telegram messages (objects with .id .date .message) that make up `entry`.

    A long AI reply may have gone out as several messages: every message whose text is
    part of the entry and was sent within WINDOW of it counts. Closest in time first.
    """
    want = norm(entry.get("content", ""))
    if not want:
        return []
    try:
        at = datetime.fromisoformat(entry.get("timestamp") or "")
    except ValueError:
        at = None
    if at is not None and at.tzinfo is not None:
        at = _local_naive(at)
    hits = []
    for m in messages:
        got = norm(getattr(m, "message", "") or "")
        if not got or not (got == want or got in want or want in got):
            continue
        gap = abs(_local_naive(m.date) - at) if at else timedelta(0)
        if at and gap > WINDOW:
            continue
        hits.append((got != want, gap, m))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits]
