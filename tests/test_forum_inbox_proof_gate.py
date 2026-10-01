"""Regression tests for the Forum Inbox topic gate.

Background: the original gate required a successful dm_campaign_logs or
watcher_dm_logs row keyed on target_user_id. Scrape campaigns store the
GROUP/CHANNEL id there (negative), never the human user id — so real
conversations were silently dropped and telegram_topics stayed empty.
These tests pin the widened gate so it cannot regress.
"""
import pytest

import database as db
import telegram_forum_inbox as inbox


async def _seed_campaign_log(account_id, target_user_id, status="success"):
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO dm_campaigns (id, name, scrape_job_id, status) VALUES (1, 'Launch', 'job-1', 'completed')"
        )
        await conn.execute(
            "INSERT INTO dm_campaign_logs (campaign_id, account_id, target_user_id, status, sent_at) "
            "VALUES (1, ?, ?, ?, datetime('now'))",
            (account_id, target_user_id, status),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_proof_ignores_group_id_targets():
    """A campaign 'success' whose target is a negative (group) id is NOT proof.

    This is the exact shape that made every topic vanish: the gate matched the
    group id against the lead's user id, so it could never succeed.
    """
    await _seed_campaign_log(11, -993970524)
    assert await db.get_telegram_outreach_proof(11, -993970524) is None
    # ...and a real user id must not accidentally match the group row either.
    assert await db.get_telegram_outreach_proof(11, 993970524) is None


@pytest.mark.asyncio
async def test_proof_accepts_followup_chat_interaction():
    """An AI follow-up chat proves contact even without a campaign log."""
    await db.get_or_create_followup_chat(account_id=21, user_id=31, username="lead21")
    proof = await db.get_telegram_outreach_proof(21, 31)
    assert proof is not None and proof["source"] == "followup"


@pytest.mark.asyncio
async def test_proof_accepts_inbound_reply():
    """A recorded inbound reply proves contact even without any follow-up chat."""
    await db.add_dm_reply({
        "watcher_id": None, "account_id": 41, "sender_user_id": 51,
        "sender_username": "lead41", "sender_name": "Lead 41",
        "message_text": "hello", "platform": "telegram",
    })
    proof = await db.get_telegram_outreach_proof(41, 51)
    assert proof is not None and proof["source"] == "reply"


@pytest.mark.asyncio
async def test_inbound_creates_topic_for_replied_lead_without_campaign_proof():
    """End-to-end: a lead who replied now gets a topic (the reported bug)."""
    await db.get_or_create_followup_chat(account_id=61, user_id=71, username="lead61")

    class FakeBot:
        def __init__(self):
            self.created = []
            self.sent = []

        async def create_forum_topic(self, group_id, title):
            self.created.append(title)
            return 4242

        async def send_topic_message(self, group_id, topic_id, text, buttons=None):
            self.sent.append((group_id, topic_id, text))

    bot = FakeBot()
    result = await inbox.relay_inbound(
        db, bot, inbox.ForumConfig(True, -1005, 1, {9}),
        account_id=61, user_id=71, username="lead61", name="Lead 61",
        text="I am interested", has_media=False,
    )
    assert result == "relayed"
    assert len(bot.created) == 1
    topic = await db.find_telegram_topic_for_user(-1005, 61, 71)
    assert topic is not None and int(topic["topic_id"]) == 4242


@pytest.mark.asyncio
async def test_backfill_is_bounded_by_days_and_skips_existing_topics():
    """Backfill only picks recent replied chats missing a topic."""
    recent = await db.get_or_create_followup_chat(account_id=81, user_id=91, username="recent")
    old = await db.get_or_create_followup_chat(account_id=82, user_id=92, username="old")
    # inc_reply_count=True is what marks a chat as "the lead replied" (reply_count > 0).
    await db.append_followup_chat_message(81, 91, "user", "recent reply", inc_reply_count=True)
    await db.append_followup_chat_message(82, 92, "user", "old reply", inc_reply_count=True)

    async with db.get_db() as conn:
        # Make one chat look stale (outside the 7-day window) and give the other
        # chat an existing topic so it must be skipped.
        await conn.execute(
            "UPDATE ai_followup_chats SET last_user_message_at = datetime('now','-40 days'), "
            "updated_at = datetime('now','-40 days') WHERE account_id = 82"
        )
        await conn.execute(
            "INSERT INTO telegram_topics (group_id, topic_id, account_id, user_id, status) "
            "VALUES (-1005, 9001, 81, 91, 'active')"
        )
        await conn.commit()

    rows = await db.get_forum_backfill_candidates(days=7, limit=100)
    assert [r["user_id"] for r in rows] == [] or all(
        r["user_id"] not in (91, 92) for r in rows
    ), f"backfill must skip existing topics and stale chats, got {rows}"


@pytest.mark.asyncio
async def test_backfill_creates_one_topic_per_replied_conversation():
    await db.get_or_create_followup_chat(account_id=101, user_id=111, username="bf1")
    await db.append_followup_chat_message(101, 111, "user", "hello", inc_reply_count=True)

    class FakeBot:
        def __init__(self):
            self.created = []
            self.sent = []

        async def create_forum_topic(self, group_id, title):
            self.created.append(title)
            return 5000 + len(self.created)

        async def send_topic_message(self, group_id, topic_id, text, buttons=None):
            self.sent.append((group_id, topic_id, text))

    bot = FakeBot()
    report = await inbox.backfill_topics(db, bot, inbox.ForumConfig(True, -1005, 1, {9}),
                                         days=7, limit=100)
    assert report["created"] >= 1
    assert report["failed"] == 0
    topic = await db.find_telegram_topic_for_user(-1005, 101, 111)
    assert topic is not None
    # History must NOT be replayed into the group — only a header per new topic.
    assert len(bot.sent) == report["created"]
    assert "backfill" in bot.sent[0][2].lower()


@pytest.mark.asyncio
async def test_backfill_disabled_creates_nothing():
    class FakeBot:
        async def create_forum_topic(self, group_id, title):
            raise AssertionError("must not create topics while disabled")

    report = await inbox.backfill_topics(db, FakeBot(), inbox.ForumConfig(False, -1005, 1, {9}))
    assert report["created"] == 0 and report["reason"] == "disabled"
