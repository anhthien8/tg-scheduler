"""Connect the WEEX affiliate portal: the "Kết nối WEEX" bookmarklet (run on affiliates.weex.com) POSTs the
portal token straight to /api/weex/push with a connect secret that can ONLY set this token.
No popup: the dashboard is an installed PWA and its windows swallow any 127.0.0.1 page."""
import secrets
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import alerts
import database as db
import weex_affiliate as wx

router = APIRouter(prefix="/api/weex", tags=["weex"])
public_router = APIRouter(prefix="/api/weex", tags=["weex"])  # no dashboard auth: guarded by the connect secret

SECRET_KEY = "weex_connect_secret"


class TokenPayload(BaseModel):
    token: str


class PushPayload(BaseModel):
    token: str
    secret: str


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


@router.get("/connect-secret")
async def connect_secret():
    """Secret baked into the bookmarklet; created once."""
    sec = await db.get_setting(SECRET_KEY, "")
    if not sec:
        sec = secrets.token_urlsafe(24)
        await db.set_setting(SECRET_KEY, sec)
    return {"secret": sec}


@public_router.post("/push")
async def push_token(payload: PushPayload):
    sec = await db.get_setting(SECRET_KEY, "") or ""
    if not sec or not secrets.compare_digest(payload.secret, sec):
        raise HTTPException(403, "Bookmark cũ hoặc sai — kéo lại nút Kết nối WEEX trong Cài đặt")
    return await save_token(TokenPayload(token=payload.token))


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
