"""
Routes for AI Agents management.
CRUD + test + duplicate endpoints.
All agents use the system-wide global AI provider and API keys.
"""
import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import database as db
import ai_remix as ai_rmx

logger = logging.getLogger("tg-scheduler.ai_agents")
router = APIRouter(prefix="/api/ai-agents", tags=["AI Agents"])


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    avatar_emoji: str = "🤖"
    provider: str = "gemini"
    model: str = ""
    base_url: str = ""
    api_keys_json: list[str] = []
    system_prompt: str = ""
    remix_instruction: str = ""
    knowledge_base: str = ""
    handover_keywords: list[str] = []
    max_replies: int = 10
    tone: str = "friendly"


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_emoji: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_keys_json: Optional[list[str]] = None
    system_prompt: Optional[str] = None
    remix_instruction: Optional[str] = None
    knowledge_base: Optional[str] = None
    handover_keywords: Optional[list[str]] = None
    max_replies: Optional[int] = None
    tone: Optional[str] = None
    is_active: Optional[int] = None


class AgentTestRequest(BaseModel):
    text: str = "Chào bạn, mình đang tìm hiểu về Weex. Cho mình biết thêm thông tin được không?"


async def _get_system_ai_config():
    """Load system-wide global AI provider & keys."""
    ai_provider = await db.get_setting("ai_provider", None)
    ai_keys = []
    if ai_provider:
        try:
            raw = await db.get_setting(f"ai_keys_{ai_provider}", "[]")
            ai_keys = json.loads(raw) if raw else []
        except Exception:
            ai_keys = []

    if not ai_keys:
        for alt_prov in ["chatgpt_oauth", "gemini", "groq", "openai", "deepseek", "openai_compatible"]:
            try:
                raw = await db.get_setting(f"ai_keys_{alt_prov}", "[]")
                alt_keys = json.loads(raw) if raw else []
                if alt_keys:
                    ai_provider = alt_prov
                    ai_keys = alt_keys
                    break
            except Exception:
                pass

    kwargs = {}
    if ai_provider == "openai_compatible":
        b_url = await db.get_setting("ai_oai_compat_base_url", "")
        mod = await db.get_setting("ai_oai_compat_model", "")
        if b_url and b_url.strip():
            kwargs["base_url"] = b_url.strip()
        if mod and mod.strip():
            kwargs["model"] = mod.strip()
    elif ai_provider == "chatgpt_oauth":
        b_url = await db.get_setting("ai_chatgpt_oauth_base_url", "")
        mod = await db.get_setting("ai_chatgpt_oauth_model", "")
        if b_url and b_url.strip():
            kwargs["base_url"] = b_url.strip()
        if mod and mod.strip():
            kwargs["model"] = mod.strip()

    return ai_provider, ai_keys, kwargs


CRYPTO_BD_DEFAULT_PROMPT = """Bạn là Senior Business Development Manager (BD Manager) của Sàn Giao Dịch Crypto WEEX Exchange.
Phong cách giao tiếp: Chuyên nghiệp, am hiểu thị trường Crypto (Futures, Spot, Leverage), đàm phán tự tin nhưng lịch sự, tự nhiên như người thật đang nhắn tin trên Telegram.

Nhiệm vụ chính:
1. Chào hỏi và tìm hiểu quy mô đối tác (KOL, Channel Admin, Trader Group, Partner).
2. Trả lời các câu hỏi về Phí giao dịch (Maker/Taker), Tỷ lệ chia sẻ doanh thu (RevShare), Quỹ hỗ trợ Marketing & Milestone Payments.
3. Đánh giá quy mô Volume/Cộng đồng và xếp hạng đối tác (Tier A / Tier B / Tier C).
4. Khéo léo chốt lịch hẹn Meeting / Tạo Group Telegram làm việc trực tiếp với Trưởng phòng BD."""

