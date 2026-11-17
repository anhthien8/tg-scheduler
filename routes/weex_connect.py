"""Connect the WEEX affiliate portal: receive the portal token (sent by the "Kết nối WEEX" bookmarklet
via /weex-connect.html), report status, toggle auto tier-up / onboarding."""
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import alerts
import database as db
import weex_affiliate as wx

router = APIRouter(prefix="/api/weex", tags=["weex"])


class TokenPayload(BaseModel):
    token: str


class EnablePayload(BaseModel):
    enabled: bool


async def _status() -> dict:
    token = (await db.get_setting(wx.TOKEN_KEY, "") or "").strip()
    exp = alerts.jwt_exp(token)
    return {
        "connected": bool(token) and exp > time.time(),
        "expires_at": exp or None,
        "enabled": await db.get_setting(wx.ENABLED_KEY, "0") == "1",
    }


@router.get("/status")
async def status():
    return await _status()


@router.post("/token")
async def save_token(payload: TokenPayload):
    token = payload.token.strip().strip('"')
    if alerts.jwt_exp(token) <= time.time():
        raise HTTPException(400, "Token không hợp lệ hoặc đã hết hạn — đăng nhập lại portal rồi bấm lại")
    old = await db.get_setting(wx.TOKEN_KEY, "")
    await db.set_setting(wx.TOKEN_KEY, token)
    try:
        await wx.list_agents("0")  # live check against the portal
    except Exception as e:
        await db.set_setting(wx.TOKEN_KEY, old or "")
        raise HTTPException(400, f"WEEX không nhận token: {e}")
    return await _status()


@router.post("/enable")
async def enable(payload: EnablePayload):
    await db.set_setting(wx.ENABLED_KEY, "1" if payload.enabled else "0")
    return await _status()
