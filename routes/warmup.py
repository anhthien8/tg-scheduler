"""
Warmup / Group Seeding API routes.
"""
import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import database as db
from database import get_db
import warmup_engine

logger = logging.getLogger("tg-scheduler.warmup")
router = APIRouter(prefix="/api/warmup", tags=["warmup"])


# ── Request Models ──────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name: str
    chat_id: str
    chat_title: Optional[str] = ""
    chat_username: Optional[str] = ""


class ScriptCreate(BaseModel):
    content: str
    msg_type: Optional[str] = "text"
    use_ai_remix: Optional[int] = 1
    sort_order: Optional[int] = 0


class JobCreate(BaseModel):
    group_id: int
    account_ids: list[int] = []
    interval_min: Optional[int] = 30
    interval_max: Optional[int] = 120
    daily_post_limit: Optional[int] = 10
    schedule_start: Optional[str] = "09:00"
    schedule_end: Optional[str] = "22:00"
    ai_write: Optional[bool] = True


class JobPatch(BaseModel):
    account_ids: Optional[list[int]] = None
    ai_write: Optional[bool] = None


class OneClick(BaseModel):
    link: Optional[str] = ""
    group_id: Optional[int] = None
    account_ids: Optional[list[int]] = None  # None = pick healthy accounts automatically


ONE_CLICK_MAX_ACCOUNTS = 10
_UNSAFE_VI = {"paused": "tạm dừng", "flagged": "bị flag", "PeerFlood": "PeerFlood",
              "session error": "lỗi session", "not found": "không tìm thấy"}


def _brand(name: str) -> str:
    """'Coy | Weex' -> 'WEEX' (same rule as the dashboard's brand chips)."""
    parts = (name or "").split("|")
    if len(parts) < 2:
        return ""
    return re.sub(r"[\W_]+", " ", " ".join(parts[1:])).strip().upper()


def _link_key(link: str) -> str:
    """'https://t.me/BloFin_Official/12' / '@BloFin_Official' -> 'blofin_official'."""
    t = re.sub(r"^(https?://)?(www\.)?(t|telegram)\.(me|dog)/", "", (link or "").strip(), flags=re.I)
    return t.lstrip("@").split("/")[0].split("?")[0].lower()


def _find_group(link: str, groups: list[dict]) -> dict | None:
    key = _link_key(link)
    if not key:
        return None
    for g in groups:
        if key.lstrip("-").isdigit():
            if str(g.get("chat_id")) == key or str(g.get("chat_id")) == link.strip():
                return g
        elif (g.get("chat_username") or "").lower() == key:
            return g
    return None


async def _account_pool(group: dict | None = None, hint: str = "",
                        current: list[int] | None = None) -> list[dict]:
    """Every account with whether warmup can use it now and whether 1-click picks it.

    Picked: healthy + connected + not graduated, max 10. Accounts whose name
    carries another brand (e.g. 'Coy | Weex' in a BloFin group) are not picked
    by default: admins tend to kick competitors. `current` (the job's accounts)
    wins over the automatic pick when given.
    """
    import telegram_client as tg
    accounts = await db.get_all_accounts()
    async with get_db() as conn:
        cur = await conn.execute("SELECT account_id FROM warmup_progress WHERE graduated_at IS NOT NULL")
        graduated = {r[0] for r in await cur.fetchall()}
    hay = " ".join(str((group or {}).get(k) or "") for k in ("name", "chat_title", "chat_username")) + " " + (hint or "")
    hay = re.sub(r"[\W_]+", " ", hay).upper()
    out = []
    for a in accounts:
        aid = a["id"]
        why = None
        if not a.get("is_logged_in"):
            why = "chưa đăng nhập"
        elif getattr(tg, "_session_errors", {}).get(aid):
            why = "lỗi session"
        else:
            u = warmup_engine._unsafe_reason(a)
            if u:
                why = _UNSAFE_VI.get(u, u)
        if not why:
            c = tg.get_client(aid)
            if not c or not c.is_connected():
                why = "chưa kết nối"
        if not why and aid in graduated:
            why = "đã tốt nghiệp warmup"
        brand = _brand(a.get("name"))
        match = bool(brand) and f" {brand} " in f" {hay} "
        out.append({"id": aid, "name": a.get("name") or f"#{aid}", "brand": brand, "ok": why is None,
                    "why": why, "brand_match": match, "other_brand": bool(brand) and not match})
    if current:
        cur_set = {int(x) for x in current}
        picks = {x["id"] for x in out if x["ok"] and x["id"] in cur_set}
    else:
        ranked = sorted((x for x in out if x["ok"] and not x["other_brand"]),
                        key=lambda x: (not x["brand_match"], x["id"]))
        picks = {x["id"] for x in ranked[:ONE_CLICK_MAX_ACCOUNTS]}
    for x in out:
        x["pick"] = x["id"] in picks
    return out


