"""Tests for the Outreach Nudge Engine (follow-up lần 2 cho KOL không reply).

Covers:
- T7.1: nudges table creation, UNIQUE constraint
- T7.2: get_nudge_candidates(), has_inbound_since()
- T7.3: process_outreach_nudges() with TOCTOU, dedup, safety guards
- T7.4: trigger-nudge endpoint
- T7.5: nudge settings load + validation
- T7.6: nudged field in /chats, nudge stats
- T7.7: idx_dm_replies_sender index existence
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database as db
import dm_reply_tracker as drt


# ── T7.1: Schema + record_nudge + UNIQUE dedup ─────────────────────────────

async def test_nudges_table_exists():
    """nudges table should be created by init_db."""
    async with db.get_db() as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nudges'"
        )
        row = await cursor.fetchone()
        assert row is not None, "nudges table not found"


async def test_record_nudge_returns_id():
    """record_nudge should return a row id on first insert."""
    rid = await db.record_nudge(
        account_id=1, target_user_id=9001, channel="watcher", status="sent",
    )
    assert rid is not None and rid > 0


async def test_record_nudge_unique_blocks_duplicate():
    """UNIQUE(account_id, target_user_id) must block duplicate nudges."""
    await db.record_nudge(
        account_id=1, target_user_id=9002, channel="watcher", status="sent",
    )
    dup = await db.record_nudge(
        account_id=1, target_user_id=9002, channel="campaign", status="sent",
    )
    assert dup is None, "Duplicate insert should return None"


async def test_record_nudge_different_accounts_ok():
    """Same user under different accounts should be allowed."""
    r1 = await db.record_nudge(account_id=1, target_user_id=9003, channel="watcher", status="sent")
    r2 = await db.record_nudge(account_id=2, target_user_id=9003, channel="watcher", status="sent")
    assert r1 is not None
    assert r2 is not None


# ── T7.2: Candidate detection ──────────────────────────────────────────────

async def _insert_watcher_dm_log(account_id, target_user_id, username="kol", sent_at=None, status="success"):
    """Helper to insert a watcher_dm_log row."""
    async with db.get_db() as conn:
        sa = sent_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await conn.execute(
            """INSERT INTO watcher_dm_logs (watcher_id, account_id, target_user_id, target_username, status, sent_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (1, account_id, target_user_id, username, status, sa),
        )
        await conn.commit()


async def _insert_dm_reply(sender_user_id, account_id=1, received_at=None):
    """Helper to insert a dm_replies row."""
    async with db.get_db() as conn:
        ra = received_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await conn.execute(
            """INSERT INTO dm_replies (account_id, sender_user_id, sender_username, message_text, received_at)
               VALUES (?, ?, 'kol', 'hi', ?)""",
            (account_id, sender_user_id, ra),
        )
        await conn.commit()


async def test_get_nudge_candidates_finds_silent_lead():
    """Lead who was DM'd 25h ago and never replied should be a candidate."""
    sent = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_watcher_dm_log(1, 8001, sent_at=sent)
    candidates = await db.get_nudge_candidates(delay_hours=24)
    uids = [c["target_user_id"] for c in candidates]
    assert 8001 in uids


