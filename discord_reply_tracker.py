"""
discord_reply_tracker.py
────────────────────────
Discord DM reply tracker.
Listens for incoming private messages on ALL connected Discord bot accounts.
When a DM arrives from a user who was previously DM'd by any keyword watcher
(status='success' + platform='discord'), it is recorded as a "hot lead reply"
in the dm_replies table.

Messages from users who were never DM'd are also stored so no conversation
is lost (watcher_id = NULL in that case).

Mirrors the pattern of dm_reply_tracker.py but uses discord.py events.

Dedup key: (bot_id, sender_user_id, message_id) — stored in-memory
to prevent double-insertion within a session.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import database as db

logger = logging.getLogger("tg-scheduler.discord.inbox")

# ── Discord Adapter reference ─────────────────────────────────────────────────
_adapter: Any | None = None


def set_adapter(adapter) -> None:
    """Inject the DiscordAdapter singleton."""
    global _adapter
    _adapter = adapter
    logger.info("[Discord Inbox] Adapter set: %s", type(adapter).__name__)


# ── In-memory state ───────────────────────────────────────────────────────────
# handler_removers: bot_id → (client, handler_fn)
_handler_removers: dict[int, tuple[Any, Any]] = {}

# Dedup set: (bot_id, sender_user_id, message_id)
_seen: set[tuple[int, int, int]] = set()
_MAX_SEEN = 5000  # cap to prevent unbounded growth


def _trim_seen() -> None:
    global _seen
    if len(_seen) > _MAX_SEEN:
        to_remove = list(_seen)[: _MAX_SEEN // 2]
        for item in to_remove:
            _seen.discard(item)


# ── Core handler factory ─────────────────────────────────────────────────────

def _make_handler(bot_id: int):
    """Return a discord.py on_message handler for DM messages on bot_id."""

    async def _handler(message) -> None:
        # Only care about DMs (discord.py: DMChannel)
        # In discord.py, DMs have message.guild == None
        if message.guild is not None:
            return

        author = message.author
        if author is None:
            return

        # Ignore messages from bots (including ourselves)
        if author.bot:
            return

        sender_id = author.id
        msg_id = message.id

        # Dedup
        key = (bot_id, sender_id, msg_id)
        if key in _seen:
            return
        _seen.add(key)
        _trim_seen()

        # Build sender info
        sender_name = getattr(author, "display_name", None) or author.name
        sender_username = author.name  # Discord username (unique handle)
        message_text = message.content or ""

        # Append attachment URLs to message text if present
        if message.attachments:
            attachment_urls = [a.url for a in message.attachments]
            if message_text:
                message_text += "\n"
            message_text += "\n".join(attachment_urls)

        # Check if this sender was previously DM'd by any watcher
        watcher_id = await _find_discord_watcher_for_user(sender_id)

        logger.info(
            "[Discord Inbox] bot=%d ← @%s (id=%d) | watcher=%s | %s",
            bot_id,
            sender_username or "?",
            sender_id,
            watcher_id or "none",
            repr(message_text[:60]),
        )

        await db.add_dm_reply({
            "account_id":      bot_id,
            "sender_user_id":  sender_id,
            "sender_username": sender_username,
            "sender_name":     sender_name,
            "message_text":    message_text,
            "watcher_id":      watcher_id,
            "platform":        "discord",
        })

    return _handler


async def _find_discord_watcher_for_user(user_id: int) -> int | None:
    """
    Return the watcher_id of the most recent successful DM sent to user_id
    on the Discord platform, or None if never DM'd.

    Falls back to db.find_watcher_id_for_user() which is platform-agnostic
    (checks all platforms). In the future, the DB query can be made
    platform-aware by adding a platform filter.
    """
    # Try platform-specific lookup first
    try:
        import aiosqlite
        from database import DB_PATH as db_path
        async with aiosqlite.connect(db_path) as conn:
            row = await (await conn.execute(
                """SELECT watcher_id FROM watcher_dm_logs
                   WHERE target_user_id = ? AND status = 'success'
                   AND platform = 'discord'
                   ORDER BY sent_at DESC LIMIT 1""",
                (user_id,)
            )).fetchone()
            if row:
                return row[0]
    except Exception:
        pass

    # Fallback: platform-agnostic lookup
    return await db.find_watcher_id_for_user(user_id)


# ── Registration ─────────────────────────────────────────────────────────────

def _register_bot(bot_id: int) -> None:
    """Register a on_message handler on the given bot's client."""
    if not _adapter:
        logger.debug("[Discord Inbox] bot=%d: adapter not set, skip register", bot_id)
        return

    client = _adapter.get_bot(bot_id)
    if client is None:
        logger.debug("[Discord Inbox] bot=%d: client not found, skip register", bot_id)
        return

    # Remove stale handler for this bot if any
    _unregister_bot(bot_id)

    handler_fn = _make_handler(bot_id)
    client.add_listener(handler_fn, "on_message")
    _handler_removers[bot_id] = (client, handler_fn)
    logger.info("[Discord Inbox] bot=%d: reply handler registered", bot_id)


def _unregister_bot(bot_id: int) -> None:
    """Remove the reply handler for a given bot."""
    entry = _handler_removers.pop(bot_id, None)
    if entry:
        client, handler_fn = entry
        try:
            client.remove_listener(handler_fn, "on_message")
        except Exception:
            pass


# ── Public API ────────────────────────────────────────────────────────────────

async def start_reply_tracker() -> None:
    """
    Register inbox handlers on all currently connected Discord bots.
    Called once at server startup (after all bot clients are connected).
    """
    try:
        bots = await db.get_all_discord_bots()
    except Exception as e:
        logger.warning("[Discord Inbox] Could not load bots: %s", e)
        bots = []

    registered = 0
    for bot in bots:
        if bot.get("is_connected"):
            _register_bot(bot["id"])
            registered += 1
    logger.info("[Discord Inbox] Reply tracker started — %d bot(s) monitored", registered)


async def stop_reply_tracker() -> None:
    """Remove all reply handlers (called at shutdown)."""
    for bot_id in list(_handler_removers.keys()):
        _unregister_bot(bot_id)
    logger.info("[Discord Inbox] Reply tracker stopped")


def register_bot(bot_id: int) -> None:
    """
    Public helper: call this after a new bot connects so its
    inbox is immediately monitored without restarting the server.
    """
    _register_bot(bot_id)


def unregister_bot(bot_id: int) -> None:
    """Public helper: call when a bot disconnects."""
    _unregister_bot(bot_id)