async def _start_warnings(job: dict) -> list[str]:
    """What will (not) happen once the job runs, so 'Chạy' is never a silent no-op."""
    import telegram_client as tg
    warnings = []
    if job.get("ai_write"):
        import message_queue
        if not await message_queue._remix_config({"id": f"warmup-{job['id']}"}):
            warnings.append("Chưa cấu hình AI (Cài đặt → AI) → chưa đăng tin, chỉ đọc tin / thả cảm xúc")
    elif not await db.get_warmup_scripts(job["group_id"]):
        warnings.append("Nhóm chưa có kịch bản → chỉ đọc tin / thả cảm xúc / xem profile, chưa đăng tin "
                        "(bật ✨ AI tự viết hoặc bấm vào tên nhóm để thêm kịch bản)")
    if not warmup_engine.in_window(datetime.now().strftime("%H:%M"), job["schedule_start"], job["schedule_end"]):
        warnings.append(f"Đang ngoài khung giờ → bắt đầu lúc {job['schedule_start']}")
    acc_ids = json.loads(job.get("account_ids") or "[]")
    offline = [a for a in acc_ids if not tg.get_client(a)]
    if offline:
        warnings.append(f"{len(offline)}/{len(acc_ids)} account chưa kết nối sẽ bị bỏ qua")
    return warnings


# ── Group Endpoints ─────────────────────────────────────────────────────────

@router.get("/groups")
async def list_groups():
    groups = await db.get_warmup_groups()
    return {"groups": groups}


@router.post("/groups")
async def add_group(req: GroupCreate):
    group_id = await db.create_warmup_group(req.model_dump())
    return {"id": group_id, "success": True}


class GroupQuickAdd(BaseModel):
    link: str
    name: Optional[str] = ""


@router.post("/groups/quick-add")
async def quick_add_group(req: GroupQuickAdd):
    """Add a warmup group from just a t.me link / @username / chat id."""
    group, created = await _resolve_group(req.link, req.name, reuse=False)
    return {"success": True, **group}


async def _resolve_group(link: str, name: str = "", reuse: bool = True) -> tuple[dict, bool]:
    """(group, created). Resolves the link with the first connected account
    (joining public groups or invite links if needed) so the user never has to
    look up the chat id. reuse=True returns an existing group instead of a 400."""
    import telegram_client as tg
    from telethon import utils as tl_utils
    from reaction_watcher import _join_or_get_entity

    link = (link or "").strip()
    if not link:
        raise HTTPException(status_code=400, detail="Thiếu link nhóm")
    if reuse:
        known = _find_group(link, await db.get_warmup_groups())
        if known:
            return known, False
    target = int(link) if link.lstrip("-").isdigit() else link
    last_err = "Không có tài khoản nào đang kết nối"
    for acc_id, client in list(tg._clients.items()):
        if not client or not client.is_connected():
            continue
        try:
            if isinstance(target, int):
                entity = await asyncio.wait_for(client.get_entity(target), timeout=20.0)
            else:
                entity = await asyncio.wait_for(_join_or_get_entity(client, target), timeout=30.0)
            if entity is None:
                raise ValueError("Không xác định được nhóm từ link")
        except Exception as e:
            last_err = str(e) or type(e).__name__
            continue
        title = getattr(entity, "title", "") or ""
        uname = getattr(entity, "username", "") or ""
        data = {
            "name": (name or "").strip() or title or uname or link,
            "chat_id": str(tl_utils.get_peer_id(entity)),
            "chat_title": title,
            "chat_username": uname,
        }
        for g in await db.get_warmup_groups():
            if str(g.get("chat_id")) == data["chat_id"]:
                if reuse:
                    return g, False
                raise HTTPException(status_code=400, detail=f"Nhóm đã có trong danh sách: {g.get('name')}")
        group_id = await db.create_warmup_group(data)
        tg._dialogs_cache.pop(acc_id, None)
        return {"id": group_id, **data}, True
    raise HTTPException(status_code=400, detail=f"Không lấy được nhóm: {last_err}")


