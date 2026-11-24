"""23/11 AR MALKU: the AI handed a campaign lead over and nobody saw it.

- campaign logs keep the group id as target, so the forum never opened the lead's topic;
- the handover alert never reached the lead's topic (no topic, no buttons);
- the 🔔 Việc đang chạy panel did not show KOLs waiting on a person.
"""
import pytest

import database as db
import dm_reply_tracker as drt
import telegram_forum_inbox as fx
from routes import tasks


async def _campaign_log(account_id, target_user_id, username):
    async with db.get_db() as conn:
        await conn.execute("INSERT INTO dm_campaigns (id, name, scrape_job_id, status) "
                           "VALUES (19, 'KOL list', 'job-19', 'completed')")
        await conn.execute(
            "INSERT INTO dm_campaign_logs (campaign_id, account_id, target_user_id, target_username, status, sent_at) "
            "VALUES (19, ?, ?, ?, 'success', datetime('now'))", (account_id, target_user_id, username))
        await conn.commit()


@pytest.mark.asyncio
async def test_campaign_log_with_group_id_matches_by_username():
    await _campaign_log(3, -1943278308, "armalku")
    assert (await db.get_telegram_outreach_proof(3, 5514659202, username="ArMalku"))["source"] == "campaign"
    # Username read from the follow-up chat when the caller has none.
    await db.get_or_create_followup_chat(account_id=3, user_id=5514659202, username="armalku")
    assert (await db.get_telegram_outreach_proof(3, 5514659202))["source"] == "campaign"
    # Another person, same nick: still nothing.
    assert await db.get_telegram_outreach_proof(3, 777, username="someone_else") is None


@pytest.mark.asyncio
async def test_chat_linked_to_a_campaign_is_proof():
    await db.get_or_create_followup_chat(account_id=4, user_id=44, username="x", campaign_id=19)
    assert (await db.get_telegram_outreach_proof(4, 44))["source"] == "followup"


class _Bot:
    def __init__(self):
        self.created, self.sent = [], []

    async def create_forum_topic(self, group_id, title):
        self.created.append(title)
        return 321

    async def send_topic_message(self, group_id, topic_id, text, buttons=None):
        self.sent.append((topic_id, text, buttons))


@pytest.mark.asyncio
async def test_handover_lands_in_the_leads_topic_with_buttons():
    await _campaign_log(3, -1943278308, "armalku")
    bot = _Bot()
    res = await fx.relay_handover(db, bot, fx.ForumConfig(True, -1005, 1, {9}), account_id=3,
                                  user_id=5514659202, username="armalku", name="AR MALKU",
                                  reason="AI tự chuyển", buttons=[["on", "off"]])
    assert res == "relayed" and len(bot.created) == 1
    topic_id, text, buttons = bot.sent[0]
    assert topic_id == 321 and "AI đã dừng" in text and "AI tự chuyển" in text and buttons


@pytest.mark.asyncio
async def test_handover_without_proof_or_forum_off_posts_nothing():
    bot = _Bot()
    cfg = fx.ForumConfig(True, -1005, 1, {9})
    assert await fx.relay_handover(db, bot, cfg, account_id=9, user_id=99, username="nobody",
                                   name=None, reason="r") == "no_outreach_proof"
    assert await fx.relay_handover(db, bot, fx.ForumConfig(False, -1005, 1, {9}), account_id=9,
                                   user_id=99, username=None, name=None, reason="r") == "disabled"
    assert bot.sent == [] and bot.created == []


def test_handover_reason_names_the_kol_message_and_the_dangling_question():
    hist = [{"role": "assistant", "content": "hi"}, {"role": "user", "content": "I'm affiliate  with exness"}]
    why = drt._ai_handover_reason(hist, "What deal do you have there?\n[METRICS: {\"intent_score\": 60}]")
    assert "«I'm affiliate with exness»" in why and "vừa hỏi KOL" in why
    assert "vừa hỏi KOL" not in drt._ai_handover_reason(hist, "The team will verify it and get back to you.")
    assert drt._ends_with_question("Which plan? [banner: deposit10]")


async def _chat(uid, status, *msgs, step=None, owed=False):
    await db.get_or_create_followup_chat(account_id=1, user_id=uid, username=f"kol{uid}")
    for role, text in msgs:
        await db.append_followup_chat_message(1, uid, role, text)
    await db.update_followup_chat_status(1, uid, status)
    async with db.get_db() as conn:
        if step:
            await conn.execute("UPDATE ai_followup_chats SET kol_step = ? WHERE user_id = ?", (step, uid))
        if owed:
            await conn.execute("UPDATE ai_followup_chats SET owed_since = datetime('now') WHERE user_id = ?", (uid,))
        await conn.commit()


@pytest.mark.asyncio
async def test_task_center_shows_kols_waiting_on_a_person():
    await _chat(1, "needs_human", ("assistant", "What deal do you have?"), ("user", "Gold plan"))
    await _chat(2, "needs_human", ("user", "hi"), ("assistant", "Team will check."))       # AI handed over
    await _chat(3, "needs_human", ("user", "hi"), ("assistant", "[Human Admin]: done"))   # a person answered
    await _chat(4, "active", ("user", "hi"))                                               # AI has it
    await _chat(5, "needs_human", ("user", "no thanks"), step="declined")                 # closed lead
    await _chat(6, "active", ("assistant", "we'll get back"), owed=True)                  # open promise
    rows = await tasks._reply_queue()
    assert len(rows) == 1
    row = rows[0]
    assert row["bucket"] == "warn" and row["page"] == "ai-followup"
    assert row["name"].startswith("3 KOL")
    for who in ("@kol1", "@kol2", "@kol6"):
        assert who in row["note"]
    assert "@kol3" not in row["note"] and "@kol5" not in row["note"]


@pytest.mark.asyncio
async def test_task_center_has_no_queue_row_when_nobody_waits():
    await _chat(7, "active", ("user", "hi"), ("assistant", "hello"))
    assert await tasks._reply_queue() == []
