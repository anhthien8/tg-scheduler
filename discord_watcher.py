"""
discord_watcher.py
──────────────────
Discord keyword watcher engine.
Listens for new messages in configured Discord channels, matches keywords,
and auto-DMs the sender via multi-bot account rotation with fallback.

Mirrors the pattern of keyword_watcher.py but uses discord.py events
and the platforms.discord_adapter abstraction layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time as _time
from typing import Any

import database as db
import ai_remix as ai_rmx
from engines.keyword_engine import normalize_text, match_keyword_advanced

logger = logging.getLogger("tg-scheduler.discord.watcher")

# ── Discord Adapter reference ─────────────────────────────────────────────────
# Set via set_adapter() during startup, before start_all_watchers() is called.
_adapter: Any | None = None  # platforms.discord_adapter.DiscordAdapter


def set_adapter(adapter) -> None:
    """Inject the DiscordAdapter singleton so watcher can send DMs."""
    global _adapter
    _adapter = adapter
    logger.info("[Discord Watcher] Adapter set: %s", type(adapter).__name__)


# ── In-memory state ───────────────────────────────────────────────────────────
_handler_removers: dict[int, list] = {}       # watcher_id → [(bot_client, handler_fn), ...]
_background_tasks: set[asyncio.Task] = set()

# Cooldown: {(watcher_id, user_id): timestamp_last_dm}
_user_dm_sent: dict[tuple[int, int], float] = {}
USER_DM_COOLDOWN_SECS = 24 * 60 * 60  # 24 hours (default, overridden by watcher config)

# Per-message dedup: {(channel_id, msg_id): timestamp}
_seen_msg_ids: dict[tuple[int, int], float] = {}
MSG_DEDUP_TTL_SECS = 10 * 60  # 10 minutes

# Users currently being processed (anti-race-condition lock)
_user_dm_in_progress: set[tuple[int, int]] = set()  # (watcher_id, user_id)

# Global DM rate-limit: 1 DM at a time with random delay
_dm_global_lock: asyncio.Lock | None = None
_dm_last_send_time: float = 0.0
DM_DELAY_MIN_SECS = 30   # 30 seconds (Discord is less restrictive than TG)
DM_DELAY_MAX_SECS = 5 * 60  # 5 minutes

# Bot account rotation index per watcher
_rr_index: dict[int, int] = {}


def _get_dm_lock() -> asyncio.Lock:
    """Lazily create the global DM lock on the running event loop."""
    global _dm_global_lock
    if _dm_global_lock is None:
        _dm_global_lock = asyncio.Lock()
    return _dm_global_lock


# ── Dedup & Cooldown helpers ──────────────────────────────────────────────────

def _already_dmed(watcher_id: int, user_id: int, cooldown_secs: int) -> bool:
    """Check if user was recently DM'd by this watcher (in-memory fast check)."""
    key = (watcher_id, user_id)
    if key in _user_dm_in_progress:
        return True
    last = _user_dm_sent.get(key)
    if last and _time.time() - last < cooldown_secs:
        return True
    return False


def _mark_dmed(watcher_id: int, user_id: int) -> None:
    _user_dm_sent[(watcher_id, user_id)] = _time.time()


def _purge_seen_msgs() -> None:
    """Remove expired entries from _seen_msg_ids to keep memory bounded."""
    now = _time.time()
    expired = [k for k, ts in _seen_msg_ids.items() if now - ts > MSG_DEDUP_TTL_SECS]
    for k in expired:
        _seen_msg_ids.pop(k, None)


def _next_bot_account(watcher_id: int, account_ids: list[int]) -> list[int]:
    """
    Round-robin rotate through sender_account_ids.
    Returns account_ids reordered starting from the next one in rotation.
    """
    if not account_ids:
        return []
    idx = _rr_index.get(watcher_id, 0) % len(account_ids)
    rotated = account_ids[idx:] + account_ids[:idx]
    _rr_index[watcher_id] = (idx + 1) % len(account_ids)
    return rotated


