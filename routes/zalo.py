"""
Zalo API Routes
───────────────
QR login, group/event scraping into zalo_contacts, outreach campaigns
(friend request + greeting after accept).
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import zalo_outreach as zo
import zalo_store as store

logger = logging.getLogger("tg-scheduler.zalo")

router = APIRouter(prefix="/api/zalo", tags=["zalo"])


async def _bridge(method: str, path: str, json: dict | None = None, timeout: float = 60):
    try:
        return await zo.call(method, path, json, timeout=timeout)
    except zo.BridgeError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))


# ── Account ───────────────────────────────────────────────────────────────────

@router.get("/status")
async def status():
    return await _bridge("GET", "/status")


@router.post("/login")
async def start_login():
    return await _bridge("POST", "/login/qr")


@router.post("/logout")
async def logout():
    return await _bridge("POST", "/logout")


# ── Sources ───────────────────────────────────────────────────────────────────

@router.get("/groups")
async def groups():
    return await _bridge("GET", "/groups", timeout=120)


@router.get("/groups/{group_id}/events")
async def group_events(group_id: str):
    return await _bridge("GET", f"/groups/{group_id}/events")


class GroupScrape(BaseModel):
    group_name: str = ""


@router.post("/groups/{group_id}/scrape")
async def scrape_group(group_id: str, body: GroupScrape):
    res = await _bridge("GET", f"/groups/{group_id}/members", timeout=600)
    name = res["group"].get("name") or body.group_name or group_id
    counts = await store.upsert_contacts(res["members"], "group", group_id, name)
    total = res["group"].get("total") or 0
    return {**counts, "group_total": total,
            "partial": bool(total and counts["total"] < total - 1)}


class EventScrape(BaseModel):
    title: str = ""
    group_name: str = ""


@router.post("/events/{event_id}/scrape")
async def scrape_event(event_id: str, body: EventScrape):
    res = await _bridge("GET", f"/events/{event_id}/responses", timeout=600)
    label = body.title or event_id
    if body.group_name:
        label = f"{label} ({body.group_name})"
    return await store.upsert_contacts(res["accept"], "event", event_id, f"📅 {label}")


@router.get("/sources")
async def sources():
    return await store.list_sources()


@router.get("/contacts")
async def contacts(status: str = "", source: str = "", q: str = "", limit: int = 200, offset: int = 0):
    return await store.list_contacts(status, source, q, min(limit, 1000), offset)


@router.get("/stats")
async def stats():
    return await store.contact_stats()


# ── Campaigns ─────────────────────────────────────────────────────────────────

class CampaignPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sources: list[str] = []
    friend_msg: str = Field(default="", max_length=150)  # Zalo caps the friend-request note
    greet_msg: str = Field(default="", max_length=2000)
    daily_limit: int = Field(default=20, ge=1, le=100)
    min_delay_sec: int = Field(default=300, ge=60)
    max_delay_sec: int = Field(default=900, ge=60)
    start_hour: int = Field(default=8, ge=0, le=23)
    end_hour: int = Field(default=22, ge=0, le=24)


@router.get("/campaigns")
async def list_campaigns():
    return await store.list_campaigns()


@router.post("/campaigns")
async def create_campaign(p: CampaignPayload):
    cid = await store.create_campaign(p.model_dump())
    return {"id": cid}


@router.put("/campaigns/{cid}")
async def update_campaign(cid: int, p: CampaignPayload):
    if not await store.get_campaign(cid):
        raise HTTPException(404, "Không tìm thấy chiến dịch")
    await store.update_campaign(cid, p.model_dump())
    return {"success": True}


@router.post("/campaigns/{cid}/start")
async def start_campaign(cid: int):
    if not await store.get_campaign(cid):
        raise HTTPException(404, "Không tìm thấy chiến dịch")
    st = await _bridge("GET", "/status")
    if not st.get("logged_in"):
        raise HTTPException(409, "Zalo chưa đăng nhập")
    await store.set_campaign_state(cid, status="running", consecutive_errors=0, last_error=None)
    await zo.start()
    return {"success": True}


@router.post("/campaigns/{cid}/pause")
async def pause_campaign(cid: int):
    await store.set_campaign_state(cid, status="paused")
    return {"success": True}


@router.delete("/campaigns/{cid}")
async def delete_campaign(cid: int):
    await store.delete_campaign(cid)
    return {"success": True}


@router.get("/campaigns/{cid}/logs")
async def campaign_logs(cid: int, limit: int = 100):
    return await store.list_logs(cid, min(limit, 500))
