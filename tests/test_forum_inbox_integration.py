"""Integration test: real database.py (isolated temp DB via conftest DATA_DIR),
real telegram_forum_inbox module, fake Telegram bot only. Verifies:
- no message sent to the wrong chat_id/topic_id
- append/confirm never drops existing history
- concurrent inbound never creates two topics for the same user (race guard)
"""
import asyncio
import pytest

import database as db
import telegram_forum_inbox as inbox


@pytest.mark.asyncio
async def test_takeover_creates_missing_chat():
    await db.set_human_takeover(777, 888)
    chat = await db.get_followup_chat(777, 888)
    assert chat and chat["status"] == "needs_human" and chat["human_takeover_at"]


@pytest.mark.asyncio
async def test_concurrent_history_append_preserves_every_message():
    await db.get_or_create_followup_chat(777, 888)
    await asyncio.gather(*[db.append_followup_chat_message(777, 888, "user", str(i), True) for i in range(12)])
    chat = await db.get_followup_chat(777, 888)
    assert {m["content"] for m in chat["history"]} == {str(i) for i in range(12)}
    assert chat["reply_count"] == 12


@pytest.mark.asyncio
async def test_confirm_blocks_queued_ai_and_drip(monkeypatch):
    import dm_reply_tracker as tracker
    from unittest.mock import AsyncMock
    await db.upsert_telegram_topic({"group_id": -1005, "topic_id": 902, "account_id": 777, "user_id": 888})
    p = await inbox.prepare_reply(db, inbox.ForumConfig(True, -1005, 1, {9}), 9, -1005, 902, "human")
    entered, release = asyncio.Event(), asyncio.Event()
    async def send(*args):
        entered.set()
        await release.wait()
        return True
    monkeypatch.setattr(tracker.tg, "send_text_message", AsyncMock(return_value=True))
    task = asyncio.create_task(inbox.confirm_reply(db, p.token, 9, -1005, 902, send))
    await entered.wait()
    ai = asyncio.create_task(tracker._send_ai_message(777, 888, "ai"))
    drip = asyncio.create_task(tracker._send_ai_message(777, 888, "drip", drip_stage=1))
    await asyncio.sleep(0)
    assert not ai.done() and not drip.done()
    release.set()
    assert await task == "sent"
    assert await ai is False and await drip is False
    tracker.tg.send_text_message.assert_not_awaited()
    chat = await db.get_followup_chat(777, 888)
    assert chat["status"] == "needs_human"
    assert [m["content"] for m in chat["history"]] == ["[Human Admin via Forum]: human"]


class FakeBot:
    def __init__(self):
        self.sent = []
        self.create_calls = 0
        self.create_delay = 0.0

    async def create_forum_topic(self, group_id, title):
        self.create_calls += 1
        if self.create_delay:
            await asyncio.sleep(self.create_delay)
        return 500 + self.create_calls

    async def send_topic_message(self, group_id, topic_id, text, buttons=None):
        self.sent.append((group_id, topic_id, text))


@pytest.mark.asyncio
async def test_concurrent_inbound_creates_only_one_topic():
    """Race: two inbound messages for the same lead arriving near-simultaneously
    must not create two forum topics (would split the conversation in half)."""
    bot = FakeBot()
    bot.create_delay = 0.05  # widen the race window

    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO dm_campaigns (id, name, scrape_job_id, status) VALUES (1, 'Launch', 'job-1', 'completed')"
        )
        await conn.execute(
            "INSERT INTO dm_campaign_logs (campaign_id, account_id, target_user_id, status, sent_at) "
            "VALUES (1, 11, 22, 'success', datetime('now'))"
        )
        await conn.commit()

    cfg = inbox.ForumConfig(True, -1005, 1, {9})
    results = await asyncio.gather(*[
        inbox.relay_inbound(db, bot, cfg, account_id=11, user_id=22, username="lead",
                            name="Lead", text=f"msg{i}", has_media=False)
        for i in range(5)
    ])
    assert all(r == "relayed" for r in results)
    assert bot.create_calls == 1, f"expected exactly 1 topic created, got {bot.create_calls}"
    topic = await db.find_telegram_topic_for_user(-1005, 11, 22)
    assert topic is not None


@pytest.mark.asyncio
async def test_confirm_reply_appends_without_losing_existing_history():
    await db.get_or_create_followup_chat(account_id=31, user_id=42, username="lead2")
    await db.append_followup_chat_message(31, 42, "user", "hello there")
    await db.append_followup_chat_message(31, 42, "assistant", "hi, how can I help")

    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO telegram_topics (group_id, topic_id, account_id, user_id, username, status) "
            "VALUES (-1005, 900, 31, 42, 'lead2', 'active')"
        )
        await conn.commit()

    cfg = inbox.ForumConfig(True, -1005, 1, {9})
    preview = await inbox.prepare_reply(db, cfg, 9, -1005, 900, "human reply text")
    assert preview is not None

    sent_calls = []
    async def sender(account_id, user_id, text):
        sent_calls.append((account_id, user_id, text))

    result = await inbox.confirm_reply(db, preview.token, 9, -1005, 900, sender)
    assert result == "sent"
    assert sent_calls == [(31, 42, "human reply text")]

    chat = await db.get_followup_chat(31, 42)
    roles_and_content = [(m["role"], m["content"]) for m in chat["history"]]
    assert ("user", "hello there") in roles_and_content
    assert ("assistant", "hi, how can I help") in roles_and_content
    assert ("assistant", "[Human Admin via Forum]: human reply text") in roles_and_content
    assert chat["status"] == "needs_human"


@pytest.mark.asyncio
async def test_reply_never_sent_to_wrong_group_or_topic():
    """binding_mismatch must block send — proves no cross-topic/cross-group leak."""
    await db.get_or_create_followup_chat(account_id=51, user_id=61, username="leadX")
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO telegram_topics (group_id, topic_id, account_id, user_id, username, status) "
            "VALUES (-1005, 700, 51, 61, 'leadX', 'active')"
        )
        await conn.commit()

    cfg = inbox.ForumConfig(True, -1005, 1, {9})
    preview = await inbox.prepare_reply(db, cfg, 9, -1005, 700, "draft")

    calls = []
    async def sender(a, u, t):
        calls.append((a, u, t))

    # Attempt confirm against a DIFFERENT group_id (spoofed/misrouted event)
    result = await inbox.confirm_reply(db, preview.token, 9, -9999, 700, sender)
    assert result == "binding_mismatch"
    assert calls == []
