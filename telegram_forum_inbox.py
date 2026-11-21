"""Telegram forum inbox bridge helpers.

V1 is text-only: one forum topic per (managed account, user) after proven outreach.
ponytail: in-memory confirm tokens are lost on process restart; persist only if admins need draft recovery.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

logger = logging.getLogger("tg-scheduler.forum_inbox")


@dataclass(frozen=True)
class ForumConfig:
    enabled: bool
    group_id: int
    general_topic_id: int = 1
    admin_ids: set[int] | None = None


@dataclass(frozen=True)
class ReplyPreview:
    token: str
    admin_id: int
    group_id: int
    topic_id: int
    account_id: int
    user_id: int
    text: str
    created_at: float
    media: tuple | None = None   # (chat_id, message_id) of the admin's photo / video / file / voice…
    label: str = ""              # what that attachment is, e.g. "Ảnh"


_PENDING: dict[str, ReplyPreview] = {}
_PENDING_TTL = 300
_FORUM_ORIGIN: dict[tuple[int, int, str], float] = {}
_LOOP_TTL = 60
_TOPIC_LOCKS: dict[tuple[int, int, int], "asyncio.Lock"] = {}
_SEND_LOCKS: dict[tuple[int, int], "asyncio.Lock"] = {}


def get_send_lock(account_id: int, user_id: int) -> "asyncio.Lock":
    """Shared per account/user send lock for Forum confirm, AI replies, and drip sends."""
    key = (account_id, user_id)
    lock = _SEND_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SEND_LOCKS[key] = lock
    return lock



def _topic_lock(group_id: int, account_id: int, user_id: int) -> "asyncio.Lock":
    """One lock per (group, account, user) so concurrent inbound messages can't
    both pass the 'no topic yet' check and create two Telegram forum topics."""
    key = (group_id, account_id, user_id)
    lock = _TOPIC_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _TOPIC_LOCKS[key] = lock
    return lock


def parse_int_set(raw: str | None) -> set[int]:
    out: set[int] = set()
    for item in (raw or "").replace("\n", ",").split(","):
        item = item.strip()
        if item.lstrip("-").isdigit():
            out.add(int(item))
    return out


def is_relayable_admin_message(sender_id: int | None, is_bot: bool, is_anonymous: bool) -> bool:
    return bool(sender_id and not is_bot and not is_anonymous)


FORUM_MEDIA_ORIGIN = "\x00forum-media"  # loop-guard key for an attachment sent from a topic


def consume_forum_media_origin(account_id: int, user_id: int) -> bool:
    """True when the nick's outgoing attachment is the one an admin just sent from the forum."""
    return _consume_forum_origin(account_id, user_id, FORUM_MEDIA_ORIGIN)


def note_forum_origin(account_id: int, user_id: int, text: str) -> None:
    _FORUM_ORIGIN[(account_id, user_id, text)] = time.time()


def _consume_forum_origin(account_id: int, user_id: int, text: str) -> bool:
    key = (account_id, user_id, text)
    created = _FORUM_ORIGIN.pop(key, None)
    return created is not None and time.time() - created <= _LOOP_TTL


def _topic_title(account_id: int, user_id: int, username: str | None, name: str | None) -> str:
    label = (username or name or str(user_id)).strip().lstrip("@")[:48]
    return f"a{account_id} · {label} · {user_id}"[:128]


def _lead_label(username: str | None, name: str | None, user_id: int) -> str:
    user = f"@{username.lstrip('@')}" if username else str(user_id)
    return f"{name} ({user})" if name and name != user else user


# account_id -> display name, so the forum shows "Will - SEA Weex" instead of
# the cryptic "Nick phụ #3 · user_id 1393222596". Cached: account names rarely
# change and this runs on every relayed message.
_ACCOUNT_NAME_CACHE: dict[int, str] = {}


