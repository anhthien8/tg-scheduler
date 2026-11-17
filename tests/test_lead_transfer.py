"""Hand a lead over to another of our nicks (the KOL blocked the old one).

The new nick's chat inherits profile / summary / step with the AI held off and an EMPTY
transcript (a chat only holds what that nick really exchanged), the old chat is hidden,
the forum gets a new topic with the old transcript, and nothing is ever sent to the lead.
"""
import asyncio
import json
import types

import database as db
import routes.ai_followup as routes
import telegram_forum_inbox as fx

A, B, U = 1, 2, 777


async def _setup(step="deal_agreed"):
    for acc_id, name in ((A, "Will"), (B, "Nauy")):
        if not await db.get_account(acc_id):
            await db.create_account({"id": acc_id, "name": name, "phone": f"+840{acc_id}", "api_id": "x",
                                     "api_hash": "y", "session_name": f"s{acc_id}"})
    await db.get_or_create_followup_chat(account_id=A, user_id=U, username="kol_lead", name="KOL")
    await db.append_followup_chat_message(A, U, "assistant", "Hi, WEEX BD here")
    await db.append_followup_chat_message(A, U, "user", "pay me a fixed fee upfront")
    await db.set_kol_step(A, U, step, actor="admin:test")
    await db.upsert_kol_profile(A, U, {"weex_uid": "2000148169", "volume_claimed": "18",
                                       "distribution_enabled": True})


async def test_transfer_carries_lead_and_holds_ai():
    await _setup()
    await db.set_owed_answer(A, U, "fixed fee — team will confirm")
    res = await db.transfer_followup_chat(A, B, U)
    assert res == {"created": True, "kol_step": "deal_agreed"}

    new = await db.get_followup_chat(B, U)
    # Nothing was ever said from this nick: an AI switched on later has no stale message to answer.
    assert new["history"] == [] and not new["last_user_message_at"]
    assert new["username"] == "kol_lead" and new["kol_step"] == "deal_agreed"
    assert new["summary"].startswith("[Chuyển từ nick Will")
    assert new["owed_since"] and new["owed_note"] == "fixed fee — team will confirm"
    # Operator hold: stage 'paused' never auto-resumes the AI (unlike a 12h human takeover).
    assert new["stage"] == "paused" and new["status"] == "needs_human" and new["human_takeover_at"]

    profile = await db.get_kol_profile(B, U)
    assert profile["weex_uid"] == "2000148169" and profile["volume_claimed"] == "18"
    # Channel posts must not go out from a nick the KOL has never heard from.
    assert not profile.get("distribution_enabled")
    assert all(k["account_id"] != B for k in await db.get_kols_for_distribution())

    old = await db.get_followup_chat(A, U)
    assert old["status"] == "bot_ignored" and old["stage"] == "paused"
    assert len(old["history"]) == 2                                       # old transcript stays put
    assert (await db.get_kol_profile(A, U))["distribution_enabled"] is False

    events = await db.get_kol_step_events(B, U)
    assert events[0]["reason"] == f"Chuyển lead từ nick #{A}"
    assert any(e["to_step"] == "deal_agreed" and e["actor"] == "admin:test" for e in events)  # deal date kept
    supported = [(k["account_id"], k["user_id"]) for k in await db.get_supported_kols()]
    assert supported == [(B, U)]                                          # listed once, on the new nick


async def test_ai_switched_on_after_transfer_has_nothing_to_answer(client, monkeypatch):
    await _setup()
    await db.transfer_followup_chat(A, B, U)
    fired = []

    async def fake_reply(account_id, user_id):
        fired.append((account_id, user_id))
    monkeypatch.setattr(routes, "trigger_ai_reply", fake_reply)
    r = client.post(f"/api/ai-followup/chats/{B}/{U}/status", json={"status": "active"})
    assert r.status_code == 200, r.text
    await asyncio.sleep(0)
    assert fired == []                                                    # no copied message to reply to
    new = await db.get_followup_chat(B, U)
    assert new["status"] == "active" and new["stage"] == "nurture"        # hold lifted consistently


async def test_transfer_keeps_an_existing_chat_on_the_new_nick():
    await _setup()
    await db.get_or_create_followup_chat(account_id=B, user_id=U, username="kol_lead", name="KOL")
    await db.append_followup_chat_message(B, U, "user", "hello new nick")
    await db.upsert_kol_profile(B, U, {"weex_uid": "999"})

    res = await db.transfer_followup_chat(A, B, U)
    assert res == {"created": False, "kol_step": "prospect"}
    new = await db.get_followup_chat(B, U)
    assert [m["content"] for m in new["history"]] == ["hello new nick"]   # its own transcript survives
    assert new["stage"] == "paused" and new["status"] == "needs_human"
    profile = await db.get_kol_profile(B, U)
    assert profile["weex_uid"] == "999" and profile["volume_claimed"] == "18"   # only gaps are filled


async def test_transfer_unknown_chat_returns_none():
    assert await db.transfer_followup_chat(A, B, 4242) is None


class _Bot:
    def __init__(self):
        self.created, self.sent = [], []

    async def create_forum_topic(self, group_id, title):
        self.created.append(title)
        return 500

    async def send_topic_message(self, group_id, topic_id, text, buttons=None):
        self.sent.append((topic_id, text))


