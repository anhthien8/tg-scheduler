"""
discord_reaction_watcher.py
────────────────────────────
Discord reaction watcher engine.
Monitors new messages in configured Discord channels and automatically
adds emoji reactions with random delays.

Mirrors the pattern of reaction_watcher.py but uses discord.py events
and the platforms.discord_adapter abstraction layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time as _time
from typing import Any

import database as db

logger = logging.getLogger("tg-scheduler.discord.reactions")

# ── Discord Adapter reference ─────────────────────────────────────────────────
_adapter: Any | None = None


def set_adapter(adapter) -> None:
    """Inject the DiscordAdapter singleton."""
    global _adapter
    _adapter = adapter
    logger.info("[Discord Reactions] Adapter set: %s", type(adapter).__name__)


# ── In-memory state ───────────────────────────────────────────────────────────
_handler_removers: dict[int, list[tuple[Any, Any]]] = {}  # target_id → [(client, fn), ...]
_background_tasks: set[asyncio.Task] = set()

# Dedup: set of (target_id, account_id, msg_id) to prevent double-react in same session
_reacted: set[tuple[int, int, int]] = set()


# ── Core reaction logic ──────────────────────────────────────────────────────

async def _do_react(
    target: dict,
    acc_id: int,
    msg_id: int,
    channel_id: int,
) -> None:
    """Send a single reaction from one bot account."""
    if not _adapter:
        return

    target_id = target["id"]
    reactions = target.get("reactions") or ["👍"]
    emoji = random.choice(reactions)
    key = (target_id, acc_id, msg_id)

    if key in _reacted:
        return
    _reacted.add(key)

    # DB dedup check (survives restarts)
    if await db.was_msg_reacted(target_id, acc_id, msg_id):
        _reacted.discard(key)
        return

    try:
        success = await _adapter.add_reaction(
            account_id=acc_id,
            channel_id=channel_id,
            msg_id=msg_id,
            emoji=emoji,
        )
        status = "success" if success else "failed"
        err_msg = None if success else "add_reaction returned False"

        await db.add_reaction_log(target_id, acc_id, channel_id, msg_id, emoji, status, err_msg, platform="discord")
        logger.info(
            "[Discord Reactions] Target %d | bot=%d → %s on msg %d (%s)",
            target_id, acc_id, emoji, msg_id, status,
        )
    except Exception as e:
        err = str(e)
        await db.add_reaction_log(target_id, acc_id, channel_id, msg_id, emoji, "failed", err, platform="discord")
        logger.warning(
            "[Discord Reactions] Target %d | bot=%d react failed: %s",
            target_id, acc_id, err,
        )
    finally:
        _reacted.discard(key)


async def _react_all_accounts(target: dict, msg_id: int, channel_id: int) -> None:
    """Background task: react from each bot account with a random delay."""
    account_ids = target.get("account_ids") or []
    delay_min = target.get("delay_min", 5)
    delay_max = target.get("delay_max", 30)

    # Shuffle so bots react in random order
    shuffled = list(account_ids)
    random.shuffle(shuffled)

    for i, acc_id in enumerate(shuffled):
        if not await _adapter.is_connected(acc_id):
            continue

        # Apply delay between accounts (skip wait for first)
        if i > 0:
            wait = random.uniform(delay_min, delay_max)
            await asyncio.sleep(wait)

        try:
            await _do_react(target, acc_id, msg_id, channel_id)
        except Exception as e:
            logger.warning(
                "[Discord Reactions] Target %d | bot=%d error: %s",
                target["id"], acc_id, e,
            )


# ── Handler factory ──────────────────────────────────────────────────────────

def _make_reaction_handler(target: dict):
    """Create a discord.py on_message handler for a reaction target."""
    target_id = target["id"]
    # For Discord, channel_id is stored as the primary identifier
    target_channel_id = target.get("channel_id")

    async def handler(message) -> None:
        # Only handle messages from the specific channel
        ch_id = message.channel.id
        if target_channel_id and ch_id != int(target_channel_id):
            return

        # Ignore DMs / system messages
        if not hasattr(message.channel, "guild") or message.channel.guild is None:
            return

        # Re-fetch target to respect is_active toggle at runtime
        t = await db.get_reaction_target(target_id)
        if not t or not t.get("is_active"):
            return

        # Parse JSON fields if needed
        for field in ("account_ids", "reactions"):
            val = t.get(field)
            if isinstance(val, str):
                try:
                    t[field] = json.loads(val)
                except Exception:
                    t[field] = []

        msg_id = message.id
        logger.info(
            "[Discord Reactions] Target %d | new post msg_id=%d in channel %d",
            target_id, msg_id, ch_id,
        )

        task = asyncio.create_task(_react_all_accounts(t, msg_id, ch_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return handler


# ── Registration ─────────────────────────────────────────────────────────────

def _register_target(target: dict) -> None:
    """Register on_message handler for the target on all bot accounts."""
    if not _adapter:
        logger.warning("[Discord Reactions] Adapter not set – cannot register handlers")
        return

    target_id = target["id"]
    account_ids = target.get("account_ids") or []

    _unregister_target(target_id)  # clean old handlers first

    handler_fn = _make_reaction_handler(target)
    removers: list[tuple] = []

    for acc_id in account_ids:
        try:
            client = _adapter.get_bot(acc_id)
            if client is None:
                continue
            client.add_listener(handler_fn, "on_message")
            removers.append((client, handler_fn))
        except Exception as e:
            logger.warning(
                "[Discord Reactions] Target %d | bot %d register failed: %s",
                target_id, acc_id, e,
            )

    _handler_removers[target_id] = removers
    logger.info(
        "[Discord Reactions] Target %d (%s) registered on %d bot(s)",
        target_id,
        target.get("channel_title") or target.get("channel_link", "?"),
        len(removers),
    )


def _unregister_target(target_id: int) -> None:
    if target_id not in _handler_removers:
        return
    for client, fn in _handler_removers[target_id]:
        try:
            client.remove_listener(fn, "on_message")
        except Exception:
            pass
    del _handler_removers[target_id]


# ── Public API ────────────────────────────────────────────────────────────────

async def start_all() -> None:
    """Load all active Discord reaction targets and register handlers."""
    try:
        targets = await db.get_reaction_targets_by_platform("discord")
    except Exception as e:
        logger.warning("[Discord Reactions] Could not load targets: %s", e)
        targets = []

    active = [t for t in targets if t.get("is_active")]
    for t in active:
        # Parse JSON fields
        for field in ("account_ids", "reactions"):
            val = t.get(field)
            if isinstance(val, str):
                try:
                    t[field] = json.loads(val)
                except Exception:
                    t[field] = []
        _register_target(t)
    logger.info("[Discord Reactions] %d target(s) loaded", len(active))


async def stop_all() -> None:
    """Remove all Discord reaction handlers."""
    for tid in list(_handler_removers.keys()):
        _unregister_target(tid)
    logger.info("[Discord Reactions] All handlers removed")


async def start_target(target_id: int) -> None:
    """Start/reload a single Discord reaction target."""
    t = await db.get_reaction_target(target_id)
    if t and t.get("is_active"):
        # Parse JSON fields
        for field in ("account_ids", "reactions"):
            val = t.get(field)
            if isinstance(val, str):
                try:
                    t[field] = json.loads(val)
                except Exception:
                    t[field] = []
        _register_target(t)
    else:
        _unregister_target(target_id)


async def stop_target(target_id: int) -> None:
    """Stop a single Discord reaction target."""
    _unregister_target(target_id)


async def reload_target(target_id: int) -> None:
    """Alias for start_target (reload after config change via API)."""
    await start_target(target_id)