@router.delete("/groups/{group_id}")
async def remove_group(group_id: int):
    # Stop any running jobs for this group first
    jobs = await db.get_warmup_jobs(group_id=group_id)
    for job in jobs:
        if warmup_engine.is_job_running(job["id"]):
            warmup_engine.stop_warmup_job(job["id"])
    await db.delete_warmup_group(group_id)
    return {"success": True}


# ── Script Endpoints ────────────────────────────────────────────────────────

@router.get("/groups/{group_id}/scripts")
async def list_scripts(group_id: int):
    scripts = await db.get_warmup_scripts(group_id)
    return {"scripts": scripts}


@router.post("/groups/{group_id}/scripts")
async def add_script(group_id: int, req: ScriptCreate):
    group = await db.get_warmup_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    script_id = await db.create_warmup_script(
        group_id, req.content, req.msg_type, req.use_ai_remix, req.sort_order
    )
    return {"id": script_id, "success": True}


@router.delete("/scripts/{script_id}")
async def remove_script(script_id: int):
    await db.delete_warmup_script(script_id)
    return {"success": True}


# ── Job Endpoints ───────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(group_id: Optional[int] = Query(None)):
    jobs = await db.get_warmup_jobs(group_id=group_id)
    # Enrich with running state + what the engine is doing right now
    script_counts: dict[int, int] = {}
    for job in jobs:
        gid = job["group_id"]
        if gid not in script_counts:
            script_counts[gid] = len(await db.get_warmup_scripts(gid))
        job["scripts_count"] = script_counts[gid]
        job["is_running"] = warmup_engine.is_job_running(job["id"])
        job["state"] = warmup_engine.job_state(job["id"])
    return {"jobs": jobs}


