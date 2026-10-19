"""
Warmup / Group Seeding API routes.
"""
import asyncio
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
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


# ── Group Endpoints ─────────────────────────────────────────────────────────

@router.get("/groups")
async def list_groups():
    groups = await db.get_warmup_groups()
    return {"groups": groups}


@router.post("/groups")
async def add_group(req: GroupCreate):
    group_id = await db.create_warmup_group(req.model_dump())
    return {"id": group_id, "success": True}


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
    # Enrich with running state
    for job in jobs:
        job["is_running"] = warmup_engine.is_job_running(job["id"])
    return {"jobs": jobs}


@router.post("/jobs")
async def create_job(req: JobCreate):
    group = await db.get_warmup_group(req.group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    job_id = await db.create_warmup_job(req.model_dump())
    return {"id": job_id, "success": True}


@router.post("/jobs/{job_id}/start")
async def start_job(job_id: int, background_tasks: BackgroundTasks):
    job = await db.get_warmup_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if warmup_engine.is_job_running(job_id):
        raise HTTPException(status_code=400, detail="Job already running")
    background_tasks.add_task(warmup_engine.run_warmup_job, job_id)
    return {"success": True, "message": "Job started"}


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
        # Total actions today
        cur4 = await conn.execute("""
            SELECT COUNT(*) AS cnt FROM warmup_action_log
            WHERE created_at >= datetime('now', 'start of day')
        """)
        total_today = (await cur4.fetchone())["cnt"]
        # Accounts in warmup (not graduated)
        cur5 = await conn.execute("""
            SELECT COUNT(*) AS cnt FROM warmup_progress
            WHERE graduated_at IS NULL
        """)
        accounts_active = (await cur5.fetchone())["cnt"]
        # Graduated accounts
        cur6 = await conn.execute("""
            SELECT COUNT(*) AS cnt FROM warmup_progress
            WHERE graduated_at IS NOT NULL
        """)
        accounts_graduated = (await cur6.fetchone())["cnt"]
        # Flood events in last 3 days
        cur7 = await conn.execute("""
            SELECT COUNT(*) AS cnt FROM warmup_action_log
            WHERE status = 'flood'
            AND created_at >= datetime('now', '-3 days')
        """)
        flood_events_3d = (await cur7.fetchone())["cnt"]
    return {
        "by_type": by_type,
        "by_status": by_status,
        "per_account": per_account,
        "total_today": total_today,
        "accounts_active": accounts_active,
        "accounts_graduated": accounts_graduated,
        "flood_events_3d": flood_events_3d,
    }

