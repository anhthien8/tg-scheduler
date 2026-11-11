"""A photo / video / file our account sends to a lead shows up in the lead's
forum topic as the file itself, not as a bare "[Media]" line."""
import os
import pathlib
from types import SimpleNamespace

import command_bot
import telegram_forum_inbox as fx

CFG = fx.ForumConfig(True, -1001, 1, {9})
TOPIC = {"group_id": -1001, "topic_id": 77, "account_id": 3, "user_id": 4}


class _DB:
    def __init__(self, topic=TOPIC):
        self.topic = topic

    async def find_telegram_topic_for_user(self, group_id, account_id, user_id):
        return self.topic

    async def get_account(self, account_id):
        return {"name": "William Been"}


class _Bot:
    def __init__(self, fail=False):
        self.files, self.texts, self.fail = [], [], fail

    async def send_topic_file(self, group_id, topic_id, path, caption):
        if self.fail:
            raise RuntimeError("upload refused")
        self.files.append((topic_id, path, caption, os.path.isfile(path)))

    async def send_topic_message(self, group_id, topic_id, text, buttons=None):
        self.texts.append((topic_id, text))


class _Msg:
    id = 55
    photo = object()
    document = None

    async def download_media(self, file):
        path = f"{file}.jpg"
        with open(path, "wb") as f:
            f.write(b"x")
        return path


def _wire(monkeypatch, bot, db=None, cfg=CFG):
    async def _cfg():
        return cfg
    monkeypatch.setattr(command_bot, "_forum_bot", lambda: bot)
    monkeypatch.setattr(command_bot, "_load_forum_config", _cfg)
    monkeypatch.setattr(command_bot, "db", db or _DB())
    fx._ACCOUNT_NAME_CACHE.clear()


async def test_photo_we_sent_is_posted_as_a_file(monkeypatch):
    bot = _Bot()
    _wire(monkeypatch, bot)
    assert await command_bot.relay_outgoing_media_to_forum(3, 4, _Msg(), "") == "relayed"
    (topic_id, path, caption, existed), = bot.files
    assert topic_id == 77 and existed and caption == "📤 William Been:"
    assert bot.texts == []                      # no "[Media]" line next to the photo
    assert not os.path.exists(path)             # nothing left on disk


async def test_caption_travels_with_the_file(monkeypatch):
    bot = _Bot()
    _wire(monkeypatch, bot)
    await command_bot.relay_outgoing_media_to_forum(3, 4, _Msg(), "đây là bảng phí")
    assert bot.files[0][2] == "📤 William Been:\n\nđây là bảng phí"
    assert bot.texts == []


async def test_long_caption_is_sent_as_its_own_message(monkeypatch):
    bot = _Bot()
    _wire(monkeypatch, bot)
    await command_bot.relay_outgoing_media_to_forum(3, 4, _Msg(), "a" * 1500)
    assert bot.files[0][2] == "📤 William Been:"
    assert bot.texts[0][1].endswith("a" * 1500)


async def test_failed_upload_still_leaves_a_line_in_the_topic(monkeypatch):
    bot = _Bot(fail=True)
    _wire(monkeypatch, bot)
    assert await command_bot.relay_outgoing_media_to_forum(3, 4, _Msg(), "") == "error"
    assert bot.texts == [(77, "📤 William Been:\n\n[Media]")]
    import proof_relay
    assert not [f for f in os.listdir(proof_relay.MEDIA_DIR) if f.startswith("out_3_4_")]


async def test_no_topic_means_no_download(monkeypatch):
    bot = _Bot()
    _wire(monkeypatch, bot, db=_DB(topic=None))
    msg = SimpleNamespace(id=1, photo=object(), document=None)   # no download_media: must not be called
    assert await command_bot.relay_outgoing_media_to_forum(3, 4, msg, "") == "no_topic"
    assert bot.files == [] and bot.texts == []


def test_outgoing_handler_routes_media_to_the_file_relay():
    src = (pathlib.Path(__file__).resolve().parent.parent / "dm_reply_tracker.py").read_text(encoding="utf-8")
    assert "relay_outgoing_media_to_forum" in src
