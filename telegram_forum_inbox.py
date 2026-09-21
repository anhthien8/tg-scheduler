"""Telegram forum inbox bridge helpers.

V1 is text-only: one forum topic per (managed account, user) after proven outreach.
ponytail: in-memory confirm tokens are lost on process restart; persist only if admins need draft recovery.
"""
from __future__ import annotations

import asyncio
import html
import secrets
import time
from dataclasses import dataclass
from typing import Awaitable, Callable


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


def _format_inbound(account_id: int, user_id: int, username: str | None, name: str | None, text: str, has_media: bool) -> str:
    body = text.strip() if text and text.strip() else "[media chưa được hỗ trợ trong Forum Inbox v1]"
    if has_media and text and text.strip():
        body += "\n\n[media chưa được hỗ trợ trong Forum Inbox v1]"
    return f"📥 Inbound từ {_lead_label(username, name, user_id)}\nNick phụ #{account_id} · user_id {user_id}\n\n{body}"


async def relay_general_alert(bot, cfg: ForumConfig, text: str) -> bool:
    if not cfg.enabled:
        return False
    await bot.send_topic_message(cfg.group_id, cfg.general_topic_id or 1, text)
    return True


async def relay_inbound(db, bot, cfg: ForumConfig, *, account_id: int, user_id: int,
                        username: str | None, name: str | None, text: str,
                        has_media: bool = False) -> str:
    if not cfg.enabled:
        return "disabled"
    proof = await db.get_telegram_outreach_proof(account_id, user_id)
    if not proof:
        return "no_outreach_proof"

    async with _topic_lock(cfg.group_id, account_id, user_id):
        topic = await db.find_telegram_topic_for_user(cfg.group_id, account_id, user_id)
        if not topic:
            topic_id = await bot.create_forum_topic(cfg.group_id, _topic_title(account_id, user_id, username, name))
            topic = {"group_id": cfg.group_id, "topic_id": int(topic_id), "account_id": account_id,
                     "user_id": user_id, "username": username, "name": name,
                     "campaign_name": proof.get("name"), "status": "active", "last_lead_message": text}
            await db.upsert_telegram_topic(topic)
        else:
            await db.update_telegram_topic_last_message(cfg.group_id, int(topic["topic_id"]), text)

    await bot.send_topic_message(cfg.group_id, int(topic["topic_id"]), _format_inbound(account_id, user_id, username, name, text, has_media))
    return "relayed"


async def relay_outgoing(db, bot, cfg: ForumConfig, *, account_id: int, user_id: int, text: str) -> str:
    if not cfg.enabled:
        return "disabled"
    if _consume_forum_origin(account_id, user_id, text):
        return "loop_suppressed"
    topic = await db.find_telegram_topic_for_user(cfg.group_id, account_id, user_id)
    if not topic:
        return "no_topic"
    await bot.send_topic_message(cfg.group_id, int(topic["topic_id"]), f"📤 Nick phụ #{account_id}:\n\n{text}")
    return "relayed"


async def prepare_reply(db, cfg: ForumConfig, admin_id: int, group_id: int, topic_id: int, text: str) -> ReplyPreview | None:
    if not cfg.enabled or group_id != cfg.group_id or topic_id == (cfg.general_topic_id or 1):
        return None
    if cfg.admin_ids is not None and admin_id not in cfg.admin_ids:
        return None
    draft = (text or "").strip()
    if not draft:
        return None
    topic = await db.get_telegram_topic_by_thread(group_id, topic_id)
    if not topic:
        return None
    token = secrets.token_urlsafe(12)
    preview = ReplyPreview(token, admin_id, group_id, topic_id, int(topic["account_id"]), int(topic["user_id"]), draft, time.time())
    _PENDING[token] = preview
    return preview


def render_reply_preview(preview: ReplyPreview) -> str:
    return ("🧾 Xác nhận gửi reply\n"
            f"Admin: {preview.admin_id}\nGroup: {preview.group_id}\nTopic: {preview.topic_id}\n"
            f"Nick phụ: #{preview.account_id}\nLead user_id: {preview.user_id}\n\nDraft:\n{html.escape(preview.text)}")


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
        note_forum_origin(preview.account_id, preview.user_id, preview.text)
        try:
            sent = await sender(preview.account_id, preview.user_id, preview.text)
            if sent is False:
                raise RuntimeError("sender_false")
        except Exception as exc:
            _FORUM_ORIGIN.pop((preview.account_id, preview.user_id, preview.text), None)
            await _audit_reply(db, preview, admin_id, group_id, topic_id, "failure", type(exc).__name__)
            return "send_failed"
        await db.append_followup_chat_message(preview.account_id, preview.user_id, "assistant", f"[Human Admin via Forum]: {preview.text}")
        await _audit_reply(db, preview, admin_id, group_id, topic_id, "success")
        return "sent"


def cancel_reply(token: str, admin_id: int, group_id: int, topic_id: int) -> str:
    preview = _PENDING.get(token)
    if not preview:
        return "missing_or_used"
    if (preview.admin_id, preview.group_id, preview.topic_id) != (admin_id, group_id, topic_id):
        return "binding_mismatch"
    _PENDING.pop(token, None)
    return "cancelled"
