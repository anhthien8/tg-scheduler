"""KOL archetypes (WEEX BD training "Relationship-driven deal sourcing", 2026-11-20).

Profile before you pitch: every KOL is one of four types, and each type gets a
different first move. The type is stored on the lead's profile as `archetype`:

* set by the profile extractor (ai_remix.extract_kol_profile) from what the KOL
  says about themself, or
* picked by an admin in the profile form (`archetype_source = "manual"`); a manual
  pick is never overwritten by the extractor.

`opening_rule()` is the prompt block the AI gets for a typed lead. It changes the
order and framing of the conversation only — every figure still comes from the
offer ladder / knowledge base and passes the off-ladder guard.
"""
from __future__ import annotations

ARCHETYPES = ("trader", "connector", "content", "signal")

LABELS_VI = {
    "trader": "Trader",
    "connector": "Connector (người kết nối)",
    "content": "Content KOL",
    "signal": "Signal provider",
}

_ALIASES = {
    "trader": "trader", "key trader": "trader", "key_trader": "trader", "pro trader": "trader",
    "connector": "connector", "intermediary": "connector", "broker": "connector", "agent": "connector",
    "content": "content", "content kol": "content", "content_kol": "content", "content creator": "content",
    "creator": "content", "youtuber": "content", "influencer": "content",
    "signal": "signal", "signal provider": "signal", "signal_provider": "signal", "signals": "signal",
    "signal group": "signal", "copy trading": "signal",
}


def normalize(value) -> str | None:
    """'Signal provider' -> 'signal'; anything unknown -> None."""
    if not value:
        return None
    key = " ".join(str(value).strip().lower().replace("-", " ").split())
    return _ALIASES.get(key) or _ALIASES.get(key.replace(" ", "_"))


# What the extractor is told (kept here so the definitions live next to the rules).
EXTRACT_HINT = (
    " - archetype: ONE of trader | connector | content | signal, ONLY when the user's own messages make it clear: "
    "trader = trades daily themself and talks about their own positions/strategy; "
    "connector = knows many traders/KOLs or runs a network/agency but rarely trades themself; "
    "content = runs a YouTube/X/TikTok/Telegram channel posting news, analysis or education; "
    "signal = posts trading signals/calls (entries, TP/SL) to a following that copies them. "
    "Empty string when unclear.\n"
)

_RULES = {
    "trader": (
        "KOL TYPE: TRADER — trades daily, has strong views and wants to feel like the boss. ASK, DON'T TELL.\n"
        "- Before any terms, show interest in THEIR trading: how the market is treating them, their style or "
        "what they trade. One short question, like a peer, not a salesperson.\n"
        "- When you bring up WEEX, frame it as asking for their opinion: they try the platform (trial funds ONLY as "
        "written in the knowledge base) and tell us honestly what they would improve. Never 'here is a bonus, now "
        "promote us'.\n"
        "- When they give feedback, thank them for the specific point and say you passed it to the product team. "
        "Never promise it will ship or when.\n"
        "- Only after they feel heard, weave in ONE edge that fits what they trade (from the reasons-to-choose-us "
        "list or the knowledge base), then invite them to partner."
    ),
    "content": (
        "KOL TYPE: CONTENT KOL — posts news/analysis to a large audience and cares about earnings. SHOW THE MONEY.\n"
        "- Percentages mean little to them: talk in monthly dollars at their volume, using ONLY the pre-computed "
        "VALUE ESTIMATE / SHOW THE MONEY figures in this prompt. If their volume is unknown, the VOLUME QUESTION is "
        "what unlocks the number — say so in plain words.\n"
        "- A big audience does not mean traders: ask (once) how many of their followers actually trade futures."
    ),
    "signal": (
        "KOL TYPE: SIGNAL PROVIDER — posts calls to a dedicated group that follows them; a small group can trade a "
        "lot.\n"
        "- Lead with growing THEIR community: campaigns and trial funds for their members (ONLY as written in the "
        "knowledge base), and what their group's trading earns them each month (pre-computed figures only).\n"
        "- Ask (once) how many members actively follow their calls and on which exchange they trade."
    ),
    "connector": (
        "KOL TYPE: CONNECTOR — knows many traders and KOLs but rarely trades themself. They are worth more for who "
        "they know than for their own volume.\n"
        "- Do not push terms or ask for their personal volume. Build trust: be relaxed, ask how they work with the "
        "traders they know.\n"
        "- Propose a short call with our team (or meeting up if they are at an upcoming event) and ask who in their "
        "network would be a good fit. When they agree to a call/meet-up or offer an introduction, say our team will "
        "reach out and end your reply with [HANDOVER_REQUIRED]."
    ),
}


def opening_rule(archetype) -> str:
    """Prompt block for a typed lead, '' when the type is unknown."""
    a = normalize(archetype)
    return _RULES.get(a, "") if a else ""


def merge_extracted(extracted: dict, existing: dict | None) -> dict:
    """Normalize the extractor's archetype; drop it when an admin already picked one."""
    out = dict(extracted or {})
    if "archetype" not in out:
        return out
    a = normalize(out.pop("archetype"))
    if a and (existing or {}).get("archetype_source") != "manual":
        out["archetype"] = a
        out["archetype_source"] = "ai"
    return out