async def _account_label(db, account_id: int) -> str:
    """Human-readable account name (falls back to 'Nick #<id>' if unknown)."""
    cached = _ACCOUNT_NAME_CACHE.get(int(account_id))
    if cached:
        return cached
    name = None
    try:
        acc = await db.get_account(int(account_id))
        if acc:
            name = (acc.get("name") or "").strip()
    except Exception:
        name = None
    label = name or f"Nick #{account_id}"
    _ACCOUNT_NAME_CACHE[int(account_id)] = label
    return label


def _format_inbound(account_label: str, user_id: int, username: str | None, name: str | None, text: str, has_media: bool) -> str:
    body = text.strip() if text and text.strip() else "[media chưa được hỗ trợ trong Forum Inbox v1]"
    if has_media and text and text.strip():
        body += "\n\n[media chưa được hỗ trợ trong Forum Inbox v1]"
    return f"📥 Inbound từ {_lead_label(username, name, user_id)}\n{account_label}\n\n{body}"


def _format_history_message(role: str, content: str, ts: str) -> str:
    """Render one stored history entry as a transcript line.

    `assistant` covers both AI drafts and human-admin replies (the tracker marks
    manual sends with a '[Human Admin]:' prefix), so both get the same arrow —
    the prefix already shows who typed it.
    """
    when = (ts or "").replace("T", " ")[:16]
    arrow = "📥 Lead" if role == "user" else "📤 Mình"
    stamp = f" · {when}" if when else ""
    return f"{arrow}{stamp}\n{content.strip()}"


