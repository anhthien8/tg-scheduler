"""Edit / revoke / delete our own messages from the chat modal."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import chat_message_ops as ops
import database as db
import routes.ai_followup as route

A, U = 1, 777


def _tg(mid, text, at: datetime):
    return SimpleNamespace(id=mid, message=text, date=at.astimezone(timezone.utc))


def test_matches_by_text_and_time_and_skips_others():
    now = datetime.now()
    entry = {"role": "assistant", "content": "[Human Admin]: Got it bro, UID received", "timestamp": now.isoformat()}
    msgs = [_tg(1, "Got it bro, UID received", now.astimezone() + timedelta(seconds=2)),
            _tg(2, "Got it bro, UID received", now.astimezone() - timedelta(days=2)),  # same text, other day
            _tg(3, "something else", now.astimezone())]
    assert [m.id for m in ops.find_matches(entry, msgs)] == [1]


def test_multi_part_reply_whole_match_first_and_html_ignored():
    now = datetime.now().astimezone()
    entry = {"content": "Hi <b>bro</b>\n\nWhich channel?", "timestamp": datetime.now().isoformat()}
    msgs = [_tg(5, "Which channel?", now), _tg(4, "Hi bro Which channel?", now + timedelta(seconds=30))]
    assert [m.id for m in ops.find_matches(entry, msgs)] == [4, 5]


class _Client:
    def __init__(self, msgs):
        self.msgs, self.edited, self.deleted = msgs, [], []

    async def iter_messages(self, peer, limit=None, from_user=None):
        for m in self.msgs:
            yield m

    async def edit_message(self, peer, mid, text, parse_mode=None):
        self.edited.append((mid, text))

    async def delete_messages(self, peer, ids, revoke=True):
        self.deleted.append((ids, revoke))


async def _chat(content):
    await db.get_or_create_followup_chat(account_id=A, user_id=U, username="kol", name="KOL")
    await db.append_followup_chat_message(A, U, "user", "my uid 123")
    chat = await db.append_followup_chat_message(A, U, "assistant", content)
    return chat["history"][-1]


@pytest.mark.asyncio
async def test_edit_rewrites_telegram_and_history(monkeypatch):
    e = await _chat("[Human Admin]: old text")
    cl = _Client([_tg(9, "old text", datetime.now().astimezone())])
    monkeypatch.setattr(route.tg, "get_client", lambda aid: cl)
    r = await route.message_op(A, U, route.MessageOpRequest(
        timestamp=e["timestamp"], content=e["content"], action="edit", new_text="new text"))
    assert r["ok"] and cl.edited == [(9, "new text")]
    last = (await db.get_followup_chat(A, U))["history"][-1]
    assert last["content"] == "[Human Admin]: new text" and last["edited"] is True


@pytest.mark.asyncio
async def test_revoke_deletes_both_sides_and_drops_history(monkeypatch):
    e = await _chat("AI reply here")
    cl = _Client([_tg(9, "AI reply here", datetime.now().astimezone())])
    monkeypatch.setattr(route.tg, "get_client", lambda aid: cl)
    await route.message_op(A, U, route.MessageOpRequest(timestamp=e["timestamp"], content=e["content"], action="revoke"))
    assert cl.deleted == [([9], True)]
    hist = (await db.get_followup_chat(A, U))["history"]
    assert [m["content"] for m in hist] == ["my uid 123"]


@pytest.mark.asyncio
async def test_lead_messages_and_missing_telegram_message_are_refused(monkeypatch):
    await _chat("AI reply here")
    hist = (await db.get_followup_chat(A, U))["history"]
    monkeypatch.setattr(route.tg, "get_client", lambda aid: _Client([]))
    with pytest.raises(HTTPException) as ex:
        await route.message_op(A, U, route.MessageOpRequest(
            timestamp=hist[0]["timestamp"], content=hist[0]["content"], action="delete"))
    assert ex.value.status_code == 400
    with pytest.raises(HTTPException) as ex:
        await route.message_op(A, U, route.MessageOpRequest(
            timestamp=hist[1]["timestamp"], content=hist[1]["content"], action="delete"))
    assert ex.value.status_code == 404
    assert len((await db.get_followup_chat(A, U))["history"]) == 2
