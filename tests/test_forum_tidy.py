"""Forum topics that read at a glance (24/11): status emoji titles, dead topics closed,
one pinned "📋 Việc cần làm" board edited in place."""
import json
from datetime import datetime, timezone

import pytest

import database as db
import telegram_forum_inbox as fx

G = -1004352266125
CFG = fx.ForumConfig(True, G, 1, {9})
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def _r(**kw):
    base = {"topic_status": "active", "status": "active", "kol_step": "prospect", "owed_since": None,
            "last_role": "user", "last_text": "hi", "is_own": 0}
    return {**base, **kw}


@pytest.mark.parametrize("row,bucket", [
    (_r(topic_status="hidden"), "closed"),
    (_r(status="bot_ignored"), "closed"),
    (_r(kol_step="declined", status="needs_human"), "closed"),
    (_r(is_own=1), "closed"),
    (_r(status="needs_human", last_role="user"), "wait"),
    (_r(status="needs_human", last_role="assistant", last_text="Team will check"), "wait"),
    (_r(status="needs_human", last_role="assistant", last_text="[Human Admin via Forum]: ok"), "ball"),
    (_r(owed_since="2026-11-20 10:00:00"), "wait"),
    (_r(kol_step="uid_received"), "link"),
    (_r(kol_step="first_post"), "signed"),
    (_r(status="needs_human", kol_step="active", last_role="user"), "wait"),   # signed but waiting: answer first
    (_r(), "ai"),
    (_r(status=None), "ball"),
])
def test_bucket(row, bucket):
    assert fx.topic_bucket(row) == bucket


def test_title_has_status_and_nick_not_the_numeric_id():
    t = fx.topic_title("wait", "armalku", "AR MALKU", 5514659202, "Will - SEA Weex")
    assert t == "🙋 @armalku · Will - SEA Weex" and "5514659202" not in t
    assert fx.topic_title("closed", None, "", 42, "Nick #3").startswith("💤 42")


class Bot:
    def __init__(self, fail_edit=None):
        self.edits, self.created, self.pinned, self.html, self.edited_html = [], [], [], [], []
        self.fail_edit = fail_edit or {}

    async def create_forum_topic(self, g, title):
        self.created.append(title)
        return 900 + len(self.created)

    async def edit_topic(self, g, tid, title=None, closed=None):
        if tid in self.fail_edit:
            raise self.fail_edit[tid]
        self.edits.append((tid, title, closed))

    async def pin_topic(self, g, tid):
        self.pinned.append(tid)

    async def send_html(self, g, tid, text):
        self.html.append((tid, text))
        return type("M", (), {"id": 77})()

    async def edit_html(self, g, mid, text):
        self.edited_html.append((mid, text))

    async def send_topic_message(self, g, tid, text, buttons=None):
        pass


async def _lead(uid, topic_id, *, status="active", step=None, msgs=(("user", "hi"),), topic_status="active",
                username=None):
    await db.get_or_create_followup_chat(account_id=3, user_id=uid, username=username or f"kol{uid}")
    for role, text in msgs:
        await db.append_followup_chat_message(3, uid, role, text)
    await db.update_followup_chat_status(3, uid, status)
    async with db.get_db() as conn:
        if step:
            await conn.execute("UPDATE ai_followup_chats SET kol_step = ? WHERE user_id = ?", (step, uid))
        await conn.commit()
    await db.upsert_telegram_topic({"group_id": G, "topic_id": topic_id, "account_id": 3, "user_id": uid,
                                    "username": username or f"kol{uid}", "name": None, "status": topic_status})


@pytest.mark.asyncio
async def test_tidy_edits_dead_first_then_live_and_posts_a_pinned_board():
    await _lead(1, 101, status="needs_human", msgs=(("assistant", "hey"), ("user", "Gold plan")))  # 🙋
    await _lead(2, 102, status="needs_human", step="declined")                                     # 💤
    await _lead(3, 103)                                                                            # 🤖
    await _lead(4, 104, topic_status="hidden")                                                     # 💤
    bot = Bot()
    rep = await fx.tidy_forum(db, bot, CFG, pause=0, now=NOW)
    assert rep["edited"] == 4 and rep["pending"] == 0 and rep["board"] == "posted"
    order = [tid for tid, _, _ in bot.edits if tid < 900]          # 9xx = the board topic
    assert order[-1] == 101 and set(order[:2]) == {102, 104}       # the one waiting on you ends on top
    titles = {tid: (title, closed) for tid, title, closed in bot.edits}
    assert titles[101] == ("🙋 @kol1 · Nick #3", False)
    assert titles[102][0].startswith("💤") and titles[102][1] is True
    # Board: own read-only topic (pinning is manual: bots may not); only 🙋 / ⏳ / 🔗 leads, with topic links.
    assert bot.created == [fx.BOARD_TITLE] and bot.pinned == []
    assert (901, None, True) in bot.edits
    tid, text = bot.html[0]
    assert tid == 901 and "@kol1" in text and "/101" in text and "@kol3" not in text
    # Nothing changed: no edits, board untouched.
    bot2 = Bot()
    rep2 = await fx.tidy_forum(db, bot2, CFG, pause=0, now=NOW)
    assert rep2["edited"] == 0 and rep2["board"] == "unchanged" and not bot2.edits and not bot2.html


