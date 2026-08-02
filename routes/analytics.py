"""
Analytics, CSV Export, Template Library, and Auto-Reply Rules routes.
"""
import csv
import io
import time
from typing import Dict, Any, Tuple
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import database as db

router = APIRouter(tags=["analytics"])


# ── TTL Cache for Analytics ──────────────────────────────────────────────────

class AsyncTTLCache:
    def __init__(self, ttl_seconds: int = 30):
        self.ttl = ttl_seconds
        self.cache: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        if key in self.cache:
            timestamp, value = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            del self.cache[key]
        return None

    def set(self, key: str, value: Any):
        self.cache[key] = (time.time(), value)


analytics_cache = AsyncTTLCache(ttl_seconds=30)


# ── CSV Export ────────────────────────────────────────────────────────────────

class CSVStreamBuffer:
    def __init__(self):
        self._data = []
    def write(self, data):
        self._data.append(data)
    def read(self) -> str:
        temp = "".join(self._data)
        self._data.clear()
        return temp


async def generate_csv_rows(cols, async_data_generator):
    buffer = CSVStreamBuffer()
    buffer.write("\ufeff") # Prepends BOM
    writer = csv.DictWriter(buffer, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    yield buffer.read()
    async for row in async_data_generator:
        writer.writerow(row)
        yield buffer.read()


async def member_generator(scrape_job_id: str, first_chunk: list, chunk_size: int = 1000):
    for member in first_chunk:
        yield member
    offset = chunk_size
    while True:
        chunk = await db.get_scraped_members(scrape_job_id, limit=chunk_size, offset=offset)
        if not chunk:
            break
        for member in chunk:
            yield member
        offset += chunk_size


@router.get("/api/export/members/{scrape_job_id}")
async def export_members_csv(scrape_job_id: str):
    chunk_size = 1000
    first_chunk = await db.get_scraped_members(scrape_job_id, limit=chunk_size, offset=0)
    if not first_chunk:
        raise HTTPException(status_code=404, detail="No members found")
    cols = ["username", "first_name", "last_name", "user_id", "phone", "is_premium", "status", "scraped_at"]
    return StreamingResponse(
        generate_csv_rows(cols, member_generator(scrape_job_id, first_chunk, chunk_size)),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=members_{scrape_job_id}.csv"},
    )


async def campaign_logs_generator(campaign_id: int, first_chunk: list, chunk_size: int = 1000):
    for log in first_chunk:
        yield log
    offset = chunk_size
    while True:
        chunk = await db.get_dm_campaign_logs(campaign_id, limit=chunk_size, offset=offset)
        if not chunk:
            break
        for log in chunk:
            yield log
        offset += chunk_size


@router.get("/api/export/campaign-logs/{campaign_id}")
async def export_campaign_logs_csv(campaign_id: int):
    chunk_size = 1000
    first_chunk = await db.get_dm_campaign_logs(campaign_id, limit=chunk_size, offset=0)
    if not first_chunk:
        raise HTTPException(status_code=404, detail="No logs found")
    cols = ["target_username", "target_user_id", "account_id", "status", "error_message", "sent_at"]
    return StreamingResponse(
        generate_csv_rows(cols, campaign_logs_generator(campaign_id, first_chunk, chunk_size)),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=campaign_{campaign_id}_logs.csv"},
    )


async def contacts_generator(first_chunk: list, chunk_size: int = 1000):
    for member in first_chunk:
        yield member
    offset = chunk_size
    while True:
        chunk = await db.get_all_scraped_contacts(limit=chunk_size, offset=offset)
        if not chunk:
            break
        for member in chunk:
            yield member
        offset += chunk_size


@router.get("/api/export/contacts")
async def export_all_contacts_csv():
    chunk_size = 1000
    first_chunk = await db.get_all_scraped_contacts(limit=chunk_size, offset=0)
    if not first_chunk:
        raise HTTPException(status_code=404, detail="No contacts found")
    cols = ["username", "first_name", "last_name", "user_id", "phone", "is_premium", "status", "group_title", "scraped_at"]
    return StreamingResponse(
        generate_csv_rows(cols, contacts_generator(first_chunk, chunk_size)),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=all_contacts.csv"},
    )


# ── Analytics Dashboard ──────────────────────────────────────────────────────

@router.get("/api/analytics/overview")
async def analytics_overview():
    cached = analytics_cache.get("overview")
    if cached is not None:
        return cached
    data = await db.get_analytics_overview()
    analytics_cache.set("overview", data)
    return data


@router.get("/api/analytics/daily-stats")
async def analytics_daily_stats(days: int = Query(default=30, ge=1, le=365)):
    key = f"daily_stats_{days}"
    cached = analytics_cache.get(key)
    if cached is not None:
        return cached
    data = await db.get_analytics_daily_stats(days)
    analytics_cache.set(key, data)
    return data


@router.get("/api/analytics/account-health")
async def analytics_account_health():
    cached = analytics_cache.get("account_health")
    if cached is not None:
        return cached
    data = await db.get_analytics_account_health()
    analytics_cache.set("account_health", data)
    return data


@router.get("/api/analytics/campaign-performance")
async def analytics_campaign_performance():
    cached = analytics_cache.get("campaign_performance")
    if cached is not None:
        return cached
    data = await db.get_analytics_campaign_performance()
    analytics_cache.set("campaign_performance", data)
    return data


# ── Template Library ─────────────────────────────────────────────────────────

class TemplatePayload(BaseModel):
    name: str
    category: Optional[str] = "general"
    messages: Optional[list] = []
    is_default: Optional[int] = 0


@router.get("/api/templates")
async def list_templates():
    return await db.get_all_templates()


@router.post("/api/templates")
async def create_template(payload: TemplatePayload):
    tid = await db.create_template(payload.model_dump())
    return {"ok": True, "id": tid}


@router.put("/api/templates/{template_id}")
async def update_template(template_id: int, payload: TemplatePayload):
    ok = await db.update_template(template_id, payload.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


@router.delete("/api/templates/{template_id}")
async def delete_template(template_id: int):
    ok = await db.delete_template(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


@router.get("/api/templates/{template_id}/performance")
async def get_template_performance(template_id: int):
    """Return variant performance stats for a template."""
    perf = await db.get_template_performance(template_id=template_id)
    best = await db.get_best_template_variant(template_id)
    return {
        "template_id": template_id,
        "variants": perf,
        "best_variant_index": best,
    }


# ── Auto-Reply Rules ─────────────────────────────────────────────────────────

class AutoReplyRulePayload(BaseModel):
    name: str
    trigger_type: Optional[str] = "keyword"
    trigger_keywords: Optional[list] = []
    reply_messages: Optional[list] = []
    account_ids: Optional[list] = []
    use_ai: Optional[int] = 0
    ai_system_prompt: Optional[str] = None
    max_replies_per_user: Optional[int] = 3
    is_active: Optional[int] = 1


@router.get("/api/auto-reply/rules")
async def list_auto_reply_rules():
    return await db.get_all_auto_reply_rules()


@router.post("/api/auto-reply/rules")
async def create_auto_reply_rule(payload: AutoReplyRulePayload):
    rid = await db.create_auto_reply_rule(payload.model_dump())
    return {"ok": True, "id": rid}


@router.put("/api/auto-reply/rules/{rule_id}")
async def update_auto_reply_rule(rule_id: int, payload: AutoReplyRulePayload):
    ok = await db.update_auto_reply_rule(rule_id, payload.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"ok": True}


@router.delete("/api/auto-reply/rules/{rule_id}")
async def delete_auto_reply_rule(rule_id: int):
    ok = await db.delete_auto_reply_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"ok": True}


@router.post("/api/auto-reply/rules/{rule_id}/toggle")
async def toggle_auto_reply_rule(rule_id: int):
    result = await db.toggle_auto_reply_rule(rule_id)
    if not result:
        raise HTTPException(status_code=404, detail="Rule not found")
    return result


@router.get("/api/auto-reply/logs/{rule_id}")
async def get_auto_reply_logs(rule_id: int, limit: int = Query(default=100, ge=1, le=1000)):
    return await db.get_auto_reply_logs(rule_id, limit)
