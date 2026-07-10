"""
reaction_watcher.py
───────────────────
Engine for:
  1. Auto-joining Telegram channels/groups with configured accounts
  2. Monitoring new posts and sending reactions automatically
"""

from __future__ import annotations

import asyncio
import logging
import random
import time as _time
from typing import Any

import database as db
import telegram_client as tg

from telethon import events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, SendReactionRequest, GetMessagesViewsRequest
from telethon.tl.types import ReactionEmoji

logger = logging.getLogger("tg-scheduler.reactions")

# ── In-memory state ────────────────────────────────────────────────────────────
_handler_removers: dict[int, list[tuple[Any, Any]]] = {}   # target_id → [(client, fn), ...]
_background_tasks: set[asyncio.Task] = set()

# Dedup: set of (target_id, account_id, msg_id) to prevent double-react in same session
_reacted: set[tuple[int, int, int]] = set()


def _clean_channel_id(raw_id: int) -> int:
    """Strip -100 prefix from supergroup/channel IDs."""
    s = str(raw_id)
    if s.startswith("-100"):
        return int(s[4:])
    if s.startswith("-"):
        return int(s[1:])
    return raw_id


def _extract_invite_hash(link: str) -> str | None:
    """
    Extract invite hash from private links like:
      https://t.me/+AbCdEfGh
      https://t.me/joinchat/AbCdEfGh
    Returns None if it's a public link.
    """
    import re
    # t.me/+hash or t.me/joinchat/hash
    m = re.search(r't\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)', link)
    if m:
        return m.group(1)
    return None


async def _join_or_get_entity(client, link: str):
    """
    Join a channel and return its entity.
    Handles both:
      - Public links/usernames: get_entity() + JoinChannelRequest
      - Private invite links (t.me/+ or joinchat): ImportChatInviteRequest
    """
    invite_hash = _extract_invite_hash(link)
    if invite_hash:
        # Private invite link
        try:
            result = await client(ImportChatInviteRequest(invite_hash))
            # ImportChatInviteRequest returns Updates with chats list
            if result.chats:
                return result.chats[0]
        except Exception as e:
            err_str = str(e)
            if "already" in err_str.lower() or "USER_ALREADY_PARTICIPANT" in err_str:
                # Already a member — just get the entity via invite hash
                try:
                    from telethon.tl.functions.messages import CheckChatInviteRequest
                    invite_info = await client(CheckChatInviteRequest(invite_hash))
                    if hasattr(invite_info, 'chat'):
                        return invite_info.chat
                except Exception:
                    pass
            raise
    else:
        # Public link or username
        entity = await client.get_entity(link)
        await client(JoinChannelRequest(entity))
        return entity


async def _get_entity_only(client, link: str, channel_id: int | None = None):
    """
    Get entity for listening/reacting (no join).
    For private channels: tries channel_id first (fastest), then CheckChatInviteRequest.
    For public channels: uses get_entity().
    Returns None if entity cannot be resolved (caller should skip gracefully).
    """
    # Fast path: try channel_id if we already know it
    if channel_id:
        try:
            return await client.get_entity(int(f"-100{channel_id}"))
        except Exception:
            pass

    invite_hash = _extract_invite_hash(link)
    if invite_hash:
        # Private link — NEVER call get_entity(link), it will raise
        # for accounts not yet in the channel.
        # CheckChatInviteRequest returns ChatInviteAlready (has .chat) if member
        # or ChatInvite (no .chat) if not. Either way we don't raise.
        try:
            from telethon.tl.functions.messages import CheckChatInviteRequest
            invite_info = await client(CheckChatInviteRequest(invite_hash))
            if hasattr(invite_info, 'chat') and invite_info.chat is not None:
                return invite_info.chat
        except Exception:
            pass
        # Private link but couldn't get entity — return None
        return None

    # Public link or @username
    try:
        return await client.get_entity(link)
    except Exception:
        return None