@pytest.mark.asyncio
async def test_status_change_renames_and_board_is_edited_in_place():
    await _lead(5, 105, status="needs_human", msgs=(("user", "hello?"),))
    await fx.tidy_forum(db, Bot(), CFG, pause=0, now=NOW)
    await db.append_followup_chat_message(3, 5, "assistant", "[Human Admin]: here you go")
    bot = Bot()
    rep = await fx.tidy_forum(db, bot, CFG, pause=0, now=NOW)
    assert bot.edits == [(105, "💬 @kol5 · Nick #3", False)]
    assert rep["board"] == "edited" and bot.edited_html and "Không có KOL" in bot.edited_html[0][1]


@pytest.mark.asyncio
async def test_rate_limit_stops_the_run_and_failed_edits_retry_later():
    await _lead(6, 106, status="needs_human", step="declined")
    await _lead(7, 107, status="needs_human", step="declined")
    bot = Bot(fail_edit={106: RuntimeError("FloodWaitError: wait 30s")})
    rep = await fx.tidy_forum(db, bot, CFG, pause=0, now=NOW)
    assert rep["edited"] == 0 and rep["pending"] == 2 and [e for e in bot.edits if e[0] < 900] == []
    # A topic deleted in Telegram is recorded once and not retried forever.
    bot = Bot(fail_edit={106: RuntimeError("TOPIC_ID_INVALID")})
    rep = await fx.tidy_forum(db, bot, CFG, pause=0, now=NOW)
    assert rep["edited"] == 2 and rep["pending"] == 0


@pytest.mark.asyncio
async def test_board_reposts_when_its_topic_was_deleted():
    await db.set_setting(fx.BOARD_SETTING, json.dumps({"group_id": G, "topic_id": 500, "message_id": 9, "hash": "x"}))

    class Gone(Bot):
        async def edit_html(self, g, mid, text):
            raise RuntimeError("MESSAGE_ID_INVALID")

        async def send_html(self, g, tid, text):
            if tid == 500:
                raise RuntimeError("TOPIC_DELETED")
            return await super().send_html(g, tid, text)

    bot = Gone()
    assert await fx.refresh_board(db, bot, CFG, [], NOW) == "posted"
    assert bot.created == [fx.BOARD_TITLE] and bot.html[0][0] == 901
    assert json.loads(await db.get_setting(fx.BOARD_SETTING))["topic_id"] == 901


def test_board_sections_and_order():
    rows = [
        dict(bucket="wait", topic_id=1, user_id=1, account_id=3, username="old", account_name="Will",
             last_user_message_at="2026-11-24 01:00:00", lead_tier="Tier A"),
        dict(bucket="wait", topic_id=2, user_id=2, account_id=3, username="new", account_name="Will",
             last_user_message_at="2026-11-24 11:30:00"),
        dict(bucket="wait", topic_id=3, user_id=3, account_id=3, username="promised", account_name="Will",
             owed_since="2026-11-20 12:00:00"),
        dict(bucket="link", topic_id=4, user_id=4, account_id=3, username="uid", account_name="Will",
             updated_at="2026-11-10 12:00:00"),
        dict(bucket="ai", topic_id=5, user_id=5, account_id=3, username="ai", account_name="Will"),
    ]
    text = fx.build_board(rows, G, NOW)
    assert text.index("@new") < text.index("@old") < text.index("Lời hứa") < text.index("@promised") < text.index("@uid")
    assert "chờ dưới 1 giờ" in text and "chờ 11 giờ · ⭐ Tier A" in text and "hứa 4 ngày" in text
    assert "@ai" not in text and 'href="https://t.me/c/4352266125/2"' in text


def test_board_is_capped_under_telegrams_limit():
    rows = [dict(bucket="wait", topic_id=i, user_id=i, account_id=3, username=f"kol_{i:04d}_" + "x" * 30,
                 account_name="Will - SEA Weex", last_user_message_at="2026-11-24 10:00:00") for i in range(200)]
    text = fx.build_board(rows, G, NOW)
    import re
    assert len(re.sub(r"<[^>]+>", "", text)) < 4096 and "lead nữa" in text
