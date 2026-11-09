import os
from types import SimpleNamespace

import pytest

import database as db
import proof_relay
import telegram_forum_inbox as fx


def test_media_kind():
    assert proof_relay.media_kind(SimpleNamespace(photo=object())) == "photo"
    assert proof_relay.media_kind(SimpleNamespace(video=object(), document=object())) == "video"
    assert proof_relay.media_kind(SimpleNamespace(sticker=object(), document=object())) is None
    assert proof_relay.media_kind(SimpleNamespace(gif=object(), document=object())) is None
    img_doc = SimpleNamespace(mime_type="image/png")
    assert proof_relay.media_kind(SimpleNamespace(document=img_doc)) == "photo"
    assert proof_relay.media_kind(SimpleNamespace(document=SimpleNamespace(mime_type="application/pdf"))) == "document"
    assert proof_relay.media_kind(SimpleNamespace()) is None


def test_topic_message_link():
    assert fx.topic_message_link(-1004352266125, 12, 345) == "https://t.me/c/4352266125/12/345"


def test_safe_media_path_rejects_traversal():
    assert proof_relay.safe_media_path("../scheduler.db") is None
    assert proof_relay.safe_media_path("1_2_3.jpg") is None  # not on disk


class _Msg:
    def __init__(self, mid, ext):
        self.id = mid
        self.photo = object() if ext == "jpg" else None
        self.video = object() if ext == "mp4" else None
        self.document = SimpleNamespace(size=10, mime_type="video/mp4") if ext == "mp4" else None
        self._ext = ext

    async def download_media(self, file):
        path = f"{file}.{self._ext}"
        with open(path, "wb") as f:
            f.write(b"x")
        return path


async def _seed_chat(account_id, user_id):
    await db.get_or_create_followup_chat(account_id=account_id, user_id=user_id, username="lead", name="Lead")
    chat = await db.append_followup_chat_message(account_id, user_id, "user", "[Attached Video Proof]")
    return chat["history"][-1]["timestamp"]


@pytest.mark.asyncio
async def test_photo_kept_and_tagged(monkeypatch):
    import command_bot

    async def fake_relay(account_id, user_id, username, name, path, kind):
        assert os.path.isfile(path)
        return {"link": "https://t.me/c/1/2/3"}
    monkeypatch.setattr(command_bot, "relay_proof_to_forum", fake_relay)

    ts = await _seed_chat(1, 501)
    media = await proof_relay.capture(1, 501, "lead", "Lead", _Msg(77, "jpg"), ts, "photo")
    assert media["image_url"] == "/api/ai-followup/proof-media/1_501_77.jpg"
    assert media["topic_link"] == "https://t.me/c/1/2/3"
    assert proof_relay.safe_media_path("1_501_77.jpg")

    chat = await db.get_followup_chat(1, 501)
    import json
    assert json.loads(chat["history_json"])[-1]["media"]["image_url"].endswith("1_501_77.jpg")


@pytest.mark.asyncio
async def test_video_deleted_after_upload_and_reason_kept(monkeypatch):
    import command_bot
    seen = {}

    async def fake_relay(account_id, user_id, username, name, path, kind):
        seen["path"] = path
        return {"reason": "no_outreach_proof"}
    monkeypatch.setattr(command_bot, "relay_proof_to_forum", fake_relay)

    ts = await _seed_chat(1, 502)
    media = await proof_relay.capture(1, 502, "lead", "Lead", _Msg(78, "mp4"), ts, "video")
    assert media == {"type": "video", "forward": "no_outreach_proof"}
    assert not os.path.exists(seen["path"])
