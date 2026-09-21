import asyncio
from types import SimpleNamespace

import pytest

import telegram_forum_inbox as inbox


class FakeDB:
    def __init__(self, proof=None, topic=None):
        self.proof = proof
        self.topic = topic
        self.saved = []
        self.status = []
        self.audit = []

    async def get_telegram_outreach_proof(self, account_id, user_id):
        return self.proof

    async def find_telegram_topic_for_user(self, group_id, account_id, user_id):
        return self.topic

    async def upsert_telegram_topic(self, data):
        self.saved.append(data)

    async def update_telegram_topic_last_message(self, group_id, topic_id, message):
        pass

    async def get_telegram_topic_by_thread(self, group_id, topic_id):
        return self.topic

    async def set_human_takeover(self, account_id, user_id):
        self.status.append((account_id, user_id))

    async def append_followup_chat_message(self, account_id, user_id, role, text):
        pass

    async def log_command_action(self, **kwargs):
        self.audit.append(kwargs)


class FakeBot:
    def __init__(self):
        self.sent = []
        self.created = []

    async def create_forum_topic(self, group_id, title):
        self.created.append((group_id, title))
        return 77

    async def send_topic_message(self, group_id, topic_id, text, buttons=None):
        self.sent.append((group_id, topic_id, text, buttons))


@pytest.mark.asyncio
async def test_inbound_requires_exact_account_user_outreach_proof():
    bot = FakeBot(); db = FakeDB(proof=None)
    result = await inbox.relay_inbound(db, bot, inbox.ForumConfig(True, -1001, 1, {9}),
        account_id=3, user_id=4, username="lead", name="Lead", text="hello", has_media=False)
    assert result == "no_outreach_proof"
    assert bot.created == []


@pytest.mark.asyncio
async def test_inbound_creates_topic_and_reports_unsupported_media():
    bot = FakeBot(); db = FakeDB(proof={"source":"campaign", "name":"Launch"})
    result = await inbox.relay_inbound(db, bot, inbox.ForumConfig(True, -1001, 1, {9}),
        account_id=3, user_id=4, username="lead", name="Lead", text="", has_media=True)
    assert result == "relayed"
    assert db.saved[0]["account_id"] == 3 and db.saved[0]["user_id"] == 4
    assert bot.sent[-1][1] == 77
    assert "media chưa được hỗ trợ" in bot.sent[-1][2].lower()


@pytest.mark.asyncio
async def test_topic_reply_preview_bound_to_admin_group_topic_and_double_send_safe():
    db = FakeDB(topic={"group_id":-1001,"topic_id":77,"account_id":3,"user_id":4,"username":"lead"})
    preview = await inbox.prepare_reply(db, inbox.ForumConfig(True, -1001, 1, {9}), 9, -1001, 77, "Hi")
    assert preview and preview.admin_id == 9 and preview.group_id == -1001 and preview.topic_id == 77
    calls = []
    async def sender(account_id, user_id, text): calls.append((account_id, user_id, text))
    assert await inbox.confirm_reply(db, preview.token, 9, -1001, 77, sender) == "sent"
    assert await inbox.confirm_reply(db, preview.token, 9, -1001, 77, sender) == "missing_or_used"
    assert calls == [(3, 4, "Hi")]
    assert db.status == [(3, 4)]


@pytest.mark.asyncio
async def test_rejects_wrong_admin_group_general_bot_and_anonymous():
    cfg = inbox.ForumConfig(True, -1001, 1, {9})
    db = FakeDB(topic={"group_id":-1001,"topic_id":77,"account_id":3,"user_id":4})
    assert await inbox.prepare_reply(db, cfg, 8, -1001, 77, "x") is None
    assert await inbox.prepare_reply(db, cfg, 9, -1002, 77, "x") is None
    assert await inbox.prepare_reply(db, cfg, 9, -1001, 1, "x") is None
    assert not inbox.is_relayable_admin_message(sender_id=9, is_bot=True, is_anonymous=False)
    assert not inbox.is_relayable_admin_message(sender_id=None, is_bot=False, is_anonymous=True)


@pytest.mark.asyncio
async def test_outgoing_transcript_skips_forum_originated_message_once():
    cfg = inbox.ForumConfig(True, -1001, 1, {9})
    bot = FakeBot()
    db = FakeDB(topic={"group_id":-1001,"topic_id":77,"account_id":3,"user_id":4})

    inbox.note_forum_origin(3, 4, "Hi")
    first = await inbox.relay_outgoing(db, bot, cfg, account_id=3, user_id=4, text="Hi")
    second = await inbox.relay_outgoing(db, bot, cfg, account_id=3, user_id=4, text="Hi")

    assert first == "loop_suppressed"
    assert second == "relayed"
    assert len(bot.sent) == 1 and "📤" in bot.sent[0][2]


@pytest.mark.asyncio
async def test_outgoing_transcript_ignored_without_existing_topic():
    bot = FakeBot(); db = FakeDB(topic=None)
    result = await inbox.relay_outgoing(db, bot, inbox.ForumConfig(True, -1001, 1, {9}),
                                        account_id=3, user_id=4, text="Hi")
    assert result == "no_topic" and bot.sent == []


@pytest.mark.asyncio
async def test_invalid_confirm_keeps_draft():
    db = FakeDB(topic={"account_id": 3, "user_id": 4})
    cfg = inbox.ForumConfig(True, -1001, 1, {9})
    p = await inbox.prepare_reply(db, cfg, 9, -1001, 77, "hello")
    async def send(*args): return True
    assert await inbox.confirm_reply(db, p.token, 8, -1001, 77, send, cfg) == "binding_mismatch"
    assert p.token in inbox._PENDING
    assert await inbox.confirm_reply(db, p.token, 9, -1001, 77, send, inbox.ForumConfig(True, -1001, 1, set())) == "not_allowed"
    db.topic = {"account_id": 5, "user_id": 4}
    assert await inbox.confirm_reply(db, p.token, 9, -1001, 77, send, cfg) == "mapping_changed"
    assert len(db.audit) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_send_failure_audited_without_history(raises):
    from unittest.mock import AsyncMock
    db = FakeDB(topic={"account_id": 3, "user_id": 4})
    db.append_followup_chat_message = AsyncMock()
    cfg = inbox.ForumConfig(True, -1001, 1, {9})
    p = await inbox.prepare_reply(db, cfg, 9, -1001, 77, "failure")
    async def send(*args):
        if raises: raise RuntimeError("offline")
        return False
    assert await inbox.confirm_reply(db, p.token, 9, -1001, 77, send, cfg) == "send_failed"
    assert db.audit[-1]["result"] == "failure"
    db.append_followup_chat_message.assert_not_awaited()
    assert not inbox._consume_forum_origin(3, 4, "failure")


def test_parse_int_set_accepts_negative_group_ids_and_ignores_junk():
    assert inbox.parse_int_set(" -1001, 42 ,abc,\n7 ") == {-1001, 42, 7}
