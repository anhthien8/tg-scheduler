"""
tests/test_engagement_api.py — Tests for Engagement API endpoints.

Covers:
- CRUD: GET/POST/PUT/DELETE /api/engagement/campaigns
- Validation: rate limits, schedule, reply_mode
- Consent gate: 409 on activate without consent
- Toggle: activate/pause
- Logs: GET /api/engagement/logs
- Stats: GET /api/engagement/stats
"""
import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def clean_db():
    """Initialize a clean database."""
    import database as db
    asyncio.run(db.init_db())
    yield db
    async def _cleanup():
        async with db.get_db() as conn:
            for table in (
                "engagement_campaigns", "engagement_logs",
                "engagement_claims", "engagement_window_counters",
                "engagement_dm_map",
            ):
                try:
                    await conn.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            await conn.commit()
    asyncio.run(_cleanup())


@pytest.fixture
def client(clean_db):
    """Create an async test client."""
    from main import app
    import os
    api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

    async def _make_client():
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": api_key},
        ) as ac:
            return ac

    return asyncio.run(_make_client())


# Helper to create a campaign via API
async def _create_campaign(client, **overrides):
    data = {
        "name": "Test Campaign",
        "group_ids": [123],
        "reply_account_ids": [1],
        "trigger_keywords": ["btc"],
        "reply_mode": "reactive",
    }
    data.update(overrides)
    resp = await client.post("/api/engagement/campaigns", json=data)
    return resp


class TestCRUD:
    def test_create_campaign(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.post("/api/engagement/campaigns", json={
                    "name": "My Campaign",
                    "group_ids": [100, 200],
                    "reply_account_ids": [1, 2],
                    "trigger_keywords": ["btc", "eth"],
                    "reply_mode": "reactive",
                })
                assert resp.status_code == 200
                data = resp.json()
                assert "id" in data
                assert data["status"] == "created"
        asyncio.run(_test())

    def test_get_campaigns(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                await client.post("/api/engagement/campaigns", json={
                    "name": "List Test",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                })
                resp = await client.get("/api/engagement/campaigns")
                assert resp.status_code == 200
                data = resp.json()
                assert "campaigns" in data
                assert len(data["campaigns"]) >= 1
        asyncio.run(_test())

    def test_get_single_campaign(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                r = await client.post("/api/engagement/campaigns", json={
                    "name": "Single Test",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                })
                cid = r.json()["id"]
                resp = await client.get(f"/api/engagement/campaigns/{cid}")
                assert resp.status_code == 200
                assert resp.json()["name"] == "Single Test"
        asyncio.run(_test())

    def test_get_nonexistent_campaign_404(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.get("/api/engagement/campaigns/99999")
                assert resp.status_code == 404
        asyncio.run(_test())

    def test_update_campaign(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                r = await client.post("/api/engagement/campaigns", json={
                    "name": "Update Me",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                })
                cid = r.json()["id"]
                resp = await client.put(f"/api/engagement/campaigns/{cid}", json={
                    "name": "Updated",
                    "max_replies_per_hour_per_group": 3,
                })
                assert resp.status_code == 200

                c = await clean_db.get_engagement_campaign(cid)
                assert c["name"] == "Updated"
                assert c["max_replies_per_hour_per_group"] == 3
        asyncio.run(_test())

    def test_delete_campaign(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                r = await client.post("/api/engagement/campaigns", json={
                    "name": "Delete Me",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                })
                cid = r.json()["id"]
                resp = await client.delete(f"/api/engagement/campaigns/{cid}")
                assert resp.status_code == 200
                assert resp.json()["status"] == "deleted"
        asyncio.run(_test())


class TestValidation:
    def test_rate_limit_hard_cap_hour(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.post("/api/engagement/campaigns", json={
                    "name": "Over Cap",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                    "max_replies_per_hour_per_group": 10,  # exceeds cap of 6
                })
                assert resp.status_code == 400
        asyncio.run(_test())

    def test_rate_limit_hard_cap_day(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.post("/api/engagement/campaigns", json={
                    "name": "Over Day Cap",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                    "max_replies_per_day_per_group": 25,  # exceeds cap of 20
                })
                assert resp.status_code == 400
        asyncio.run(_test())

    def test_min_gap_too_low(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.post("/api/engagement/campaigns", json={
                    "name": "Low Gap",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                    "min_gap_seconds": 30,  # below minimum of 60
                })
                assert resp.status_code == 400
        asyncio.run(_test())

    def test_invalid_reply_mode(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.post("/api/engagement/campaigns", json={
                    "name": "Proactive",
                    "group_ids": [1],
                    "reply_mode": "proactive",  # not supported in v1
                })
                assert resp.status_code == 400
        asyncio.run(_test())

    def test_invalid_schedule_format(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.post("/api/engagement/campaigns", json={
                    "name": "Bad Schedule",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                    "schedule_start": "25:00",  # invalid
                })
                assert resp.status_code == 400
        asyncio.run(_test())


class TestConsentGate:
    def test_activate_without_consent_409(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                r = await client.post("/api/engagement/campaigns", json={
                    "name": "No Consent",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                    "operator_consent_confirmed": 0,
                })
                cid = r.json()["id"]
                resp = await client.post(f"/api/engagement/campaigns/{cid}/activate")
                assert resp.status_code == 409
                assert "consent_required" in resp.json()["detail"]
        asyncio.run(_test())

    def test_activate_with_consent_succeeds(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                r = await client.post("/api/engagement/campaigns", json={
                    "name": "With Consent",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                    "operator_consent_confirmed": 1,
                })
                cid = r.json()["id"]
                resp = await client.post(f"/api/engagement/campaigns/{cid}/activate")
                assert resp.status_code == 200
                assert resp.json()["status"] == "active"
        asyncio.run(_test())

    def test_pause_campaign(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                r = await client.post("/api/engagement/campaigns", json={
                    "name": "Pause Test",
                    "group_ids": [1],
                    "reply_mode": "reactive",
                    "operator_consent_confirmed": 1,
                })
                cid = r.json()["id"]
                await client.post(f"/api/engagement/campaigns/{cid}/activate")
                resp = await client.post(f"/api/engagement/campaigns/{cid}/pause")
                assert resp.status_code == 200
                assert resp.json()["status"] == "paused"
        asyncio.run(_test())


class TestLogsAndStats:
    def test_get_logs(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.get("/api/engagement/logs")
                assert resp.status_code == 200
                data = resp.json()
                assert "logs" in data
                assert "total" in data
        asyncio.run(_test())

    def test_get_stats(self, clean_db):
        from main import app
        import os
        api_key = os.environ.get("DASHBOARD_SECRET_KEY", "test_secret_key")

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": api_key},
            ) as client:
                resp = await client.get("/api/engagement/stats")
                assert resp.status_code == 200
                data = resp.json()
                assert "total" in data
                assert "success" in data
        asyncio.run(_test())