@router.post("/jobs")
async def create_job(req: JobCreate):
    group = await db.get_warmup_group(req.group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    job_id = await db.create_warmup_job(req.model_dump())
    return {"id": job_id, "success": True}


@router.patch("/jobs/{job_id}")
async def patch_job(job_id: int, req: JobPatch):
    """Toggle ✨ AI tự viết / change accounts. A running job picks it up on its next turn."""
    if not await db.get_warmup_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    if req.account_ids is not None and not req.account_ids:
        raise HTTPException(status_code=400, detail="Cần ít nhất 1 account")
    await db.update_warmup_job_fields(job_id, account_ids=req.account_ids, ai_write=req.ai_write)
    return {"success": True}


@router.post("/jobs/{job_id}/start")
async def start_job(job_id: int):
    job = await db.get_warmup_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if warmup_engine.is_job_running(job_id):
        raise HTTPException(status_code=400, detail="Job đang chạy rồi")
    if not json.loads(job.get("account_ids") or "[]"):
        raise HTTPException(status_code=400, detail="Job chưa có account nào")
    warnings = await _start_warnings(job)
    warmup_engine.start_job(job_id)
    return {"success": True, "message": "Đã bật job warmup", "warnings": warnings}


# ── 1-click warmup ──────────────────────────────────────────────────────────

@router.get("/eligible-accounts")
async def eligible_accounts(group_id: Optional[int] = Query(None), link: Optional[str] = Query("")):
    """Accounts for the 1-click modal: which ones warmup can use and which are pre-picked."""
    group = await db.get_warmup_group(group_id) if group_id else None
    if not group and (link or "").strip():
        group = _find_group(link, await db.get_warmup_groups())  # known group: keep its job's accounts
    current, job_id = None, None
    if group:
        jobs = await db.get_warmup_jobs(group_id=group["id"])
        job = next((j for j in jobs if warmup_engine.is_job_running(j["id"])), jobs[0] if jobs else None)
        if job:
            current, job_id = json.loads(job.get("account_ids") or "[]"), job["id"]
    pool = await _account_pool(group, hint=_link_key(link or ""), current=current)
    return {"accounts": pool, "picked": [x["id"] for x in pool if x["pick"]], "max": ONE_CLICK_MAX_ACCOUNTS,
            "job_id": job_id}


@router.post("/one-click")
async def one_click(req: OneClick):
    """Link (or existing group) → group + job with ✨ AI tự viết → running. Re-clicking
    reuses the same group/job instead of creating duplicates."""
    if req.group_id:
        group = await db.get_warmup_group(req.group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        created = False
    elif (req.link or "").strip():
        group, created = await _resolve_group(req.link, reuse=True)
    else:
        raise HTTPException(status_code=400, detail="Dán link nhóm (t.me/… hoặc @username)")

    jobs = await db.get_warmup_jobs(group_id=group["id"])
    job = next((j for j in jobs if warmup_engine.is_job_running(j["id"])), jobs[0] if jobs else None)
    current = json.loads(job.get("account_ids") or "[]") if job and req.account_ids is None else None
    pool = await _account_pool(group, hint=_link_key(req.link or ""), current=current)
    by_id = {x["id"]: x for x in pool}
    if req.account_ids is not None:
        chosen = [a for a in dict.fromkeys(int(x) for x in req.account_ids) if by_id.get(a, {}).get("ok")]
        skipped = [{"id": a, "name": by_id.get(a, {}).get("name", f"#{a}"),
                    "why": by_id.get(a, {}).get("why") or "không tồn tại"}
                   for a in dict.fromkeys(int(x) for x in req.account_ids) if not by_id.get(a, {}).get("ok")]
    else:
        chosen = [x["id"] for x in pool if x["pick"]]
        if job and not chosen:  # the job's old accounts are all unusable now → pick fresh ones
            pool = await _account_pool(group, hint=_link_key(req.link or ""))
            chosen = [x["id"] for x in pool if x["pick"]]
        skipped = []
    chosen = chosen[:ONE_CLICK_MAX_ACCOUNTS]
    if not chosen:
        raise HTTPException(status_code=400, detail="Không có account khỏe nào đang kết nối để warmup")

    if job:
        await db.update_warmup_job_fields(job["id"], account_ids=chosen, ai_write=True)
        job_id = job["id"]
    else:
        job_id = await db.create_warmup_job({"group_id": group["id"], "account_ids": chosen, "ai_write": 1})
    job = await db.get_warmup_job(job_id)

    running = warmup_engine.is_job_running(job_id)
    warnings = await _start_warnings(job)
    if not running:
        warmup_engine.start_job(job_id)
    names = [by_id[a]["name"] for a in chosen if a in by_id]
    msg = (f"{'Đã thêm' if created else 'Dùng'} nhóm {group.get('name')} · {len(chosen)} account · ✨ AI tự viết · "
           + ("job đang chạy, đã cập nhật" if running else "đã bật"))
    return {"success": True, "message": msg, "group": group, "group_created": created, "job_id": job_id,
            "accounts": [{"id": a, "name": n} for a, n in zip(chosen, names)], "skipped": skipped,
            "warnings": warnings}


@router.post("/jobs/{job_id}/stop")
async def stop_job(job_id: int):
    warmup_engine.stop_warmup_job(job_id)
    return {"success": True, "message": "Stop signal sent"}


@router.delete("/jobs/{job_id}")
async def remove_job(job_id: int):
    if warmup_engine.is_job_running(job_id):
        warmup_engine.stop_warmup_job(job_id)
        await asyncio.sleep(1)  # Give it a moment to stop
    await db.delete_warmup_job(job_id)
    return {"success": True}


# ── Log Endpoints ───────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/logs")
async def get_logs(job_id: int, limit: int = Query(100)):
    logs = await db.get_warmup_logs(job_id=job_id, limit=limit)
    return {"logs": logs}


# ── Progress Endpoints (v2 Dashboard) ──────────────────────────────────────

@router.get("/progress")
async def list_all_progress():
    """Return warmup progress for ALL accounts that have records, with account info."""
    async with get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("""
            SELECT wp.*,
                   a.name   AS account_name,
                   a.phone  AS account_phone,
                   CASE
                       WHEN a.is_flagged = 1 THEN 'flagged'
                       WHEN a.is_paused = 1 THEN 'paused'
                       WHEN a.is_logged_in = 1 THEN 'active'
                       ELSE 'inactive'
                   END AS account_status,
                   COALESCE(
                       CAST((julianday('now') - julianday(wp.started_at)) AS INTEGER),
                       0
                   ) AS days_in_warmup,
                   (SELECT COUNT(*) FROM warmup_action_log wal
                    WHERE wal.account_id = wp.account_id
                    AND wal.status = 'flood'
                    AND wal.created_at >= datetime('now', '-3 days')
                   ) AS flood_count_3d
            FROM warmup_progress wp
            LEFT JOIN accounts a ON a.id = wp.account_id
            ORDER BY wp.updated_at DESC
        """)
        rows = await cursor.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["graduated"] = d.get("graduated_at") is not None
        result.append(d)
    return {"progress": result}



@router.get("/progress/{account_id}")
async def get_account_progress(account_id: int):
    """Return detailed warmup progress + aggregated stats for one account."""
    progress = await db.get_warmup_progress(account_id)
    if not progress:
        raise HTTPException(status_code=404, detail="No warmup progress for this account")
    stats = await db.get_warmup_stats(account_id)
    recent_floods = await db.get_recent_warmup_floods(account_id, days=3)
    # Fetch account name/phone
    async with get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """SELECT name, phone,
                      CASE
                          WHEN is_flagged = 1 THEN 'flagged'
                          WHEN is_paused = 1 THEN 'paused'
                          WHEN is_logged_in = 1 THEN 'active'
                          ELSE 'inactive'
                      END AS status
               FROM accounts WHERE id = ?""",
            (account_id,)
        )
        acct = await cursor.fetchone()
    account_info = dict(acct) if acct else {}
    return {
        "progress": progress,
        "stats": stats,
        "recent_floods_3d": recent_floods,
        "account": account_info,
    }


# ── Action Log Endpoints (v2 Dashboard) ────────────────────────────────────

@router.get("/action-logs")
async def list_action_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    account_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
):
    """Return recent warmup action logs with pagination and optional filters."""
    conditions = []
    params: list = []
    if account_id is not None:
        conditions.append("wal.account_id = ?")
        params.append(account_id)
    if status is not None:
        conditions.append("wal.status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    async with get_db() as conn:
        conn.row_factory = aiosqlite.Row
        # Total count for pagination
        count_cursor = await conn.execute(
            f"SELECT COUNT(*) AS total FROM warmup_action_log wal {where}",
            tuple(params),
        )
        total = (await count_cursor.fetchone())["total"]
        # Fetch page
        cursor = await conn.execute(
            f"""SELECT wal.*,
                       a.name AS account_name
                FROM warmup_action_log wal
                LEFT JOIN accounts a ON a.id = wal.account_id
                {where}
                ORDER BY wal.created_at DESC
                LIMIT ? OFFSET ?""",
            tuple(params) + (limit, offset),
        )
        rows = await cursor.fetchall()
    return {
        "logs": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/action-logs/summary")
async def action_logs_summary():
    """Return summary stats: actions by type, status breakdown, per-account counts."""
    async with get_db() as conn:
        conn.row_factory = aiosqlite.Row
        # Actions by type
        cur1 = await conn.execute("""
            SELECT action_type, COUNT(*) AS cnt
            FROM warmup_action_log
            GROUP BY action_type
            ORDER BY cnt DESC
        """)
        by_type = {r["action_type"]: r["cnt"] for r in await cur1.fetchall()}
        # Actions by status
        cur2 = await conn.execute("""
            SELECT status, COUNT(*) AS cnt
            FROM warmup_action_log
            GROUP BY status
            ORDER BY cnt DESC
        """)
        by_status = {r["status"]: r["cnt"] for r in await cur2.fetchall()}
        # Per-account counts (top 50)
        cur3 = await conn.execute("""
            SELECT wal.account_id,
                   a.name AS account_name,
                   COUNT(*) AS total_actions,
                   SUM(CASE WHEN wal.status = 'success' THEN 1 ELSE 0 END) AS success,
                   SUM(CASE WHEN wal.status = 'error'   THEN 1 ELSE 0 END) AS errors,
                   SUM(CASE WHEN wal.status = 'flood'   THEN 1 ELSE 0 END) AS floods
            FROM warmup_action_log wal
            LEFT JOIN accounts a ON a.id = wal.account_id
            GROUP BY wal.account_id
            ORDER BY total_actions DESC
            LIMIT 50
        """)
        per_account = [dict(r) for r in await cur3.fetchall()]
        # Scalar summary stats in one query
        cur4 = await conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM warmup_action_log
                 WHERE created_at >= datetime('now', '+7 hours', 'start of day', '-7 hours')) AS total_today,
                (SELECT COUNT(*) FROM warmup_progress WHERE graduated_at IS NULL) AS accounts_active,
                (SELECT COUNT(*) FROM warmup_progress WHERE graduated_at IS NOT NULL) AS accounts_graduated,
                (SELECT COUNT(*) FROM warmup_action_log
                 WHERE status = 'flood' AND created_at >= datetime('now', '-3 days')) AS flood_events_3d
        """)
        scalar = dict(await cur4.fetchone())
    return {
        "by_type": by_type,
        "by_status": by_status,
        "per_account": per_account,
        "total_today": scalar["total_today"],
        "accounts_active": scalar["accounts_active"],
        "accounts_graduated": scalar["accounts_graduated"],
        "flood_events_3d": scalar["flood_events_3d"],
    }

