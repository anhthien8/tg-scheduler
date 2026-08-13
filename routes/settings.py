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


# ── OpenAI OAuth PKCE Flow ──────────────────────────────────────

OPENAI_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"


class OAuthTokenExchangePayload(BaseModel):
    code: str
    code_verifier: str
    redirect_uri: str


@router.post("/chatgpt-oauth/exchange")
async def chatgpt_oauth_exchange(payload: OAuthTokenExchangePayload):
    """Exchange OAuth authorization code for access token using PKCE flow."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                OPENAI_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": OPENAI_OAUTH_CLIENT_ID,
                    "code": payload.code,
                    "code_verifier": payload.code_verifier,
                    "redirect_uri": payload.redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if resp.status_code != 200:
                err_text = resp.text[:300]
                logger.warning(f"[ChatGPT OAuth] Token exchange failed: {resp.status_code} - {err_text}")
                return {"success": False, "error": f"Token exchange thất bại (HTTP {resp.status_code}): {err_text}"}

            tokens = resp.json()

        access_token = tokens.get("access_token", "")
        if not access_token:
            return {"success": False, "error": "Không nhận được access_token từ OpenAI"}

        logger.info("[ChatGPT OAuth] Token exchange successful")
        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_in": tokens.get("expires_in", 0),
            "message": "Đăng nhập ChatGPT Subscription thành công!"
        }
    except Exception as e:
        logger.error(f"[ChatGPT OAuth] Exchange error: {e}")
        return {"success": False, "error": f"Lỗi: {str(e)[:200]}"}


from fastapi.responses import HTMLResponse


@router.get("/chatgpt-oauth/callback")
async def chatgpt_oauth_callback():
    """Serve the OAuth callback page that sends the auth code back to the parent window via postMessage."""
    html = """<!DOCTYPE html>
<html>
<head>
  <title>Đang xác thực ChatGPT...</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #0d1117; color: #e6edf3; }
    .container { text-align: center; padding: 40px; }
    .spinner { width: 40px; height: 40px; border: 3px solid rgba(16,163,127,0.2); border-top-color: #10a37f; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 20px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .success { color: #10a37f; font-size: 18px; font-weight: 600; }
    .error { color: #f85149; font-size: 14px; margin-top: 12px; }
  </style>
</head>
<body>
  <div class="container">
    <div class="spinner" id="spinner"></div>
    <div class="success" id="status">Đang xử lý xác thực...</div>
    <div class="error" id="error" style="display:none"></div>
  </div>
  <script>
    (function() {
      const params = new URLSearchParams(window.location.search);
      const code = params.get('code');
      const state = params.get('state');
      const error = params.get('error');
      const errorDesc = params.get('error_description');

      if (error) {
        document.getElementById('spinner').style.display = 'none';
        document.getElementById('status').textContent = 'Xác thực thất bại';
        document.getElementById('error').style.display = '';
        document.getElementById('error').textContent = errorDesc || error;
        return;
      }

      if (window.opener && code) {
        window.opener.postMessage({
          type: 'OPENAI_OAUTH_CALLBACK',
          code: code,
          state: state
        }, window.location.origin);
        document.getElementById('status').textContent = '✅ Xác thực thành công! Đang đóng...';
        document.getElementById('spinner').style.display = 'none';
        setTimeout(function() { window.close(); }, 1200);
      } else {
        document.getElementById('spinner').style.display = 'none';
        document.getElementById('status').textContent = 'Xác thực thất bại';
        document.getElementById('error').style.display = '';
        document.getElementById('error').textContent = 'Không thể giao tiếp với cửa sổ chính. Vui lòng thử lại.';
      }
    })();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/{key}")
async def get_setting(key: str):
    value = await db.get_setting(key, None)
    return {"key": key, "value": value}


@router.post("/{key}")
async def set_setting(key: str, payload: SettingPayload):
    await db.set_setting(key, payload.value)
    return {"key": key, "value": payload.value, "message": "Saved"}

