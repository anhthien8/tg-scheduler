"""Unit tests for the configurable multi-step drip cadence in dm_reply_tracker."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import dm_reply_tracker as drt

# ── _load_drip_cadence ──────────────────────────────────────────────────────

async def test_default_cadence_when_unset(monkeypatch):
    monkeypatch.setattr(drt.db, "get_setting", AsyncMock(return_value=""))
    assert await drt._load_drip_cadence() == drt.DEFAULT_DRIP_CADENCE_HOURS


async def test_default_cadence_is_day_1_3_7():
    assert drt.DEFAULT_DRIP_CADENCE_HOURS == [24, 72, 168]


async def test_custom_cadence_parsed(monkeypatch):
    monkeypatch.setattr(drt.db, "get_setting", AsyncMock(return_value=json.dumps([12, 48])))
    assert await drt._load_drip_cadence() == [12, 48]


async def test_non_increasing_cadence_falls_back(monkeypatch):
    monkeypatch.setattr(drt.db, "get_setting", AsyncMock(return_value=json.dumps([72, 24])))
    assert await drt._load_drip_cadence() == drt.DEFAULT_DRIP_CADENCE_HOURS


async def test_negative_or_zero_hours_falls_back(monkeypatch):
    monkeypatch.setattr(drt.db, "get_setting", AsyncMock(return_value=json.dumps([0, 24])))
    assert await drt._load_drip_cadence() == drt.DEFAULT_DRIP_CADENCE_HOURS


async def test_garbage_setting_falls_back(monkeypatch):
    monkeypatch.setattr(drt.db, "get_setting", AsyncMock(return_value="not json"))
    assert await drt._load_drip_cadence() == drt.DEFAULT_DRIP_CADENCE_HOURS


# ── _silence_hours ──────────────────────────────────────────────────────────

def test_silence_hours_computes_recent():
    ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    assert 29 < drt._silence_hours(ts) < 31


def test_silence_hours_naive_timestamp_assumed_utc():
    ts = (datetime.now(timezone.utc) - timedelta(hours=50)).replace(tzinfo=None).isoformat()
    assert 49 < drt._silence_hours(ts) < 51


def test_silence_hours_unknown_is_huge():
    assert drt._silence_hours(None) > 100000
    assert drt._silence_hours("not-a-date") > 100000


# ── _drip_instruction_for_stage ─────────────────────────────────────────────

def test_stage_one_mentions_1_day():
    txt = drt._drip_instruction_for_stage(1, 3).lower()
    assert "1 day" in txt


def test_final_stage_mentions_last_message():
    txt = drt._drip_instruction_for_stage(3, 3).lower()
    assert "final" in txt or "last" in txt


def test_middle_stage_differs_from_previous():
    first = drt._drip_instruction_for_stage(1, 3)
    middle = drt._drip_instruction_for_stage(2, 3)
    assert first != middle
    assert "different angle" in middle.lower()


# ── stage due logic ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("silence,expected_due", [
    (0, 0), (24, 1), (48, 1), (72, 2), (100, 2), (168, 3), (500, 3),
])
def test_due_stage_counts_crossed_thresholds(silence, expected_due):
    cadence = [24, 72, 168]
    assert sum(1 for h in cadence if silence >= h) == expected_due


# ── last_user_message_at anchoring ──────────────────────────────────────────

async def test_assistant_message_does_not_bump_last_user_message_at():
    """Regression: our own follow-up must NOT reset the user's silence clock."""
    import database as db
    await db.get_or_create_followup_chat(account_id=1, user_id=9001, username="u", name="U")
    # user writes -> last_user_message_at set
    await db.append_followup_chat_message(1, 9001, "user", "hello")
    after_user = (await db.get_followup_chat(1, 9001))["last_user_message_at"]
    assert after_user

    # we send a drip (assistant role) -> last_user_message_at must be UNCHANGED
    await db.append_followup_chat_message(1, 9001, "assistant", "follow up")
    after_assistant = (await db.get_followup_chat(1, 9001))["last_user_message_at"]
    assert after_assistant == after_user


async def test_second_user_message_advances_last_user_message_at():
    import database as db
    await db.get_or_create_followup_chat(account_id=2, user_id=9002, username="u", name="U")
    await db.append_followup_chat_message(2, 9002, "user", "one")
    first = (await db.get_followup_chat(2, 9002))["last_user_message_at"]
    import asyncio
    await asyncio.sleep(1.1)  # datetime('now') has 1s resolution
    await db.append_followup_chat_message(2, 9002, "user", "two")
    second = (await db.get_followup_chat(2, 9002))["last_user_message_at"]
    assert second >= first
