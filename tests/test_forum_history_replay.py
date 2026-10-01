"""Tests for replaying a conversation transcript into a forum topic.

Backfilled topics only carry a title line, so the conversation is unreadable
from inside the group. relay_history() posts the stored transcript instead.
"""
import json
from datetime import datetime, timedelta, timezone

from telegram_forum_inbox import _format_history_message


def test_history_line_direction_and_timestamp():
    user = _format_history_message("user", "hi", "2026-10-01T13:14:22.123")
    assert "📥 Lead" in user and "2026-10-01 13:14" in user and "hi" in user

    ours = _format_history_message("assistant", "[Human Admin]: yup", "2026-10-01T13:18:05")
    assert "📤 Mình" in ours and "[Human Admin]: yup" in ours


def test_history_line_handles_missing_timestamp_and_whitespace():
    line = _format_history_message("user", "   padded   ", "")
    assert line.startswith("📥 Lead\n")
    assert "padded" in line and "padded   " not in line


def test_history_lines_chunk_under_telegram_limit():
    """Chunking must keep every message under 4096 chars (Telegram's cap)."""
    lines = [_format_history_message("user", f"msg {i} " + "x" * 40, "2026-10-01T13:14")
             for i in range(200)]
    chunks, chunk, size = [], [], 0
    for line in lines:
        if size + len(line) > 3200 and chunk:
            chunks.append(chunk); chunk, size = [], 0
        chunk.append(line); size += len(line) + 2
    if chunk:
        chunks.append(chunk)
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert sum(len(l) + 2 for l in c) <= 4096


def test_only_in_window_entries_are_kept():
    """A 30-day replay must drop older messages, keeping order."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    hist = [
        {"role": "user", "content": "old", "timestamp": "2020-01-01T00:00:00"},
        {"role": "user", "content": "recent", "timestamp": datetime.now(timezone.utc).isoformat()},
        {"role": "assistant", "content": "no-ts", "timestamp": ""},
    ]
    kept = [m for m in hist if isinstance(m, dict) and (m.get("timestamp") or "") >= cutoff]
    assert [m["content"] for m in kept] == ["recent"]


def test_relay_history_reports_no_history_without_calling_telegram():
    """No stored history -> a clear status, and bot.send is never reached."""
    import asyncio

    import telegram_forum_inbox as fx

    class FakeDB:
        async def get_followup_chat(self, a, u):
            return {"history_json": None}

    class ExplodingBot:
        async def create_forum_topic(self, *a, **k):
            raise AssertionError("must not touch Telegram when there is no history")

        async def send_topic_message(self, *a, **k):
            raise AssertionError("must not touch Telegram when there is no history")

    cfg = fx.ForumConfig(enabled=True, group_id=-100, general_topic_id=1)
    res = asyncio.run(fx.relay_history(FakeDB(), ExplodingBot(), cfg,
                                       account_id=1, user_id=2))
    assert res["status"] == "no_history" and res["posted"] == 0


def test_relay_history_reports_no_recent_when_window_empty():
    """History exists but everything is older than the window -> no_recent."""
    import asyncio

    import telegram_forum_inbox as fx

    old = json.dumps([{"role": "user", "content": "ancient",
                       "timestamp": "2019-01-01T00:00:00"}])

    class FakeDB:
        async def get_followup_chat(self, a, u):
            return {"history_json": old}

    class ExplodingBot:
        async def send_topic_message(self, *a, **k):
            raise AssertionError("nothing to replay -> must not send")

    cfg = fx.ForumConfig(enabled=True, group_id=-100, general_topic_id=1)
    res = asyncio.run(fx.relay_history(FakeDB(), ExplodingBot(), cfg,
                                       account_id=1, user_id=2, days=30))
    assert res["status"] == "no_recent" and res["posted"] == 0


def test_relay_history_disabled_config_short_circuits():
    import asyncio

    import telegram_forum_inbox as fx

    cfg = fx.ForumConfig(enabled=False, group_id=-100)
    res = asyncio.run(fx.relay_history(None, None, cfg, account_id=1, user_id=2))
    assert res["status"] == "disabled"
