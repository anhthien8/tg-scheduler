"""
Watchers API Routes
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
import logging

import database as db
import keyword_watcher as kw
import telegram_client as tg

logger = logging.getLogger("tg-scheduler")

router = APIRouter(prefix="/api/watchers", tags=["watchers"])


class WatcherPayload(BaseModel):
    name: str
    sender_account_ids: list[int] = []
    keywords: list[str] = []
    group_ids: list[int] = []
    cooldown_hours: int = 24
    dm_once: bool = False
    excluded_usernames: list[str] = []
    is_active: int = 1
    messages: list[dict] = []
    reply_in_group: bool = False
    group_reply_text: str = "Check my DM 😊"
    group_reply_account_id: int | None = None


@router.get("")
async def list_watchers():
    return await db.get_all_watchers_by_platform("telegram")


@router.post("")
async def create_watcher(payload: WatcherPayload):
    watcher_id = await db.create_watcher(payload.model_dump())
    await kw.reload_watcher(watcher_id)
    return {"id": watcher_id, "message": "Watcher created"}


@router.get("/logs")
async def get_watcher_logs(
    limit: int = 50,
    offset: int = 0,
    watcher_id: Optional[int] = None,
    status: Optional[str] = None,
):
    return await db.get_watcher_dm_logs(limit=limit, offset=offset,
                                        watcher_id=watcher_id, status=status)


@router.get("/stats")
async def get_watcher_stats():
    return await db.get_watcher_log_stats()


@router.get("/debug-history")
async def debug_history(account_id: int, chat_id: int, limit: int = 20):
    if os.getenv("DEBUG_ENDPOINTS", "0") != "1":
        raise HTTPException(403, "Disabled in production")
    import telegram_client as tg
    client = tg.get_client(account_id)
    if not client:
        raise HTTPException(status_code=400, detail="Client not found")
    try:
        # Try raw chat_id, then resolve entity
        try:
            ent = await client.get_entity(chat_id)
        except Exception:
            ent = chat_id
        messages = await client.get_messages(ent, limit=limit)
        results = []
        for m in messages:
            try:
                sender = await m.get_sender()
                sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", None) or str(m.sender_id)
            except Exception:
                sender_name = str(m.sender_id)
            results.append({
                "id": m.id,
                "date": str(m.date),
                "sender_id": m.sender_id,
                "sender_name": sender_name,
                "text": m.text
            })
        return {"messages": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"debug_history error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống. Vui lòng thử lại.")




@router.post("/test-send-group")
async def test_send_group(account_id: int, chat_id: int, text: str):
    if os.getenv("DEBUG_ENDPOINTS", "0") != "1":
        raise HTTPException(403, "Disabled in production")
    import asyncio
    client = tg.get_client(account_id)
    if not client:
        raise HTTPException(status_code=400, detail="Client not found")
    try:
        msg = await client.send_message(chat_id, text)
        async def delete_later():
            await asyncio.sleep(60)
            try:
                await client.delete_messages(chat_id, [msg.id])
            except Exception:
                pass
        asyncio.create_task(delete_later())
        return {"success": True, "message_id": msg.id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"test_send_group error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống. Vui lòng thử lại.")


@router.get("/debug-handlers")
async def debug_handlers():
    if os.getenv("DEBUG_ENDPOINTS", "0") != "1":
        raise HTTPException(403, "Disabled in production")
    result = {}
    for acc_id, client in tg._clients.items():
        handlers = []
        for handler, event in client.list_event_handlers():
            handlers.append({
                "handler": str(handler),
                "event": str(event),
            })
        result[acc_id] = handlers
    return result



@router.get("/debug-entity")
async def debug_entity(account_id: int, chat_id: int):
    if os.getenv("DEBUG_ENDPOINTS", "0") != "1":
        raise HTTPException(403, "Disabled in production")
    client = tg.get_client(account_id)
    if not client:
        raise HTTPException(status_code=400, detail="Client not found")
    try:
        ent = await client.get_entity(chat_id)
        return {
            "type": type(ent).__name__,
            "title": getattr(ent, "title", None),
            "broadcast": getattr(ent, "broadcast", None),
            "megagroup": getattr(ent, "megagroup", None),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"debug_entity error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống. Vui lòng thử lại.")

@router.get("/{watcher_id}")
async def get_watcher(watcher_id: int):
    w = await db.get_watcher(watcher_id)
    if not w:
        raise HTTPException(status_code=404, detail="Watcher not found")
    return w


@router.put("/{watcher_id}")
async def update_watcher(watcher_id: int, payload: WatcherPayload):
    w = await db.get_watcher(watcher_id)
    if not w:
        raise HTTPException(status_code=404, detail="Watcher not found")

    ok = await db.update_watcher(watcher_id, payload.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Watcher not found")

    platform = w.get("platform", "telegram")
    if platform == "telegram":
        await kw.reload_watcher(watcher_id)
    elif platform == "discord":
        try:
            import discord_watcher as dw
            await dw.reload_watcher(watcher_id)
        except Exception as e:
            logger.warning(f"Could not reload Discord watcher {watcher_id}: {e}")

    return {"message": "Watcher updated"}


@router.post("/{watcher_id}/toggle")
async def toggle_watcher(watcher_id: int):
    w = await db.get_watcher(watcher_id)
    if not w:
        raise HTTPException(status_code=404, detail="Watcher not found")

    result = await db.toggle_watcher(watcher_id)
    if not result:
        raise HTTPException(status_code=404, detail="Watcher not found")

    platform = w.get("platform", "telegram")
    if platform == "telegram":
        await kw.reload_watcher(watcher_id)
    elif platform == "discord":
        try:
            import discord_watcher as dw
            await dw.reload_watcher(watcher_id)
        except Exception as e:
            logger.warning(f"Could not reload Discord watcher {watcher_id}: {e}")

    return result


@router.delete("/{watcher_id}")
async def delete_watcher(watcher_id: int):
    w = await db.get_watcher(watcher_id)
    if not w:
        raise HTTPException(status_code=404, detail="Watcher not found")

    platform = w.get("platform", "telegram")
    if platform == "telegram":
        kw.remove_watcher(watcher_id)
    elif platform == "discord":
        try:
            import discord_watcher as dw
            dw.remove_watcher(watcher_id)
        except Exception as e:
            logger.warning(f"Could not remove Discord watcher {watcher_id}: {e}")

    ok = await db.delete_watcher(watcher_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Watcher not found")
    return {"message": "Watcher deleted"}


class TestDMPayload(BaseModel):
    target: str  # @username or numeric user_id


@router.post("/{watcher_id}/test-dm")
async def test_dm(watcher_id: int, payload: TestDMPayload):
    """Send a test DM bypassing keyword detection and cooldown."""
    result = await kw.test_dm(watcher_id, payload.target)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class CheckMembershipPayload(BaseModel):
    account_ids: list[int] = []
    group_ids: list[int] = []


@router.post("/check-membership")
async def check_membership(payload: CheckMembershipPayload):
    """
    Check which sender accounts are NOT members of the specified groups.
    Called when saving/editing a watcher rule to warn users.
    """
    result = await tg.check_accounts_in_groups(payload.account_ids, payload.group_ids)
    return result

