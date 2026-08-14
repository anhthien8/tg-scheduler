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
    """Verify ChatGPT Session Token or API Key and auto-detect type."""
    token = payload.access_token.strip()
    if not token:
        return {"success": False, "error": "Access Token không được để trống"}

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    # Headers for chatgpt.com (needs proper User-Agent to avoid Cloudflare block)
    chatgpt_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    api_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Strategy 1: Try chatgpt.com/backend-api/me (ChatGPT session token)
            try:
                me_resp = await client.get(
                    "https://chatgpt.com/backend-api/me",
                    headers=chatgpt_headers,
                )
                logger.info(f"[ChatGPT OAuth] backend-api/me status: {me_resp.status_code}")
                if me_resp.status_code == 200:
                    me_data = me_resp.json()
                    email = me_data.get("email", "")
                    name = me_data.get("name", "")
                    logger.info(f"[ChatGPT OAuth] Session token verified for: {email or name}")
                    return {
                        "success": True,
                        "token": token,
                        "token_type": "session",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o",
                        "email": email,
                        "name": name,
                        "message": f"Xác thực thành công! Tài khoản: {email or name}"
                    }
                else:
                    logger.warning(f"[ChatGPT OAuth] backend-api/me returned {me_resp.status_code}: {me_resp.text[:200]}")
            except Exception as e:
                logger.warning(f"[ChatGPT OAuth] backend-api/me check failed: {e}")

            # Strategy 2: Try OpenAI API /v1/models (standard API key)
            base_url = (payload.base_url or "https://api.openai.com/v1").rstrip("/")
            models_url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
            try:
                api_resp = await client.get(models_url, headers=api_headers)
                if api_resp.status_code == 200:
                    data = api_resp.json()
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
                        "token_type": "api_key",
                        "base_url": base_url,
                        "model": best_model,
                        "models_found": len(models_list),
                        "message": f"Xác thực API Key thành công! ({len(models_list)} models)"
                    }
            except Exception as e:
                logger.debug(f"[ChatGPT OAuth] /v1/models check failed: {e}")

            # Strategy 3: If online verify fails but token looks like a valid JWT, accept it
            if token.startswith("ey") and len(token) > 100:
                logger.info("[ChatGPT OAuth] Online verification failed, but token looks like valid JWT - accepting")
                return {
                    "success": True,
                    "token": token,
                    "token_type": "session",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o",
                    "message": "Token đã được chấp nhận (định dạng JWT hợp lệ). Sẽ xác thực khi sử dụng thực tế."
                }

            # All failed
            return {
                "success": False,
                "error": "Access Token không hợp lệ. Hãy thử lấy token mới từ chatgpt.com/api/auth/session."
            }

    except Exception as e:
        # Even on network error, if token looks valid, accept it
        if token.startswith("ey") and len(token) > 100:
            return {
                "success": True,
                "token": token,
                "token_type": "session",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "message": "Token đã được chấp nhận. Sẽ xác thực khi sử dụng thực tế."
            }
        return {"success": False, "error": f"Lỗi kết nối: {str(e)[:200]}"}


# ── Fetch available ChatGPT/OpenAI models ──────────────────────


class FetchModelsPayload(BaseModel):
    access_token: str
    base_url: Optional[str] = None


@router.post("/chatgpt-oauth/models")
async def fetch_chatgpt_models(payload: FetchModelsPayload):
    """Fetch available models from ChatGPT backend or OpenAI API."""
    token = payload.access_token.strip()
    if not token:
        return {"success": False, "models": [], "error": "Token không được để trống"}

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    chatgpt_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    api_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    models = []

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Try ChatGPT backend-api/models first (session tokens)
            try:
                resp = await client.get(
                    "https://chatgpt.com/backend-api/models",
                    headers=chatgpt_headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # ChatGPT returns {"models": [{"slug": "gpt-4o", "title": "GPT-4o", ...}, ...]}
                    raw = data.get("models", [])
                    if isinstance(raw, list):
                        for m in raw:
                            if isinstance(m, dict):
                                slug = m.get("slug", "")
                                title = m.get("title", slug)
                                if slug:
                                    models.append({"id": slug, "name": title})
                    if models:
                        logger.info(f"[ChatGPT Models] Found {len(models)} models from backend-api")
                        return {"success": True, "models": models, "source": "chatgpt_backend"}
            except Exception as e:
                logger.debug(f"[ChatGPT Models] backend-api/models failed: {e}")

            # Try OpenAI API /v1/models (API keys)
            base_url = (payload.base_url or "https://api.openai.com/v1").rstrip("/")
            models_url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
            try:
                resp = await client.get(models_url, headers=api_headers)
                if resp.status_code == 200:
                    data = resp.json()
                    raw = data.get("data", [])
                    if isinstance(raw, list):
                        # Filter for chat models only
                        chat_prefixes = ("gpt-", "o1", "o3", "o4", "chatgpt-")
                        for m in raw:
                            if isinstance(m, dict):
                                mid = m.get("id", "")
                                if mid and any(mid.startswith(p) for p in chat_prefixes):
                                    models.append({"id": mid, "name": mid})
                        # Sort: gpt-4o first, then alphabetical
                        models.sort(key=lambda x: (0 if x["id"] == "gpt-4o" else 1, x["id"]))
                    if models:
                        logger.info(f"[ChatGPT Models] Found {len(models)} chat models from API")
                        return {"success": True, "models": models, "source": "openai_api"}
            except Exception as e:
                logger.debug(f"[ChatGPT Models] /v1/models failed: {e}")

    except Exception as e:
        logger.warning(f"[ChatGPT Models] Error: {e}")

    # Fallback: return default known models (updated Aug 2026)
    fallback = [
        {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (Flagship)"},
        {"id": "gpt-5.6-terra", "name": "GPT-5.6 Terra (Balanced)"},
        {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna (Fast)"},
        {"id": "gpt-4o", "name": "GPT-4o"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
        {"id": "gpt-4.1", "name": "GPT-4.1"},
        {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini"},
        {"id": "gpt-4.1-nano", "name": "GPT-4.1 Nano"},
        {"id": "o4-mini", "name": "o4-mini"},
        {"id": "o3-mini", "name": "o3-mini"},
    ]
    return {"success": True, "models": fallback, "source": "fallback"}


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

