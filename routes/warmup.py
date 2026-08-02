"""
Warmup / Group Seeding API routes.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel
from typing import Optional

import database as db
import telegram_client as tg
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
