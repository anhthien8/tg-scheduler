"""
routes/ai_followup.py
───────────────────────
API Endpoints for AI Follow-Up Sales Agent settings and live lead chat management.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import json

import database as db

router = APIRouter(prefix="/api/ai-followup", tags=["ai-followup"])


class AIFollowUpSettings(BaseModel):
    enabled: bool = True
    system_prompt: str = "Bạn là chuyên gia tư vấn bán hàng & onboard thân thiện. Mục tiêu của bạn là lắng nghe nhu cầu của khách hàng, giải đáp thắc mắc và hướng dẫn họ đăng ký dùng thử / chốt deal."
    knowledge_base: str = "Tên sản phẩm: Telegram Outreach Automation\nGiá: 50$/tháng\nTính năng: Gửi tin nhắn hàng loạt, lọc trùng, tương tác AI tự động chốt đơn.\nLink đăng ký onboard: https://example.com/onboard"
    max_replies_per_user: int = 5
    handover_keywords: list[str] = ["gặp admin", "tư vấn viên", "gọi điện", "số điện thoại", "lừa đảo"]


class UpdateChatStatusRequest(BaseModel):
    status: str  # 'active', 'paused_admin', 'onboarded', 'needs_human'


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


@router.get("/chats")
async def get_chats(
    status: Optional[str] = Query(None, description="Filter status: active, paused_admin, onboarded, needs_human"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """Get list of active/handover chats."""
    chats = await db.get_all_followup_chats(status_filter=status, limit=limit, offset=offset)
    return {"chats": chats, "count": len(chats)}


@router.post("/chats/{account_id}/{user_id}/status")
async def update_chat_status(account_id: int, user_id: int, req: UpdateChatStatusRequest):
    """Update status of a specific follow-up chat (e.g. pause AI to take over manually)."""
    valid_statuses = ("active", "paused_admin", "onboarded", "needs_human")
    if req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Trạng thái không hợp lệ. Chọn 1 trong: {valid_statuses}")

    ok = await db.update_followup_chat_status(account_id, user_id, req.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return {"status": "ok", "message": f"Đã chuyển trạng thái sang '{req.status}'"}
