"""
kol_channel_watcher.py
──────────────────────
Auto-forward bài mới từ channel nguồn (VD @weexkolglobal) tới KOL onboarded
theo assignment (region/campaign) do admin cấu hình một lần trong Lead & AI Follow-Up.

Flow:
1. Startup đọc settings: kol_channel_enabled, kol_channel_source, kol_channel_account_id.
2. Register events.NewMessage(chats=[source]) trên account listen.
3. Bài mới → dedup qua kol_broadcast_log (chat_id + message_id).
4. Lọc KOL: onboarded + distribution_enabled, và khớp region/campaign tag của bài.
5. Bài có link WEEX mà KOL thiếu vip_code → skip (không gửi link sai).
6. personalize_weex_links() gắn vipCode riêng từng KOL.
7. Gửi tuần tự, delay 2-5s, ghi log.

Tag cú pháp ở 3 dòng đầu bài: [vn] [global] [region:korea] [campaign:summer]
Bài không có tag → gửi cho mọi KOL đang bật distribution.
"""
import asyncio
import logging
import random
import re

import database as db
import telegram_client as tg
from dm_reply_tracker import personalize_weex_links, contains_weex_link

logger = logging.getLogger("tg-scheduler.kol_channel")

_handler_removers: list = []

_TAG_RE = re.compile(r"\[([a-z0-9_:-]+)\]", re.IGNORECASE)

_REGION_ALIASES = {
    "vn": "vietnam", "viet-nam": "vietnam", "vietnam": "vietnam",
    "kr": "korea", "kor": "korea",
    "cn": "china",
    "glob": "global",
}


def extract_tags(text: str) -> tuple[set, set]:
    """Extract (regions, campaigns) from leading [tags] in the first 3 lines."""
    regions: set = set()
    campaigns: set = set()
    if not text:
        return regions, campaigns
    head = "\n".join(text.split("\n")[:3])
    for m in _TAG_RE.finditer(head):
        token = m.group(1).strip().lower()
        kind, sep, val = token.partition(":")
        if sep and val:
            if kind == "campaign":
                campaigns.add(val)
            elif kind == "region":
                regions.add(_REGION_ALIASES.get(val, val))
            continue
        regions.add(_REGION_ALIASES.get(token, token))
    return regions, campaigns


def kol_matches(post_regions: set, post_campaigns: set, profile: dict) -> bool:
    """True if this KOL should receive a post carrying the given tags."""
    if not post_regions and not post_campaigns:
        return True  # untagged post → broadcast to every enabled KOL
    kol_regions = {str(r).lower() for r in (profile.get("distribution_regions") or [])}
    kol_campaigns = {str(c).lower() for c in (profile.get("distribution_campaigns") or [])}
    if post_regions and kol_regions & post_regions:
        return True
    if post_campaigns and kol_campaigns & post_campaigns:
        return True
    return False


def _post_text(event) -> str:
    msg = getattr(event, "message", None)
    if msg is None:
        return ""
    return msg.text or getattr(msg, "raw_text", "") or ""


async def process_channel_post(event) -> int:
    """Dedup, filter KOLs, personalize links, send. Returns number sent."""
    msg = getattr(event, "message", None)
    message_id = getattr(msg, "id", None) if msg else None
    chat_id = getattr(event, "chat_id", None)
    if message_id is None or chat_id is None:
        return 0

    if await db.is_kol_broadcast_sent(chat_id, message_id):
        return 0

    text = _post_text(event)
    if not text.strip():
        await db.log_kol_broadcast(chat_id, message_id, 0, 0, "")
        return 0

    post_regions, post_campaigns = extract_tags(text)
    has_weex = contains_weex_link(text)
    kols = await db.get_kols_for_distribution()

    sent = 0
    skipped = 0
    for kol in kols:
        profile = kol.get("profile") or {}
        if not kol_matches(post_regions, post_campaigns, profile):
            skipped += 1
            continue
        vip_code = (profile.get("vip_code") or "").strip()
        if has_weex and not vip_code:
            skipped += 1
            logger.info(
                "[KOL-Channel] Skip uid=%s — post has WEEX link but no vip_code",
                kol["user_id"],
            )
            continue
        personalized = personalize_weex_links(text, vip_code)
        try:
            await tg.send_text_message(
                kol["account_id"], kol["user_id"], personalized, parse_mode=None
            )
            sent += 1
            await asyncio.sleep(random.uniform(2.0, 5.0))
        except Exception as exc:
            skipped += 1
            logger.warning(
                "[KOL-Channel] Send failed acc=%s uid=%s: %s",
                kol["account_id"], kol["user_id"], exc,
            )

    await db.log_kol_broadcast(chat_id, message_id, sent, skipped, text[:200])
    logger.info(
        "[KOL-Channel] msg=%s sent=%d skipped=%d regions=%s campaigns=%s",
        message_id, sent, skipped, sorted(post_regions), sorted(post_campaigns),
    )
    return sent


def unregister_channel_listener() -> None:
    for client, fn in _handler_removers:
        try:
            client.remove_event_handler(fn)
        except Exception:
            pass
    _handler_removers.clear()


async def start_kol_channel_watcher() -> None:
    """Read settings and register the channel listener. Called on startup."""
    from telethon import events

    enabled = (await db.get_setting("kol_channel_enabled", "false") or "").lower() in ("true", "1")
    if not enabled:
        logger.info("[KOL-Channel] Disabled — skipping")
        return

    source = (await db.get_setting("kol_channel_source", "") or "").strip()
    account_raw = (await db.get_setting("kol_channel_account_id", "") or "").strip()
    if not source or not account_raw.isdigit():
        logger.warning("[KOL-Channel] Missing/invalid kol_channel_source or kol_channel_account_id")
        return

    account_id = int(account_raw)
    client = tg.get_client(account_id)
    if not client:
        logger.warning("[KOL-Channel] Account %d not connected — skipping", account_id)
        return

    unregister_channel_listener()

    async def _handler(event):
        await process_channel_post(event)

    try:
        entity = await client.get_entity(source)
        client.add_event_handler(_handler, events.NewMessage(incoming=True, chats=[entity]))
        _handler_removers.append((client, _handler))
        logger.info("[KOL-Channel] Listening on %s via acc %d", source, account_id)
    except Exception as e:
        logger.error("[KOL-Channel] Cannot resolve channel %s: %s", source, e)


async def stop_kol_channel_watcher() -> None:
    unregister_channel_listener()
    logger.info("[KOL-Channel] Listener stopped")
