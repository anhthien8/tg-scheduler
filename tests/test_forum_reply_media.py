"""An admin's photo / video / file / voice in a lead topic is sent to the lead from the nick."""
from types import SimpleNamespace

import pytest
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeSticker, InputStickerSetID

import command_bot as cb
import telegram_forum_inbox as inbox


class FakeDB:
    def __init__(self):
        self.topic = {"group_id": -1001, "topic_id": 77, "account_id": 3, "user_id": 4}
        self.history = []

    async def get_telegram_topic_by_thread(self, group_id, topic_id):
        return self.topic

    async def set_human_takeover(self, account_id, user_id):
        pass

    async def append_followup_chat_message(self, account_id, user_id, role, text):
        self.history.append(text)

    async def log_command_action(self, **kw):
        pass


CFG = inbox.ForumConfig(True, -1001, 1, {9})


@pytest.mark.asyncio
async def test_attachment_without_caption_gets_a_preview_and_is_sent_once():
    db = FakeDB()
    preview = await inbox.prepare_reply(db, CFG, 9, -1001, 77, "", media=(-1001, 555), label="Ảnh")
    assert preview and preview.media == (-1001, 555)
    assert "📎 Ảnh" in inbox.render_reply_preview(preview)
    sent = []

    async def sender(account_id, user_id, text):
        sent.append((account_id, user_id, text))

    assert await inbox.confirm_reply(db, preview.token, 9, -1001, 77, sender) == "sent"
    assert sent == [(3, 4, "")]
    assert db.history == ["[Human Admin via Forum]: [Sent Ảnh]"]
    # The nick's outgoing copy is recognised once, so it is not posted back into the topic.
    assert inbox.consume_forum_media_origin(3, 4) is True
    assert inbox.consume_forum_media_origin(3, 4) is False


@pytest.mark.asyncio
async def test_caption_travels_with_the_attachment_and_failed_send_clears_the_guard():
    db = FakeDB()
    preview = await inbox.prepare_reply(db, CFG, 9, -1001, 77, "here is the banner", media=(-1001, 556), label="File")

    async def boom(account_id, user_id, text):
        raise RuntimeError("x")

    assert await inbox.confirm_reply(db, preview.token, 9, -1001, 77, boom) == "send_failed"
    assert db.history == [] and inbox.consume_forum_media_origin(3, 4) is False
    assert await inbox.prepare_reply(db, CFG, 9, -1001, 77, "") is None  # empty text, no attachment: nothing to send


def test_link_preview_is_text_and_kinds_are_labelled():
    web = SimpleNamespace(media=type("MessageMediaWebPage", (), {})())
    assert cb._has_attachment(web) is False
    assert cb._has_attachment(SimpleNamespace(media=None)) is False
    media = object()
    assert cb._attachment_label(SimpleNamespace(media=media, photo=object())) == "Ảnh"
    assert cb._attachment_label(SimpleNamespace(media=media, voice=object(), document=object())) == "Voice"
    assert cb._attachment_label(SimpleNamespace(media=media, document=object())) == "File"
    assert cb._attachment_label(SimpleNamespace(media=media)) == ""  # contact / location / poll


@pytest.mark.asyncio
async def test_attachment_is_downloaded_then_sent_from_the_nick_as_the_same_kind(monkeypatch, tmp_path):
    f = tmp_path / "a.webp"
    f.write_bytes(b"x")
    doc = SimpleNamespace(mime_type="image/webp", size=1, attributes=[
        DocumentAttributeSticker(alt="🔥", stickerset=InputStickerSetID(id=1, access_hash=2)),
        DocumentAttributeFilename("a.webp")])

    class Msg:
        media, document, voice, video_note = object(), doc, None, None

        async def download_media(self, file=None):
            return str(f)

    class Bot:
        async def get_messages(self, chat_id, ids=None):
            return Msg()

    calls = {}

    async def fake_send(account_id, user_id, path, caption, **kw):
        calls.update(path=path, caption=caption, **kw)
        return True

    monkeypatch.setattr(cb, "_bot", Bot())
    monkeypatch.setattr(cb.tg, "send_media_message", fake_send, raising=False)
    assert await cb._send_forum_attachment(3, 4, (-1001, 555), "cap") is True
    assert calls["caption"] == "cap" and calls["mime_type"] == "image/webp"
    assert type(calls["attributes"][0].stickerset).__name__ == "InputStickerSetEmpty" and calls["attributes"][0].alt == "🔥"
    assert not f.exists()  # temp file removed
