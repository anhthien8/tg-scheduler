import pytest
import database as db
from datetime import datetime

pytestmark = pytest.mark.asyncio

async def test_schedules_limit_and_active_only(client):
    # 1. Create an account
    acc_id = await db.create_account({
        "name": "Test Acc",
        "phone": "+849999999",
        "api_id": "12345",
        "api_hash": "abc",
        "session_name": "test_sess"
    })
    
    # 2. Create schedules
    # Active schedule 1
    sch1 = await db.create_schedule({
        "account_id": acc_id,
        "name": "Active 1",
        "schedule_type": "daily",
        "time_of_day": "12:00",
        "is_active": 1
    })
    # Active schedule 2
    sch2 = await db.create_schedule({
        "account_id": acc_id,
        "name": "Active 2",
        "schedule_type": "daily",
        "time_of_day": "13:00",
        "is_active": 1
    })
    # Inactive schedule
    sch3 = await db.create_schedule({
        "account_id": acc_id,
        "name": "Inactive 1",
        "schedule_type": "daily",
        "time_of_day": "14:00",
        "is_active": 0
    })

    # Test GET /api/schedules without parameters
    resp = client.get("/api/schedules")
    assert resp.status_code == 200
    data = resp.json()["schedules"]
    assert len(data) == 3

    # Test GET /api/schedules?active_only=true
    resp = client.get("/api/schedules?active_only=true")
    assert resp.status_code == 200
    data = resp.json()["schedules"]
    assert len(data) == 2
    names = [s["name"] for s in data]
    assert "Active 1" in names
    assert "Active 2" in names
    assert "Inactive 1" not in names

    # Test GET /api/schedules?limit=1
    resp = client.get("/api/schedules?limit=1")
    assert resp.status_code == 200
    data = resp.json()["schedules"]
    assert len(data) == 1

    # Test GET /api/schedules?active_only=true&limit=1
    resp = client.get("/api/schedules?active_only=true&limit=1")
    assert resp.status_code == 200
    data = resp.json()["schedules"]
    assert len(data) == 1
    assert data[0]["is_active"] == 1


async def test_campaigns_updated_since(client):
    # Create campaigns
    # Campaign 1
    c1_id = await db.create_dm_campaign({
        "name": "Campaign 1",
        "scrape_job_id": "job_1",
        "sender_account_ids": [1],
        "messages": [],
        "total_targets": 10
    })
    
    # Let's verify and update updated_at using direct DB update so we can control timestamps
    import aiosqlite
    async with db.get_db() as conn:
        await conn.execute("UPDATE dm_campaigns SET updated_at = '2026-07-13 10:00:00' WHERE id = ?", (c1_id,))
        await conn.commit()

    # Campaign 2
    c2_id = await db.create_dm_campaign({
        "name": "Campaign 2",
        "scrape_job_id": "job_2",
        "sender_account_ids": [1],
        "messages": [],
        "total_targets": 20
    })
    async with db.get_db() as conn:
        await conn.execute("UPDATE dm_campaigns SET updated_at = '2026-07-13 12:00:00' WHERE id = ?", (c2_id,))
        await conn.commit()

    # Test GET /api/members/campaigns without parameter
    resp = client.get("/api/members/campaigns")
    assert resp.status_code == 200
    data = resp.json()["campaigns"]
    assert len(data) == 2

    # Test GET /api/members/campaigns?updated_since=2026-07-13 11:00:00
    resp = client.get("/api/members/campaigns?updated_since=2026-07-13 11:00:00")
    assert resp.status_code == 200
    data = resp.json()["campaigns"]
    assert len(data) == 1
    assert data[0]["name"] == "Campaign 2"
