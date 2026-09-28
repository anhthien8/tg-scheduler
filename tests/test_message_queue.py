"""Unit tests for message_queue.py — error translation, enqueue guards, worker.

Offline: database/telegram_client/alerts monkeypatched. The queue worker is
driven one item at a time and then cancelled instead of looping forever.
"""
import asyncio
import types
from unittest.mock import AsyncMock, patch

import pytest

import message_queue as mq

# ── translate_error ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,needle", [
    ("PeerFlood error", "PeerFlood"),
    ("Cannot resolve entity", "Không tìm thấy"),
    ("CHAT_WRITE_FORBIDDEN", "cấm gửi"),
    ("FloodWait 42 seconds", "42"),
    ("Slowmode 30s", "30"),
    ("MESSAGE_TOO_LONG", "quá dài"),
    ("INVALID PEER", "không hợp lệ"),
])
def test_translate_error_maps_known_errors(raw, needle):
    assert needle.lower() in mq.translate_error(raw).lower()


def test_translate_error_unknown_returns_input():
    assert mq.translate_error("weird unknown thing") == "weird unknown thing"


def test_translate_error_handles_none():
    # `e` falls back to "" for matching, but the generic fallback returns the
    # original argument unchanged (None) — preserve that contract.
    assert mq.translate_error(None) is None


# ── _send_single_message ────────────────────────────────────────────────────

async def test_send_text_message(monkeypatch):
    tg = types.SimpleNamespace(send_text_message=AsyncMock(return_value=True))
    monkeypatch.setattr(mq, "tg", tg)
    ok = await mq._send_single_message(1, {"msg_type": "text", "content": "hi"}, 123)
    assert ok is True
    tg.send_text_message.assert_awaited_once_with(1, 123, "hi")


async def test_send_photo_uses_caption(monkeypatch):
    tg = types.SimpleNamespace(send_photo_message=AsyncMock(return_value=True))
    monkeypatch.setattr(mq, "tg", tg)
    await mq._send_single_message(1, {"msg_type": "photo", "content": "cap", "media_path": "p.jpg"}, 123)
    tg.send_photo_message.assert_awaited_once_with(1, 123, "p.jpg", caption="cap")


async def test_send_poll_parses_options(monkeypatch):
    tg = types.SimpleNamespace(send_poll_message=AsyncMock(return_value=True))
    monkeypatch.setattr(mq, "tg", tg)
    msg = {"msg_type": "poll", "poll_options": '["a", "b"]',
           "poll_question": "Q?", "poll_multiple": False}
    await mq._send_single_message(1, msg, 123)
    tg.send_poll_message.assert_awaited_once_with(1, 123, "Q?", ["a", "b"], False)


async def test_send_unknown_type_returns_false(monkeypatch):
    assert await mq._send_single_message(1, {"msg_type": "carrier_pigeon"}, 123) is False


# ── enqueue_schedule ────────────────────────────────────────────────────────

@pytest.fixture
def fake_db(monkeypatch):
    db = types.ModuleType("database")
    db.get_schedule = AsyncMock(return_value={
        "id": 1, "name": "s", "is_active": True, "account_id": 5,
        "messages": [{"id": 1, "msg_type": "text", "content": "hi", "msg_order": 0}],
        "targets": [{"chat_id": 123, "chat_title": "G"}],
        "max_sends": None, "current_sends": 0,
    })
    db.is_target_blocked = AsyncMock(return_value=False)
    db.increment_send_count = AsyncMock(return_value={"reached_limit": False})
    db.toggle_schedule = AsyncMock()
    db.add_send_log = AsyncMock()
    monkeypatch.setattr(mq, "db", db)
    mq._queue = None  # fresh queue per test
    return db


async def test_enqueue_missing_schedule_is_noop(fake_db):
    fake_db.get_schedule = AsyncMock(return_value=None)
    await mq.enqueue_schedule(1)
    assert mq.get_queue().qsize() == 0


async def test_enqueue_inactive_schedule_is_skipped(fake_db):
    fake_db.get_schedule = AsyncMock(return_value={"is_active": False})
    await mq.enqueue_schedule(1)
    assert mq.get_queue().qsize() == 0


async def test_enqueue_over_max_sends_deactivates(fake_db):
    fake_db.get_schedule = AsyncMock(return_value={
        "id": 1, "is_active": True, "max_sends": 5, "current_sends": 5,
        "messages": [], "targets": [],
    })
    with patch("scheduler.remove_schedule_job") as rem:
        await mq.enqueue_schedule(1)
    fake_db.toggle_schedule.assert_awaited_once_with(1)
    rem.assert_called_once_with(1)


async def test_enqueue_skips_blocked_targets(fake_db):
    fake_db.is_target_blocked = AsyncMock(return_value=True)
    await mq.enqueue_schedule(1)
    assert mq.get_queue().qsize() == 0


async def test_enqueue_puts_messages_for_targets(fake_db):
    await mq.enqueue_schedule(1)
    assert mq.get_queue().qsize() == 1
    item = mq.get_queue().get_nowait()
    assert item["schedule_id"] == 1
    assert item["retry_count"] == 0


async def test_enqueue_no_messages_is_noop(fake_db):
    sched = await fake_db.get_schedule()
    sched["messages"] = []
    fake_db.get_schedule = AsyncMock(return_value=sched)
    await mq.enqueue_schedule(1)
    assert mq.get_queue().qsize() == 0
