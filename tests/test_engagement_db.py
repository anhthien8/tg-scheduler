"""
tests/test_engagement_db.py — Tests for Engagement Campaign DB CRUD.

Covers:
- Table creation (idempotent)
- Campaign CRUD: create, get, update, delete, toggle
- JSON field parsing
- Consent gate logic
- Engagement logs CRUD
- DM map CRUD
- Stats aggregation
- Claims cleanup
"""
import asyncio
import json
import os
import sys

import pytest

# Tests run from repo root; conftest.py handles env vars and mocks


@pytest.fixture
def clean_db():
    """Initialize a clean database for each test."""
    import database as db
    asyncio.run(db.init_db())
    yield db
    # Cleanup engagement tables between tests
    async def _cleanup():
        async with db.get_db() as conn:
            for table in (
                "engagement_campaigns",
                "engagement_logs",
                "engagement_claims",
                "engagement_window_counters",
                "engagement_dm_map",
            ):
                try:
                    await conn.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            await conn.commit()
    asyncio.run(_cleanup())


# ── Campaign CRUD ─────────────────────────────────────────────────────────────

class TestCampaignCRUD:
    def test_create_campaign(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Test Campaign",
                "group_ids": [123, 456],
                "group_titles": ["Group A", "Group B"],
                "observer_account_ids": [1],
                "reply_account_ids": [1, 2],
                "trigger_keywords": ["btc", "eth"],
                "max_replies_per_hour_per_group": 4,
                "max_replies_per_day_per_group": 12,
            })
            assert cid is not None
            assert cid > 0
            return cid
        asyncio.run(_test())

    def test_get_campaign(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Get Test",
                "group_ids": [100],
                "trigger_keywords": ["sol"],
            })
            c = await db.get_engagement_campaign(cid)
            assert c is not None
            assert c["name"] == "Get Test"
            assert c["group_ids"] == [100]
            assert c["trigger_keywords"] == ["sol"]
            assert c["status"] == "draft"
            assert c["operator_consent_confirmed"] == 0
        asyncio.run(_test())

    def test_get_nonexistent_campaign(self, clean_db):
        db = clean_db
        async def _test():
            c = await db.get_engagement_campaign(99999)
            assert c is None
        asyncio.run(_test())

    def test_get_all_campaigns(self, clean_db):
        db = clean_db
        async def _test():
            await db.create_engagement_campaign({"name": "C1", "group_ids": [1]})
            await db.create_engagement_campaign({"name": "C2", "group_ids": [2]})
            all_c = await db.get_all_engagement_campaigns()
            assert len(all_c) >= 2
            names = [c["name"] for c in all_c]
            assert "C1" in names
            assert "C2" in names
        asyncio.run(_test())

    def test_update_campaign(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Before",
                "group_ids": [1],
            })
            ok = await db.update_engagement_campaign(cid, {"name": "After"})
            assert ok is True
            c = await db.get_engagement_campaign(cid)
            assert c["name"] == "After"
        asyncio.run(_test())

    def test_update_json_fields(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "JSON Test",
                "group_ids": [1],
                "trigger_keywords": ["old"],
            })
            ok = await db.update_engagement_campaign(cid, {
                "group_ids": [1, 2, 3],
                "trigger_keywords": ["btc", "eth", "sol"],
            })
            assert ok is True
            c = await db.get_engagement_campaign(cid)
            assert c["group_ids"] == [1, 2, 3]
            assert c["trigger_keywords"] == ["btc", "eth", "sol"]
        asyncio.run(_test())

    def test_delete_campaign(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Delete Me",
                "group_ids": [1],
            })
            ok = await db.delete_engagement_campaign(cid)
            assert ok is True
            c = await db.get_engagement_campaign(cid)
            assert c is None
        asyncio.run(_test())

    def test_delete_nonexistent(self, clean_db):
        db = clean_db
        async def _test():
            ok = await db.delete_engagement_campaign(99999)
            assert ok is False
        asyncio.run(_test())