async def test_forum_notes_old_topic_and_opens_new_one_with_history():
    await _setup()
    cfg = fx.ForumConfig(enabled=True, group_id=-100123, general_topic_id=1, admin_ids=set())
    await db.upsert_telegram_topic({"group_id": cfg.group_id, "topic_id": 400, "account_id": A, "user_id": U,
                                    "username": "kol_lead", "name": "KOL", "campaign_name": None,
                                    "status": "active", "last_lead_message": None})
    await db.transfer_followup_chat(A, B, U)
    bot = _Bot()
    res = await fx.relay_transfer(db, bot, cfg, from_account_id=A, to_account_id=B, user_id=U,
                                  username="kol_lead", name="KOL")
    assert res == {"status": "relayed", "topic_id": 500}
    assert bot.created == [f"a{B} · kol_lead · {U}"]
    assert (await db.find_telegram_topic_for_user(cfg.group_id, B, U))["topic_id"] == 500
    old_topic = [t for tid, t in bot.sent if tid == 400]
    assert len(old_topic) == 1 and "Nauy" in old_topic[0]
    new_topic = "\n".join(t for tid, t in bot.sent if tid == 500)
    assert "Will" in new_topic and "CHƯA nhắn KOL" in new_topic
    assert "pay me a fixed fee upfront" in new_topic                     # old nick's transcript replayed
    assert "Không có tin nhắn nào" not in new_topic

    # The new nick already had its own chat: the note says so.
    await fx.relay_transfer(db, bot, cfg, from_account_id=A, to_account_id=B, user_id=U,
                            username="kol_lead", name="KOL", created=False)
    assert any("đã có hội thoại riêng" in t for _, t in bot.sent)

    # A second transfer to the same nick reuses the topic.
    await fx.relay_transfer(db, bot, cfg, from_account_id=A, to_account_id=B, user_id=U,
                            username="kol_lead", name="KOL")
    assert len(bot.created) == 1


async def test_forum_survives_a_dead_old_topic_and_an_empty_history():
    await _setup()
    cfg = fx.ForumConfig(enabled=True, group_id=-100123, general_topic_id=1, admin_ids=set())
    await db.upsert_telegram_topic({"group_id": cfg.group_id, "topic_id": 400, "account_id": A, "user_id": U,
                                    "username": "kol_lead", "name": "KOL", "campaign_name": None,
                                    "status": "active", "last_lead_message": None})
    async with db.get_db() as conn:
        await conn.execute("UPDATE ai_followup_chats SET history_json = '[]' WHERE account_id = ?", (A,))
        await conn.commit()
    await db.transfer_followup_chat(A, B, U)

    class _DeadOldTopic(_Bot):
        async def send_topic_message(self, group_id, topic_id, text, buttons=None):
            if topic_id == 400:
                raise RuntimeError("TOPIC_CLOSED")
            await super().send_topic_message(group_id, topic_id, text, buttons)

    bot = _DeadOldTopic()
    res = await fx.relay_transfer(db, bot, cfg, from_account_id=A, to_account_id=B, user_id=U,
                                  username="kol_lead", name="KOL")
    assert res["status"] == "relayed" and bot.created                    # new topic still opens
    assert "Không có tin nhắn nào" in bot.sent[-1][1]                     # and says there is no history


class _Client:
    def __init__(self, entity_id=U, fail=False):
        self.entity_id, self.fail, self.lookups, self.sent = entity_id, fail, [], []

    async def get_entity(self, key):
        self.lookups.append(key)
        if self.fail:
            raise ValueError("No user has that username")
        return types.SimpleNamespace(id=self.entity_id)

    async def send_message(self, *a, **k):
        self.sent.append(a)


async def test_route_transfers_without_messaging_the_lead(client, monkeypatch):
    await _setup()
    tg_client = _Client()
    monkeypatch.setattr(routes.tg, "get_client", lambda acc: tg_client)
    r = client.post(f"/api/ai-followup/chats/{A}/{U}/transfer", json={"to_account_id": B})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account_id"] == B and body["created"] is True and body["forum"] in ("relaying", "unavailable")
    assert tg_client.lookups == ["kol_lead"] and tg_client.sent == []
    assert (await db.get_followup_chat(B, U))["stage"] == "paused"


async def test_route_refuses_bad_targets(client, monkeypatch):
    await _setup()
    url = f"/api/ai-followup/chats/{A}/{U}/transfer"
    assert client.post(url, json={"to_account_id": A}).status_code == 400          # same nick
    assert client.post(url, json={"to_account_id": 99}).status_code == 404         # unknown nick
    assert client.post(f"/api/ai-followup/chats/{A}/4242/transfer", json={"to_account_id": B}).status_code == 404

    monkeypatch.setattr(routes.tg, "get_client", lambda acc: None)                 # nick offline
    assert client.post(url, json={"to_account_id": B}).status_code == 400

    monkeypatch.setattr(routes.tg, "get_client", lambda acc: _Client(fail=True))   # cannot find the KOL
    assert client.post(url, json={"to_account_id": B}).status_code == 400

    monkeypatch.setattr(routes.tg, "get_client", lambda acc: _Client(entity_id=5)) # @username now someone else
    r = client.post(url, json={"to_account_id": B})
    assert r.status_code == 400 and "người khác" in r.json()["detail"]

    # Nothing was transferred by any refused call.
    assert await db.get_followup_chat(B, U) is None
    assert (await db.get_followup_chat(A, U))["status"] != "bot_ignored"
    assert json.loads((await db.get_followup_chat(A, U))["history_json"])