# ── DM error translation (Vietnamese) ────────────────────────────────────────

def _translate_dm_error(raw: str) -> str:
    """Translate raw Discord error text to a short human-readable Vietnamese string."""
    r = str(raw).lower()
    if "cannot send messages to this user" in r:
        return "User không nhận DM – đã tắt DM từ server."
    if "forbidden" in r or "403" in r:
        return "Không có quyền gửi DM cho user này."
    if "rate limit" in r or "429" in r:
        return "Rate limited – Discord chặn tạm."
    if "unknown user" in r:
        return "User không tồn tại trên Discord."
    if "not found" in r or "404" in r:
        return "Không tìm thấy user hoặc channel."
    return raw[:120] if len(raw) > 120 else raw


# ── Core DM sending ──────────────────────────────────────────────────────────

async def _send_dm_with_rotation(
    watcher_id: int,
    account_ids: list[int],
    target_user_id: int,
    target_username: str | None,
    messages: list[dict],
    channel_id: int | None,
    channel_name: str | None,
    matched_keyword: str,
) -> tuple[bool, int | None, str | None]:
    """
    Try each bot account in rotation order to send DM.
    Returns (success, used_account_id, error).
    """
    if not _adapter:
        return False, None, "Discord adapter not initialized"

    # Acquire global DM lock — ensures only 1 DM is in-flight at a time
    dm_lock = _get_dm_lock()
    async with dm_lock:
        global _dm_last_send_time
        elapsed = _time.time() - _dm_last_send_time
        if _dm_last_send_time > 0 and elapsed < DM_DELAY_MIN_SECS:
            delay = random.uniform(DM_DELAY_MIN_SECS, DM_DELAY_MAX_SECS)
            logger.info(
                "[Discord DM Queue] Waiting %.0fs before next DM (anti-spam delay)", delay
            )
            await asyncio.sleep(delay)
        _dm_last_send_time = _time.time()

        return await _do_send_dm(
            watcher_id, account_ids, target_user_id, target_username,
            messages, channel_id, channel_name, matched_keyword,
        )


async def _do_send_dm(
    watcher_id: int,
    account_ids: list[int],
    target_user_id: int,
    target_username: str | None,
    messages: list[dict],
    channel_id: int | None,
    channel_name: str | None,
    matched_keyword: str,
) -> tuple[bool, int | None, str | None]:
    """Internal: actual DM sending logic with bot rotation fallback."""
    # Load AI remix settings
    ai_provider = await db.get_setting("ai_provider", None)
    ai_enabled = ai_provider in ("gemini", "deepseek", "openai", "groq")
    ai_keys = []
    if ai_enabled:
        try:
            raw = await db.get_setting("ai_keys_" + ai_provider, "[]")
            ai_keys = json.loads(raw) if raw else []
        except Exception:
            ai_keys = []
        if not ai_keys:
            ai_enabled = False

    last_error = None
    rotated_ids = _next_bot_account(watcher_id, account_ids)

    for acc_id in rotated_ids:
        # Check if this bot is connected
        if not await _adapter.is_connected(acc_id):
            logger.warning(
                "[Discord Watcher %d] Bot %d not connected, trying next", watcher_id, acc_id
            )
            continue

        try:
            # Send all messages sequentially
            for msg in sorted(messages, key=lambda m: m.get("msg_order", 0)):
                text = msg.get("content", "") or ""
                media_path = msg.get("media_path")

                # AI remix text if enabled
                if ai_enabled and text:
                    logger.info(
                        "[Discord Watcher %d] Requesting %s AI remix...",
                        watcher_id, ai_provider,
                    )
                    text = await ai_rmx.remix_message(
                        original_text=text,
                        provider=ai_provider,
                        api_keys=ai_keys,
                        sender_name=target_username,
                    )

                success = await _adapter.send_dm(
                    account_id=acc_id,
                    user_id=target_user_id,
                    text=text,
                    media_path=media_path,
                )
                if not success:
                    raise RuntimeError(f"send_dm returned False for bot {acc_id}")

                # Small delay between multi-part messages
                await asyncio.sleep(random.uniform(1.0, 3.0))

            # ✓ All messages sent successfully
            logger.info(
                "[Discord Watcher %d] ✓ DM sent to %s (id=%s) via bot %d (keyword: '%s')",
                watcher_id, target_username or "?", target_user_id, acc_id, matched_keyword,
            )
            await db.add_watcher_dm_log(
                watcher_id, acc_id, target_user_id, target_username,
                channel_id, channel_name, matched_keyword, "success",
                platform="discord",
            )
            return True, acc_id, None

        except Exception as e:
            last_error = _translate_dm_error(str(e))
            logger.warning(
                "[Discord Watcher %d] Bot %d ✗ DM to %s failed: %s, trying next",
                watcher_id, acc_id, target_user_id, e,
            )
            await db.add_watcher_dm_log(
                watcher_id, acc_id, target_user_id, target_username,
                channel_id, channel_name, matched_keyword, "failed", last_error,
                platform="discord",
            )
            continue

    # All accounts exhausted
    if last_error:
        logger.error(
            "[Discord Watcher %d] All bots failed DM to %s: %s",
            watcher_id, target_user_id, last_error,
        )
    return False, None, last_error


