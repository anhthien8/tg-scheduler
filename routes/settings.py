"""
Settings API Routes - store/retrieve key-value settings like AI API keys.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json

import database as db

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingPayload(BaseModel):
    value: str


class TestRemixPayload(BaseModel):
    provider: str
    keys: list
    text: str
    sender_name: Optional[str] = None


# NOTE: /test-remix MUST be declared before /{key} wildcard
@router.post("/test-remix")
async def test_remix(payload: TestRemixPayload):
    """Test AI remix with provided keys directly (no DB save needed)."""
    import ai_remix as ai_rmx
    if payload.provider not in ("gemini", "deepseek", "openai", "groq"):
        raise HTTPException(status_code=400, detail="Invalid provider")
    if not payload.keys:
        raise HTTPException(status_code=400, detail="No API keys provided")
    try:
        remixed = await ai_rmx.remix_message(
            original_text=payload.text,
            provider=payload.provider,
            api_keys=payload.keys,
            sender_name=payload.sender_name
        )
        return {"success": True, "remixed": remixed, "original": payload.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{key}")
async def get_setting(key: str):
    value = await db.get_setting(key, None)
    return {"key": key, "value": value}


@router.post("/{key}")
async def set_setting(key: str, payload: SettingPayload):
    await db.set_setting(key, payload.value)
    return {"key": key, "value": payload.value, "message": "Saved"}
