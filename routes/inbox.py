"""
routes/inbox.py — API endpoints for DM Reply Tracker (Inbox)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

import database as db

router = APIRouter(prefix="/api/inbox", tags=["inbox"])
logger = logging.getLogger("tg-scheduler.inbox.api")


@router.get("")
async def list_inbox(
    limit: int = 50,
    offset: int = 0,
    is_read: Optional[int] = None,
    watcher_id: Optional[int] = None,
    account_id: Optional[int] = None,
):
    """
    Return DM replies.
    Query params:
      - is_read: 0=unread only, 1=read only, omit=all
      - watcher_id: filter to a specific watcher
      - account_id: filter to a specific account
      - limit / offset: pagination
    """
    replies = await db.get_dm_replies(
        limit=limit,
        offset=offset,
        is_read=is_read,
        watcher_id=watcher_id,
        account_id=account_id,
    )
    return {"replies": replies, "count": len(replies)}


@router.get("/unread-count")
async def unread_count():
    """Return the number of unread DM replies (used by the badge in the UI)."""
    count = await db.count_unread_replies()
    return {"count": count}


@router.post("/{reply_id}/read")
async def mark_read(reply_id: int):
    """Mark a single reply as read."""
    await db.mark_reply_read(reply_id)
    return {"ok": True}


@router.post("/read-all")
async def read_all():
    """Mark all unread replies as read."""
    updated = await db.mark_all_replies_read()
    return {"ok": True, "updated": updated}