async def relay_history(db, bot, cfg: ForumConfig, *, account_id: int, user_id: int,
                        username: str | None = None, name: str | None = None,
                        days: int = 30, history_account_id: int | None = None) -> dict:
    """Replay a conversation's stored transcript into its topic.

    The topic is created if missing, then entries are posted in chronological
    order, chunked so a long conversation stays under Telegram's message-size
    limit. Only entries newer than `days` are replayed — that is what makes the
    topic readable ("what did we say this month") without dumping years of
    messages into the group.

    `history_account_id`: read the transcript of another nick's chat with this lead
    (a lead handed over to `account_id`) and post it into `account_id`'s topic.

    Returns {"status", "posted", "total", "topic_id"}.
    """
    if not cfg.enabled:
        return {"status": "disabled", "posted": 0, "total": 0}

    src_account_id = history_account_id or account_id
    chat = await db.get_followup_chat(src_account_id, user_id)
    raw = (chat or {}).get("history_json")
    if not raw:
        return {"status": "no_history", "posted": 0, "total": 0}
    try:
        history = json.loads(raw) or []
    except (ValueError, TypeError):
        return {"status": "bad_history", "posted": 0, "total": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%S")
    entries = [m for m in history
               if isinstance(m, dict) and (m.get("timestamp") or "") >= cutoff]
    if not entries:
        return {"status": "no_recent", "posted": 0, "total": 0}

    async with _topic_lock(cfg.group_id, account_id, user_id):
        topic = await db.find_telegram_topic_for_user(cfg.group_id, account_id, user_id)
        if is_hidden(topic):
            return {"status": "hidden", "posted": 0, "total": len(entries)}
        if not topic:
            topic_id = await bot.create_forum_topic(
                cfg.group_id, _topic_title(account_id, user_id, username, name))
            await db.upsert_telegram_topic({
                "group_id": cfg.group_id, "topic_id": int(topic_id),
                "account_id": account_id, "user_id": user_id,
                "username": username, "name": name, "campaign_name": None,
                "status": "active", "last_lead_message": None,
            })
            topic = {"topic_id": int(topic_id)}
        topic_id = int(topic["topic_id"])

    step_line = ""
    try:
        import dm_reply_tracker as _drt
        step_line = await _drt.render_kol_step_line(account_id, user_id)
    except Exception as e:
        logger.debug("[ForumInbox] step line skipped: %s", e)
    header = (f"🗂️ **Hội thoại {days} ngày qua**\n"
              f"Lead: {_lead_label(username, name, user_id)}\n"
              f"{await _account_label(db, src_account_id)}\n"
              + (f"{step_line}\n" if step_line else "")
              + f"Tổng {len(entries)} tin nhắn (cũ → mới)\n"
              f"{'─' * 28}")
    await bot.send_topic_message(cfg.group_id, topic_id, header)

    # Chunk by characters so each Telegram message stays well under 4096.
    chunk, size, posted = [], 0, 0
    CHUNK_LIMIT = 3200
    for m in entries:
        line = _format_history_message(m.get("role", "user"),
                                       m.get("content") or "", m.get("timestamp") or "")
        if size + len(line) > CHUNK_LIMIT and chunk:
            await bot.send_topic_message(cfg.group_id, topic_id, "\n\n".join(chunk))
            posted += 1
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 2
    if chunk:
        await bot.send_topic_message(cfg.group_id, topic_id, "\n\n".join(chunk))
        posted += 1

    return {"status": "replayed", "posted": posted,
            "total": len(entries), "topic_id": topic_id}


async def relay_transfer(db, bot, cfg: ForumConfig, *, from_account_id: int, to_account_id: int,
                         user_id: int, username: str | None = None, name: str | None = None,
                         created: bool = True) -> dict:
    """An admin handed a lead over to another nick: say so in the old topic, then open the
    new nick's topic with the old nick's transcript so whoever picks it up can read it.

    `created` False = the new nick already had its own chat with this lead."""
    if not cfg.enabled:
        return {"status": "disabled"}
    old_label = await _account_label(db, from_account_id)
    new_label = await _account_label(db, to_account_id)
    old = await db.find_telegram_topic_for_user(cfg.group_id, from_account_id, user_id)
    if old and not is_hidden(old):
        try:  # a closed/deleted old topic must not stop the new one from opening
            await bot.send_topic_message(
                cfg.group_id, int(old["topic_id"]),
                f"🔀 Lead đã chuyển sang nick **{new_label}**. Topic này ngừng dùng — trả lời ở topic của nick mới.")
        except Exception as e:
            logger.warning("[ForumInbox] transfer notice to old topic failed (acc=%s user=%s): %s: %s",
                           from_account_id, user_id, type(e).__name__, e)
    async with _topic_lock(cfg.group_id, to_account_id, user_id):
        topic = await db.find_telegram_topic_for_user(cfg.group_id, to_account_id, user_id)
        if is_hidden(topic):
            return {"status": "hidden"}
        if not topic:
            topic_id = await bot.create_forum_topic(
                cfg.group_id, _topic_title(to_account_id, user_id, username, name))
            topic = {"group_id": cfg.group_id, "topic_id": int(topic_id), "account_id": to_account_id,
                     "user_id": user_id, "username": username, "name": name, "campaign_name": None,
                     "status": "active", "last_lead_message": None}
            await db.upsert_telegram_topic(topic)
    topic_id = int(topic["topic_id"])
    state = ("Nick mới CHƯA nhắn KOL lần nào." if created
             else "Nick mới đã có hội thoại riêng với KOL này.")
    await bot.send_topic_message(
        cfg.group_id, topic_id,
        f"🔀 Lead {_lead_label(username, name, user_id)} chuyển từ nick **{old_label}** sang **{new_label}**.\n"
        f"{state} AI đang tắt — gõ tin ở đây để gửi từ nick mới.")
    hist = await relay_history(db, bot, cfg, account_id=to_account_id, user_id=user_id, username=username,
                               name=name, days=365, history_account_id=from_account_id)
    if hist.get("status") != "replayed":
        await bot.send_topic_message(
            cfg.group_id, topic_id,
            f"(Không có tin nhắn nào với nick {old_label} trong 365 ngày qua để chép sang.)")
    return {"status": "relayed", "topic_id": topic_id}


async def relay_general_alert(bot, cfg: ForumConfig, text: str) -> bool:
    if not cfg.enabled:
        return False
    await bot.send_topic_message(cfg.group_id, cfg.general_topic_id or 1, text)
    return True


async def ensure_topic(db, bot, cfg: ForumConfig, *, account_id: int, user_id: int,
                       username: str | None, name: str | None, last_text: str | None = None) -> dict | None:
    """The lead's topic, created on first need. None when we never reached out to them."""
    async with _topic_lock(cfg.group_id, account_id, user_id):
        topic = await db.find_telegram_topic_for_user(cfg.group_id, account_id, user_id)
        if is_hidden(topic):
            return None
        if topic:
            if last_text is not None:
                await db.update_telegram_topic_last_message(cfg.group_id, int(topic["topic_id"]), last_text)
            return topic
        proof = await db.get_telegram_outreach_proof(account_id, user_id)
        if not proof:
            return None
        topic_id = await bot.create_forum_topic(cfg.group_id, _topic_title(account_id, user_id, username, name))
        topic = {"group_id": cfg.group_id, "topic_id": int(topic_id), "account_id": account_id,
                 "user_id": user_id, "username": username, "name": name,
                 "campaign_name": proof.get("name"), "status": "active", "last_lead_message": last_text}
        await db.upsert_telegram_topic(topic)
        return topic


# ── Pinned deal card ─────────────────────────────────────────────────
# One message per topic, pinned, rewritten in place whenever what it says changes:
# who the KOL is, their channel, where the deal stands and what is still missing.

_CARD_DEAL_FIELDS = (
    ("monthly_volume", "Volume/tháng"), ("volume_claimed", "Volume KOL báo"),
    ("volume_verified", "Volume đã verify"), ("current_cex", "Đang ở sàn"),
    ("current_deal", "Deal hiện tại ở sàn đó"), ("revshare_request", "KOL đòi"),
    ("key_demands", "Yêu cầu"), ("selected_campaign", "Campaign"),
    ("offer_version", "Phiên bản offer"), ("weex_uid", "UID"), ("vip_code", "VIP code"),
    ("affiliate_link", "Link affiliate"), ("first_post_link", "Bài đầu tiên"),
)


def _card_val(v) -> str:
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x) for x in v if x not in (None, ""))
    return re.sub(r"\s+", " ", str(v or "")).strip()[:300]


