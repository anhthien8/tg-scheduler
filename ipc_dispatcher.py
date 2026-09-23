"""Worker-side command execution for the migrated chats/campaign/watcher slice.

Explicit table only — no getattr/eval on command names. Each handler calls the
existing route/engine coroutine so semantics stay in one place.
"""
import database as db


async def _chats_refresh(payload):
    import telegram_client as tg
    from routes.chats import get_chats
    return await get_chats(account_id=payload["account_id"])


async def _campaign_start(payload):
    from fastapi import BackgroundTasks
    from routes.members import start_campaign
    return await start_campaign(payload["campaign_id"], BackgroundTasks())


async def _campaign_stop(payload):
    from routes.members import stop_campaign
    return await stop_campaign(payload["campaign_id"])


async def _watchers_reload(payload):
    import keyword_watcher as kw
    watcher_id = payload["watcher_id"]
    platform = await db.get_watcher_platform(watcher_id)
    if platform is None:
        raise LookupError(f"watcher {watcher_id} không tồn tại")
    if platform != "telegram":
        raise ValueError(f"watcher {watcher_id} thuộc platform {platform}, ngoài slice này")
    await kw.reload_watcher(watcher_id)
    return {"watcher_id": watcher_id, "reloaded": True}


HANDLERS = {
    "chats.refresh": _chats_refresh,
    "campaign.start": _campaign_start,
    "campaign.stop": _campaign_stop,
    "watchers.reload": _watchers_reload,
}


async def dispatch(command: str, payload: dict) -> dict:
    handler = HANDLERS.get(command)
    if handler is None:
        raise ValueError(f"command {command} chưa được migrate sang worker")
    result = await handler(payload)
    return result if isinstance(result, dict) else {"result": result}