async def _join_account(client, link: str) -> str:
    """
    Join one account to a channel/group.
    Returns "ok", "already_member", "join_request_sent", or raises on real errors.

    Note: Telethon raises ValueError("Cannot get entity... not part of")
    internally during Updates processing when join is pending approval.
    We must catch both Exception and ValueError from this call.
    """
    invite_hash = _extract_invite_hash(link)
    if invite_hash:
        # Private invite link: use ImportChatInviteRequest
        try:
            await client(ImportChatInviteRequest(invite_hash))
            return "ok"
        except (Exception, ValueError) as e:
            err = str(e)
            # Already a member — treat as success
            if ("already" in err.lower()
                    or "USER_ALREADY_PARTICIPANT" in err
                    or "UserAlreadyParticipant" in err):
                return "already_member"
            # Telethon raises this ValueError when the account's join is pending
            # (channel returned left=True in Updates, meaning approval required)
            if ("not part of" in err
                    or "Join the group and retry" in err
                    or "INVITE_REQUEST_SENT" in err
                    or "InviteRequestSent" in err):
                return "join_request_sent"
            raise
    else:
        # Public link or username
        try:
            entity = await client.get_entity(link)
        except Exception:
            # Might be a username — try directly
            entity = link
        try:
            await client(JoinChannelRequest(entity))
            return "ok"
        except Exception as e:
            err = str(e)
            if "already" in err.lower() or "USER_ALREADY_PARTICIPANT" in err:
                return "already_member"
            raise


async def join_channel(target: dict) -> dict:
    """
    Join the channel/group for all configured accounts.
    Handles both public links and private invite links (t.me/+hash).
    Returns dict: {account_id: "ok" | "already_member" | error_string}
    """
    channel_link = target["channel_link"]
    account_ids  = target["account_ids"]
    results: dict[int, str] = {}

    for acc_id in account_ids:
        client = tg.get_client(acc_id)
        if not client or not client.is_connected():
            results[acc_id] = "client_not_connected"
            continue
        try:
            status = await _join_account(client, channel_link)
            results[acc_id] = status
            logger.info(f"[Reactions] Account {acc_id} → {status} for {channel_link}")
        except Exception as e:
            results[acc_id] = str(e)
            logger.warning(f"[Reactions] Account {acc_id} failed to join {channel_link}: {e}")

    return results


async def _do_react(target: dict, client: Any, acc_id: int, msg_id: int, channel_entity: Any) -> None:
    """Send a single reaction + optionally increment view from one account."""
    target_id    = target["id"]
    reactions    = target.get("reactions") or ["👍"]
    raw_emoji    = random.choice(reactions)
    # Strip variation selectors (U+FE0F / U+FE0E) — Telegram only accepts plain emoji
    emoji        = raw_emoji.replace("\uFE0F", "").replace("\uFE0E", "")
    view_enabled = target.get("view_enabled", 0)
    view_ratio   = float(target.get("view_ratio") or 1.0)
    key          = (target_id, acc_id, msg_id)

    if key in _reacted:
        return
    _reacted.add(key)

    # DB dedup check (survives restarts)
    if await db.was_msg_reacted(target_id, acc_id, msg_id):
        _reacted.discard(key)
        return

    # --- Step 1: Increment view (if enabled) ---
    if view_enabled and random.random() <= view_ratio:
        try:
            await client(GetMessagesViewsRequest(
                peer=channel_entity,
                id=[msg_id],
                increment=True,
            ))
            logger.info(f"[Reactions] Target {target_id} | acc={acc_id} → 👁 view on msg {msg_id}")
        except Exception as e:
            logger.debug(f"[Reactions] Target {target_id} | acc={acc_id} view failed: {e}")

    # --- Step 2: Send reaction emoji ---
    try:
        await client(SendReactionRequest(
            peer=channel_entity,
            msg_id=msg_id,
            reaction=[ReactionEmoji(emoticon=emoji)],
        ))
        chan_id = _clean_channel_id(getattr(channel_entity, "id", 0))
        await db.add_reaction_log(target_id, acc_id, chan_id, msg_id, emoji, "success")
        logger.info(f"[Reactions] Target {target_id} | acc={acc_id} → {emoji} on msg {msg_id}")
    except Exception as e:
        err = str(e)
        chan_id = _clean_channel_id(getattr(channel_entity, "id", 0))
        await db.add_reaction_log(target_id, acc_id, chan_id, msg_id, emoji, "failed", err)
        logger.warning(f"[Reactions] Target {target_id} | acc={acc_id} react failed: {err}")
    finally:
        _reacted.discard(key)


