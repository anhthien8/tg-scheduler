"""KOL contacts API (v3.40): leads who sent a trading proof, saved as "<name>_KOL"."""
from fastapi import APIRouter

import kol_contacts as kc

router = APIRouter(prefix="/api/kol-contacts", tags=["kol-contacts"])


@router.get("")
async def list_kol_contacts():
    rows = await kc.list_contacts()
    return {"contacts": rows, "backfill": kc.backfill_status()}


@router.get("/backfill/preview")
async def preview_backfill():
    cands = await kc.backfill_candidates()
    return {"count": len(cands)}


@router.post("/backfill")
async def start_backfill():
    return await kc.run_backfill()
