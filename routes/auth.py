"""
Authentication & Account management routes.
"""
import os
import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from models import SendCodeRequest, VerifyCodeRequest, AccountCreate
import telegram_client as tg
import database as db
import scheduler as sch

logger = logging.getLogger("tg-scheduler")
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Default Telegram Desktop credentials (safe official client creds)
# Users can override per-account, but these work for 99% of cases
_DEFAULT_API_ID   = 2040
_DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"


# ── Account CRUD ──

@router.get("/accounts")
async def list_accounts():
    """List all Telegram accounts."""
    accounts = await db.get_all_accounts()
    
    import asyncio
    
    async def get_acc_status(acc):
        try:
            acc["is_logged_in"] = await tg.is_authorized(acc["id"], timeout=2.5)
            me = await tg.get_me(acc["id"], timeout=2.5)
            if me:
                acc["user_info"] = me
            elif acc["id"] in tg._me_cache:
                acc["user_info"] = tg._me_cache[acc["id"]]
        except Exception:
            acc["is_logged_in"] = False
            if acc["id"] in tg._me_cache:
                acc["user_info"] = tg._me_cache[acc["id"]]

    await asyncio.gather(*(get_acc_status(acc) for acc in accounts), return_exceptions=True)
    return {"accounts": accounts}


@router.post("/accounts")
async def add_account(req: AccountCreate):
    """Add a new Telegram account."""
    # Create unique session name
    session_name = f"account_{req.phone.replace('+', '').replace(' ', '')}"

    # Use provided API creds or fall back to TG Desktop defaults
    api_id   = int(req.api_id)   if req.api_id   else _DEFAULT_API_ID
    api_hash = req.api_hash      if req.api_hash  else _DEFAULT_API_HASH
    name     = req.name          if req.name      else req.phone  # will be updated after login
    proxy_url = req.proxy_url or None

    account_id = await db.create_account({
        "name": name,
        "phone": req.phone,
        "api_id": str(api_id),
        "api_hash": api_hash,
        "session_name": session_name,
        "proxy_url": proxy_url,
    })

    # Create and connect the client
    try:
        await tg.create_client(account_id, api_id, api_hash, session_name, proxy_url=proxy_url)
        await tg.start_client(account_id)
    except Exception as e:
        logger.warning(f"Account {account_id}: initial connect failed: {e}")

    return {"success": True, "account_id": account_id}


@router.delete("/accounts/{account_id}")
async def remove_account(account_id: int):
    """Remove a Telegram account and disconnect its client."""
    account = await db.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Disconnect client
    client = tg.get_client(account_id)
    if client and client.is_connected():
        try:
            await client.disconnect()
        except Exception:
            pass

    # Clean up client caches
    tg._clients.pop(account_id, None)
    tg._auth_cache.pop(account_id, None)
    tg._me_cache.pop(account_id, None)
    tg._dialogs_cache.pop(account_id, None)

    # Delete session file
    session_path = os.path.join(tg.SESSION_DIR, account["session_name"] + ".session")
    if os.path.exists(session_path):
        os.remove(session_path)

    await db.delete_account(account_id)
    return {"success": True}


# ── Login Flow ──

