"""WEEX alternative domains for regions where weex.com is blocked (India, Pakistan).

The official mirrors serve the same site, so a link keeps its path, query and vipCode:
https://www.weex.com/vi/register?vipCode=ybsv -> https://wcicisx.online/vi/register?vipCode=ybsv

AI messages to such a lead are rewritten in dm_reply_tracker._send_ai_message (one place
for replies, step reminders, drips and KOL care). A lead counts as "blocked region" when
their own messages or KOL profile mention India/Pakistan (or Hindi/Urdu script), or they
say the WEEX site/link does not open.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import database as db

SETTING_KEY = "weex_mirror_domains"
DEFAULT_MIRRORS = ("https://www.weex.io", "https://wcicisx.online", "https://wqayejx.site", "https://www.wnenatx.online")

# Sync copy for personalize_weex_links (vipCode on mirror links); refreshed by get_mirrors().
_mirrors: list[str] = list(DEFAULT_MIRRORS)

_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def parse_mirrors(raw) -> list[str]:
    """Comma/space/newline list (or JSON list) -> ['https://host', ...]. Path is dropped;
    weex.com itself and invalid hosts are skipped."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            items = loaded if isinstance(loaded, list) else [raw]
        except (ValueError, TypeError):
            items = [raw]
    else:
        items = list(raw)
    out: list[str] = []
    for item in items:
        for tok in re.split(r"[\s,;]+", str(item or "")):
            tok = tok.strip()
            if not tok:
                continue
            if "://" not in tok:
                tok = "https://" + tok
            try:
                parts = urlsplit(tok)
            except ValueError:
                continue
            host = (parts.hostname or "").lower()
            scheme = parts.scheme.lower() if parts.scheme.lower() in ("http", "https") else "https"
            if not _HOST_RE.match(host) or host == "weex.com" or host.endswith(".weex.com"):
                continue
            url = f"{scheme}://{host}"
            if url not in out:
                out.append(url)
    return out


async def get_mirrors() -> list[str]:
    """Saved list (settings are cached in db.get_setting), default when never saved."""
    global _mirrors
    raw = await db.get_setting(SETTING_KEY, None)
    _mirrors = list(DEFAULT_MIRRORS) if raw is None else parse_mirrors(raw)
    return list(_mirrors)


async def save_mirrors(raw) -> list[str]:
    global _mirrors
    mirrors = parse_mirrors(raw)
    await db.set_setting(SETTING_KEY, json.dumps(mirrors))
    _mirrors = mirrors
    return list(mirrors)


def mirror_hosts() -> set[str]:
    return {(urlsplit(m).hostname or "").lower() for m in _mirrors}


def is_weex_host(host: str) -> bool:
    """weex.com, any *.weex.com, or one of the configured mirrors."""
    host = (host or "").lower()
    return host == "weex.com" or host.endswith(".weex.com") or host in mirror_hosts()


# ── Who needs the mirror ────────────────────────────────────────────────────
_REGION_RE = re.compile(
    r"\b(?:india|indians?|pakistan|pakistanis?|bharat|mumbai|delhi|bangalore|bengaluru|kolkata|"
    r"chennai|hyderabad|pune|ahmedabad|jaipur|lucknow|karachi|lahore|islamabad|rawalpindi|"
    r"faisalabad|peshawar|multan)\b"
    "|[ऀ-ॿ]"                       # Devanagari (Hindi)
    "|[ٹڈڑںے]",     # letters specific to Urdu
    re.IGNORECASE,
)
_BLOCKED_RE = re.compile(
    r"\bblock(?:ed)?\b|\bbanned\b|can'?t be reached|cannot be reached|this site can'?t|"
    r"\b(?:can'?t|cannot|unable to|couldn'?t|could not)\s+(?:open|access|load|reach|connect)|"
    r"\b(?:not|isn'?t|won'?t|doesn'?t|didn'?t|aren'?t)\s+(?:open(?:ing)?|load(?:ing)?)\b|"
    r"not accessible|inaccessible|unreachable|err_connection|dns_probe|"
    r"bị chặn|không vào được|không truy cập được",
    re.IGNORECASE,
)
# A block complaint only counts when it is about a website/link (not "you blocked me").
_SITE_CONTEXT_RE = re.compile(r"weex|web ?site|\bsite\b|\blink\b|\burl\b|\bpage\b|regist|sign ?up|domain|trang|web",
                              re.IGNORECASE)
_WEEX_IN_TEXT_RE = re.compile(r"weex\.com", re.IGNORECASE)


def needs_mirror(history: list[dict] | None, profile: dict | None = None) -> bool:
    """True when this lead should get WEEX links on the mirror domains."""
    if profile:
        try:
            blob = json.dumps(profile, ensure_ascii=False)
        except (TypeError, ValueError):
            blob = str(profile)
        if _REGION_RE.search(blob):
            return True
    prev_assistant = ""
    for m in (history or [])[-30:]:
        content = str(m.get("content") or "")
        if m.get("role") != "user":
            prev_assistant = content
            continue
        if _REGION_RE.search(content):
            return True
        if _BLOCKED_RE.search(content) and (_SITE_CONTEXT_RE.search(content)
                                            or _WEEX_IN_TEXT_RE.search(prev_assistant)):
            return True
    return False


def pick_mirror(user_id: int, mirrors: list[str] | None = None) -> str | None:
    """Same lead always gets the same domain (stable across messages)."""
    mirrors = _mirrors if mirrors is None else mirrors
    if not mirrors:
        return None
    return mirrors[abs(int(user_id or 0)) % len(mirrors)]


# weex.com / www.weex.com, with or without scheme; not other subdomains
# (affiliates.weex.com has no known mirror) and not e-mails (support@weex.com).
_MAIN_SITE_RE = re.compile(
    r"(?<![\w.@/-])(?:https?://)?(?:www\.)?weex\.com(?![\w-])(?P<rest>[/?#][^\s<>()\"']*)?",
    re.IGNORECASE,
)


def rewrite_to_mirror(text: str, mirror: str | None) -> str:
    """Point every weex.com link at `mirror`, keeping path, query (vipCode) and fragment."""
    if not text or not mirror:
        return text
    base = mirror.rstrip("/")

    def _sub(match: re.Match) -> str:
        rest = match.group("rest") or ""
        trailing = ""
        while rest and rest[-1] in ".,!?;:)]}":
            trailing = rest[-1] + trailing
            rest = rest[:-1]
        if rest.startswith("/") and rest != "/":
            return base + rest + trailing
        if rest.startswith(("?", "#")):
            return base + "/" + rest + trailing
        return base + rest + trailing

    return _MAIN_SITE_RE.sub(_sub, text)


def prompt_block(user_id: int, mirrors: list[str] | None = None) -> str:
    """Instruction for the WEEX agent when the lead is in a blocked region."""
    mirrors = _mirrors if mirrors is None else mirrors
    if not mirrors:
        return ""
    first = pick_mirror(user_id, mirrors)
    others = [m for m in mirrors if m != first]
    return (
        "--- REGION ACCESS: weex.com IS BLOCKED FOR THIS LEAD ---\n"
        "This lead is in India/Pakistan or said the WEEX site does not open. weex.com is not reachable there, "
        f"so every WEEX link you share must use the official alternative domain {first} — same path and vipCode, "
        f"e.g. {first}/register?vipCode=CODE. "
        + (f"If they say it does not open either, give the next one: {', '.join(others)}. " if others else "")
        + "Say it is WEEX's official domain for their region, keep it short, do not discuss why it is blocked "
        "and never suggest a VPN."
    )
