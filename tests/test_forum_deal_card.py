"""Pinned deal card in each lead's forum topic."""
import json
from types import SimpleNamespace

import pytest

import database as db
import telegram_forum_inbox as fx

G, T, A, U = -1001, 77, 1, 555
CFG = fx.ForumConfig(True, G, 1, {9})


class Bot:
    def __init__(self):
        self.sent, self.edited, self.pinned, self.fail_edit = [], [], [], False

    async def send_topic_message(self, group_id, topic_id, text, buttons=None):
        self.sent.append(text)
        return SimpleNamespace(id=1000 + len(self.sent))

    async def edit_topic_message(self, group_id, message_id, text):
        if self.fail_edit:
            raise RuntimeError("MESSAGE_ID_INVALID")
        self.edited.append((message_id, text))

    async def pin_topic_message(self, group_id, message_id):
        self.pinned.append(message_id)


async def _lead(profile: dict):
    await db.get_or_create_followup_chat(account_id=A, user_id=U, username="sigma", name="Syed")
    await db.upsert_telegram_topic({"group_id": G, "topic_id": T, "account_id": A, "user_id": U,
                                    "username": "sigma", "name": "Syed", "campaign_name": None,
                                    "status": "active", "last_lead_message": None})
    async with db.get_db() as conn:
        await conn.execute("INSERT OR REPLACE INTO kol_profiles (account_id, user_id, profile_json) VALUES (?,?,?)",
                           (A, U, json.dumps(profile)))
        await conn.execute("UPDATE ai_followup_chats SET summary = ? WHERE account_id = ? AND user_id = ?",
                           ("18M/month KOL wants $500 upfront", A, U))
        await conn.commit()
    return await db.find_telegram_topic_for_user(G, A, U)


@pytest.mark.asyncio
async def test_card_posted_pinned_then_edited_only_when_deal_changes():
    topic = await _lead({"platform": "Discord, Twitter", "community_link": "https://discord.gg/x",
                         "monthly_volume": "18M", "revshare_request": "1k upfront"})
    bot = Bot()
    assert await fx.refresh_deal_card(db, bot, CFG, topic) == "posted"
    card = bot.sent[0]
    for part in ("THẺ DEAL", "@sigma", "Discord, Twitter", "https://discord.gg/x",
                 "Volume/tháng: 18M", "KOL đòi: 1k upfront", "Tóm tắt: 18M/month KOL wants $500 upfront"):
        assert part in card
    assert bot.pinned == [1001]

    assert await fx.refresh_deal_card(db, bot, CFG, topic) == "unchanged"

    async with db.get_db() as conn:
        await conn.execute("UPDATE kol_profiles SET weex_uid = '8764784156' WHERE user_id = ?", (U,))
        await conn.commit()
    assert await fx.refresh_deal_card(db, bot, CFG, topic) == "edited"
    assert bot.edited[0][0] == 1001 and "UID: 8764784156" in bot.edited[0][1]
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_deleted_card_is_reposted_and_hidden_topic_skipped():
    topic = await _lead({"platform": "Telegram"})
    bot = Bot()
    await fx.refresh_deal_card(db, bot, CFG, topic)
    await db.set_telegram_topic_card(G, T, 1001, "stale")
    bot.fail_edit = True
    assert await fx.refresh_deal_card(db, bot, CFG, topic) == "posted"
    assert bot.pinned == [1001, 1002]
    assert (await db.find_telegram_topic_for_user(G, A, U))["card_message_id"] == 1002

    await db.update_telegram_topic_status(G, T, "hidden")
    hidden = await db.find_telegram_topic_for_user(G, A, U)
    assert await fx.refresh_deal_card(db, bot, CFG, hidden) == "skipped"
