"""Regression tests for AI resume/lock behaviour in the lead pipeline.

Two bugs motivated these tests:

1. A chat could be locked in needs_human FOREVER. The 24h auto-resume check was
   skipped entirely when human_takeover_at was NULL (the `if takeover_at_str`
   guard), so a chat that never recorded a takeover stamp never unlocked.

2. SQLite stores datetimes as 'YYYY-MM-DD HH:MM:SS' (SPACE separator), which
   datetime.fromisoformat() rejects on Python < 3.11. The old code swallowed the
   error in a bare `except: pass`, so even valid timestamps never resumed.
"""
from datetime import datetime, timedelta, timezone

import pytest

import database as db
from dm_reply_tracker import _parse_sqlite_utc, _silence_hours


# ── timestamp parsing (the silent-failure surface) ────────────────────────────

def test_parses_sqlite_space_format():
    """SQLite's own format must parse — fromisoformat alone rejects it."""
    ts = _parse_sqlite_utc("2026-09-30 16:54:50")
    assert ts is not None
    assert (ts.year, ts.month, ts.day, ts.hour) == (2026, 9, 30, 16)
    assert ts.tzinfo is not None


def test_parses_iso_format_and_rejects_junk():
    assert _parse_sqlite_utc("2026-09-30T16:54:50") is not None
    assert _parse_sqlite_utc("2026-09-30 16:54:50+00:00") is not None
    for bad in (None, "", "   ", "not-a-date", "2026-13-45 99:99:99"):
        assert _parse_sqlite_utc(bad) is None, f"{bad!r} must not parse"


def test_silence_hours_uses_real_elapsed_time():
    """A space-format timestamp must yield the real age, not the 'unknown' sentinel."""
    two_h_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    assert 1.5 < _silence_hours(two_h_ago) < 2.5
    # Unknown stays a large number so undatable chats remain eligible.
    assert _silence_hours(None) == 10**6
    assert _silence_hours("garbage") == 10**6


# ── explicit AI resume ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_clears_status_and_takeover_stamp():
    """Resume must clear human_takeover_at too, else the next inbound re-locks."""
    await db.get_or_create_followup_chat(9901, 9902, username="r1")
    await db.set_human_takeover(9901, 9902)
    locked = await db.get_followup_chat(9901, 9902)
    assert locked["status"] == "needs_human" and locked["human_takeover_at"]

    assert await db.resume_ai_for_user(9901, 9902) is True

    fresh = await db.get_followup_chat(9901, 9902)
    assert fresh["status"] == "active"
    assert not fresh["human_takeover_at"], "takeover stamp must be cleared, not kept"


@pytest.mark.asyncio
async def test_resume_reports_false_for_unknown_chat():
    assert await db.resume_ai_for_user(123456789, 987654321) is False


@pytest.mark.asyncio
async def test_stuck_chat_with_null_takeover_is_dateless_and_resumable():
    """A needs_human row with NO takeover stamp must still be resumable.

    This is the exact production shape of the 4 stuck chats: status locked in
    needs_human while human_takeover_at is NULL, so the old guard never ran.
    """
    await db.get_or_create_followup_chat(9911, 9912, username="stuck")
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE ai_followup_chats SET status='needs_human', human_takeover_at=NULL "
            "WHERE account_id=9911 AND user_id=9912"
        )
        await conn.commit()

    row = await db.get_followup_chat(9911, 9912)
    assert row["status"] == "needs_human" and not row["human_takeover_at"]

    # Explicit resume recovers it.
    assert await db.resume_ai_for_user(9911, 9912) is True
    assert (await db.get_followup_chat(9911, 9912))["status"] == "active"


def test_topic_ai_commands_are_recognised_and_do_not_collide_with_username_form():
    """Bare /ai* (topic-scoped) vs '/ai-on @user' (global) must not overlap."""
    import re

    import command_bot

    for cmd in ("/ai-on", "/ai-off", "/ai", "/AI-ON", " /ai-off "):
        assert command_bot._is_ai_control(cmd), f"{cmd!r} should be a topic control"

    for not_cmd in ("/ai-on @zoro", "hello", "", "/status"):
        assert not command_bot._is_ai_control(not_cmd), f"{not_cmd!r} is not a bare control"

    # The legacy per-username command requires an argument, so it never swallows
    # the bare form.
    legacy = re.compile(r"/ai-?(on|off)\s+(@?\w+)")
    assert legacy.search("/ai-on @zoro")
    assert not legacy.search("/ai-on")
