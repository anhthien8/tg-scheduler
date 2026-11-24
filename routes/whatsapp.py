"""
WhatsApp API Routes
───────────────────
QR login for several numbers, group scraping into wa_contacts, join by invite
link, group watches (newcomers / active speakers) and outreach campaigns
(opener DM, second message once they reply).
"""
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

import wa_outreach as wo
import wa_store as store

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

_INVITE_RE = re.compile(r"[A-Za-z0-9]{16,}")  # the code is the only long alphanumeric run in an invite link


async def _bridge(method: str, path: str, json: dict | None = None, timeout: float = 60,
                  account: str | None = None):
    try:
        return await wo.call(method, path, json, timeout=timeout, account=account or None)
    except wo.BridgeError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))


def _invite_code(link: str) -> str:
    m = _INVITE_RE.search(link.strip())
    if not m:
        raise HTTPException(422, "Link mời không hợp lệ (dạng https://chat.whatsapp.com/…)")
    return m.group(0)


# ── Numbers (accounts) ────────────────────────────────────────────────────────

@router.get("/accounts")
async def list_accounts():
    """Bridge login state merged with per-number settings, today's usage and health."""
    st = await _bridge("GET", "/status")
    bridge = {a["id"]: a for a in st.get("accounts") or []}
    await store.sync_accounts(list(bridge.values()))
    out = []
    for acc in await store.list_accounts():
        b = bridge.get(acc["id"])
        if not b:
            continue  # session removed on the bridge side
        cap, note = wo.account_cap(acc)
        out.append({**acc, "logged_in": b["logged_in"], "login": b["login"], "cap_today": cap, "cap_note": note,
                    "restricted": bool(b.get("restricted_until")),
                    "resting": bool(acc.get("paused_until") and not wo._due(acc["paused_until"]))})
    # Slots still waiting for their first QR scan aren't in the DB yet.
    known = {a["id"] for a in out}
    out += [{"id": b["id"], "logged_in": False, "login": b["login"], "pending": True}
            for b in bridge.values() if b["id"] not in known]
    return out


@router.post("/accounts")
async def add_account():
    return await _bridge("POST", "/accounts")


@router.post("/accounts/{acc_id}/login")
async def relogin_account(acc_id: str):
    return await _bridge("POST", f"/accounts/{acc_id}/login")


class AccountSettings(BaseModel):
    enabled: bool = True
    daily_cap: int = Field(default=20, ge=1, le=60)


@router.put("/accounts/{acc_id}")
async def update_account(acc_id: str, body: AccountSettings):
    if not await store.get_account(acc_id):
        raise HTTPException(404, "Không tìm thấy số")
    await store.set_account_state(acc_id, enabled=int(body.enabled), daily_cap=body.daily_cap)
    return {"success": True}


@router.post("/accounts/{acc_id}/resume")
async def resume_account(acc_id: str):
    await store.set_account_state(acc_id, paused_until=None, consecutive_errors=0, last_error=None)
    return {"success": True}


@router.delete("/accounts/{acc_id}")
async def remove_account(acc_id: str):
    await _bridge("DELETE", f"/accounts/{acc_id}")
    await store.delete_account(acc_id)
    return {"success": True}


# ── Groups ────────────────────────────────────────────────────────────────────

@router.get("/groups")
async def groups(account: str = "", refresh: bool = False):
    return await _bridge("GET", "/groups" + ("?refresh=1" if refresh else ""), timeout=120, account=account)


class GroupScrape(BaseModel):
    group_name: str = ""
    account: str = ""


@router.post("/groups/{group_id}/scrape")
async def scrape_group(group_id: str, body: GroupScrape):
    res = await _bridge("GET", f"/groups/{quote(group_id, safe='@')}/members", timeout=120, account=body.account)
    name = res["group"].get("name") or body.group_name or group_id
    counts = await store.upsert_contacts(res["members"], "group", group_id, name)
    admins = res.get("admins") or []
    await store.skip_admins(admins)
    hidden = sum(1 for m in res["members"] if not m.get("phone"))
    return {**counts, "admins_skipped": len(admins), "hidden_numbers": hidden,
            "group_total": res["group"].get("total") or 0}