# ── Toggle / Consent Gate ─────────────────────────────────────────────────────

class TestToggleConsent:
    def test_toggle_active_with_consent(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Consent Test",
                "group_ids": [1],
                "operator_consent_confirmed": 1,
            })
            ok = await db.toggle_engagement_campaign(cid, True)
            assert ok is True
            c = await db.get_engagement_campaign(cid)
            assert c["status"] == "active"
        asyncio.run(_test())

    def test_toggle_active_without_consent_fails(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "No Consent",
                "group_ids": [1],
                "operator_consent_confirmed": 0,
            })
            ok = await db.toggle_engagement_campaign(cid, True)
            assert ok is False
            c = await db.get_engagement_campaign(cid)
            assert c["status"] == "draft"  # unchanged
        asyncio.run(_test())

    def test_toggle_pause(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Pause Test",
                "group_ids": [1],
                "operator_consent_confirmed": 1,
            })
            await db.toggle_engagement_campaign(cid, True)
            ok = await db.toggle_engagement_campaign(cid, False)
            assert ok is True
            c = await db.get_engagement_campaign(cid)
            assert c["status"] == "paused"
        asyncio.run(_test())

    def test_get_active_campaigns(self, clean_db):
        db = clean_db
        async def _test():
            # Create draft campaign
            await db.create_engagement_campaign({
                "name": "Draft",
                "group_ids": [1],
            })
            # Create active campaign
            cid2 = await db.create_engagement_campaign({
                "name": "Active",
                "group_ids": [2],
                "operator_consent_confirmed": 1,
            })
            await db.toggle_engagement_campaign(cid2, True)
            active = await db.get_active_engagement_campaigns()
            assert len(active) == 1
            assert active[0]["name"] == "Active"
        asyncio.run(_test())


# ── JSON Field Parsing ────────────────────────────────────────────────────────

class TestJSONParsing:
    def test_json_fields_parsed_correctly(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "JSON Parse Test",
                "group_ids": [1, 2, 3],
                "group_titles": ["A", "B", "C"],
                "observer_account_ids": [10, 20],
                "reply_account_ids": [30, 40],
                "trigger_keywords": ["btc", "re:sol|solana"],
                "active_days": [1, 2, 3, 4, 5],
            })
            c = await db.get_engagement_campaign(cid)
            assert isinstance(c["group_ids"], list)
            assert isinstance(c["trigger_keywords"], list)
            assert isinstance(c["active_days"], list)
            assert c["group_ids"] == [1, 2, 3]
            assert c["active_days"] == [1, 2, 3, 4, 5]
        asyncio.run(_test())

    def test_defaults(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Defaults",
            })
            c = await db.get_engagement_campaign(cid)
            assert c["max_replies_per_hour_per_group"] == 4
            assert c["max_replies_per_day_per_group"] == 12
            assert c["min_gap_seconds"] == 180
            assert c["reply_mode"] == "reactive"
            assert c["schedule_start"] == "13:00"
            assert c["schedule_end"] == "23:00"
        asyncio.run(_test())


# ── Engagement Logs ───────────────────────────────────────────────────────────

