"""/hide in a lead's topic: nothing about that lead reaches the forum group."""
import asyncio

import telegram_forum_inbox as fx


class _DB:
    def __init__(self, topic):
        self.topic = topic

    async def find_telegram_topic_for_user(self, g, a, u):
        return self.topic

    async def get_telegram_outreach_proof(self, a, u, username=None):
        return {"name": "c"}

    async def update_telegram_topic_last_message(self, *a):
        pass


class _Bot:
    def __init__(self):
        self.sent, self.created = [], 0

    async def create_forum_topic(self, g, t):
        self.created += 1
        return 99

    async def send_topic_message(self, g, t, text):
        self.sent.append(text)


CFG = fx.ForumConfig(enabled=True, group_id=-100, general_topic_id=1, admin_ids=set())
HIDDEN = {"group_id": -100, "topic_id": 7, "account_id": 1, "user_id": 2, "status": "hidden"}


def test_hidden_lead_inbound_not_relayed_and_no_new_topic():
    bot = _Bot()
    r = asyncio.run(fx.relay_inbound(_DB(HIDDEN), bot, CFG, account_id=1, user_id=2,
                                     username="x", name="X", text="off topic"))
    assert r == "hidden" and bot.sent == [] and bot.created == 0


def test_hidden_lead_outgoing_not_relayed():
    bot = _Bot()
    r = asyncio.run(fx.relay_outgoing(_DB(HIDDEN), bot, CFG, account_id=1, user_id=2, text="hi"))
    assert r == "no_topic" and bot.sent == []


def test_active_lead_still_relayed():
    bot = _Bot()
    r = asyncio.run(fx.relay_inbound(_DB(dict(HIDDEN, status="active")), bot, CFG, account_id=1,
                                     user_id=2, username="x", name="X", text="deal"))
    assert r == "relayed" and len(bot.sent) == 1


def _run_hide(monkeypatch, hidden, stage):
    import command_bot as cb
    calls = []

    class _CbDB:
        async def get_telegram_topic_by_thread(self, g, t):
            return dict(HIDDEN, status="active")

        async def update_telegram_topic_status(self, g, t, s):
            calls.append(("topic", s))

        async def get_followup_chat(self, a, u):
            return {"stage": stage}

        async def set_followup_stage(self, a, u, s):
            calls.append(("stage", s))

        async def resume_ai_for_user(self, a, u):
            calls.append(("resume",))

    async def _cfg():
        return CFG

    class _Ev:
        message = None
        client = None

        async def respond(self, text):
            calls.append(("say", text))

    monkeypatch.setattr(cb, "db", _CbDB())
    monkeypatch.setattr(cb, "_extract_topic_id", lambda m: 7)
    monkeypatch.setattr(cb, "_load_forum_config", _cfg)
    asyncio.run(cb._handle_hide(_Ev(), hidden=hidden))
    return calls


def test_hide_also_turns_ai_off(monkeypatch):
    calls = _run_hide(monkeypatch, True, "nurture")
    assert ("topic", "hidden") in calls and ("stage", "paused") in calls and ("resume",) not in calls


def test_unhide_turns_ai_back_on(monkeypatch):
    calls = _run_hide(monkeypatch, False, "paused")
    assert ("topic", "active") in calls and ("resume",) in calls and ("stage", "nurture") in calls
