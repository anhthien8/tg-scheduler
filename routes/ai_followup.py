"""
routes/ai_followup.py
───────────────────────
API Endpoints for AI Follow-Up Sales Agent settings and live lead chat management.
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import database as db
import kol_offer
import telegram_client as tg
from dm_reply_tracker import contains_weex_link, personalize_weex_links

router = APIRouter(prefix="/api/ai-followup", tags=["ai-followup"])


class AIFollowUpSettings(BaseModel):
    enabled: bool = True
    system_prompt: str = "Bạn là chuyên gia tư vấn bán hàng & onboard thân thiện. Mục tiêu của bạn là lắng nghe nhu cầu của khách hàng, giải đáp thắc mắc và hướng dẫn họ đăng ký dùng thử / chốt deal."
    knowledge_base: str = "Tên sản phẩm: Telegram Outreach Automation\nGiá: 50$/tháng\nTính năng: Gửi tin nhắn hàng loạt, lọc trùng, tương tác AI tự động chốt đơn.\nLink đăng ký onboard: https://example.com/onboard"
    max_replies_per_user: int = 5
    handover_keywords: list[str] = ["gặp admin", "tư vấn viên", "gọi điện", "số điện thoại", "lừa đảo"]
    # Drip cadence: hours of silence before each follow-up. Default day 1/3/7.
    drip_cadence_hours: list[int] = [24, 72, 168]
    # C1: Per-tier drip cadence (hours)
    drip_cadence_tier_a: list[int] = [12, 24, 48, 96]
    drip_cadence_tier_b: list[int] = [24, 72, 168]
    drip_cadence_tier_c: list[int] = [48, 168]
    # Outreach nudge: delay hours before nudging and max nudge count.
    outreach_nudge_delay_hours: int = 24
    outreach_nudge_max: int = 1


class UpdateChatStatusRequest(BaseModel):
    status: str  # 'active', 'paused_admin', 'needs_human', 'bot_ignored' ('onboarded' = legacy alias)


class KOLProfileRequest(BaseModel):
    affiliate_link: Optional[str] = None
    vip_code: Optional[str] = None
    distribution_enabled: Optional[bool] = None
    distribution_regions: Optional[list[str]] = None
    distribution_campaigns: Optional[list[str]] = None
    # Structured KOL memory (spec §B2)
    weex_uid: Optional[str] = None
    volume_claimed: Optional[str] = None
    volume_verified: Optional[str] = None
    selected_campaign: Optional[str] = None
    channel_link: Optional[str] = None
    platform: Optional[str] = None
    followers: Optional[str] = None
    first_post_link: Optional[str] = None


class KOLStepRequest(BaseModel):
    step: str  # prospect | deal_agreed | uid_received | link_confirmed | first_post | active | declined | dormant
    reason: Optional[str] = ""


class OfferLadderRequest(BaseModel):
    ladder: dict


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
    data = req.model_dump(exclude_unset=True)
    await db.upsert_kol_profile(account_id, user_id, data)
    try:
        # Admin typed the UID by hand → that is a confirmed UID: advance deal_agreed → uid_received.
        if (data.get("weex_uid") or "").strip():
            cur = await db.get_kol_step(account_id, user_id)
            if cur == "deal_agreed":
                await db.set_kol_step(account_id, user_id, "uid_received", actor="admin:web",
                                      reason="UID entered by admin in profile")
        await db.refresh_missing_items(account_id, user_id)
    except Exception:
        pass
    return await db.get_kol_profile(account_id, user_id)


# ── Pre-approved offer ladder (spec §A) ─────────────────────────────────────

@router.get("/offer-ladder")
async def get_offer_ladder():
    """Current commission ladder (structured JSON) + the prompt block the AI receives."""
    ladder = await kol_offer.get_ladder()
    return {
        "ladder": ladder,
        "is_default": not await kol_offer.is_ladder_customized(),
        "prompt_preview": kol_offer.render_offer_ladder_prompt(ladder),
    }


@router.put("/offer-ladder")
async def save_offer_ladder(req: OfferLadderRequest):
    """Validate + save a new ladder version (version auto-increments, history recorded)."""
    try:
        saved = await kol_offer.save_ladder(req.ladder, actor="admin:web")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "ladder": saved, "prompt_preview": kol_offer.render_offer_ladder_prompt(saved),
            "message": f"Đã lưu thang offer v{saved['version']}"}


@router.get("/offer-ladder/history")
async def get_offer_ladder_history(limit: int = Query(20, ge=1, le=200)):
    return {"history": await db.get_offer_ladder_history(limit=limit)}


@router.post("/offer-ladder/check")
async def check_offer_text(req: dict):
    """Dry-run the off-ladder guard on a piece of text (debug / QA helper)."""
    text = str(req.get("text", "") or "")
    ladder = await kol_offer.get_ladder()
    bad = kol_offer.find_off_ladder_figures(text, ladder)
    return {"ok": not bad, "off_ladder": bad}


# ── KOL 5-step funnel (spec §B) ─────────────────────────────────────────────

@router.get("/chats/{account_id}/{user_id}/step")
async def get_chat_step(account_id: int, user_id: int):
    chat = await db.get_followup_chat(account_id, user_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    state = await db.get_kol_state(account_id, user_id)
    ladder = await kol_offer.get_ladder()
    state["predicted_tier"] = kol_offer.predicted_tier(ladder, state["have"].get("volume_claimed"))
    state["offer_version"] = ladder.get("version")
    state["events"] = await db.get_kol_step_events(account_id, user_id, limit=20)
    return state


@router.post("/chats/{account_id}/{user_id}/step")
async def set_chat_step(account_id: int, user_id: int, req: KOLStepRequest):
    """Admin moves a lead to any funnel step (forward or back). Logged in kol_step_events."""
    if db.normalize_kol_step(req.step) is None:
        raise HTTPException(status_code=400, detail=f"Bước không hợp lệ. Chọn 1 trong: {db.KOL_STEP_ALL}")
    res = await db.set_kol_step(account_id, user_id, req.step, actor="admin:web", reason=req.reason or "")
    if res.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    state = await db.get_kol_state(account_id, user_id)
    return {"status": "ok", "changed": res.get("changed"), "from": res.get("from"), "to": res.get("to"), "state": state}


@router.get("/chats/{account_id}/{user_id}/step-events")
async def get_chat_step_events(account_id: int, user_id: int, limit: int = Query(50, ge=1, le=500)):
    return {"events": await db.get_kol_step_events(account_id, user_id, limit=limit)}


@router.get("/step-counts")
async def get_step_counts():
    counts = await db.get_kol_step_counts()
    return {"counts": counts, "labels": db.KOL_STEP_LABELS, "steps": list(db.KOL_STEPS)}


@router.post("/trigger-step-reminders")
async def trigger_step_reminders():
    """Run the step-based reminder engine once (same as the scheduled job)."""
    import dm_reply_tracker
    res = await dm_reply_tracker.process_step_reminders()
    return {"status": "ok", "result": res}


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
    s = await db.get_settings_bulk({
        "kol_channel_enabled": "false",
        "kol_channel_source": "",
        "kol_channel_account_id": "",
    })
    return {
        "enabled": (s["kol_channel_enabled"] or "").lower() in ("true", "1"),
        "source": s["kol_channel_source"],
        "account_id": s["kol_channel_account_id"],
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


def _parse_cadence_or_default(raw: str | list[int] | None, default: list[int]) -> list[int]:
    try:
        vals = [int(h) for h in (json.loads(raw) if isinstance(raw, str) else (raw or []))]
        if vals and all(h > 0 for h in vals) and all(vals[i] < vals[i + 1] for i in range(len(vals) - 1)):
            return vals
    except Exception:
        pass
    return list(default)


@router.get("/settings")
async def get_settings():
    """Get AI Follow-Up Agent configuration settings."""
    s = await db.get_settings_bulk({
        "ai_followup_enabled": "true",
        "ai_followup_system_prompt": None,
        "ai_followup_knowledge_base": None,
        "ai_followup_max_replies": "5",
        "ai_followup_handover_keywords": '["gặp admin", "tư vấn viên", "số điện thoại"]',
        "ai_followup_drip_cadence": "",
        "ai_followup_drip_cadence_tier_a": "",
        "ai_followup_drip_cadence_tier_b": "",
        "ai_followup_drip_cadence_tier_c": "",
    })
    enabled_str = s["ai_followup_enabled"]
    sys_prompt = s["ai_followup_system_prompt"]
    kb = s["ai_followup_knowledge_base"]
    max_replies_str = s["ai_followup_max_replies"]
    handover_raw = s["ai_followup_handover_keywords"]
    cadence_raw = s["ai_followup_drip_cadence"]

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

    cadence = _parse_cadence_or_default(cadence_raw, [24, 72, 168])
    tier_a = _parse_cadence_or_default(s["ai_followup_drip_cadence_tier_a"], [12, 24, 48, 96])
    tier_b = _parse_cadence_or_default(s["ai_followup_drip_cadence_tier_b"], [24, 72, 168])
    tier_c = _parse_cadence_or_default(s["ai_followup_drip_cadence_tier_c"], [48, 168])

    # Outreach nudge settings
    nudge_s = await db.get_settings_bulk({
        "outreach_nudge_delay_hours": "24",
        "outreach_nudge_max": "1",
    })
    try:
        nudge_delay = int(nudge_s["outreach_nudge_delay_hours"])
        if nudge_delay <= 0:
            nudge_delay = 24
    except (ValueError, TypeError):
        nudge_delay = 24
    try:
        nudge_max = int(nudge_s["outreach_nudge_max"])
        if nudge_max < 0:
            nudge_max = 1
    except (ValueError, TypeError):
        nudge_max = 1

    return {
        "enabled": enabled_str.lower() in ("true", "1"),
        "system_prompt": default_prompt if sys_prompt is None else sys_prompt,
        "knowledge_base": default_kb if kb is None else kb,
        "max_replies_per_user": int(max_replies_str) if max_replies_str.isdigit() else 5,
        "handover_keywords": handover_kw,
        "drip_cadence_hours": cadence,
        "drip_cadence_tier_a": tier_a,
        "drip_cadence_tier_b": tier_b,
        "drip_cadence_tier_c": tier_c,
        "outreach_nudge_delay_hours": nudge_delay,
        "outreach_nudge_max": nudge_max,
    }


@router.post("/settings")
async def save_settings(req: AIFollowUpSettings):
    """Save AI Follow-Up Agent configuration settings."""
    await db.set_setting("ai_followup_enabled", "true" if req.enabled else "false")
    await db.set_setting("ai_followup_system_prompt", req.system_prompt)
    await db.set_setting("ai_followup_knowledge_base", req.knowledge_base)
    await db.set_setting("ai_followup_max_replies", str(req.max_replies_per_user))
    await db.set_setting("ai_followup_handover_keywords", json.dumps(req.handover_keywords))
    # Outreach nudge settings
    await db.set_setting("outreach_nudge_delay_hours", str(max(1, req.outreach_nudge_delay_hours)))
    await db.set_setting("outreach_nudge_max", str(max(0, req.outreach_nudge_max)))
    # Validate + persist the drip cadences (hours, increasing, positive).
    cadence = _parse_cadence_or_default(req.drip_cadence_hours, [24, 72, 168])
    tier_a = _parse_cadence_or_default(req.drip_cadence_tier_a, [12, 24, 48, 96])
    tier_b = _parse_cadence_or_default(req.drip_cadence_tier_b, [24, 72, 168])
    tier_c = _parse_cadence_or_default(req.drip_cadence_tier_c, [48, 168])
    await db.set_setting("ai_followup_drip_cadence", json.dumps(cadence))
    await db.set_setting("ai_followup_drip_cadence_tier_a", json.dumps(tier_a))
    await db.set_setting("ai_followup_drip_cadence_tier_b", json.dumps(tier_b))
    await db.set_setting("ai_followup_drip_cadence_tier_c", json.dumps(tier_c))
    return {"status": "ok", "message": "Đã lưu cài đặt AI Follow-Up Agent"}


@router.get("/stats")
async def get_stats():
    """Lightweight stats — counts by status. No chat data, no history."""
    counts = await db.get_followup_chat_counts()
    total = sum(counts.values())
    nudge_stats = await db.get_nudge_stats()
    nudged_total = sum(nudge_stats.values())
    step_counts = await db.get_kol_step_counts()
    return {"counts": counts, "total": total, "nudge_stats": nudge_stats, "nudged_total": nudged_total,
            "step_counts": step_counts}


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
    """Update AI control status of a chat (pause AI / take over manually).

    `status` only controls the AI now; the KOL funnel lives in kol_step.
    'onboarded' is accepted as a legacy alias: it moves the lead to the
    link_confirmed step and pauses the AI (what the old button effectively did).
    """
    valid_statuses = ("active", "paused_admin", "needs_human", "bot_ignored", "onboarded")
    if req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Trạng thái không hợp lệ. Chọn 1 trong: {valid_statuses[:-1]}")

    if req.status == "onboarded":
        if not await db.get_followup_chat(account_id, user_id):
            raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
        await db.set_kol_step(account_id, user_id, "link_confirmed", actor="admin:web", reason="legacy status=onboarded")
        await db.update_followup_chat_status(account_id, user_id, "paused_admin")
        return {"status": "ok", "message": "Đã chuyển sang bước 'Đã gắn link' và tạm dừng AI"}

    ok = await db.update_followup_chat_status(account_id, user_id, req.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return {"status": "ok", "message": f"Đã chuyển trạng thái sang '{req.status}'"}


@router.post("/chats/{account_id}/{user_id}/ai-reply")
async def trigger_ai_reply(account_id: int, user_id: int):
    """Make the AI answer the lead's last message now (e.g. one missed while AI was off)."""
    import dm_reply_tracker as drt
    chat = await db.get_followup_chat(account_id, user_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    uname = chat.get("username")
    log = (await db.find_running_campaign_log_for_user(user_id, uname)
           or await db.find_campaign_log_for_user(user_id, uname))
    wlog = await db.find_watcher_log_for_user(user_id, uname)
    sent = await drt.generate_and_send_ai_reply_for_chat(
        account_id=account_id, user_id=user_id, sender_username=uname, sender_name=chat.get("name"),
        target_campaign_id=(log or {}).get("campaign_id") or chat.get("campaign_id"),
        watcher_id=(wlog or {}).get("watcher_id") or chat.get("watcher_id"),
    )
    return {"success": bool(sent)}


@router.post("/trigger-drip")
async def trigger_drip_followup():
    """Trigger automated Drip Follow-up for inactive chats (>48h)."""
    import dm_reply_tracker
    res = await dm_reply_tracker.process_drip_followups()
    return {"status": "ok", "result": res}


@router.post("/trigger-nudge")
async def trigger_outreach_nudge():
    """Trigger outreach nudge for KOLs who received DM but never replied."""
    import dm_reply_tracker
    res = await dm_reply_tracker.process_outreach_nudges()
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