@router.post("/send-code")
async def send_code(req: SendCodeRequest):
    """Send OTP code to phone number for a specific account."""
    try:
        phone_code_hash = await tg.send_code(req.account_id, req.phone)
        return {"success": True, "phone_code_hash": phone_code_hash}
    except Exception as e:
        logger.error(f"send_code error for account {req.account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="Không thể gửi mã OTP. Vui lòng thử lại.")


@router.post("/verify")
async def verify_code(req: VerifyCodeRequest):
    """Verify OTP code and sign in."""
    result = await tg.sign_in(req.account_id, req.phone, req.code,
                              req.phone_code_hash, req.password)
    if result.get("success"):
        # Auto-update name from real TG profile
        try:
            me = await tg.get_me(req.account_id)
            if me:
                real_name = " ".join(filter(None, [me.get("first_name",""), me.get("last_name","")])).strip()
                if real_name:
                    await db.update_account_name(req.account_id, real_name)
        except Exception as _e:
            logger.debug(f"Could not auto-name account {req.account_id}: {_e}")

        await db.update_account_login_status(req.account_id, True)
        # Load scheduler jobs for this account
        await sch.load_all_jobs()
        return result
    if result.get("needs_password"):
        return {"success": False, "needs_password": True, "message": "2FA password required"}
    raise HTTPException(status_code=400, detail=result.get("error", "Login failed"))


@router.post("/logout/{account_id}")
async def logout(account_id: int):
    """Log out a specific account."""
    await tg.logout(account_id)
    await db.update_account_login_status(account_id, False)
    return {"success": True}


@router.get("/status")
async def auth_status():
    """Global auth status - checks if any account is logged in."""
    accounts = await db.get_all_accounts()
    if not accounts:
        return {"authenticated": False, "has_accounts": False}

    import asyncio

    async def check_acc(acc):
        try:
            is_auth = await tg.is_authorized(acc["id"], timeout=2.5)
            if is_auth:
                me = await tg.get_me(acc["id"], timeout=2.5)
                return {
                    "account_id": acc["id"],
                    "name": acc["name"],
                    "phone": acc["phone"],
                    "user": me
                }
        except Exception:
            pass
        return None

    results = await asyncio.gather(*(check_acc(acc) for acc in accounts), return_exceptions=True)
    logged_accounts = [r for r in results if r and not isinstance(r, Exception)]

    if logged_accounts:
        return {
            "authenticated": True,
            "accounts": logged_accounts,
            "user": logged_accounts[0]["user"] if logged_accounts else None
        }

    return {"authenticated": False, "has_accounts": len(accounts) > 0}


@router.post("/accounts/{account_id}/toggle-premium")
async def toggle_premium(account_id: int, is_premium: bool = True):
    """Toggle premium status for an account (affects daily DM limit: 10 normal / 50 premium)."""
    await db.set_account_premium(account_id, is_premium)
    return {
        "success": True,
        "is_premium": is_premium,
        "daily_limit": 50 if is_premium else 10,
        "message": f"Tài khoản {'premium (50 DM/ngày)' if is_premium else 'thường (10 DM/ngày)'}"
    }


@router.get("/accounts/{account_id}/dm-stats")
async def get_dm_stats(account_id: int):
    """Get today's DM count and limit for an account."""
    limit_reached, count, limit = await db.is_account_dm_limit_reached(account_id)
    return {
        "account_id": account_id,
        "today_dm_count": count,
        "daily_limit": limit,
        "limit_reached": limit_reached,
        "remaining": max(0, limit - count)
    }

@router.post("/accounts/{account_id}/unflag")
async def unflag_account_route(account_id: int):
    """Clear the warning flag on an account."""
    await db.unflag_account(account_id)
    return {"ok": True}


@router.get("/accounts/paused")
async def get_paused():
    """List all auto-paused accounts."""
    return await db.get_paused_accounts()


@router.post("/accounts/{account_id}/unpause")
async def unpause_account_route(account_id: int):
    """Manually unpause an auto-paused account."""
    await db.unpause_account(account_id)
    return {"ok": True, "message": f"Account {account_id} unpaused"}


@router.post("/accounts/{account_id}/toggle-active")
async def toggle_account_active(account_id: int, is_active: bool = True):
    """Toggle ON/OFF (active/paused) status for a Telegram account."""
    acc = await db.get_account(account_id)
    if not acc:
        raise HTTPException(404, "Tài khoản không tồn tại")
    
    if is_active:
        await db.unpause_account(account_id)
        msg = f"Đã BẬT tài khoản {acc.get('name') or acc.get('phone')} (Hoạt động bình thường)"
    else:
        await db.pause_account(account_id, "Tắt thủ công bởi người dùng")
        msg = f"Đã TẮT tài khoản {acc.get('name') or acc.get('phone')} (Ngắt tất cả AI, DM & Tự động hóa)"
        
    return {
        "success": True,
        "is_paused": not is_active,
        "is_active": is_active,
        "message": msg
    }


class SetAccountAiAgentRequest(BaseModel):
    ai_agent_id: Optional[int] = None


@router.post("/accounts/{account_id}/set-ai-agent")
async def set_account_ai_agent_route(account_id: int, req: SetAccountAiAgentRequest):
    """Assign or remove an AI Agent for organic DMs on this Telegram account."""
    acc = await db.get_account(account_id)
    if not acc:
        raise HTTPException(404, "Tài khoản không tồn tại")
    
    agent = None
    if req.ai_agent_id:
        agent = await db.get_ai_agent(req.ai_agent_id)
        if not agent:
            raise HTTPException(404, "AI Agent không tồn tại")
            
    await db.set_account_ai_agent(account_id, req.ai_agent_id)
    
    if agent:
        msg = f"Đã gán AI Agent '{agent['name']}' cho tài khoản {acc.get('name') or acc.get('phone')} (Auto-Pilot khi off máy)"
    else:
        msg = f"Đã tắt AI Agent cho tài khoản {acc.get('name') or acc.get('phone')}"
        
    return {
        "success": True,
        "account_id": account_id,
        "ai_agent_id": req.ai_agent_id,
        "message": msg
    }
