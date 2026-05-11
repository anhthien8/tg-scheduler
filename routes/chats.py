"""
Chat listing routes - per account.
"""
from fastapi import APIRouter, Query
import telegram_client as tg

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("")
async def get_chats(account_id: int = Query(1, description="Account ID to fetch chats for")):
    """List groups and channels for a specific account."""
    chats = await tg.get_dialogs(account_id)
    return {"chats": chats, "account_id": account_id}