async def build_deal_card(db, account_id: int, user_id: int,
                          username: str | None, name: str | None) -> str:
    """Card body (no timestamp, so an unchanged deal hashes the same)."""
    profile = await db.get_kol_profile(account_id, user_id) or {}
    chat = await db.get_followup_chat(account_id, user_id) or {}
    lines = [f"📌 **THẺ DEAL** · {_lead_label(username, name, user_id)}",
             f"Nick: {await _account_label(db, account_id)}"]
    platform = _card_val(profile.get("platform"))
    links = [_card_val(profile.get(k)) for k in ("channel_link", "community_link")]
    links = list(dict.fromkeys(x for x in links if x))
    followers = _card_val(profile.get("followers"))
    lines.append("📺 Kênh: " + (" · ".join(x for x in (platform, followers and f"{followers} followers") if x)
                                 or "chưa rõ"))
    lines += [f"   {x}" for x in links]
    try:
        import dm_reply_tracker as _drt
        step = await _drt.render_kol_step_line(account_id, user_id)
        if step:
            lines.append(step)
    except Exception as e:
        logger.debug("[ForumInbox] card step line skipped: %s", e)
    deal = [f"• {label}: {_card_val(profile.get(k))}" for k, label in _CARD_DEAL_FIELDS
            if _card_val(profile.get(k))]
    lines.append("💼 Deal:" if deal else "💼 Deal: chưa có thông tin")
    lines += deal
    summary = _card_val(chat.get("summary"))
    if summary:
        lines.append(f"📝 Tóm tắt: {summary}")
    if chat.get("owed_note"):
        lines.append(f"⏳ Mình đang nợ KOL: {_card_val(chat.get('owed_note'))}")
    return "\n".join(lines)


_card_locks: dict = {}