# ── Handler factory ──────────────────────────────────────────────────────────

def _make_handler(watcher: dict):
    """
    Create a discord.py on_message callback closure for a watcher config.
    The returned async function is registered on each bot client.
    """
    watcher_id = watcher["id"]
    keywords = [normalize_text(kw) for kw in watcher.get("keywords", [])]
    channel_ids = watcher.get("group_ids", [])  # reuse group_ids for Discord channel IDs
    account_ids = watcher.get("sender_account_ids", [])
    cooldown_hours = watcher.get("cooldown_hours", 24)
    cooldown_secs = cooldown_hours * 3600
    dm_once = bool(watcher.get("dm_once", False))
    excluded = {u.lstrip("@").lower() for u in watcher.get("excluded_usernames", [])}
    messages = watcher.get("messages", [])

    async def process_msg(message, matched_keyword: str) -> None:
        """Background task: check all conditions and send DM."""
        user_id = None
        try:
            author = message.author
            if author.bot:
                return

            user_id = author.id
            username = author.name
            display_name = getattr(author, "display_name", username) or username

            # Exclusion list check
            uname_lower = username.lower() if username else ""
            if uname_lower and uname_lower in excluded:
                logger.info(
                    "[Discord Watcher %d] Skipped – @%s in exclusion list", watcher_id, uname_lower
                )
                return
            if str(user_id) in excluded:
                logger.info(
                    "[Discord Watcher %d] Skipped – user_id %s in exclusion list",
                    watcher_id, user_id,
                )
                return

            # Re-fetch watcher from DB to respect runtime is_active toggle
            w = await db.get_watcher(watcher_id)
            if not w or not w["is_active"]:
                return

            # Check global DM blacklist
            if await db.is_user_blacklisted(user_id):
                logger.info(
                    "[Discord Watcher %d] User %s blacklisted, skip DM",
                    watcher_id, user_id,
                )
                return

            # Cooldown / dm_once check (DB-level)
            skip = await db.was_user_dmed_recently(
                watcher_id, user_id, cooldown_hours, dm_once=dm_once
            )
            if skip:
                reason = "đã DM vĩnh viễn" if dm_once else "still in cooldown"
                logger.info(
                    "[Discord Watcher %d] Skipped DM to %s – %s",
                    watcher_id, username, reason,
                )
                return

            if not messages:
                logger.warning(
                    "[Discord Watcher %d] No messages configured, skipping", watcher_id
                )
                return

            # Resolve channel name
            channel_name = getattr(message.channel, "name", None) or str(message.channel.id)
            ch_id = message.channel.id

            logger.info(
                "[Discord Watcher %d] Keyword '%s' matched in #%s – DM to @%s (id=%s)",
                watcher_id, matched_keyword, channel_name, username, user_id,
            )

            # ── Race-condition safe dedup ─────────────────────────────────────
            dm_key = (watcher_id, user_id)
            if _already_dmed(watcher_id, user_id, cooldown_secs):
                logger.info(
                    "[Discord Watcher %d] Already DM'd/processing user %s (in-mem), skipping",
                    watcher_id, user_id,
                )
                return
            _user_dm_in_progress.add(dm_key)

            # Send DM with bot rotation
            success, used_acc, err = await _send_dm_with_rotation(
                watcher_id=watcher_id,
                account_ids=account_ids,
                target_user_id=user_id,
                target_username=username,
                messages=messages,
                channel_id=ch_id,
                channel_name=channel_name,
                matched_keyword=matched_keyword,
            )
            # Always mark in-memory after attempt (success OR fail)
            _mark_dmed(watcher_id, user_id)
            if not success:
                logger.info(
                    "[Discord Watcher %d] All bots failed DM to %s (id=%s). Error: %s",
                    watcher_id, username, user_id, err,
                )

        except Exception as e:
            logger.exception(
                "[Discord Watcher %d] Error in process_msg: %s", watcher_id, e
            )
            if user_id is not None:
                _mark_dmed(watcher_id, user_id)
        finally:
            if user_id is not None:
                _user_dm_in_progress.discard((watcher_id, user_id))

    async def handler(message) -> None:
        """
        Discord on_message callback.
        Filters by configured channel IDs, matches keywords, then spawns
        a background task for the heavy DM logic.
        """
        # Only handle messages from configured channels
        ch_id = message.channel.id
        if ch_id not in channel_ids:
            return

        # Ignore DMs / system messages
        if not hasattr(message.channel, "guild") or message.channel.guild is None:
            return

        text = normalize_text(message.content or "")
        if not text:
            return

        # Keyword matching
        matched = None
        for kw in keywords:
            if match_keyword_advanced(text, kw):
                matched = kw
                break
        if not matched:
            return

        # Per-message dedup
        _purge_seen_msgs()
        msg_key = (ch_id, message.id)
        if msg_key in _seen_msg_ids:
            return
        _seen_msg_ids[msg_key] = _time.time()

        logger.debug(
            "[Discord Watcher %d] Spawning background task for matched keyword '%s'",
            watcher_id, matched,
        )
        task = asyncio.create_task(process_msg(message, matched))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return handler