async def _react_all_accounts(target: dict, msg_id: int, channel_link: str) -> None:
    """Background task: react from each account with a random delay."""
    account_ids = target.get("account_ids") or []
    delay_min   = target.get("delay_min", 5)
    delay_max   = target.get("delay_max", 30)
    channel_id  = target.get("channel_id")

    # Shuffle so accounts react in random order
    shuffled = list(account_ids)
    random.shuffle(shuffled)

    for i, acc_id in enumerate(shuffled):
        client = tg.get_client(acc_id)
        if not client or not client.is_connected():
            continue

        # Apply delay between accounts (skip wait for first)
        if i > 0:
            wait = random.uniform(delay_min, delay_max)
            await asyncio.sleep(wait)

        try:
            # Use _get_entity_only: tries channel_id first (fast), falls back to link
            channel_entity = await _get_entity_only(client, channel_link, channel_id)
            await _do_react(target, client, acc_id, msg_id, channel_entity)
        except Exception as e:
            logger.warning(f"[Reactions] Target {target['id']} | acc={acc_id} entity error: {e}")


def _make_reaction_handler(target: dict):
    """Create a NewMessage event handler for a reaction target."""
    target_id    = target["id"]
    channel_link = target["channel_link"]
    channel_id   = target.get("channel_id")

    async def handler(event):
        # Only handle posts from this specific channel
        evt_cid = _clean_channel_id(event.chat_id)
        if channel_id and evt_cid != int(channel_id):
            return

        # Re-fetch target to respect is_active toggle at runtime
        t = await db.get_reaction_target(target_id)
        if not t or not t.get("is_active"):
            return

        msg_id = event.id
        logger.info(f"[Reactions] Target {target_id} | new post msg_id={msg_id}")

        task = asyncio.create_task(_react_all_accounts(t, msg_id, channel_link))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return handler


def _unregister_target(target_id: int) -> None:
    if target_id not in _handler_removers:
        return
    for client, fn in _handler_removers[target_id]:
        try:
            client.remove_event_handler(fn)
        except Exception:
            pass
    del _handler_removers[target_id]


def _register_target(target: dict) -> None:
    """Register NewMessage handler for the target on all accounts."""
    if target.get("platform", "telegram") != "telegram":
        return
    target_id   = target["id"]
    account_ids = target.get("account_ids") or []

    _unregister_target(target_id)  # clean old handlers first

    handler_fn = _make_reaction_handler(target)
    removers = []

    for acc_id in account_ids:
        client = tg.get_client(acc_id)
        if not client:
            continue
        client.add_event_handler(handler_fn, events.NewMessage(incoming=True))
        removers.append((client, handler_fn))

    _handler_removers[target_id] = removers
    logger.info(
        f"[Reactions] Target {target_id} "
        f"({target.get('channel_title') or target['channel_link']}) "
        f"registered on accounts {account_ids}"
    )


# ── Public API ─────────────────────────────────────────────────────────────────

async def start_all() -> None:
    """Load all active reaction targets and register handlers."""
    targets = await db.get_all_reaction_targets(active_only=True)
    for t in targets:
        _register_target(t)
    logger.info(f"[Reactions] {len(targets)} target(s) loaded")


async def reload_target(target_id: int) -> None:
    """Re-register a single target (e.g. after config change via API)."""
    t = await db.get_reaction_target(target_id)
    if t and t.get("is_active"):
        _register_target(t)
    else:
        _unregister_target(target_id)


async def stop_all() -> None:
    for tid in list(_handler_removers.keys()):
        _unregister_target(tid)
    logger.info("[Reactions] All handlers removed")