async def refresh_deal_card(db, bot, cfg: ForumConfig, topic: dict) -> str:
    """Post + pin the topic's deal card, or edit it when the deal changed. Never raises."""
    try:
        if not cfg.enabled or not topic or is_hidden(topic) or not hasattr(bot, "pin_topic_message"):
            return "skipped"
        gid, tid = cfg.group_id, int(topic["topic_id"])
        lock = _card_locks.setdefault((gid, tid), asyncio.Lock())
        async with lock:
            fresh = await db.find_telegram_topic_for_user(gid, int(topic["account_id"]), int(topic["user_id"])) or topic
            body = await build_deal_card(db, int(fresh["account_id"]), int(fresh["user_id"]),
                                         fresh.get("username"), fresh.get("name"))
            h = hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]
            if fresh.get("card_message_id") and fresh.get("card_hash") == h:
                return "unchanged"
            text = f"{body}\n🕒 Cập nhật {datetime.now().strftime('%d/%m %H:%M')}"
            if fresh.get("card_message_id"):
                try:
                    await bot.edit_topic_message(gid, int(fresh["card_message_id"]), text)
                    await db.set_telegram_topic_card(gid, tid, int(fresh["card_message_id"]), h)
                    return "edited"
                except Exception as e:  # deleted by an admin → post a new one below
                    logger.info("[ForumInbox] deal card edit failed (topic %s), reposting: %s", tid, e)
            msg = await bot.send_topic_message(gid, tid, text)
            mid = int(getattr(msg, "id", 0) or 0)
            if mid:
                try:
                    await bot.pin_topic_message(gid, mid)
                except Exception as e:
                    logger.warning("[ForumInbox] deal card pin failed (topic %s): %s", tid, e)
            await db.set_telegram_topic_card(gid, tid, mid or None, h)
            return "posted"
    except Exception as e:
        logger.warning("[ForumInbox] deal card refresh failed: %s: %s", type(e).__name__, e)
        return "error"


def is_hidden(topic: dict | None) -> bool:
    """Operator ran /hide in this lead's topic: nothing about them reaches the group."""
    return bool(topic) and topic.get("status") == "hidden"


def topic_message_link(group_id: int, topic_id: int, message_id: int) -> str:
    """t.me link that opens a message inside a forum topic (members of the group only)."""
    internal = str(abs(int(group_id)))
    if internal.startswith("100") and len(internal) > 10:
        internal = internal[3:]
    return f"https://t.me/c/{internal}/{int(topic_id)}/{int(message_id)}"


async def relay_inbound(db, bot, cfg: ForumConfig, *, account_id: int, user_id: int,
                        username: str | None, name: str | None, text: str,
                        has_media: bool = False) -> str:
    if not cfg.enabled:
        return "disabled"
    existing = await db.find_telegram_topic_for_user(cfg.group_id, account_id, user_id)
    if is_hidden(existing):
        return "hidden"
    if not existing and not has_media:
        # A first reply that is only "thanks, no need" is not a lead: no topic for it.
        import dm_reply_tracker as _drt
        if _drt.is_polite_decline(text):
            return "polite_decline"
    topic = await ensure_topic(db, bot, cfg, account_id=account_id, user_id=user_id,
                               username=username, name=name, last_text=text)
    if not topic:
        return "no_outreach_proof"

    _label = await _account_label(db, account_id)
    await bot.send_topic_message(cfg.group_id, int(topic["topic_id"]), _format_inbound(_label, user_id, username, name, text, has_media))
    await refresh_deal_card(db, bot, cfg, topic)
    return "relayed"


