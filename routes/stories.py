"""Story scheduler API (v3.39): bulk story posts + internal nicks viewing each other.

DB-only routes (no Telegram calls here): the `story_tick` job in the engine process
does the posting/viewing, so this router also works in split web mode.
"""
import os
import time
import uuid
from datetime import datetime
from typing import Optional

import pytz
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

import database as db
import story_engine as se

router = APIRouter(prefix="/api/stories", tags=["stories"])

TZ = pytz.timezone(os.getenv("TIMEZONE") or "Asia/Ho_Chi_Minh")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_CAPTION = 2048
MAX_ACCOUNTS = 50


class StoryJobCreate(BaseModel):
    name: Optional[str] = ""
    account_ids: list[int]
    media_file: str
    caption: Optional[str] = ""
    scheduled_at: Optional[str] = None      # "YYYY-MM-DDTHH:MM" local time; empty = now
    stagger_min: int = 5
    privacy: str = "all"
    cross_view: bool = True
    viewer_scope: str = "job"
    view_delay_min: int = 3
    view_delay_max: int = 45
    vary_caption: bool = False              # AI writes a different caption for each nick
    caption_lang: str = "vi"


class CaptionRequest(BaseModel):
    idea: Optional[str] = ""                # current caption / topic to rewrite; empty = generic
    lang: str = "vi"
    n: int = 1


def _parse_local(value: str | None) -> float:
    if not value:
        return time.time()
    try:
        dt = datetime.fromisoformat(value.strip().replace(" ", "T"))
    except ValueError:
        raise HTTPException(400, "Giờ đăng không hợp lệ (định dạng YYYY-MM-DDTHH:MM)")
    if dt.tzinfo is None:
        dt = TZ.localize(dt)
    return dt.timestamp()


def _fmt(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), TZ).strftime("%H:%M %d/%m/%Y")


@router.post("/upload")
async def upload_media(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if not se.media_type_for(file.filename or ""):
        raise HTTPException(400, f"File '{ext}' không dùng được cho story. Chọn ảnh jpg/png/webp hoặc video mp4/mov/webm.")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File quá lớn (tối đa 50 MB)")
    os.makedirs(se.MEDIA_DIR, exist_ok=True)
    name = f"story_{uuid.uuid4().hex[:12]}{ext}"
    with open(os.path.join(se.MEDIA_DIR, name), "wb") as f:
        f.write(content)
    return {"success": True, "filename": name, "media_type": se.media_type_for(name), "size": len(content)}


@router.post("/caption")
async def ai_caption(body: CaptionRequest):
    """✨ AI viết caption: n short captions (≤ 200 chars, no internal tags)."""
    if len(body.idea or "") > MAX_CAPTION:
        raise HTTPException(400, f"Ý tưởng tối đa {MAX_CAPTION} ký tự")
    try:
        caps = await se.ai_captions(body.idea or "", body.lang, max(1, min(body.n, MAX_ACCOUNTS)))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"captions": caps}


@router.get("/accounts")
async def story_accounts():
    """Nicks with whether they can post/view now (DB only, cheap)."""
    now = time.time()
    out = []
    for a in await db.get_all_accounts():
        why = se.unsafe_reason(a, now)
        out.append({"id": a["id"], "name": a.get("name") or f"#{a['id']}",
                    "is_premium": bool(a.get("is_premium")), "ok": why is None, "why": why or ""})
    return {"accounts": out}


@router.get("/jobs")
async def list_story_jobs(limit: int = 50):
    jobs = await se.list_jobs(min(max(limit, 1), 200))
    for j in jobs:
        j["scheduled_at_text"] = _fmt(j["scheduled_at"])
    return {"jobs": jobs}


@router.get("/jobs/{job_id}")
async def get_story_job(job_id: int):
    job = await se.job_detail(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy lịch story")
    job["scheduled_at_text"] = _fmt(job["scheduled_at"])
    for p in job["post_rows"]:
        p["post_at_text"] = _fmt(p["post_at"])
        p["posted_at_text"] = _fmt(p["posted_at"])
    return job


@router.post("/jobs")
async def create_story_job(body: StoryJobCreate):
    if not body.account_ids:
        raise HTTPException(400, "Chọn ít nhất 1 nick")
    if len(body.account_ids) > MAX_ACCOUNTS:
        raise HTTPException(400, f"Tối đa {MAX_ACCOUNTS} nick mỗi lịch")
    if len(body.caption or "") > MAX_CAPTION:
        raise HTTPException(400, f"Caption tối đa {MAX_CAPTION} ký tự")
    if not se.media_type_for(body.media_file):
        raise HTTPException(400, "Chưa chọn ảnh/video hợp lệ")
    if not os.path.isfile(os.path.join(se.MEDIA_DIR, os.path.basename(body.media_file))):
        raise HTTPException(400, "Không thấy file media, hãy tải lên lại")
    if not (0 <= body.stagger_min <= 720):
        raise HTTPException(400, "Giãn giờ giữa các nick: 0–720 phút")
    if not (1 <= body.view_delay_min <= body.view_delay_max <= 600):
        raise HTTPException(400, "Thời gian chờ xem: 1 ≤ từ ≤ đến ≤ 600 phút")
    ts = _parse_local(body.scheduled_at)
    if ts < time.time() - 300:
        raise HTTPException(400, "Giờ đăng đã qua")
    known = {a["id"] for a in await db.get_all_accounts()}
    missing = [a for a in body.account_ids if a not in known]
    if missing:
        raise HTTPException(400, f"Không có nick: {missing}")
    captions = None
    ids = list(dict.fromkeys(body.account_ids))
    if body.vary_caption and len(ids) > 1:
        # First nick keeps the typed caption (if any); the others get AI rewrites of it.
        base = (body.caption or "").strip()
        need = len(ids) - (1 if base else 0)
        try:
            variants = await se.ai_captions(base, body.caption_lang, need)
        except RuntimeError as e:
            raise HTTPException(502, f"AI viết caption lỗi: {e}")
        captions = dict(zip(ids[1:] if base else ids, variants))
    job_id = await se.create_job(
        name=body.name or "", account_ids=body.account_ids, media_file=body.media_file,
        caption=body.caption or "", scheduled_at=ts, stagger_min=body.stagger_min,
        privacy=body.privacy, cross_view=body.cross_view, viewer_scope=body.viewer_scope,
        view_delay_min=body.view_delay_min, view_delay_max=body.view_delay_max, captions=captions)
    return {"success": True, "id": job_id, "scheduled_at_text": _fmt(ts), "ai_captions": len(captions or {})}


@router.post("/jobs/{job_id}/cancel")
async def cancel_story_job(job_id: int):
    if not await se.cancel_job(job_id):
        raise HTTPException(400, "Lịch này không còn chạy")
    return {"success": True}


@router.post("/jobs/{job_id}/retry")
async def retry_story_job(job_id: int):
    """↻ Thử lại: queue the failed / skipped posts of a finished job again, from now on."""
    n = await se.retry_job(job_id)
    if not n:
        raise HTTPException(400, "Không có lượt đăng lỗi nào để thử lại (hoặc lịch vẫn đang chạy)")
    return {"success": True, "retried": n}


@router.delete("/jobs/{job_id}")
async def delete_story_job(job_id: int):
    if not await se.delete_job(job_id):
        raise HTTPException(404, "Không tìm thấy lịch story")
    return {"success": True}
