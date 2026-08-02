"""
API Routes for Invite Campaign feature.
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import logging

import database as db
import telegram_client as tg
from invite_engine import run_invite_campaign, stop_invite_campaign, _active_invite_campaigns

logger = logging.getLogger("tg-scheduler.routes.invite")

router = APIRouter(prefix="/api", tags=["invite"])


class InviteCampaignCreate(BaseModel):
    name: str
    scrape_job_id: str
    target_chat: str
    invite_mode: str = "direct"
    invite_link: str | None = None
    sender_account_ids: list[int]
    daily_limit: int = 50
    delay_min: int = 45
    delay_max: int = 120
    dm_message: str | None = None
    use_ai_remix: bool = False
    schedule_enabled: bool = False
    schedule_time: str | None = None
    schedule_days: int = 7


class InviteCampaignUpdate(BaseModel):
    name: str | None = None
    target_chat: str | None = None
    invite_mode: str | None = None
    invite_link: str | None = None
    sender_account_ids: list[int] | None = None
    daily_limit: int | None = None
    delay_min: int | None = None
    delay_max: int | None = None
    dm_message: str | None = None
    use_ai_remix: bool | None = None
    schedule_enabled: bool | None = None
    schedule_time: str | None = None
    schedule_days: int | None = None


# ── List all invite campaigns ──────────────────────────────────────────────────
@router.get("/invite-campaigns")
async def list_invite_campaigns(updated_since: str | None = None):
    campaigns = await db.get_all_invite_campaigns()
    # Add running status from engine state
    for c in campaigns:
        c["is_running"] = c["id"] in _active_invite_campaigns and _active_invite_campaigns[c["id"]]
    if updated_since:
        campaigns = [c for c in campaigns if c.get("updated_at", "") > updated_since]
    return campaigns


# ── Create invite campaign ─────────────────────────────────────────────────────
@router.post("/invite-campaigns")
async def create_invite_campaign(data: InviteCampaignCreate):
    # Resolve target chat info
    target_title = data.target_chat
    if data.sender_account_ids:
        acc_id = data.sender_account_ids[0]
        info = await tg.get_channel_info(acc_id, data.target_chat)
        if info.get("success"):
            target_title = info.get("title", data.target_chat)

    # Count members
    members = await db.get_scraped_members(data.scrape_job_id, limit=10000)
    total_targets = len(members)

    payload = data.model_dump()
    payload["target_chat_title"] = target_title
    payload["total_targets"] = total_targets

    campaign_id = await db.create_invite_campaign(payload)
    return {"id": campaign_id, "status": "created",
            "target_chat_title": target_title, "total_targets": total_targets}


# ── Get single invite campaign ─────────────────────────────────────────────────
@router.get("/invite-campaigns/{campaign_id}")
async def get_invite_campaign(campaign_id: int):
    campaign = await db.get_invite_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign["is_running"] = campaign_id in _active_invite_campaigns and _active_invite_campaigns[campaign_id]
    return campaign


# ── Update invite campaign ─────────────────────────────────────────────────────
@router.put("/invite-campaigns/{campaign_id}")
async def update_invite_campaign(campaign_id: int, data: InviteCampaignUpdate):
    campaign = await db.get_invite_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign_id in _active_invite_campaigns and _active_invite_campaigns[campaign_id]:
        raise HTTPException(status_code=400, detail="Cannot update running campaign")

    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if update_data:
        await db.update_invite_campaign(campaign_id, update_data)
    return {"status": "updated"}


# ── Delete invite campaign ─────────────────────────────────────────────────────
@router.delete("/invite-campaigns/{campaign_id}")
async def delete_invite_campaign(campaign_id: int):
    # Stop if running
    if campaign_id in _active_invite_campaigns:
        stop_invite_campaign(campaign_id)
        await asyncio.sleep(1)

    await db.delete_invite_campaign(campaign_id)
    return {"status": "deleted"}


# ── Start invite campaign ──────────────────────────────────────────────────────
@router.post("/invite-campaigns/{campaign_id}/start")
async def start_invite_campaign(campaign_id: int, background_tasks: BackgroundTasks):
    campaign = await db.get_invite_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign_id in _active_invite_campaigns and _active_invite_campaigns[campaign_id]:
        raise HTTPException(status_code=400, detail="Campaign already running")

    background_tasks.add_task(run_invite_campaign, campaign_id)
    return {"status": "started"}


# ── Stop invite campaign ──────────────────────────────────────────────────────
@router.post("/invite-campaigns/{campaign_id}/stop")
async def stop_invite_campaign_route(campaign_id: int):
    if campaign_id not in _active_invite_campaigns:
        # Still update status
        await db.update_invite_campaign_status(campaign_id, "paused")
        return {"status": "paused"}

    stop_invite_campaign(campaign_id)
    await db.update_invite_campaign_status(campaign_id, "paused")
    return {"status": "stopping"}


# ── Get campaign logs ──────────────────────────────────────────────────────────
@router.get("/invite-campaigns/{campaign_id}/logs")
async def get_invite_campaign_logs(campaign_id: int, limit: int = 200, offset: int = 0):
    logs = await db.get_invite_campaign_logs(campaign_id, limit=limit, offset=offset)
    return logs


# ── Resolve group info ─────────────────────────────────────────────────────────
class ResolveGroupRequest(BaseModel):
    identifier: str
    account_id: int | None = None

@router.post("/invite-campaigns/resolve-group")
async def resolve_group(data: ResolveGroupRequest):
    """Resolve a group/channel identifier to get its info."""
    account_id = data.account_id
    # Use first available account if not specified
    if account_id is None:
        accounts = await db.get_all_accounts()
        if not accounts:
            raise HTTPException(status_code=400, detail="No accounts available")
        account_id = accounts[0]["id"]

    info = await tg.get_channel_info(account_id, data.identifier)
    if not info.get("success"):
        raise HTTPException(status_code=400, detail=info.get("error", "Cannot resolve group"))
    return info


import asyncio