async def backfill_topics(db, bot, cfg: ForumConfig, *, days: int = 7, limit: int = 500) -> dict:
    """Open topics for already-replied conversations missing one, bounded by `days`.

    Only creates the topic and posts a header; existing history is NOT replayed
    (that would spam the group). Returns a small report so the caller can toast it.
    """
    if not cfg.enabled:
        return {"created": 0, "skipped": 0, "failed": 0, "reason": "disabled"}
    candidates = await db.get_forum_backfill_candidates(days=days, limit=limit)
    created = skipped = failed = 0
    for cand in candidates:
        account_id = int(cand["account_id"])
        user_id = int(cand["user_id"])
        username = cand.get("username")
        name = cand.get("name")
        try:
            async with _topic_lock(cfg.group_id, account_id, user_id):
                existing = await db.find_telegram_topic_for_user(cfg.group_id, account_id, user_id)
                if existing:
                    skipped += 1
                    continue
                topic_id = await bot.create_forum_topic(
                    cfg.group_id, _topic_title(account_id, user_id, username, name))
                await db.upsert_telegram_topic({
                    "group_id": cfg.group_id, "topic_id": int(topic_id),
                    "account_id": account_id, "user_id": user_id,
                    "username": username, "name": name,
                    "campaign_name": None, "status": "active",
                    "last_lead_message": None,
                })
                header = (f"🗂️ Topic tạo bởi backfill ({days} ngày gần nhất)\n"
                          f"Lead: {_lead_label(username, name, user_id)}\n"
                          f"{await _account_label(db, account_id)}\n\n"
                          f"Chỉ hội thoại MỚI kể từ lúc bật mới được relay vào đây.")
                await bot.send_topic_message(cfg.group_id, int(topic_id), header)
                created += 1
        except Exception as e:
            # NEVER swallow silently: an unlogged failure here hid the
            # CreateForumTopicRequest ImportError and left the group empty for
            # weeks with no visible error.
            failed += 1
            logger.warning("[ForumInbox] backfill: create topic failed for acc=%s user=%s: %s: %s",
                           account_id, user_id, type(e).__name__, e)
    return {"created": created, "skipped": skipped, "failed": failed,
            "candidates": len(candidates), "days": days}


async def relay_outgoing(db, bot, cfg: ForumConfig, *, account_id: int, user_id: int, text: str) -> str:
    if not cfg.enabled:
        return "disabled"
    if _consume_forum_origin(account_id, user_id, text):
        return "loop_suppressed"
    topic = await db.find_telegram_topic_for_user(cfg.group_id, account_id, user_id)
    if not topic or is_hidden(topic):
        return "no_topic"
    await bot.send_topic_message(cfg.group_id, int(topic["topic_id"]), f"📤 {await _account_label(db, account_id)}:\n\n{text}")
    await refresh_deal_card(db, bot, cfg, topic)
    return "relayed"


_CAPTION_LIMIT = 1000  # Telegram allows 1024 characters under a file


async def relay_outgoing_media(db, bot, cfg: ForumConfig, *, account_id: int, user_id: int,
                               path: str, text: str = "") -> str:
    """Post a photo / video / file our account sent to the lead into their topic."""
    if not cfg.enabled:
        return "disabled"
    topic = await db.find_telegram_topic_for_user(cfg.group_id, account_id, user_id)
    if not topic or is_hidden(topic):
        return "no_topic"
    topic_id = int(topic["topic_id"])
    head = f"📤 {await _account_label(db, account_id)}:"
    body = (text or "").strip()
    fits = len(head) + len(body) + 2 <= _CAPTION_LIMIT
    await bot.send_topic_file(cfg.group_id, topic_id, path,
                              caption=f"{head}\n\n{body}" if body and fits else head)
    if body and not fits:
        await bot.send_topic_message(cfg.group_id, topic_id, f"{head}\n\n{body}")
    return "relayed"


async def prepare_reply(db, cfg: ForumConfig, admin_id: int, group_id: int, topic_id: int, text: str,
                        media: tuple | None = None, label: str = "") -> ReplyPreview | None:
    if not cfg.enabled or group_id != cfg.group_id or topic_id == (cfg.general_topic_id or 1):
        return None
    if cfg.admin_ids is not None and admin_id not in cfg.admin_ids:
        return None
    draft = (text or "").strip()
    if not draft and not media:
        return None
    topic = await db.get_telegram_topic_by_thread(group_id, topic_id)
    if not topic:
        return None
    token = secrets.token_urlsafe(12)
    preview = ReplyPreview(token, admin_id, group_id, topic_id, int(topic["account_id"]), int(topic["user_id"]),
                           draft, time.time(), media, label)
    _PENDING[token] = preview
    return preview


def render_reply_preview(preview: ReplyPreview, account_label: str | None = None) -> str:
    nick = account_label or f"Nick phụ: #{preview.account_id}"
    return ("🧾 Xác nhận gửi reply\n"
            f"Admin: {preview.admin_id}\nGroup: {preview.group_id}\nTopic: {preview.topic_id}\n"
            f"Gửi từ: {nick}\nLead user_id: {preview.user_id}\n\nDraft:\n"
            + (f"📎 {preview.label or 'File'}\n" if preview.media else "") + html.escape(preview.text))