CRYPTO_BD_DEFAULT_KB = """--- WEEX EXCHANGE PARTNERSHIP PROGRAM ---
1. Phí Giao Dịch Cơ Bản:
   - Maker Fee: 0.02%
   - Taker Fee: 0.06%

2. Mô Hình Hoa Hồng Partnership & Performance Fund:
   - Revenue Share: 50% - 75% tùy Volume hàng tháng.
   - Quỹ Hỗ Trợ Marketing (Marketing Fund): $200 - $5,000/tháng.
     * Quy tắc thưởng Marketing Fund: Thưởng $200 cho mỗi $10M Trading Volume hoàn thành (Ví dụ: 10M Vol -> $200, 50M Vol -> $1,000, max $5,000/tháng).
   - Tier 1 (Vol > $10M/tháng): RevShare 70% - 75% + Marketing Fund ($200 sau mỗi 10M Vol) + Hỗ trợ Event riêng.
   - Tier 2 (Vol $2M - $10M/tháng): RevShare 60% - 65%.
   - Tier 3 (Vol < $2M/tháng): RevShare 50% - 55%.

3. Ưu Điểm Nổi Bật Của WEEX:
   - Zero Slippage (Không trượt giá lệnh Futures).
   - Tốc độ khớp lệnh VIP API latency < 10ms.
   - Hỗ trợ Quỹ bảo hiểm người dùng $100,000,000.
   - Nạp rút siêu tốc 24/7, không giữ tiền.

4. Hướng Dẫn Chốt Hẹn:
   - Nếu đối tác có Volume > $5M hoặc Channel > 10k Subs: Hãy đề xuất họp Cal.com hoặc tạo Group Telegram riêng để chốt DEAL tùy chỉnh."""


@router.get("")
async def list_agents():
    agents = await db.get_all_ai_agents(active_only=True)
    if not agents:
        default_agent = {
            "name": "🤖 Crypto Exchange BD Pro",
            "description": "Agent BD chuyên nghiệp cho sàn Crypto, hỗ trợ tính phí, đàm phán RevShare & phân loại Lead Tier A/B/C.",
            "avatar_emoji": "💎",
            "provider": "gemini",
            "system_prompt": CRYPTO_BD_DEFAULT_PROMPT,
            "knowledge_base": CRYPTO_BD_DEFAULT_KB,
            "tone": "professional",
            "max_replies": 50,
            "handover_keywords": ["gặp admin", "tạo group", "họp trực tiếp", "sàn khác", "thương lượng"]
        }
        await db.create_ai_agent(default_agent)
        agents = await db.get_all_ai_agents(active_only=True)

    for agent in agents:
        agent["campaign_count"] = await db.count_campaigns_by_agent(agent["id"])
    return {"agents": agents}


@router.get("/test-router")
async def test_router_models(base_url: str = "http://127.0.0.1:20128/v1", model: str = ""):
    """Diagnostic endpoint to test 9Router proxy and list available models."""
    import httpx
    url = f"{base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            models = []
            if resp.status_code == 200:
                data = resp.json()
                if "data" in data and isinstance(data["data"], list):
                    models = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]

            test_result = None
            if model:
                try:
                    test_reply = await ai_rmx.generate_chat_response(
                        [{"role": "user", "content": "Ping test"}],
                        "You are a helpful assistant.",
                        "openai_compatible",
                        ["sk-none"],
                        base_url=base_url,
                        model=model
                    )
                    test_result = {"status": "ok", "reply": test_reply}
                except Exception as ex:
                    test_result = {"status": "error", "error": str(ex)}

            return {
                "status": "connected",
                "base_url": base_url,
                "available_models": models,
                "model_count": len(models),
                "tested_model": model,
                "test_result": test_result
            }
    except Exception as e:
        return {"status": "unreachable", "base_url": base_url, "error": str(e)}


@router.get("/{agent_id}")
async def get_agent(agent_id: int):
    agent = await db.get_ai_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    agent["campaign_count"] = await db.count_campaigns_by_agent(agent_id)
    return agent


@router.post("")
async def create_agent(req: AgentCreate):
    data = req.model_dump()
    agent_id = await db.create_ai_agent(data)
    return {"id": agent_id, "status": "created"}


@router.put("/{agent_id}")
async def update_agent(agent_id: int, req: AgentUpdate):
    agent = await db.get_ai_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    await db.update_ai_agent(agent_id, data)
    return {"status": "updated"}


