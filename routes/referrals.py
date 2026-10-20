"""API routes for C5: Referral / Viral Loop."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import database as db

logger = logging.getLogger("tg-scheduler.referrals")
router = APIRouter(prefix="/api/referrals", tags=["referrals"])

REFERRAL_PATTERN = re.compile(r'\bREF-[A-Z0-9]{8}\b')


# ── Pydantic Models ──────────────────────────────────────────────────────

class CreateCodeRequest(BaseModel):
    account_id: int
    referrer_user_id: int
    max_uses: int = 10
    expires_at: str | None = None


class ProcessReferralRequest(BaseModel):
    code: str
    referred_user_id: int
    referred_username: str | None = None


# ── Routes ───────────────────────────────────────────────────────────────

@router.post("/codes")
async def create_code(req: CreateCodeRequest):
    """Create a new referral code for a KOL."""
    result = await db.create_referral_code(
        account_id=req.account_id,
        referrer_user_id=req.referrer_user_id,
        max_uses=req.max_uses,
        expires_at=req.expires_at,
    )
    return {"ok": True, **result}


@router.get("/codes")
async def list_codes():
    """List all referral codes."""
    codes = await db.get_all_referral_codes()
    return {"ok": True, "codes": codes}


@router.get("/codes/{code}")
async def get_code(code: str):
    """Get referral code details + its attributions."""
    ref = await db.get_referral_code(code)
    if not ref:
        raise HTTPException(status_code=404, detail="Code not found")
    attrs = await db.get_attributions_by_referrer(ref["referrer_user_id"])
    return {"ok": True, "code": ref, "attributions": attrs}


@router.delete("/codes/{code_id}")
async def deactivate_code(code_id: int):
    """Deactivate a referral code."""
    await db.deactivate_referral_code(code_id)
    return {"ok": True}


@router.post("/process")
async def process_referral(req: ProcessReferralRequest):
    """Manually attribute a referral."""
    success, msg = await _process_referral_code(
        req.code, req.referred_user_id, req.referred_username
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


@router.get("/attributions")
async def list_attributions():
    """List all referral attributions."""
    all_attrs = await db.get_all_referral_attributions()
    return {"ok": True, "attributions": all_attrs}


@router.get("/stats")
async def get_stats():
    """Get referral program summary stats."""
    stats = await db.get_referral_stats()
    return {"ok": True, **stats}


# ── Referral Processing Logic ────────────────────────────────────────────

async def _process_referral_code(
    code: str, referred_user_id: int, referred_username: str | None = None,
) -> tuple[bool, str]:
    """Process a referral code. Returns (success, message)."""
    ref_code = await db.get_referral_code(code)
    if not ref_code:
        return False, "Referral code not found"
    if not ref_code["is_active"]:
        return False, "Referral code is inactive"
    if ref_code["uses_count"] >= ref_code["max_uses"]:
        return False, "Referral code has reached maximum uses"
    if ref_code.get("expires_at"):
        from datetime import datetime, timezone
        try:
            exp = datetime.fromisoformat(ref_code["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return False, "Referral code has expired"
        except (ValueError, TypeError):
            pass
    # Self-referral check
    if ref_code["referrer_user_id"] == referred_user_id:
        return False, "Cannot self-refer"
    # Check duplicate
    existing = await db.get_attribution_by_referred(referred_user_id)
    if existing:
        return False, "User already referred"

    attr_id = await db.create_referral_attribution(
        referral_code_id=ref_code["id"],
        referrer_user_id=ref_code["referrer_user_id"],
        referred_user_id=referred_user_id,
        referred_username=referred_username,
    )
    await db.increment_referral_uses(ref_code["id"])
    logger.info("[Referral] Attributed: %s → user %d (attr_id=%d)", code, referred_user_id, attr_id)
    return True, f"Successfully attributed referral {code}"


async def check_referral_in_message(text: str, user_id: int, username: str | None = None):
    """Detect and process referral codes in incoming messages."""
    if not text:
        return
    match = REFERRAL_PATTERN.search(text)
    if match:
        code = match.group(0)
        success, msg = await _process_referral_code(code, user_id, username)
        if success:
            logger.info("[Referral] Auto-detected code %s from user %d: %s", code, user_id, msg)
