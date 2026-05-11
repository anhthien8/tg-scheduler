"""
Authentication & Account management routes.
"""
import os
import logging
from fastapi import APIRouter, HTTPException
from models import SendCodeRequest, VerifyCodeRequest, AccountCreate
import telegram_client as tg
import database as db
import scheduler as sch

logger = logging.getLogger("tg-scheduler")
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Account CRUD ──

@router.get("/accounts")
async def list_accounts():
    """List all Telegram accounts."""
    accounts = await db.get_all_accounts()
    # Add login status from live client
    for acc in accounts:
        acc["is_logged_in"] = await tg.is_authorized(acc["id"])
        me = await tg.get_me(acc["id"])
        if me:
            acc["user_info"] = me
    return {"accounts": accounts}


@router.post("/accounts")
async def add_account(req: AccountCreate):
    """Add a new Telegram account."""
    # Create unique session name
    session_name = f"account_{req.phone.replace('+', '').replace(' ', '')}"

    account_id = await db.create_account({
        "name": req.name,
        "phone": req.phone,
        "api_id": req.api_id,
        "api_hash": req.api_hash,
        "session_name": session_name
    })

    # Create and connect the client
    try:
        await tg.create_client(account_id, int(req.api_id), req.api_hash, session_name)
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
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/verify")
async def verify_code(req: VerifyCodeRequest):
    """Verify OTP code and sign in."""
    result = await tg.sign_in(req.account_id, req.phone, req.code,
                              req.phone_code_hash, req.password)
    if result.get("success"):
        await db.update_account_login_status(req.account_id, True)
        # Load scheduler jobs for this account
        await sch.load_all_jobs()
        return result
    if result.get("needs_password"):
        raise HTTPException(status_code=401, detail="2FA password required")
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
    any_logged_in = False
    logged_accounts = []

    for acc in accounts:
        is_auth = await tg.is_authorized(acc["id"])
        if is_auth:
            any_logged_in = True
            me = await tg.get_me(acc["id"])
            logged_accounts.append({
                "account_id": acc["id"],
                "name": acc["name"],
                "phone": acc["phone"],
                "user": me
            })

    if any_logged_in:
        return {
            "authenticated": True,
            "accounts": logged_accounts,
            "user": logged_accounts[0]["user"] if logged_accounts else None
        }

    return {"authenticated": False, "has_accounts": len(accounts) > 0}
