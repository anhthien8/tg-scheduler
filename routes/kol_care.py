"""KOL care API: due greetings / check-ins / monthly reviews, drafts, touch log, BD scorecard.

Nothing here sends a message: the dashboard sends the edited draft through
/api/ai-followup/chats/{account_id}/{user_id}/send, then logs the touch here.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import kol_care

router = APIRouter(prefix="/api/kol-care", tags=["kol-care"])


class DraftRequest(BaseModel):
    account_id: int
    user_id: int
    kind: str


class TouchRequest(BaseModel):
    account_id: int
    user_id: int
    kind: str
    note: str = ""


@router.get("/due")
async def due():
    return {"items": await kol_care.due_items()}


@router.post("/draft")
async def draft(req: DraftRequest):
    try:
        text = await kol_care.generate_draft(req.account_id, req.user_id, req.kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not text:
        raise HTTPException(status_code=502, detail="AI không trả về nội dung, thử lại sau")
    return {"text": text}


@router.post("/touch")
async def touch(req: TouchRequest):
    try:
        await kol_care.log_touchpoint(req.account_id, req.user_id, req.kind, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/refresh-volumes")
async def refresh_volumes():
    import weex_affiliate
    try:
        return {"saved": await kol_care.refresh_volumes()}
    except weex_affiliate.WeexAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Không lấy được volume WEEX: {e}")


@router.get("/scorecard")
async def scorecard():
    return await kol_care.scorecard()
