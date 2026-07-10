"""
Discord Bot Management API Routes
──────────────────────────────────
Bot CRUD, connect/disconnect, guild listing, watcher management.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

import database as db

logger = logging.getLogger("tg-scheduler.discord")

router = APIRouter(prefix="/api/discord", tags=["discord"])

# ── Lazy import to avoid circular / missing dependency errors ──
_adapter = None


def _get_adapter():
    global _adapter
    if _adapter is None:
        from platforms.discord_adapter import DiscordAdapter
        _adapter = DiscordAdapter()
    return _adapter


# ── Pydantic models ──────────────────────────────────────────────────────────

class DiscordBotPayload(BaseModel):
    name: str
    bot_token: str


class DiscordWatcherPayload(BaseModel):
    name: str
    sender_bot_ids: list[int] = []
    keywords: list[str] = []
    channel_ids: list[str] = []
    cooldown_hours: int = 24
    dm_once: bool = False
    excluded_usernames: list[str] = []
    is_active: int = 1
    messages: list[dict] = []


# ── Bot CRUD ─────────────────────────────────────────────────────────────────

@router.get("/bots")
async def list_discord_bots():
    """List all registered Discord bots."""
    bots = await db.get_all_discord_bots()
    adapter = _get_adapter()
    # Enrich with live status
    for bot in bots:
        bot["is_connected"] = await adapter.is_connected(bot["id"])
    return bots


@router.post("/bots")
async def add_discord_bot(payload: DiscordBotPayload):
    """Register a new Discord bot."""
    bot_id = await db.create_discord_bot(payload.model_dump())
    return {"id": bot_id, "message": "Discord bot registered"}


@router.put("/bots/{bot_id}")
async def edit_discord_bot(bot_id: int, payload: DiscordBotPayload):
    """Update a Discord bot's name/token."""
    existing = await db.get_discord_bot(bot_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Bot not found")
    await db.update_discord_bot(bot_id, payload.model_dump())
    return {"message": "Bot updated"}


@router.delete("/bots/{bot_id}")
async def remove_discord_bot(bot_id: int):
    """Remove a Discord bot and disconnect it."""
    adapter = _get_adapter()
    await adapter.disconnect_bot(bot_id)
    await db.delete_discord_bot(bot_id)
    return {"message": "Bot deleted"}


@router.post("/bots/{bot_id}/connect")
async def connect_discord_bot(bot_id: int):
    """Connect a Discord bot to the Gateway."""
    bot_data = await db.get_discord_bot(bot_id)
    if not bot_data:
        raise HTTPException(status_code=404, detail="Bot not found")

    adapter = _get_adapter()
    success = await adapter.connect_bot(bot_id, bot_data["bot_token"])

    if success:
        info = await adapter.get_account_info(bot_id)
        await db.update_discord_bot_status(
            bot_id, True,
            user_id=str(info.get("user_id", "")),
            username=info.get("username", ""),
            guild_count=info.get("guild_count", 0),
        )

        # Register handlers on this newly connected bot
        try:
            import discord_reply_tracker as drt_discord
            import discord_watcher as dw
            import discord_reaction_watcher as drw
            import json

            # Set adapters in case they weren't set
            dw.set_adapter(adapter)
            drw.set_adapter(adapter)
            drt_discord.set_adapter(adapter)

            drt_discord.register_bot(bot_id)

            # Reload watchers that use this bot
            watchers = await db.get_all_watchers_by_platform("discord")
            for w in watchers:
                if w.get("is_active"):
                    sender_ids_str = w.get("sender_account_ids", "[]")
                    if isinstance(sender_ids_str, str):
                        try:
                            sender_ids = json.loads(sender_ids_str)
                        except Exception:
                            sender_ids = []
                    else:
                        sender_ids = sender_ids_str

                    if bot_id in sender_ids:
                        await dw.start_watcher(w["id"])

            # Reload reaction targets that use this bot
            targets = await db.get_reaction_targets_by_platform("discord")
            for t in targets:
                if t.get("is_active"):
                    acc_ids_str = t.get("account_ids", "[]")
                    if isinstance(acc_ids_str, str):
                        try:
                            acc_ids = json.loads(acc_ids_str)
                        except Exception:
                            acc_ids = []
                    else:
                        acc_ids = acc_ids_str

                    if bot_id in acc_ids:
                        await drw.start_target(t["id"])
        except Exception as e:
            logger.warning(f"Error registering handlers on newly connected bot {bot_id}: {e}", exc_info=True)

        return {"message": "Connected", "info": info}
    else:
        raise HTTPException(status_code=500, detail="Connect failed (check token)")


@router.post("/bots/{bot_id}/disconnect")
async def disconnect_discord_bot(bot_id: int):
    """Disconnect a Discord bot."""
    adapter = _get_adapter()
    await adapter.disconnect_bot(bot_id)
    await db.update_discord_bot_status(bot_id, False)

    try:
        import discord_reply_tracker as drt_discord
        drt_discord.unregister_bot(bot_id)
    except Exception:
        pass

    return {"message": "Disconnected"}


@router.get("/bots/{bot_id}/guilds")
async def get_bot_guilds(bot_id: int):
    """List all guilds (servers) the bot is in."""
    adapter = _get_adapter()
    guilds = await adapter.get_guilds(bot_id)
    return guilds


# ── Discord Watchers ─────────────────────────────────────────────────────────

@router.get("/watchers")
async def list_discord_watchers():
    """List all Discord keyword watchers."""
    return await db.get_all_watchers_by_platform("discord")


@router.post("/watchers")
async def create_discord_watcher(payload: DiscordWatcherPayload):
    """Create a Discord keyword watcher."""
    import json
    watcher_data = {
        "name": payload.name,
        "sender_account_ids": payload.sender_bot_ids,
        "keywords": payload.keywords,
        "group_ids": payload.channel_ids,
        "cooldown_hours": payload.cooldown_hours,
        "dm_once": 1 if payload.dm_once else 0,
        "excluded_usernames": payload.excluded_usernames,
        "is_active": payload.is_active,
        "platform": "discord",
        "messages": payload.messages,
    }
    watcher_id = await db.create_watcher(watcher_data)

    # Start the watcher if possible
    try:
        import discord_watcher as dw
        await dw.start_watcher(watcher_id)
    except Exception as e:
        logger.warning(f"Could not auto-start Discord watcher {watcher_id}: {e}")

    return {"id": watcher_id, "message": "Discord watcher created"}


@router.get("/watchers/logs")
async def get_discord_watcher_logs(
    limit: int = 50,
    offset: int = 0,
    watcher_id: Optional[int] = None,
):
    """Get Discord DM logs."""
    return await db.get_dm_logs_by_platform(
        platform="discord", limit=limit, offset=offset, watcher_id=watcher_id
    )


# ── Discord Reactions ────────────────────────────────────────────────────────

@router.get("/reactions")
async def list_discord_reactions():
    """List Discord reaction targets."""
    return await db.get_reaction_targets_by_platform("discord")


# ── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def discord_stats():
    """Overview stats for Discord tab."""
    bots = await db.get_all_discord_bots()
    watchers = await db.get_all_watchers_by_platform("discord")
    adapter = _get_adapter()

    connected_count = 0
    for bot in bots:
        if await adapter.is_connected(bot["id"]):
            connected_count += 1

    active_watchers = len([w for w in watchers if w.get("is_active")])

    return {
        "total_bots": len(bots),
        "connected_bots": connected_count,
        "total_watchers": len(watchers),
        "active_watchers": active_watchers,
    }
