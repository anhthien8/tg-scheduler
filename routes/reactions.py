"""
routes/reactions.py  — API endpoints for the auto-react feature
"""

from __future__ import annotations
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import database as db
import reaction_watcher as rw
import telegram_client as tg

router = APIRouter(prefix="/api/reactions", tags=["reactions"])
logger = logging.getLogger("tg-scheduler.reactions.api")


# ── Request schemas ─────────────────────────────────────────────────────────────

class AddTargetRequest(BaseModel):
    channel_link: str            # e.g. "https://t.me/channelname"
    account_ids:  list[int]
    reactions:    list[str] = ["👍"]
    delay_min:    int = 5
    delay_max:    int = 30
    auto_join:    bool = True    # automatically join with all accounts on add
    view_enabled: int   = 0      # 0=off, 1=on
    view_ratio:   float = 1.0    # fraction of accounts that view (0.0-1.0)


class UpdateTargetRequest(BaseModel):
    account_ids: list[int] | None = None
    reactions:   list[str] | None = None
    delay_min:   int | None       = None
    delay_max:   int | None       = None
    is_active:   int | None       = None
    view_enabled: int   | None    = None
    view_ratio:   float | None    = None


# ── GET /api/reactions/targets ──────────────────────────────────────────────────

@router.get("/targets")
async def list_targets():
    targets = await db.get_all_reaction_targets(active_only=False)
    return {"targets": targets}


# ── POST /api/reactions/targets ─────────────────────────────────────────────────

@router.post("/targets")
async def add_target(req: AddTargetRequest):
    link = req.channel_link.strip()
    if not link:
        raise HTTPException(400, "channel_link is required")

    # Resolve channel metadata using first available account
    # For private invite links: resolve AFTER join (get_entity fails before joining)
    # For public links: resolve before joining
    channel_id    = None
    channel_title = None
    invite_hash = rw._extract_invite_hash(link)

    if not invite_hash:
        # Public link — safe to resolve entity before joining
        for acc_id in req.account_ids:
            client = tg.get_client(acc_id)
            if not client or not client.is_connected():
                continue
            entity = await rw._get_entity_only(client, link)
            if entity:
                channel_id    = abs(entity.id)
                channel_title = getattr(entity, "title", link)
                break


    # Insert into DB (channel_id/title may be None for private links — filled in after join)
    target_id = await db.add_reaction_target(
        channel_link  = link,
        channel_id    = channel_id,
        channel_title = channel_title,
        account_ids   = req.account_ids,
        reactions     = req.reactions,
        delay_min     = req.delay_min,
        delay_max     = req.delay_max,
        view_enabled  = req.view_enabled,
        view_ratio    = req.view_ratio,
    )

    target = await db.get_reaction_target(target_id)

    # Auto-join all accounts if requested
    join_results = {}
    if req.auto_join and req.account_ids:
        join_results = await rw.join_channel(target)

    # For private links: resolve channel metadata AFTER join
    # (get_entity only works once the account is a member)
    if invite_hash and not channel_id:
        for acc_id in req.account_ids:
            if join_results.get(acc_id) not in ("ok", "already_member"):
                continue
            client = tg.get_client(acc_id)
            if not client or not client.is_connected():
                continue
            try:
                entity = await rw._get_entity_only(client, link, None)
                if entity:
                    cid   = abs(entity.id)
                    title = getattr(entity, "title", link)
                    await db.update_reaction_target(target_id, channel_id=cid, channel_title=title)
                    break
            except Exception:
                pass

    # Register event handler
    await rw.reload_target(target_id)
    target = await db.get_reaction_target(target_id)  # re-fetch with updated metadata

    return {
        "ok": True,
        "target": target,
        "join_results": join_results,
    }



# ── PUT /api/reactions/targets/{id} ────────────────────────────────────────────

@router.put("/targets/{target_id}")
async def update_target(target_id: int, req: UpdateTargetRequest):
    existing = await db.get_reaction_target(target_id)
    if not existing:
        raise HTTPException(404, "Target not found")

    updates = req.model_dump(exclude_none=True)
    if updates:
        await db.update_reaction_target(target_id, **updates)

    await rw.reload_target(target_id)
    updated = await db.get_reaction_target(target_id)
    return {"ok": True, "target": updated}


# ── DELETE /api/reactions/targets/{id} ─────────────────────────────────────────

@router.delete("/targets/{target_id}")
async def delete_target(target_id: int):
    existing = await db.get_reaction_target(target_id)
    if not existing:
        raise HTTPException(404, "Target not found")
    rw._unregister_target(target_id)
    await db.delete_reaction_target(target_id)
    return {"ok": True}


# ── POST /api/reactions/targets/{id}/join ───────────────────────────────────────

@router.post("/targets/{target_id}/join")
async def manual_join(target_id: int):
    """Trigger manual re-join for all accounts in this target."""
    target = await db.get_reaction_target(target_id)
    if not target:
        raise HTTPException(404, "Target not found")
    results = await rw.join_channel(target)
    return {"ok": True, "join_results": results}


# ── GET /api/reactions/logs ─────────────────────────────────────────────────────

@router.get("/logs")
async def get_logs(target_id: int | None = None, limit: int = 100):
    logs = await db.get_reaction_logs(target_id=target_id, limit=limit)
    return {"logs": logs}


# ── GET /api/reactions/targets/{id}/views ───────────────────────────────────────

@router.get("/targets/{target_id}/views")
async def get_channel_views(target_id: int, posts: int = 5):
    """
    Fetch view counts for the latest N posts in a reaction target channel.
    Returns avg_views, max_views, and per-post list.
    """
    target = await db.get_reaction_target(target_id)
    if not target:
        raise HTTPException(404, "Target not found")

    account_ids = target.get("account_ids") or []
    channel_link = target["channel_link"]

    # Try each account until one works
    for acc_id in account_ids:
        client = tg.get_client(acc_id)
        if not client or not client.is_connected():
            continue
        try:
            entity = await client.get_entity(channel_link)
            messages = await client.get_messages(entity, limit=posts)
            post_views = [
                {"msg_id": m.id, "views": m.views or 0, "date": str(m.date)}
                for m in messages if m
            ]
            total_views = sum(p["views"] for p in post_views)
            avg_views   = round(total_views / len(post_views)) if post_views else 0
            max_views   = max((p["views"] for p in post_views), default=0)
            return {
                "ok": True,
                "target_id": target_id,
                "channel_title": target.get("channel_title"),
                "posts": post_views,
                "avg_views": avg_views,
                "max_views": max_views,
                "total_posts_checked": len(post_views),
            }
        except Exception as e:
            logger.warning(f"[Reactions] Could not fetch views via acc={acc_id}: {e}")
            continue

    raise HTTPException(503, "No available account to fetch channel views")