class TestEngagementLogs:
    def test_create_and_get_logs(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Log Test",
                "group_ids": [1],
            })
            log_id = await db.create_engagement_log({
                "campaign_id": cid,
                "group_id": 1,
                "account_id": 10,
                "trigger_user_id": 100,
                "trigger_msg_id": 1000,
                "trigger_text": "What about BTC?",
                "reply_text": "BTC is consolidating near 62k...",
                "kind": "reply",
                "status": "success",
                "latency_ms": 35000,
            })
            assert log_id > 0

            logs = await db.get_engagement_logs(campaign_id=cid)
            assert len(logs) == 1
            assert logs[0]["trigger_text"] == "What about BTC?"
            assert logs[0]["status"] == "success"
        asyncio.run(_test())

    def test_log_count(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Count Test",
                "group_ids": [1],
            })
            for i in range(5):
                await db.create_engagement_log({
                    "campaign_id": cid,
                    "group_id": 1,
                    "kind": "reply",
                    "status": "success" if i < 3 else "skipped",
                })
            count = await db.get_engagement_log_count(cid)
            assert count == 5
        asyncio.run(_test())

    def test_log_filtering(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Filter Test",
                "group_ids": [1],
            })
            await db.create_engagement_log({"campaign_id": cid, "kind": "reply", "status": "success"})
            await db.create_engagement_log({"campaign_id": cid, "kind": "reply", "status": "skipped"})
            await db.create_engagement_log({"campaign_id": cid, "kind": "join", "status": "success"})

            success_logs = await db.get_engagement_logs(campaign_id=cid, status="success")
            assert len(success_logs) == 2
            reply_logs = await db.get_engagement_logs(campaign_id=cid, kind="reply")
            assert len(reply_logs) == 2
        asyncio.run(_test())


# ── DM Map ────────────────────────────────────────────────────────────────────

class TestDMMap:
    def test_create_and_find_dm_map(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "DM Map Test",
                "group_ids": [1],
            })
            map_id = await db.create_engagement_dm_map(cid, 10, 100)
            assert map_id > 0

            found = await db.find_engagement_dm_map(10, 100)
            assert found is not None
            assert found["campaign_id"] == cid
        asyncio.run(_test())

    def test_dm_map_not_found(self, clean_db):
        db = clean_db
        async def _test():
            found = await db.find_engagement_dm_map(999, 888)
            assert found is None
        asyncio.run(_test())


# ── Stats ─────────────────────────────────────────────────────────────────────

class TestEngagementStats:
    def test_stats_aggregation(self, clean_db):
        db = clean_db
        async def _test():
            cid = await db.create_engagement_campaign({
                "name": "Stats Test",
                "group_ids": [1],
            })
            await db.create_engagement_log({
                "campaign_id": cid, "kind": "reply", "status": "success",
            })
            await db.create_engagement_log({
                "campaign_id": cid, "kind": "reply", "status": "success",
            })
            await db.create_engagement_log({
                "campaign_id": cid, "kind": "reply", "status": "skipped",
                "skip_reason": "rate_limit",
            })
            await db.create_engagement_log({
                "campaign_id": cid, "kind": "reply", "status": "failed",
            })
            stats = await db.get_engagement_stats(campaign_id=cid)
            assert stats["total"] == 4
            assert stats["success"] == 2
            assert stats["skipped"] == 1
            assert stats["failed"] == 1
            assert "rate_limit" in stats["skip_reasons"]
        asyncio.run(_test())


# ── Claims Cleanup ────────────────────────────────────────────────────────────

class TestClaimsCleanup:
    def test_cleanup(self, clean_db):
        db = clean_db
        async def _test():
            # Insert old claims (simulate 3 days old)
            async with db.get_db() as conn:
                await conn.execute(
                    "INSERT INTO engagement_claims(campaign_id, group_id, account_id, window_key, created_at) "
                    "VALUES(1, 1, 1, 'test:old', datetime('now', '-3 days'))"
                )
                await conn.execute(
                    "INSERT INTO engagement_claims(campaign_id, group_id, account_id, window_key) "
                    "VALUES(1, 1, 1, 'test:new')"
                )
                await conn.commit()
            cleaned = await db.cleanup_engagement_claims(max_age_days=2)
            assert cleaned >= 1
        asyncio.run(_test())


# ── Migration Idempotency ────────────────────────────────────────────────────

class TestMigration:
    def test_init_db_idempotent(self, clean_db):
        db = clean_db
        async def _test():
            # init_db already ran in fixture; run again should not error
            await db.init_db()
            # Verify tables exist by inserting
            cid = await db.create_engagement_campaign({
                "name": "Idempotent",
                "group_ids": [1],
            })
            assert cid > 0
        asyncio.run(_test())