@router.get("/invite")
async def invite_info(link: str, account: str = ""):
    return await _bridge("GET", f"/invite/{_invite_code(link)}", account=account)


class JoinBody(BaseModel):
    link: str
    account: str


@router.post("/groups/join")
async def join_group(body: JoinBody):
    acc = await store.get_account(body.account)
    if not acc:
        raise HTTPException(404, "Không tìm thấy số")
    if int(acc.get("joins_today") or 0) >= wo.MAX_JOINS_PER_DAY:
        raise HTTPException(429, f"Số này đã vào {wo.MAX_JOINS_PER_DAY} nhóm hôm nay — vào thêm dễ bị WhatsApp khoá")
    res = await _bridge("POST", "/groups/join", {"code": _invite_code(body.link)}, account=body.account)
    await store.add_log(None, res.get("group_id") or "", "", "join", True, account_id=body.account)
    return res


# ── Watches ───────────────────────────────────────────────────────────────────

class WatchPayload(BaseModel):
    group_name: str = ""
    account: str = Field(min_length=1)
    campaign_id: int | None = None
    watch_joins: bool = False
    track_active: bool = False


@router.get("/watches")
async def list_watches():
    return await store.list_watches()


@router.put("/watches/{group_id}")
async def save_watch(group_id: str, body: WatchPayload):
    if not body.watch_joins and not body.track_active:
        await store.delete_watch(group_id)
        return {"success": True}
    c = await store.get_campaign(body.campaign_id) if body.campaign_id else None
    if body.watch_joins and not c:
        raise HTTPException(422, "Chọn chiến dịch để nhắn người mới vào nhóm")
    await store.save_watch(group_id, body.group_name, body.account, body.campaign_id if c else None,
                           body.watch_joins, body.track_active)
    # a campaign limited to some sources must also include this group's newcomers
    if body.watch_joins and c["sources"] and f"join:{group_id}" not in c["sources"]:
        await store.update_campaign(c["id"], {**c, "sources": c["sources"] + [f"join:{group_id}"]})
    await wo.start()
    return {"success": True}


@router.delete("/watches/{group_id}")
async def delete_watch(group_id: str):
    await store.delete_watch(group_id)
    return {"success": True}


# ── Contacts ──────────────────────────────────────────────────────────────────

@router.get("/sources")
async def sources():
    return await store.list_sources()


@router.get("/contacts")
async def contacts(status: str = "", source: str = "", q: str = "", limit: int = 200, offset: int = 0):
    return await store.list_contacts(status, source, q, min(limit, 1000), offset)


class BlockBody(BaseModel):
    blocked: bool = True


@router.post("/contacts/{uid}/block")
async def block_contact(uid: str, body: BlockBody):
    await store.block_contact(uid, body.blocked)
    return {"success": True}


@router.get("/stats")
async def stats():
    return await store.contact_stats()


# ── Campaigns ─────────────────────────────────────────────────────────────────

class CampaignPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sources: list[str] = []
    accounts: list[str] = []    # number ids allowed to send; empty = every enabled number
    countries: list[str] = []   # calling codes; empty = anyone
    open_msg: str = Field(min_length=1, max_length=2000)
    reply_msg: str = Field(default="", max_length=2000)
    daily_limit: int = Field(default=20, ge=1, le=500)  # campaign total across numbers
    min_delay_sec: int = Field(default=300, ge=60)
    max_delay_sec: int = Field(default=900, ge=60)
    start_hour: int = Field(default=8, ge=0, le=23)
    end_hour: int = Field(default=22, ge=0, le=24)

    @field_validator("countries")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        codes = [c.strip().lstrip("+") for c in v if c.strip()]
        if any(not re.fullmatch(r"\d{1,4}", c) for c in codes):
            raise ValueError("Đầu số quốc gia chỉ gồm 1–4 chữ số, ví dụ 84")
        return codes


@router.get("/campaigns")
async def list_campaigns():
    return await store.list_campaigns()


@router.post("/campaigns")
async def create_campaign(p: CampaignPayload):
    return {"id": await store.create_campaign(p.model_dump())}


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
        raise HTTPException(409, "Chưa có số WhatsApp nào kết nối")
    await store.set_campaign_state(cid, status="running", last_error=None)
    await wo.start()
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