# ── Registration ─────────────────────────────────────────────────────────────

def _register_watcher(watcher: dict) -> None:
    """Register discord.py on_message handlers for all bot accounts of a watcher."""
    if not _adapter:
        logger.warning("[Discord Watcher] Adapter not set – cannot register handlers")
        return

    watcher_id = watcher["id"]
    account_ids = watcher.get("sender_account_ids", [])
    channel_ids = watcher.get("group_ids", [])

    if not channel_ids or not account_ids:
        logger.warning(
            "[Discord Watcher %d] No channels or bots – skipping registration",
            watcher_id,
        )
        return

    _unregister_watcher(watcher_id)  # clean up old handlers first

    removers: list[tuple] = []
    handler_fn = _make_handler(watcher)

    for acc_id in account_ids:
        try:
            client = _adapter.get_bot(acc_id)
            if client is None:
                logger.warning(
                    "[Discord Watcher %d] Bot %d client not found, skipping",
                    watcher_id, acc_id,
                )
                continue
            client.add_listener(handler_fn, "on_message")
            removers.append((client, handler_fn))
            logger.info(
                "[Discord Watcher %d] Registered on bot %d – channels: %s",
                watcher_id, acc_id, channel_ids,
            )
        except Exception as e:
            logger.warning(
                "[Discord Watcher %d] Failed to register on bot %d: %s",
                watcher_id, acc_id, e,
            )

    _handler_removers[watcher_id] = removers