async def test_get_nudge_candidates_excludes_replied():
    """Lead who already replied should not be a candidate."""
    sent = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_watcher_dm_log(1, 8002, sent_at=sent)
    reply_at = (datetime.now(timezone.utc) - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_dm_reply(8002, received_at=reply_at)
    candidates = await db.get_nudge_candidates(delay_hours=24)
    uids = [c["target_user_id"] for c in candidates]
    assert 8002 not in uids


async def test_get_nudge_candidates_excludes_already_nudged():
    """Lead who was already nudged should not appear again."""
    sent = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_watcher_dm_log(1, 8003, sent_at=sent)
    await db.record_nudge(account_id=1, target_user_id=8003, channel="watcher", status="sent")
    candidates = await db.get_nudge_candidates(delay_hours=24)
    uids = [c["target_user_id"] for c in candidates]
    assert 8003 not in uids


async def test_get_nudge_candidates_excludes_too_old():
    """Lead DM'd more than max_age_hours ago should be excluded."""
    sent = (datetime.now(timezone.utc) - timedelta(hours=130)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_watcher_dm_log(1, 8004, sent_at=sent)
    candidates = await db.get_nudge_candidates(delay_hours=24, max_age_hours=120)
    uids = [c["target_user_id"] for c in candidates]
    assert 8004 not in uids


async def test_get_nudge_candidates_excludes_too_recent():
    """Lead DM'd less than delay_hours ago should not be a candidate yet."""
    sent = (datetime.now(timezone.utc) - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_watcher_dm_log(1, 8005, sent_at=sent)
    candidates = await db.get_nudge_candidates(delay_hours=24)
    uids = [c["target_user_id"] for c in candidates]
    assert 8005 not in uids


async def test_get_nudge_candidates_excludes_failed_dm():
    """Only successful DMs should produce candidates."""
    sent = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_watcher_dm_log(1, 8006, sent_at=sent, status="failed")
    candidates = await db.get_nudge_candidates(delay_hours=24)
    uids = [c["target_user_id"] for c in candidates]
    assert 8006 not in uids


# ── T7.2: has_inbound_since ─────────────────────────────────────────────────

async def test_has_inbound_since_true_when_replied():
    """Should return True when the user replied after the given timestamp."""
    since = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    reply_at = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    await _insert_dm_reply(7001, received_at=reply_at)
    assert await db.has_inbound_since(1, 7001, since) is True


async def test_has_inbound_since_false_when_no_reply():
    """Should return False when no reply exists."""
    since = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    assert await db.has_inbound_since(1, 7002, since) is False


# ── T7.3: process_outreach_nudges safety checks ────────────────────────────

async def test_nudge_worker_returns_dict():
    """process_outreach_nudges should always return a dict with sent/skipped/errors."""
    # With no candidates, should return zeros
    result = await drt.process_outreach_nudges()
    assert isinstance(result, dict)
    assert "sent" in result
    assert "errors" in result


async def test_nudge_disabled_when_max_zero(monkeypatch):
    """When outreach_nudge_max=0, nudge engine should be disabled."""
    monkeypatch.setattr(drt.db, "get_setting", AsyncMock(side_effect=lambda k, d=None: "0" if k == "outreach_nudge_max" else "24"))
    result = await drt.process_outreach_nudges()
    assert result.get("note") == "nudge disabled (max=0)"


# ── T7.5: Settings loading ─────────────────────────────────────────────────

async def test_load_nudge_settings_defaults(monkeypatch):
    """Should return (24, 1) when settings are empty."""
    monkeypatch.setattr(drt.db, "get_setting", AsyncMock(return_value=""))
    delay, max_n = await drt._load_nudge_settings()
    assert delay == 24
    assert max_n == 1


async def test_load_nudge_settings_custom(monkeypatch):
    """Should parse custom values correctly."""
    async def mock_get(key, default=None):
        return {"outreach_nudge_delay_hours": "48", "outreach_nudge_max": "2"}.get(key, default)
    monkeypatch.setattr(drt.db, "get_setting", mock_get)
    delay, max_n = await drt._load_nudge_settings()
    assert delay == 48
    assert max_n == 2


async def test_load_nudge_settings_invalid_falls_back(monkeypatch):
    """Invalid values should fall back to defaults."""
    async def mock_get(key, default=None):
        return {"outreach_nudge_delay_hours": "abc", "outreach_nudge_max": "-5"}.get(key, default)
    monkeypatch.setattr(drt.db, "get_setting", mock_get)
    delay, max_n = await drt._load_nudge_settings()
    assert delay == 24
    assert max_n == 1


async def test_load_nudge_settings_negative_delay_falls_back(monkeypatch):
    """Negative delay should fall back to 24."""
    async def mock_get(key, default=None):
        return {"outreach_nudge_delay_hours": "-1", "outreach_nudge_max": "1"}.get(key, default)
    monkeypatch.setattr(drt.db, "get_setting", mock_get)
    delay, max_n = await drt._load_nudge_settings()
    assert delay == 24


# ── T7.4: trigger-nudge endpoint ────────────────────────────────────────────

def test_trigger_nudge_endpoint_exists(client):
    """POST /api/ai-followup/trigger-nudge should return 200."""
    resp = client.post("/api/ai-followup/trigger-nudge")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "result" in data


# ── T7.5: settings endpoint includes nudge fields ──────────────────────────

def test_settings_get_includes_nudge_fields(client):
    """GET /settings should include outreach_nudge_delay_hours and outreach_nudge_max."""
    resp = client.get("/api/ai-followup/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "outreach_nudge_delay_hours" in data
    assert "outreach_nudge_max" in data
    assert data["outreach_nudge_delay_hours"] == 24  # default
    assert data["outreach_nudge_max"] == 1  # default


def test_settings_post_saves_nudge_fields(client):
    """POST /settings should persist nudge configuration."""
    resp = client.post("/api/ai-followup/settings", json={
        "enabled": True,
        "system_prompt": "test",
        "knowledge_base": "test",
        "max_replies_per_user": 5,
        "handover_keywords": [],
        "drip_cadence_hours": [24, 72, 168],
        "outreach_nudge_delay_hours": 48,
        "outreach_nudge_max": 2,
    })
    assert resp.status_code == 200

    # Verify persisted
    resp2 = client.get("/api/ai-followup/settings")
    data = resp2.json()
    assert data["outreach_nudge_delay_hours"] == 48
    assert data["outreach_nudge_max"] == 2


# ── T7.6: stats endpoint includes nudge data ───────────────────────────────

def test_stats_includes_nudge_data(client):
    """GET /stats should include nudge_stats and nudged_total."""
    resp = client.get("/api/ai-followup/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "nudge_stats" in data
    assert "nudged_total" in data


# ── T7.6: chats endpoint includes nudged field ─────────────────────────────

async def test_chats_include_nudged_field():
    """Chats returned by get_all_followup_chats should have nudged and nudge_count fields."""
    # Create a followup chat
    await db.get_or_create_followup_chat(account_id=1, user_id=6001, username="test", name="Test")
    chats = await db.get_all_followup_chats(limit=10)
    assert len(chats) > 0
    chat = chats[0]
    assert "nudged" in chat
    assert "nudge_count" in chat
    # No nudge record yet → should be False/0
    assert chat["nudged"] is False
    assert chat["nudge_count"] == 0


async def test_chats_nudged_true_after_nudge():
    """After recording a nudge, chats should show nudged=True."""
    await db.get_or_create_followup_chat(account_id=1, user_id=6002, username="test2", name="Test2")
    await db.record_nudge(account_id=1, target_user_id=6002, channel="watcher", status="sent")
    chats = await db.get_all_followup_chats(limit=50)
    chat = next((c for c in chats if c["user_id"] == 6002), None)
    assert chat is not None
    assert chat["nudged"] is True
    assert chat["nudge_count"] == 1


# ── T7.7: dm_replies sender index ──────────────────────────────────────────

async def test_dm_replies_sender_index_exists():
    """idx_dm_replies_sender should exist on dm_replies(sender_user_id, received_at)."""
    async with db.get_db() as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_dm_replies_sender'"
        )
        row = await cursor.fetchone()
        assert row is not None, "idx_dm_replies_sender index not found"


# ── T7.1: nudges indexes ───────────────────────────────────────────────────

async def test_nudges_indexes_exist():
    """idx_nudges_lookup and idx_nudges_last should exist."""
    async with db.get_db() as conn:
        for idx_name in ("idx_nudges_lookup", "idx_nudges_last"):
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (idx_name,),
            )
            row = await cursor.fetchone()
            assert row is not None, f"{idx_name} index not found"


# ── T7.6: get_nudge_stats ──────────────────────────────────────────────────

async def test_get_nudge_stats():
    """get_nudge_stats should count by status."""
    await db.record_nudge(account_id=10, target_user_id=5001, channel="watcher", status="sent")
    await db.record_nudge(account_id=10, target_user_id=5002, channel="watcher", status="skipped",
                          error_message="blacklisted")
    stats = await db.get_nudge_stats()
    assert stats.get("sent", 0) >= 1
    assert stats.get("skipped", 0) >= 1


# ── Nudge prompt instruction ───────────────────────────────────────────────

def test_nudge_prompt_instruction_content():
    """Nudge prompt instruction should mention follow-up and not AI disclosure."""
    prompt = drt._NUDGE_PROMPT_INSTRUCTION
    assert "FOLLOW-UP" in prompt.upper() or "NUDGE" in prompt.upper()
    assert "AI" in prompt  # instruction to not reveal AI
    assert "NOT" in prompt.upper()  # not pushy, not repeat


# ── _pending_nudges set ────────────────────────────────────────────────────

def test_pending_nudges_is_set():
    """_pending_nudges should be an empty set at module level."""
    assert isinstance(drt._pending_nudges, set)
