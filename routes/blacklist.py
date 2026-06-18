"""
Blacklist routes - global DM blacklist management.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import database as db

router = APIRouter(prefix="/api/blacklist", tags=["blacklist"])


class BlacklistPayload(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    reason: Optional[str] = ""


@router.get("")
async def list_blacklist():
    """Get all blacklisted users."""
    return await db.get_dm_blacklist()


@router.post("")
async def add_blacklist(payload: BlacklistPayload):
    """Add a user to the global DM blacklist."""
    if not payload.user_id and not payload.username:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Cần có user_id hoặc username")
    return await db.add_to_dm_blacklist(payload.user_id, payload.username, payload.reason or "")


@router.delete("/{blacklist_id}")
async def remove_blacklist(blacklist_id: int):
    """Remove a user from the global DM blacklist."""
    await db.remove_from_dm_blacklist(blacklist_id)
    return {"ok": True}
