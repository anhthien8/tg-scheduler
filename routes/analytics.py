"""
Analytics, CSV Export, Template Library, and Auto-Reply Rules routes.
"""
import csv
import io
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import database as db

router = APIRouter(tags=["analytics"])


# ── CSV Export ────────────────────────────────────────────────────────────────

@router.get("/api/export/members/{scrape_job_id}")
async def export_members_csv(scrape_job_id: str):
    members = await db.get_scraped_members(scrape_job_id, limit=100000, offset=0)
    if not members:
        raise HTTPException(status_code=404, detail="No members found")
    cols = ["username", "first_name", "last_name", "user_id", "phone", "is_premium", "status", "scraped_at"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(members)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=members_{scrape_job_id}.csv"},
    )


@router.get("/api/export/campaign-logs/{campaign_id}")
async def export_campaign_logs_csv(campaign_id: int):
    logs = await db.get_dm_campaign_logs(campaign_id, limit=100000)
    if not logs:
        raise HTTPException(status_code=404, detail="No logs found")
    cols = ["target_username", "target_user_id", "account_id", "status", "error_message", "sent_at"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(logs)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=campaign_{campaign_id}_logs.csv"},
    )


@router.get("/api/export/contacts")
async def export_all_contacts_csv():
    members = await db.get_all_scraped_contacts()
    if not members:
        raise HTTPException(status_code=404, detail="No contacts found")
    cols = ["username", "first_name", "last_name", "user_id", "phone", "is_premium", "status", "group_title", "scraped_at"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(members)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=all_contacts.csv"},
    )


# ── Analytics Dashboard ──────────────────────────────────────────────────────

@router.get("/api/analytics/overview")
async def analytics_overview():
    return await db.get_analytics_overview()


@router.get("/api/analytics/daily-stats")
async def analytics_daily_stats(days: int = Query(default=30, ge=1, le=365)):
    return await db.get_analytics_daily_stats(days)


@router.get("/api/analytics/account-health")
async def analytics_account_health():
    return await db.get_analytics_account_health()


@router.get("/api/analytics/campaign-performance")
async def analytics_campaign_performance():
    return await db.get_analytics_campaign_performance()


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