@router.delete("/{agent_id}")
async def delete_agent(agent_id: int):
    agent = await db.get_ai_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    await db.delete_ai_agent(agent_id)
    return {"status": "deleted"}


@router.post("/{agent_id}/test")
async def test_agent(agent_id: int, req: AgentTestRequest):
    agent = await db.get_ai_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    kwargs = {}
    provider = agent.get("provider")
    api_keys = agent.get("api_keys_json", [])
    if isinstance(api_keys, str):
        try:
            api_keys = json.loads(api_keys)
        except Exception:
            api_keys = []

    # If agent doesn't have keys, fallback to global system settings
    if not api_keys:
        provider, api_keys, kwargs = await _get_system_ai_config()

    if not api_keys:
        raise HTTPException(400, f"AI Agent '{agent['name']}' chưa có API Key và hệ thống chưa cấu hình API Key!")

    # Merge base_url and model for openai_compatible
    if provider == "openai_compatible":
        b_url = agent.get("base_url", "")
        mod = agent.get("model", "")
        if not b_url or not b_url.strip():
            b_url = await db.get_setting("ai_oai_compat_base_url", "")
        if not mod or not mod.strip():
            mod = await db.get_setting("ai_oai_compat_model", "")
        if b_url and b_url.strip():
            kwargs["base_url"] = b_url.strip()
        if mod and mod.strip():
            kwargs["model"] = mod.strip()

    sys_prompt = agent.get("system_prompt", "")
    kb = agent.get("knowledge_base", "")
    combined = sys_prompt
    if kb and kb.strip():
        combined += "\n\n--- KNOWLEDGE BASE ---\n" + kb.strip()

    history = [{"role": "user", "content": req.text}]
    used_model = kwargs.get("model") or "default"

    try:
        reply = await ai_rmx.generate_chat_response(
            history, combined, provider, api_keys, **kwargs
        )
        if not reply:
            raise HTTPException(
                400,
                f"AI Provider '{provider}' (Model: '{used_model}') không trả về kết quả. "
                "Vui lòng kiểm tra lại API Key, Provider hoặc Model/Base URL!"
            )
        return {
            "reply": reply,
            "provider": provider,
            "model": used_model,
            "status": "ok"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("AI Agent test error: %s", e)
        raise HTTPException(500, f"Lỗi gọi AI: {str(e)}")


@router.post("/{agent_id}/duplicate")
async def duplicate_agent(agent_id: int):
    new_id = await db.duplicate_ai_agent(agent_id)
    if not new_id:
        raise HTTPException(404, "Agent not found")
    return {"id": new_id, "status": "duplicated"}


@router.post("/{agent_id}/reset-chats")
async def reset_agent_chats(agent_id: int):
    """Reset all paused/needs_human chats for this agent or globally back to active."""
    agent = await db.get_ai_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    
    async with db.get_db() as database:
        await database.execute("UPDATE ai_followup_chats SET status = 'active', reply_count = 0")
        await database.commit()
    
    logger.info("Reset all AI followup chats to 'active' state")
    return {"status": "ok", "message": "Đã kích hoạt lại toàn bộ hội thoại AI!"}


# ── AI Memory & Self-Learning Endpoints ────────────────────────────────────────
@router.get("/{agent_id}/learned-rules")
async def get_learned_rules(agent_id: int):
    """List all Q&A rules auto-learned by this AI Agent from human admin interventions."""
    rules = await db.get_learned_knowledge_for_agent(agent_id, status="approved")
    return {"rules": rules}


@router.delete("/{agent_id}/learned-rules/{rule_id}")
async def delete_learned_rule(agent_id: int, rule_id: int):
    """Delete a learned rule."""
    ok = await db.delete_learned_knowledge(rule_id)
    if not ok:
        raise HTTPException(404, "Rule not found")
    return {"status": "deleted"}


@router.get("/kol-profile/{account_id}/{user_id}")
async def get_kol_profile(account_id: int, user_id: int):
    """Fetch remembered facts about a specific KOL/User."""
    profile = await db.get_kol_profile(account_id, user_id)
    return {"account_id": account_id, "user_id": user_id, "profile": profile}