def _unregister_watcher(watcher_id: int) -> None:
    """Remove all event handlers for a watcher."""
    if watcher_id not in _handler_removers:
        return
    for client, fn in _handler_removers[watcher_id]:
        try:
            client.remove_listener(fn, "on_message")
        except Exception:
            pass
    del _handler_removers[watcher_id]
    logger.info("[Discord Watcher %d] Unregistered handlers", watcher_id)


# ── Public API ────────────────────────────────────────────────────────────────

async def start_all_watchers() -> None:
    """Load all active Discord watchers from DB and register handlers. Call on startup."""
    await _preload_dm_history()
    try:
        watchers = await db.get_all_watchers_by_platform("discord")
    except Exception as e:
        logger.warning("[Discord Watcher] Could not load watchers: %s", e)
        watchers = []

    active = [w for w in watchers if w.get("is_active")]
    for w in active:
        # Parse JSON fields that get_all_watchers_by_platform may not auto-parse
        for field in ("sender_account_ids", "keywords", "group_ids", "excluded_usernames"):
            val = w.get(field)
            if isinstance(val, str):
                try:
                    w[field] = json.loads(val)
                except Exception:
                    w[field] = []
        _register_watcher(w)
    logger.info("[Discord Watcher] %d active rule(s) registered", len(active))


async def stop_all_watchers() -> None:
    """Remove all Discord watcher handlers."""
    for wid in list(_handler_removers.keys()):
        _unregister_watcher(wid)
    logger.info("[Discord Watcher] All handlers removed")


async def start_watcher(watcher_id: int) -> None:
    """Start/reload a single Discord watcher."""
    w = await db.get_watcher(watcher_id)
    if not w:
        _unregister_watcher(watcher_id)
        return
    if w.get("is_active"):
        # Parse JSON fields
        for field in ("sender_account_ids", "keywords", "group_ids", "excluded_usernames"):
            val = w.get(field)
            if isinstance(val, str):
                try:
                    w[field] = json.loads(val)
                except Exception:
                    w[field] = []
        _register_watcher(w)
    else:
        _unregister_watcher(watcher_id)


async def stop_watcher(watcher_id: int) -> None:
    """Stop a single Discord watcher."""
    _unregister_watcher(watcher_id)


async def reload_watcher(watcher_id: int) -> None:
    """Re-register a single watcher (call after create/update/toggle)."""
    await start_watcher(watcher_id)


def remove_watcher(watcher_id: int) -> None:
    """Remove handlers when a watcher is deleted."""
    _unregister_watcher(watcher_id)


# ── DM History Preload ────────────────────────────────────────────────────────

async def _preload_dm_history() -> None:
    """Load recent successful DMs from DB into _user_dm_sent to persist across restarts."""
    try:
        import aiosqlite
        from datetime import datetime, timezone
        from database import DB_PATH as db_path

        cutoff_secs = USER_DM_COOLDOWN_SECS
        async with aiosqlite.connect(db_path) as conn:
            rows = await conn.execute_fetchall(
                """SELECT watcher_id, target_user_id, MAX(sent_at) as last_dm
                   FROM watcher_dm_logs
                   WHERE status = 'success'
                   AND platform = 'discord'
                   AND sent_at >= datetime('now', '-' || ? || ' seconds')
                   GROUP BY watcher_id, target_user_id""",
                (str(cutoff_secs),)
            )
        loaded = 0
        for row in rows:
            w_id = row[0]
            uid_val = row[1]
            last_str = row[2] or ""
            try:
                dt = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                _user_dm_sent[(int(w_id), int(uid_val))] = dt.timestamp()
                loaded += 1
            except Exception:
                pass
        logger.info("[Discord DM Dedup] Preloaded %d user(s) from DB history (24h window)", loaded)
    except Exception as e:
        logger.warning("[Discord DM Dedup] Could not preload history: %s", e)
