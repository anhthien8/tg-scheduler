"""Public t.me web preview: channel description and username kind, no Telegram account.

Deep Crawl used to spend one account request per channel (GetFullChannel) and one
ResolveUsername per @contact. ResolveUsername has a strict per-account limit (a
FloodWait of up to ~24h after ~200 lookups), which stopped crawls early. The
public page https://t.me/<username> carries the same data for public usernames,
costs no account request and cannot get an account limited.

Results are cached for 7 days. Any network problem returns None so the caller
falls back to the Telegram API.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time

logger = logging.getLogger(__name__)

_TTL = 7 * 86400
_MAX_CACHE = 20000
_CONCURRENCY = 4
_BACKOFF_429 = 120  # seconds to stop hitting t.me after a rate-limit answer

_cache: dict[str, tuple[dict, float]] = {}
_sem: asyncio.Semaphore | None = None
_client = None
_blocked_until = 0.0

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
_TITLE_RE = re.compile(r'<div class="tgme_page_title"[^>]*>\s*<span[^>]*>(.*?)</span>', re.S)
_EXTRA_RE = re.compile(r'<div class="tgme_page_extra">(.*?)</div>', re.S)
_DESC_RE = re.compile(r'<div class="tgme_page_description[^"]*"[^>]*>(.*?)</div>', re.S)
_BUTTON_RE = re.compile(r'class="tgme_action_button_new[^"]*"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)


def _text(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", _BR_RE.sub("\n", fragment or ""))).strip()


def _count(extra: str) -> int | None:
    m = re.search(r"([\d][\d\s ,.]*)\s*(subscriber|member|monthly user)", extra)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else None


def parse_page(page: str) -> dict | None:
    """Classify a t.me/<username> page.

    Returns {"kind", "title", "description", "count"} where kind is
    'user' | 'bot' | 'channel' | 'group' | 'invalid', or None when the page is
    not a recognisable Telegram preview (blocked, captcha, layout change)."""
    if not page or "tgme_page" not in page:
        return None
    title_m = _TITLE_RE.search(page)
    extra = " ".join(_text(x) for x in _EXTRA_RE.findall(page))
    desc_m = _DESC_RE.search(page)
    buttons = " ".join(_text(b) for b in _BUTTON_RE.findall(page))
    if not title_m:
        # "Telegram: Contact @name" page = the username is free (never used or deleted)
        if "tgme_action_button_new" in page:
            return {"kind": "invalid", "title": "", "description": "", "count": None}
        return None
    low = extra.lower()
    if "start bot" in buttons.lower() or "monthly user" in low:
        kind = "bot"
    elif "subscriber" in low:
        kind = "channel"
    elif "member" in low:
        kind = "group"
    else:
        kind = "user"
    return {
        "kind": kind,
        "title": _text(title_m.group(1)),
        "description": _text(desc_m.group(1)) if desc_m else "",
        "count": _count(extra),
    }


def cached(username: str) -> dict | None:
    hit = _cache.get((username or "").lstrip("@").lower())
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    return None


def _store(key: str, info: dict) -> None:
    if len(_cache) >= _MAX_CACHE:
        for k in list(_cache)[: _MAX_CACHE // 10]:  # drop the oldest 10%
            _cache.pop(k, None)
    _cache[key] = (info, time.time())


async def _http():
    global _client, _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_CONCURRENCY)
    if _client is None:
        import httpx
        _client = httpx.AsyncClient(
            timeout=8.0, follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                     "Accept-Language": "en-US,en;q=0.8"})
    return _client


async def fetch(username: str) -> dict | None:
    """Public info for @username (cached 7 days). None = unknown, use the API instead."""
    global _blocked_until
    key = (username or "").lstrip("@").lower()
    if not _USERNAME_RE.match(key):
        return None
    hit = cached(key)
    if hit is not None:
        return hit
    if time.time() < _blocked_until:
        return None
    client = await _http()
    async with _sem:
        if time.time() < _blocked_until:
            return None
        try:
            r = await client.get(f"https://t.me/{key}")
        except Exception as e:
            logger.debug("[t.me] %s: %s", key, e)
            return None
    if r.status_code == 429:
        _blocked_until = time.time() + _BACKOFF_429
        logger.warning("[t.me] rate limited — using the Telegram API for %ss", _BACKOFF_429)
        return None
    if r.status_code != 200:
        return None
    info = parse_page(r.text)
    if info is not None:
        _store(key, info)
    return info


async def fetch_many(usernames) -> dict[str, dict]:
    """Fetch several usernames concurrently. Returns {lower_username: info} for the ones found."""
    keys = list(dict.fromkeys((u or "").lstrip("@").lower() for u in usernames if u))
    if not keys:
        return {}
    res = await asyncio.gather(*(fetch(k) for k in keys), return_exceptions=True)
    return {k: v for k, v in zip(keys, res, strict=False) if isinstance(v, dict)}