async def _audit_reply(db, preview: ReplyPreview | None, admin_id: int, group_id: int, topic_id: int,
                       result: str, details: str = "") -> None:
    if not hasattr(db, "log_command_action"):
        return
    await db.log_command_action(
        admin_user_id=admin_id,
        action="forum_reply_send",
        account_id=preview.account_id if preview else None,
        target_username=str(preview.user_id) if preview else None,
        message_text=preview.text if preview else None,
        result=result,
        details=f"group={group_id} topic={topic_id}" + (f" {details}" if details else ""),
    )


async def confirm_reply(db, token: str, admin_id: int, group_id: int, topic_id: int,
                        sender: Callable[[int, int, str], Awaitable[object]],
                        cfg: ForumConfig | None = None) -> str:
    preview = _PENDING.get(token)
    if not preview:
        await _audit_reply(db, None, admin_id, group_id, topic_id, "failure", "missing_or_used")
        return "missing_or_used"
    if time.time() - preview.created_at > _PENDING_TTL:
        _PENDING.pop(token, None)
        await _audit_reply(db, preview, admin_id, group_id, topic_id, "failure", "expired")
        return "expired"
    if (preview.admin_id, preview.group_id, preview.topic_id) != (admin_id, group_id, topic_id):
        await _audit_reply(db, preview, admin_id, group_id, topic_id, "failure", "binding_mismatch")
        return "binding_mismatch"
    if cfg and (not cfg.enabled or group_id != cfg.group_id or (cfg.admin_ids is not None and admin_id not in cfg.admin_ids)):
        await _audit_reply(db, preview, admin_id, group_id, topic_id, "failure", "admin_or_group_revoked")
        return "not_allowed"
    topic = await db.get_telegram_topic_by_thread(group_id, topic_id)
    if (not topic or int(topic["account_id"]) != preview.account_id or int(topic["user_id"]) != preview.user_id):
        await _audit_reply(db, preview, admin_id, group_id, topic_id, "failure", "mapping_changed")
        return "mapping_changed"

    async with get_send_lock(preview.account_id, preview.user_id):
        if _PENDING.get(token) is not preview:
            await _audit_reply(db, preview, admin_id, group_id, topic_id, "failure", "missing_or_used")
            return "missing_or_used"
        _PENDING.pop(token, None)
        await db.set_human_takeover(preview.account_id, preview.user_id)
        # The topic already shows the admin's own attachment: the nick's outgoing copy must not be relayed back.
        origin = FORUM_MEDIA_ORIGIN if preview.media else preview.text
        note_forum_origin(preview.account_id, preview.user_id, origin)
        try:
            sent = await sender(preview.account_id, preview.user_id, preview.text)
            if sent is False:
                raise RuntimeError("sender_false")
        except Exception as exc:
            _FORUM_ORIGIN.pop((preview.account_id, preview.user_id, origin), None)
            await _audit_reply(db, preview, admin_id, group_id, topic_id, "failure", type(exc).__name__)
            return "send_failed"
        said = f"[Sent {preview.label or 'file'}] {preview.text}".strip() if preview.media else preview.text
        await db.append_followup_chat_message(preview.account_id, preview.user_id, "assistant", f"[Human Admin via Forum]: {said}")
        await _audit_reply(db, preview, admin_id, group_id, topic_id, "success")
        try:
            import dm_reply_tracker
            dm_reply_tracker.note_admin_message(preview.account_id, preview.user_id, preview.text)
        except Exception:
            pass
        return "sent"


def cancel_reply(token: str, admin_id: int, group_id: int, topic_id: int) -> str:
    preview = _PENDING.get(token)
    if not preview:
        return "missing_or_used"
    if (preview.admin_id, preview.group_id, preview.topic_id) != (admin_id, group_id, topic_id):
        return "binding_mismatch"
    _PENDING.pop(token, None)
    return "cancelled"
