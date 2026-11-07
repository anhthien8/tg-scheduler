"""Shared FloodWait registry + 7-day health window."""
import pytest

import database as db
import flood_state

pytestmark = pytest.mark.asyncio


async def test_flood_state_never_shortens_rest():
    flood_state.mark(901, 600)
    flood_state.mark(901, 10)
    assert flood_state.is_resting(901) and flood_state.remaining(901) > 500
    assert not flood_state.is_resting(902)


async def test_account_health_counts_only_last_7_days():
    acc_id = await db.create_account({"id": 77, "name": "A", "phone": "+1", "api_id": "a",
                             "api_hash": "h", "session_name": "s77"})
    async with db.get_db() as conn:
        await conn.execute("PRAGMA foreign_keys=OFF")  # logs only; no campaign row needed
        for age in ("-1 days", "-30 days", "-40 days"):
            await conn.execute(
                "INSERT INTO dm_campaign_logs (campaign_id, account_id, target_user_id, status, error_message, sent_at) "
                "VALUES (1, ?, 5, 'failed', 'FloodWait: chờ 60s', datetime('now', ?))", (acc_id, age))
        await conn.commit()
    row = next(r for r in await db.get_analytics_account_health() if r["account_id"] == acc_id)
    assert row["flood_count"] == 1
