"""Leads waiting on us for 30+ days go dormant; they wake up when the KOL writes again."""
import json
from datetime import datetime, timedelta

import pytest

import database as db
import dm_reply_tracker as tracker

A = 1


async def _lead(uid, *, status="needs_human", last_role="user", text="hello?", days=40, owed_days=None, step=None):
    await db.get_or_create_followup_chat(account_id=A, user_id=uid, username=f"k{uid}", name="K")
    ts = (datetime.now() - timedelta(days=days)).isoformat()
    hist = [{"role": "assistant", "content": "hi", "timestamp": ts},
            {"role": last_role, "content": text, "timestamp": ts}]
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE ai_followup_chats SET history_json = ?, status = ?, kol_step = ?, "
            "owed_since = CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END WHERE account_id = ? AND user_id = ?",
            (json.dumps(hist), status, step, owed_days, f"-{owed_days or 0} days", A, uid))
        await conn.commit()


@pytest.mark.asyncio
async def test_only_leads_waiting_on_us_for_30_days_go_dormant():
    await _lead(1)                                              # KOL spoke last, 40 days → dormant
    await _lead(2, last_role="assistant", text="let me check")  # AI handed over, 40 days → dormant
    await _lead(3, days=5, owed_days=35, last_role="assistant", text="team will get back")  # recent activity → stays
    await _lead(4, days=40, owed_days=35, last_role="assistant", text="team will get back")  # old promise → dormant
    await _lead(5, days=10)                                     # only 10 days → stays
    await _lead(6, last_role="assistant", text="[Human Admin]: done")  # we answered, ball with KOL → stays
    await _lead(7, status="active")                             # AI still chatting → stays
    await _lead(8, step="active")                               # running KOL → stays

    res = await tracker.process_stale_queue_dormant(force=True)
    assert res == {"dormant": 3}
    steps = {u: await db.get_kol_step(A, u) for u in range(1, 9)}
    assert [u for u, s in steps.items() if s == "dormant"] == [1, 2, 4]
    assert (await db.get_followup_chat(A, 4))["owed_since"] is None
    assert (await tracker.process_stale_queue_dormant(force=True)) == {"dormant": 0}


@pytest.mark.asyncio
async def test_dormant_lead_wakes_when_they_write_but_admin_dormant_stays():
    await _lead(1, step="deal_agreed")
    await tracker.process_stale_queue_dormant(force=True)
    assert await db.wake_dormant_lead(A, 1) == "deal_agreed"
    assert await db.get_kol_step(A, 1) == "deal_agreed"

    await _lead(2, days=1)
    await db.set_kol_step(A, 2, "dormant", actor="admin", reason="manual")
    assert await db.wake_dormant_lead(A, 2) is None
    assert await db.get_kol_step(A, 2) == "dormant"
