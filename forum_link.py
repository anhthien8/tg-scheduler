"""Parse/presolve Telegram group links into the IDs Forum Inbox needs.

Supports (offline, no Telethon):
  - https://t.me/c/<internal_id>/<topic>     -> group_id=-100<internal_id>, topic
  - https://t.me/c/<internal_id>             -> group_id=-100<internal_id>, topic=1
  - tg://privatepost?channel=<id>&post=<t>   -> same as /c/
  - plain "-1004352266125" or "4352266125"   -> group_id as-is / prefixed

Needs Telethon (handled by the caller):
  - t.me/+<invite_hash>       (private invite)
  - t.me/<username>[/<topic>] (public username)
  - joinchat/<hash>

This module is pure and unit-testable; the network part lives in the route.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass
class ResolvedLink:
    group_id: int | None = None
    general_topic_id: int | None = None
    # When set, the caller must resolve this via Telethon (invite hash or username).
    needs_network: bool = False
    invite_hash: str | None = None
    username: str | None = None
    source: str = ""


_C_INTERNAL = re.compile(r"^/c/(\d+)(?:/(\d+))?/?$")
_USERNAME_TOPIC = re.compile(r"^/([A-Za-z0-9_]{3,})(?:/(\d+))?/?$")
_INVITE = re.compile(r"^/(?:\+|joinchat/)([A-Za-z0-9_-]+)/?$")


def _to_supergroup_id(internal_id: int | str) -> int:
    """Telegram's /c/<id> is the bare channel id; the real API id is -100<id>."""
    return int(f"-100{internal_id}")


def parse_forum_link(raw: str) -> ResolvedLink:
    """Parse a user-pasted link into what we can determine offline.

    Raises ValueError on input we cannot interpret at all.
    """
    if raw is None:
        raise ValueError("Thiếu link")
    s = raw.strip()
    if not s:
        raise ValueError("Link trống")

    # Bare numeric ID: "-1004352266125", "-4352266125", "4352266125"
    if re.fullmatch(r"-?\d+", s):
        num = int(s)
        if num < 0:
            # Already a supergroup id (-100...) or an older chat id.
            gid = num
        else:
            gid = _to_supergroup_id(num)
        return ResolvedLink(group_id=gid, general_topic_id=1, source="numeric")

    # Normalise scheme-less input to a URL for parsing.
    url = s
    if url.startswith("tg://"):
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if parsed.netloc.endswith("privatepost") or "channel" in qs:
            chan = (qs.get("channel") or [None])[0]
            post = (qs.get("post") or [None])[0]
            if chan and chan.isdigit():
                topic = int(post) if post and post.isdigit() else 1
                return ResolvedLink(group_id=_to_supergroup_id(chan),
                                    general_topic_id=topic, source="privatepost")
        raise ValueError("Link tg:// không được hỗ trợ")
    if not re.match(r"^https?://", url):
        url = "https://" + url

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
        raise ValueError(f"Không phải link Telegram (host={host or '?'})")
    path = parsed.path or ""

    m = _C_INTERNAL.match(path)
    if m:
        internal = m.group(1)
        topic = int(m.group(2)) if m.group(2) else 1
        return ResolvedLink(group_id=_to_supergroup_id(internal),
                            general_topic_id=topic, source="c_link")

    m = _INVITE.match(path)
    if m:
        return ResolvedLink(invite_hash=m.group(1), needs_network=True, source="invite")

    m = _USERNAME_TOPIC.match(path)
    if m:
        username = m.group(1)
        topic = int(m.group(2)) if m.group(2) else 1
        return ResolvedLink(username=username, general_topic_id=topic,
                            needs_network=True, source="username")

    raise ValueError("Không nhận dạng được link. Dùng dạng t.me/c/<id>/<topic> hoặc t.me/<username>")
