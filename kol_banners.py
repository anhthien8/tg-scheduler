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


# ── Campaign catalog (data/kol_banners/campaigns.json) ──────────────────────
# Operator-editable list of campaigns the AI may pitch when a KOL asks about
# campaigns / promotions. Re-read only when the file changes (mtime cache).
CATALOG_PATH = os.path.join(BANNER_DIR, "campaigns.json")
MAX_BANNERS_PER_REPLY = 2
_catalog_cache: tuple[float, list] = (-1.0, [])


def active_campaigns(today: str | None = None) -> list[dict]:
    """Active campaigns whose banner file exists and whose end date has not passed."""
    import json
    from datetime import date
    global _catalog_cache
    try:
        mtime = os.path.getmtime(CATALOG_PATH)
    except OSError:
        return []
    if mtime != _catalog_cache[0]:
        try:
            with open(CATALOG_PATH, encoding="utf-8") as f:
                items = json.load(f).get("campaigns") or []
        except (OSError, ValueError):
            items = []
        _catalog_cache = (mtime, items)
    today = today or date.today().isoformat()
    return [c for c in _catalog_cache[1]
            if isinstance(c, dict) and c.get("active") and c.get("details")
            and str(c.get("ends") or "9999-12-31") >= today and banner_path(c.get("key", ""))]


# Partner onboarding links (owner, 2026-11-19).
REGISTER_LINK = "https://www.weex.com/en/register?vipCode=kolglobal"
AFFILIATE_DASHBOARD_LINK = "https://affiliates.weex.com"


def campaign_prompt_block(today: str | None = None) -> str:
    """Prompt block: answer campaign questions with full details + the banner tag."""
    camps = active_campaigns(today)
    if not camps:
        return ""
    lines = [
        "--- CURRENT WEEX CAMPAIGNS (send these when asked) ---",
        "ASK FIRST WHEN WE KNOW NOTHING (owner rule 2026-11-19): if the KOL asks in general what campaigns we have "
        "while we still have neither their channel/community link nor their trading performance (dashboard video or "
        "stated volume), do NOT send campaign details or a banner yet. In ONE short message, react to what they told "
        "you about their community, then ask which kind of campaign fits their members best, naming 2-3 kinds taken "
        "from the list below in plain words (e.g. a deposit bonus for new members, a trading competition / volume "
        "rewards, copy trading) — no figures, no banner. That question is the only ask of the message, asked ONCE per "
        "chat; if it is already in the history, use their answer instead of asking again. When their channel or "
        "performance is already known, recommend the best-fitting campaign directly as the other rules say.",
        "ONCE THE KOL CONFIRMS A CAMPAIGN (they picked one, said yes to the one you suggested, or asked about a specific "
        "campaign and want it): in that reply send the FULL details of the matching campaign(s) from the list below (who can join, the "
        "reward table, the key rules), written naturally in their language, and add the tag [[BANNER:<key>]] for each "
        f"campaign you describe (at most {MAX_BANNERS_PER_REPLY} per reply) — the system sends the official banner image "
        "right after your text — and, if the registration link is not in the history yet, end with it so they can "
        f"onboard: {REGISTER_LINK} . If they leave the choice to you, pick the 1-2 that best fit what they said about "
        "their community (deposit bonus first). Skip a banner "
        "whose [banner sent: <key>] mark is already in the history. Never invent a campaign, a figure or a date that is "
        "not below; for anything not covered (caps, exact dates, custom campaigns) say you will confirm with the team.",
    ]
    for c in camps:
        lines.append(f"* [[BANNER:{c['key']}]] {c.get('title', '')} — for: {c.get('for', '')}. {c['details']} "
                     f"{c.get('rules', '')}".strip())
    return "\n".join(lines)
