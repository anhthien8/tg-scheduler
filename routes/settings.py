"""
Settings API Routes - store/retrieve key-value settings like AI API keys.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json

import database as db
import httpx
import logging

logger = logging.getLogger("tg-scheduler.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingPayload(BaseModel):
    value: str


class TestRemixPayload(BaseModel):
    provider: str
    keys: list
    text: str
    sender_name: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class DailySummaryPayload(BaseModel):
    enabled: str        # "0" or "1"
    time: str           # "HH:MM"
    account_id: str     # "" for auto-detect, or account id


# NOTE: /test-remix MUST be declared before /{key} wildcard
@router.post("/test-remix")
async def test_remix(payload: TestRemixPayload):
    """Test AI remix with provided keys directly (no DB save needed)."""
    import ai_remix as ai_rmx
    if payload.provider not in ("gemini", "deepseek", "openai", "groq", "openai_compatible"):
        raise HTTPException(status_code=400, detail="Invalid provider")
    if not payload.keys:
        raise HTTPException(status_code=400, detail="No API keys provided")
    kwargs = {}
    if payload.provider == "openai_compatible":
        if not payload.base_url or not payload.model:
            raise HTTPException(status_code=400, detail="base_url and model required for openai_compatible")
        kwargs["base_url"] = payload.base_url
        kwargs["model"] = payload.model
    try:
        remixed = await ai_rmx.remix_message(
            original_text=payload.text,
            provider=payload.provider,
            api_keys=payload.keys,
            sender_name=payload.sender_name,
            **kwargs
        )
        return {"success": True, "remixed": remixed, "original": payload.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/daily-summary")
async def save_daily_summary(payload: DailySummaryPayload):
    """Save daily summary settings and reschedule the job."""
    import scheduler as sch
    from apscheduler.triggers.cron import CronTrigger

    # Validate time format
    try:
        hour, minute = map(int, payload.time.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid time format, use HH:MM")

    await db.set_setting("daily_summary_enabled", payload.enabled)
    await db.set_setting("daily_summary_time", payload.time)
    await db.set_setting("daily_summary_account_id", payload.account_id)

    # Reschedule the job
    from daily_summary import send_daily_summary
    scheduler = sch.get_scheduler()
    if scheduler.running:
        scheduler.add_job(
            send_daily_summary,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=sch.TZ),
            id="daily_summary",
            name="Daily Summary",
            replace_existing=True,
        )

    return {"message": "Saved", "time": payload.time, "enabled": payload.enabled}


@router.get("/daily-summary")
async def get_daily_summary():
    """Get daily summary settings."""
    enabled = await db.get_setting("daily_summary_enabled", "0")
    time = await db.get_setting("daily_summary_time", "21:00")
    account_id = await db.get_setting("daily_summary_account_id", "")
    return {"enabled": enabled, "time": time, "account_id": account_id}


class FetchModelsPayload(BaseModel):
    base_url: str
    api_key: Optional[str] = None


@router.post("/fetch-models")
async def fetch_models(payload: FetchModelsPayload):
    """Proxy to fetch available models from an OpenAI-compatible API."""
    base_url = payload.base_url.rstrip("/")
    # Ensure we hit /models endpoint
    if base_url.endswith("/v1"):
        models_url = base_url + "/models"
    else:
        models_url = base_url + "/v1/models"

    headers = {"Content-Type": "application/json"}
    if payload.api_key:
        headers["Authorization"] = f"Bearer {payload.api_key}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(models_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # OpenAI format: { "data": [ { "id": "model-name", ... }, ... ] }
        models_list = []
        raw_models = data.get("data", [])
        if isinstance(raw_models, list):
            for m in raw_models:
                model_id = m.get("id", "") if isinstance(m, dict) else str(m)
                if model_id:
                    models_list.append({
                        "id": model_id,
                        "owned_by": m.get("owned_by", "") if isinstance(m, dict) else "",
                    })

        # Sort alphabetically
        models_list.sort(key=lambda x: x["id"].lower())
        logger.info(f"[FetchModels] Found {len(models_list)} models from {base_url}")
        return {"success": True, "models": models_list}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except httpx.ConnectError:
        return {"success": False, "error": f"Không thể kết nối đến {base_url}"}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}


class VerifyChatgptOauthPayload(BaseModel):
    access_token: str
    base_url: Optional[str] = None


@router.post("/verify-chatgpt-oauth")
async def verify_chatgpt_oauth(payload: VerifyChatgptOauthPayload):
    """Verify ChatGPT Subscription Access Token / OAuth Token and auto-discover models."""
    token = payload.access_token.strip()
    if not token:
        return {"success": False, "error": "Access Token không được để trống"}

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    base_url = (payload.base_url or "https://api.openai.com/v1").rstrip("/")
    if not base_url:
        base_url = "https://api.openai.com/v1"

    models_url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(models_url, headers=headers)
            if resp.status_code == 401:
                return {"success": False, "error": "Access Token không hợp lệ hoặc đã hết hạn (HTTP 401 Unauthorized)"}
            resp.raise_for_status()
            data = resp.json()

        models_list = []
        raw_models = data.get("data", [])
        if isinstance(raw_models, list):
            models_list = [m.get("id", "") for m in raw_models if isinstance(m, dict) and m.get("id")]

        best_model = "gpt-4o"
        if "gpt-4o" in models_list:
            best_model = "gpt-4o"
        elif "gpt-4o-mini" in models_list:
            best_model = "gpt-4o-mini"
        elif models_list:
            best_model = models_list[0]

        return {
            "success": True,
            "token": token,
            "base_url": base_url,
            "model": best_model,
            "models_found": len(models_list),
            "message": "Xác thực ChatGPT OAuth Access Token thành công!"
        }
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": f"Lỗi kết nối: {str(e)[:200]}"}


@router.get("/{key}")
async def get_setting(key: str):
    value = await db.get_setting(key, None)
    return {"key": key, "value": value}


@router.post("/{key}")
async def set_setting(key: str, payload: SettingPayload):
    await db.set_setting(key, payload.value)
    return {"key": key, "value": payload.value, "message": "Saved"}

