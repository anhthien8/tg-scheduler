"""
routes/engagement.py — API endpoints for the Community Engagement Engine.

CRUD for engagement campaigns, join stagger, logs, stats, and consent management.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import database as db

router = APIRouter(prefix="/api/engagement", tags=["engagement"])
logger = logging.getLogger("tg-scheduler.engagement.api")

# Hard caps from design doc §4.1
MAX_REPLIES_PER_HOUR_HARD_CAP = 6
MAX_REPLIES_PER_DAY_HARD_CAP = 20


# ── Request schemas ────────────────────────────────────────────────────────────

class CampaignCreateRequest(BaseModel):
    name: str
    group_ids: list[int] = []
    group_titles: list[str] = []
    observer_account_ids: list[int] = []
    reply_account_ids: list[int] = []
    ai_agent_id: int | None = None
    knowledge_extra: str = ""
    reply_mode: str = "reactive"
    trigger_keywords: list[str] = []
    max_replies_per_hour_per_group: int = 4
    max_replies_per_day_per_group: int = 12
    min_gap_seconds: int = 180
    schedule_start: str = "13:00"
    schedule_end: str = "23:00"
    active_days: list[int] = [1, 2, 3, 4, 5, 6, 7]
    signal_group_link: str = ""
    signal_group_title: str = ""
    operator_consent_confirmed: int = 0
    operator_consent_by: str = ""


class CampaignUpdateRequest(BaseModel):
    name: str | None = None
    group_ids: list[int] | None = None
    group_titles: list[str] | None = None
    observer_account_ids: list[int] | None = None
    reply_account_ids: list[int] | None = None
    ai_agent_id: int | None = None
    knowledge_extra: str | None = None
    reply_mode: str | None = None
    trigger_keywords: list[str] | None = None
    max_replies_per_hour_per_group: int | None = None
    max_replies_per_day_per_group: int | None = None
    min_gap_seconds: int | None = None
    schedule_start: str | None = None
    schedule_end: str | None = None
    active_days: list[int] | None = None
    signal_group_link: str | None = None
    signal_group_title: str | None = None
    status: str | None = None
    operator_consent_confirmed: int | None = None
    operator_consent_by: str | None = None


class JoinStaggerRequest(BaseModel):
    account_ids: list[int]
    group_ids: list[int]
    campaign_id: int | None = None


# ── Validation helpers ─────────────────────────────────────────────────────────

def _validate_rate_limits(data: dict) -> None:
    """Validate rate limit values against hard caps."""
    if "max_replies_per_hour_per_group" in data:
        v = data["max_replies_per_hour_per_group"]
        if not (1 <= v <= MAX_REPLIES_PER_HOUR_HARD_CAP):
            raise HTTPException(
                status_code=400,
                detail=f"max_replies_per_hour_per_group must be 1-{MAX_REPLIES_PER_HOUR_HARD_CAP}"
            )
    if "max_replies_per_day_per_group" in data:
        v = data["max_replies_per_day_per_group"]
        if not (1 <= v <= MAX_REPLIES_PER_DAY_HARD_CAP):
            raise HTTPException(
                status_code=400,
                detail=f"max_replies_per_day_per_group must be 1-{MAX_REPLIES_PER_DAY_HARD_CAP}"
            )
    if "min_gap_seconds" in data:
        if data["min_gap_seconds"] < 60:
            raise HTTPException(status_code=400, detail="min_gap_seconds must be >= 60")


def _validate_schedule(data: dict) -> None:
    """Validate schedule fields."""
    for field in ("schedule_start", "schedule_end"):
        if field in data and data[field]:
            try:
                datetime.strptime(data[field], "%H:%M")
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{field} must be HH:MM format")
    if "active_days" in data:
        for d in data["active_days"]:
            if d not in range(1, 8):
                raise HTTPException(status_code=400, detail="active_days values must be 1-7")


# ── CRUD endpoints ─────────────────────────────────────────────────────────────

@router.get("/campaigns")
async def list_campaigns():
    campaigns = await db.get_all_engagement_campaigns()
    return {"campaigns": campaigns}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int):
    campaign = await db.get_engagement_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.post("/campaigns")
async def create_campaign(req: CampaignCreateRequest):
    data = req.model_dump()
    _validate_rate_limits(data)
    _validate_schedule(data)
    if data.get("reply_mode") not in ("reactive",):
        raise HTTPException(status_code=400, detail="Only 'reactive' mode is supported in v1")

    # Set consent timestamp if confirmed
    if data.get("operator_consent_confirmed"):
        data["operator_consent_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    campaign_id = await db.create_engagement_campaign(data)
    return {"id": campaign_id, "status": "created"}


@router.put("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: int, req: CampaignUpdateRequest):
    existing = await db.get_engagement_campaign(campaign_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Campaign not found")

    data = {k: v for k, v in req.model_dump().items() if v is not None}
    _validate_rate_limits(data)
    _validate_schedule(data)

    if "reply_mode" in data and data["reply_mode"] not in ("reactive",):
        raise HTTPException(status_code=400, detail="Only 'reactive' mode is supported in v1")

    # Handle consent update
    if "operator_consent_confirmed" in data:
        if data["operator_consent_confirmed"]:
            data["operator_consent_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        else:
            # Revoke consent → pause campaign
            data["operator_consent_at"] = None
            if existing.get("status") == "active":
                data["status"] = "paused"

    ok = await db.update_engagement_campaign(campaign_id, data)
    if not ok:
        raise HTTPException(status_code=500, detail="Update failed")
    return {"status": "updated"}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int):
    ok = await db.delete_engagement_campaign(campaign_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"status": "deleted"}


@router.post("/campaigns/{campaign_id}/activate")
async def activate_campaign(campaign_id: int):
    """Activate a campaign. Requires operator consent (fail-closed §4.8.1)."""
    campaign = await db.get_engagement_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not campaign.get("operator_consent_confirmed"):
        raise HTTPException(
            status_code=409,
            detail="consent_required: Operator must confirm consent before activating"
        )
    ok = await db.toggle_engagement_campaign(campaign_id, True)
    if not ok:
        raise HTTPException(status_code=500, detail="Activation failed")

    # Log consent confirmation
    await db.create_engagement_log({
        "campaign_id": campaign_id,
        "kind": "paused",
        "status": "success",
        "skip_reason": "consent_confirmed",
    })

    # Reload engine handlers
    try:
        import engagement_engine as eng
        await eng.reload_engagement_campaign(campaign_id)
    except ImportError:
        pass

    return {"status": "active"}


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int):
    ok = await db.toggle_engagement_campaign(campaign_id, False)
    if not ok:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Unregister engine handlers
    try:
        import engagement_engine as eng
        eng.unregister_engagement_campaign(campaign_id)
    except ImportError:
        pass

    return {"status": "paused"}


@router.post("/campaigns/{campaign_id}/toggle")
async def toggle_campaign(campaign_id: int, active: bool = True):
    if active:
        return await activate_campaign(campaign_id)
    return await pause_campaign(campaign_id)


# ── Join stagger endpoint ──────────────────────────────────────────────────────

_join_tasks: dict[int, asyncio.Task] = {}


@router.post("/join-stagger")
async def join_stagger(req: JoinStaggerRequest):
    """Join accounts to groups with staggered delays (15-45 min between accounts)."""
    import telegram_client as tg

    if not req.account_ids or not req.group_ids:
        raise HTTPException(status_code=400, detail="account_ids and group_ids required")

    async def _do_stagger():
        results = []
        for group_id in req.group_ids:
            for i, acc_id in enumerate(req.account_ids):
                if i > 0:
                    delay = random.uniform(900, 2700)  # 15-45 minutes
                    await asyncio.sleep(delay)
                try:
                    result = await tg.join_channel(acc_id, str(group_id))
                    results.append({"account_id": acc_id, "group_id": group_id, "status": "joined"})
                    if req.campaign_id:
                        await db.create_engagement_log({
                            "campaign_id": req.campaign_id,
                            "group_id": group_id,
                            "account_id": acc_id,
                            "kind": "join",
                            "status": "success",
                        })
                except Exception as e:
                    results.append({"account_id": acc_id, "group_id": group_id, "status": "failed",
                                    "error": str(e)})
                    if req.campaign_id:
                        await db.create_engagement_log({
                            "campaign_id": req.campaign_id,
                            "group_id": group_id,
                            "account_id": acc_id,
                            "kind": "join",
                            "status": "failed",
                            "skip_reason": str(e)[:200],
                        })
                # Delay between same account joining different groups
                if len(req.group_ids) > 1:
                    await asyncio.sleep(random.uniform(90, 240))
        return results

    task = asyncio.create_task(_do_stagger())
    task_id = id(task)
    _join_tasks[task_id] = task
    task.add_done_callback(lambda t: _join_tasks.pop(task_id, None))

    return {
        "status": "started",
        "task_id": task_id,
        "message": f"Joining {len(req.account_ids)} accounts to {len(req.group_ids)} groups with staggered delays",
    }


# ── Logs endpoint ──────────────────────────────────────────────────────────────

@router.get("/logs")
async def get_logs(
    campaign_id: int | None = None,
    status: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    logs = await db.get_engagement_logs(
        campaign_id=campaign_id, limit=limit, offset=offset,
        status=status, kind=kind,
    )
    total = await db.get_engagement_log_count(
        campaign_id=campaign_id, status=status, kind=kind,
    )
    return {"logs": logs, "total": total}


# ── Stats endpoint ─────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    campaign_id: int | None = None,
    days: int = Query(default=7, le=90),
):
    stats = await db.get_engagement_stats(campaign_id=campaign_id, days=days)
    return stats
