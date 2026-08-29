"""
routes/ai_followup.py
───────────────────────
API Endpoints for AI Follow-Up Sales Agent settings and live lead chat management.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import asyncio
import json

import database as db
import telegram_client as tg
from dm_reply_tracker import personalize_weex_links, contains_weex_link

router = APIRouter(prefix="/api/ai-followup", tags=["ai-followup"])


class AIFollowUpSettings(BaseModel):
    enabled: bool = True
    system_prompt: str = "Bạn là chuyên gia tư vấn bán hàng & onboard thân thiện. Mục tiêu của bạn là lắng nghe nhu cầu của khách hàng, giải đáp thắc mắc và hướng dẫn họ đăng ký dùng thử / chốt deal."
    knowledge_base: str = "Tên sản phẩm: Telegram Outreach Automation\nGiá: 50$/tháng\nTính năng: Gửi tin nhắn hàng loạt, lọc trùng, tương tác AI tự động chốt đơn.\nLink đăng ký onboard: https://example.com/onboard"
    max_replies_per_user: int = 5
    handover_keywords: list[str] = ["gặp admin", "tư vấn viên", "gọi điện", "số điện thoại", "lừa đảo"]


class UpdateChatStatusRequest(BaseModel):
    status: str  # 'active', 'paused_admin', 'onboarded', 'needs_human'


class KOLProfileRequest(BaseModel):
    affiliate_link: Optional[str] = None
    vip_code: Optional[str] = None
    distribution_enabled: Optional[bool] = None
    distribution_regions: Optional[list[str]] = None
    distribution_campaigns: Optional[list[str]] = None


class KOLRecipient(BaseModel):
    account_id: int
    user_id: int


class KOLBulkSendRequest(BaseModel):
    recipients: list[KOLRecipient]
    message: str


@router.get("/kol-profiles/{account_id}/{user_id}")
async def get_kol_profile(account_id: int, user_id: int):
    return await db.get_kol_profile(account_id, user_id)


@router.put("/kol-profiles/{account_id}/{user_id}")
async def update_kol_profile(account_id: int, user_id: int, req: KOLProfileRequest):
    if not await db.get_account(account_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy account")
    await db.upsert_kol_profile(account_id, user_id, req.dict(exclude_unset=True))
    return await db.get_kol_profile(account_id, user_id)


@router.post("/kol-profiles/bulk-send")
async def bulk_send_kol_messages(req: KOLBulkSendRequest):
    text = (req.message or "").strip()
    if not text or len(text) > 4000:
        raise HTTPException(status_code=400, detail="Tin nhắn trống hoặc quá dài")
    result = {"sent": [], "skipped": [], "errors": []}
    for target in req.recipients:
        profile = await db.get_kol_profile(target.account_id, target.user_id)
        if contains_weex_link(text) and not profile.get("vip_code"):
            result["skipped"].append({"account_id": target.account_id, "user_id": target.user_id, "reason": "missing vip_code"})
            continue
        personalized = personalize_weex_links(text, profile.get("vip_code", ""))
        try:
            await tg.send_text_message(target.account_id, target.user_id, personalized, parse_mode=None)
            result["sent"].append({"account_id": target.account_id, "user_id": target.user_id})
            await asyncio.sleep(0.2)
        except Exception as exc:
            result["errors"].append({"account_id": target.account_id, "user_id": target.user_id, "error": str(exc)})
    return result


# Aliases matching the Lead & AI Follow-Up frontend (chats/{account_id}/{user_id}/profile, bulk-send)
@router.get("/chats/{account_id}/{user_id}/profile")
async def get_chat_profile(account_id: int, user_id: int):
    return await get_kol_profile(account_id, user_id)


@router.post("/chats/{account_id}/{user_id}/profile")
async def post_chat_profile(account_id: int, user_id: int, req: KOLProfileRequest):
    return await update_kol_profile(account_id, user_id, req)


@router.post("/bulk-send")
async def bulk_send_chats(req: KOLBulkSendRequest):
    return await bulk_send_kol_messages(req)


class KOLChannelSettings(BaseModel):
    enabled: bool = False
    source: str = ""
    account_id: str = ""


@router.get("/kol-channel-settings")
async def get_kol_channel_settings():
    return {
        "enabled": (await db.get_setting("kol_channel_enabled", "false") or "").lower() in ("true", "1"),
        "source": await db.get_setting("kol_channel_source", ""),
        "account_id": await db.get_setting("kol_channel_account_id", ""),
    }


@router.post("/kol-channel-settings")
async def save_kol_channel_settings(req: KOLChannelSettings):
    import kol_channel_watcher as kcw
    await db.set_setting("kol_channel_enabled", "true" if req.enabled else "false")
    await db.set_setting("kol_channel_source", req.source.strip())
    await db.set_setting("kol_channel_account_id", req.account_id.strip())
    # Re-register listener with new settings
    kcw.unregister_channel_listener()
    if req.enabled and req.source.strip() and req.account_id.strip().isdigit():
        await kcw.start_kol_channel_watcher()
    return {"ok": True, "message": "Đã lưu cài đặt KOL Channel Watcher"}


@router.get("/kol-broadcast-log")
async def get_kol_broadcast_log(limit: int = 30, offset: int = 0):
    """Recent broadcast events — for dashboard monitoring."""
    import aiosqlite
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM kol_broadcast_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    return {"logs": rows, "count": len(rows)}


@router.get("/settings")
async def get_settings():
    """Get AI Follow-Up Agent configuration settings."""
    enabled_str = await db.get_setting("ai_followup_enabled", "true")
    sys_prompt = await db.get_setting("ai_followup_system_prompt", None)
    kb = await db.get_setting("ai_followup_knowledge_base", None)
    max_replies_str = await db.get_setting("ai_followup_max_replies", "5")
    handover_raw = await db.get_setting("ai_followup_handover_keywords", '["gặp admin", "tư vấn viên", "số điện thoại"]')

    default_prompt = (
        "Bạn là chuyên gia tư vấn bán hàng & onboard thân thiện. Giọng văn tự nhiên, ngắn gọn như người thật đang nhắn tin.\n"
        "Nhiệm vụ của bạn là giải đáp thắc mắc của người dùng dựa trên Knowledge Base và khéo léo chốt deal/onboard họ qua Link Onboard.\n"
        "Nếu người dùng đồng ý dùng thử hoặc muốn mua, hãy cung cấp Link Onboard."
    )
    default_kb = (
        "Sản phẩm: TG-Scheduler Tool Automated Outreach\n"
        "Tính năng: Quản lý hàng loạt tài khoản Tele, DM tự động, kéo member, lọc nick ảo, AI chat tự động.\n"
        "Bảng giá: Gói Pro: $49/tháng, Gói Unlimited: $99/tháng.\n"
        "Link Onboard / Đăng ký: https://t.me/your_admin_bot?start=onboard"
    )

    try:
        handover_kw = json.loads(handover_raw) if handover_raw else []
    except Exception:
        handover_kw = ["gặp admin", "tư vấn viên", "số điện thoại"]

    return {
        "enabled": enabled_str.lower() in ("true", "1"),
        "system_prompt": default_prompt if sys_prompt is None else sys_prompt,
        "knowledge_base": default_kb if kb is None else kb,
        "max_replies_per_user": int(max_replies_str) if max_replies_str.isdigit() else 5,
        "handover_keywords": handover_kw
    }


@router.post("/settings")
async def save_settings(req: AIFollowUpSettings):
    """Save AI Follow-Up Agent configuration settings."""
    await db.set_setting("ai_followup_enabled", "true" if req.enabled else "false")
    await db.set_setting("ai_followup_system_prompt", req.system_prompt)
    await db.set_setting("ai_followup_knowledge_base", req.knowledge_base)
    await db.set_setting("ai_followup_max_replies", str(req.max_replies_per_user))
    await db.set_setting("ai_followup_handover_keywords", json.dumps(req.handover_keywords))
    return {"status": "ok", "message": "Đã lưu cài đặt AI Follow-Up Agent"}


@router.get("/stats")
async def get_stats():
    """Lightweight stats — counts by status. No chat data, no history."""
    counts = await db.get_followup_chat_counts()
    total = sum(counts.values())
    return {"counts": counts, "total": total}


@router.get("/chats")
async def get_chats(
    status: Optional[str] = Query(None, description="Filter status: active, paused_admin, onboarded, needs_human"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_history: bool = Query(False, description="Include chat history (heavy)")
):
    """Get list of active/handover chats. Pass include_history=true for full message history."""
    chats = await db.get_all_followup_chats(status_filter=status, limit=limit, offset=offset, include_history=include_history)
    return {"chats": chats, "count": len(chats)}


@router.post("/chats/{account_id}/{user_id}/status")
async def update_chat_status(account_id: int, user_id: int, req: UpdateChatStatusRequest):
    """Update status of a specific follow-up chat (e.g. pause AI to take over manually)."""
    valid_statuses = ("active", "paused_admin", "onboarded", "needs_human", "bot_ignored")
    if req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Trạng thái không hợp lệ. Chọn 1 trong: {valid_statuses}")

    ok = await db.update_followup_chat_status(account_id, user_id, req.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return {"status": "ok", "message": f"Đã chuyển trạng thái sang '{req.status}'"}


@router.post("/trigger-drip")
async def trigger_drip_followup():
    """Trigger automated Drip Follow-up for inactive chats (>48h)."""
    import dm_reply_tracker
    res = await dm_reply_tracker.process_drip_followups()
    return {"status": "ok", "result": res}


@router.post("/chats/{account_id}/{user_id}/verification")
async def update_verification(account_id: int, user_id: int, req: dict):
    """Update verification status: none, requested, submitted, verified, rejected."""
    valid = ("none", "requested", "submitted", "verified", "rejected")
    status = req.get("status", "")
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Choose: {valid}")
    ok = await db.update_followup_verification(account_id, user_id, status)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return {"status": "ok", "verification_status": status}


class SendChatMessageRequest(BaseModel):
    text: str


@router.post("/chats/{account_id}/{user_id}/send")
async def send_chat_message(account_id: int, user_id: int, req: SendChatMessageRequest):
    """Gửi tin nhắn tay từ admin tới lead. Interceptor sẽ tự pause AI + ghi history."""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Tin nhắn trống")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="Tin nhắn quá dài (max 4000 ký tự)")
    try:
        await tg.send_text_message(account_id, user_id, text, parse_mode=None)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gửi thất bại: {e}")
    return {"ok": True}


@router.get("/chats/{account_id}/{user_id}/history")
async def get_chat_history(account_id: int, user_id: int):
    """Get full message history for a single chat (lazy-loaded by modal)."""
    chat = await db.get_followup_chat(account_id, user_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return {"history": chat.get("history", []), "summary": chat.get("summary", ""), "lead_tier": chat.get("lead_tier", "Tier C"), "intent_score": chat.get("intent_score", 0)}


@router.get("/chats/{account_id}/{user_id}/summary")
async def get_chat_summary(account_id: int, user_id: int):
    """Get context summary and lead tier metrics for a chat."""
    chat = await db.get_followup_chat(account_id, user_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return {
        "user_id": user_id,
        "account_id": account_id,
        "lead_tier": chat.get("lead_tier", "Tier C"),
        "intent_score": chat.get("intent_score", 0),
        "summary": chat.get("summary", "Chưa có tóm tắt nhu cầu khách hàng."),
        "status": chat.get("status", "active")
    }
