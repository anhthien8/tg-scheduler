"""
Chat listing routes - per account.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import asyncio
import telegram_client as tg

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("")
async def get_chats(account_id: int = Query(1, description="Account ID to fetch chats for")):
    """List groups and channels for a specific account."""
    chats = await tg.get_dialogs(account_id)
    return {"chats": chats, "account_id": account_id}


class LeaveChannelPayload(BaseModel):
    account_id: int
    chat_id: int


class LeaveAllPayload(BaseModel):
    account_id: int
    delay_seconds: float = 1.5


@router.post("/leave-channel")
async def leave_channel(payload: LeaveChannelPayload):
    """Leave a single group or channel."""
    result = await tg.leave_channel(payload.account_id, payload.chat_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    return result


@router.post("/leave-all")
async def leave_all_channels(payload: LeaveAllPayload):
    """Leave all groups and channels for an account."""
    chats = await tg.get_dialogs(payload.account_id)
    results = []
    for chat in chats:
        res = await tg.leave_channel(payload.account_id, chat["chat_id"])
        results.append({
            "chat_id": chat["chat_id"],
            "chat_title": chat["chat_title"],
            **res,
        })
        if payload.delay_seconds > 0:
            await asyncio.sleep(payload.delay_seconds)
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - success_count
    return {
        "total": len(results),
        "success": success_count,
        "failed": fail_count,
        "details": results,
    }
