"""A hand-typed reply refused for PeerFlood waits and is sent once the nick is usable again.

It is sent only when @SpamBot says the nick is free, never when the chat changed
while it waited, and never more than once.
"""
import database as db
import pending_sends as ps
import routes.ai_followup as routes

A, U = 1, 777


class PeerFloodError(Exception):
    def __str__(self):
        return "Too many requests (caused by SendMessageRequest)"


class _Client:
    def is_connected(self):
        return True


async def _setup(monkeypatch, spam="limited", until_msg="Your account is limited until 20 Nov 2099, 08:00 UTC."):
    if not await db.get_account(A):
        await db.create_account({"id": A, "name": "Near", "phone": "+8401", "api_id": "x", "api_hash": "y",
                                 "session_name": "s1"})
    await db.get_or_create_followup_chat(account_id=A, user_id=U, username="apon", name="APON")
    await db.append_followup_chat_message(A, U, "assistant", "Hi, WEEX BD here")
    await db.append_followup_chat_message(A, U, "user", "Can you give me demo funds?")
    state = {"spam": spam, "msg": until_msg, "sent": [], "fail": None, "spam_checks": 0, "unpause_flags": []}

    async def check_spam_status(account_id, max_age_hours=0, auto_unpause=True):
        state["spam_checks"] += 1
        state["unpause_flags"].append(auto_unpause)
        return {"status": state["spam"], "message": state["msg"] if state["spam"] == "limited" else "Good news, no limits"}

    async def get_cached_spam_status(account_id, max_age_hours=24):
        return {"status": state["spam"], "message": state["msg"]} if state["spam"] == "limited" else None

    async def send_text_message(account_id, chat_id, text, parse_mode="html"):
        if state["fail"]:
            raise state["fail"]
        state["sent"].append((account_id, chat_id, text))
        return True

    async def no_alert(*a, **k):
        return None

    async def no_sleep(*a, **k):
        return None

    for mod in (ps.tg, routes.tg):
        monkeypatch.setattr(mod, "check_spam_status", check_spam_status, raising=False)
        monkeypatch.setattr(mod, "get_cached_spam_status", get_cached_spam_status, raising=False)
        monkeypatch.setattr(mod, "send_text_message", send_text_message, raising=False)
        monkeypatch.setattr(mod, "get_client", lambda acc: _Client(), raising=False)
    import alerts
    monkeypatch.setattr(alerts, "send_alert", no_alert)
    monkeypatch.setattr(ps.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(ps, "_schedule_flush", lambda account_id, until: state.setdefault("scheduled", []).append(until))
    return state


async def test_peerflood_send_is_queued_not_lost(client, monkeypatch):
    state = await _setup(monkeypatch)
    state["fail"] = PeerFloodError()
    r = client.post(f"/api/ai-followup/chats/{A}/{U}/send", json={"text": "How much volume per month?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["queued"] is True and body["replaced"] is False
    assert body["until"] and state["scheduled"] == [body["until"]]       # one-shot job right after the limit date
    row = await db.get_pending_send(A, U)
    assert row["text"] == "How much volume per month?" and row["status"] == "waiting"
    assert (await db.get_account(A))["is_paused"]                         # flood guard protected the nick
    assert state["unpause_flags"] == [False]                              # the queue-time check never lifts that pause

    # The chat window shows it (same request that loads the history).
    hist = client.get(f"/api/ai-followup/chats/{A}/{U}/history").json()
    assert hist["pending_send"]["text"] == "How much volume per month?" and hist["pending_send"]["until"] == body["until"]

    # While the nick is paused for PeerFlood a second Send does not even try Telegram.
    state["fail"] = AssertionError("must not call Telegram while the nick is limited")
    r2 = client.post(f"/api/ai-followup/chats/{A}/{U}/send", json={"text": "Edited question"})
    assert r2.json()["queued"] is True and r2.json()["replaced"] is True
    assert (await db.get_pending_send(A, U))["text"] == "Edited question"


async def test_other_send_errors_are_not_queued(client, monkeypatch):
    state = await _setup(monkeypatch)
    state["fail"] = RuntimeError("USER_PRIVACY_RESTRICTED")
    r = client.post(f"/api/ai-followup/chats/{A}/{U}/send", json={"text": "hello"})
    assert r.status_code == 502 and await db.get_pending_send(A, U) is None


async def test_flush_waits_for_spambot_then_sends_once(monkeypatch):
    state = await _setup(monkeypatch)
    chat = await db.get_followup_chat(A, U)
    await db.set_pending_send(A, U, "How much volume per month?", ps.chat_marker(chat))

    assert await ps.flush() == {"sent": 0, "dropped": 0}                  # still limited: nothing goes out
    assert state["sent"] == [] and (await db.get_pending_send(A, U))["status"] == "waiting"

    state["spam"] = "unknown"                                             # cannot tell: do not risk it
    assert (await ps.flush())["sent"] == 0 and state["sent"] == []

    state["spam"] = "free"
    assert await ps.flush() == {"sent": 1, "dropped": 0}
    assert state["sent"] == [(A, U, "How much volume per month?")]
    assert await db.get_pending_send(A, U) is None
    chat = await db.get_followup_chat(A, U)                               # in the transcript, AI held for the human
    assert chat["history"][-1]["content"] == "[Human Admin]: How much volume per month?"
    assert chat["status"] == "needs_human" and chat["human_takeover_at"]
    assert await ps.flush() == {"sent": 0, "dropped": 0} and len(state["sent"]) == 1   # never twice


async def test_flush_drops_when_the_chat_moved_on(monkeypatch):
    state = await _setup(monkeypatch, spam="free")
    chat = await db.get_followup_chat(A, U)
    await db.set_pending_send(A, U, "How much volume per month?", ps.chat_marker(chat))
    await db.append_followup_chat_message(A, U, "user", "never mind, I joined another exchange")

    assert await ps.flush() == {"sent": 0, "dropped": 1}
    assert state["sent"] == []
    row = await db.get_pending_send(A, U)
    assert row["status"] == "dropped" and "tin mới" in row["reason"]      # kept for the owner to read
    assert await ps.flush() == {"sent": 0, "dropped": 0}                  # a dropped reply is never retried


async def test_flush_drops_ignored_blacklisted_and_expired(monkeypatch):
    state = await _setup(monkeypatch, spam="free")
    chat = await db.get_followup_chat(A, U)
    await db.set_pending_send(A, U, "x", ps.chat_marker(chat))
    await db.update_followup_chat_status(A, U, "bot_ignored")             # ignored, or moved to another nick
    assert await ps.flush() == {"sent": 0, "dropped": 1}
    assert "bỏ qua" in (await db.get_pending_send(A, U))["reason"]

    await db.update_followup_chat_status(A, U, "needs_human")
    chat = await db.get_followup_chat(A, U)
    await db.set_pending_send(A, U, "x", ps.chat_marker(chat))
    async with db.get_db() as conn:                                       # waited longer than the limit
        await conn.execute("UPDATE pending_sends SET created_at = datetime('now', '-8 days')")
        await conn.commit()
    state["spam"] = "limited"                                             # expiry does not need the nick free
    assert await ps.flush() == {"sent": 0, "dropped": 1}
    assert "quá 7 ngày" in (await db.get_pending_send(A, U))["reason"] and state["sent"] == []


async def test_flush_stops_on_peerflood_and_gives_up_after_three(monkeypatch):
    state = await _setup(monkeypatch, spam="free")
    chat = await db.get_followup_chat(A, U)
    await db.set_pending_send(A, U, "x", ps.chat_marker(chat))
    state["fail"] = PeerFloodError()
    for attempt in (1, 2):
        assert await ps.flush() == {"sent": 0, "dropped": 0}
        row = await db.get_pending_send(A, U)
        assert row["status"] == "waiting" and row["attempts"] == attempt
        await db.unpause_account(A)                                       # SpamBot said free again
    assert await ps.flush() == {"sent": 0, "dropped": 1}
    assert (await db.get_pending_send(A, U))["status"] == "dropped"


async def test_owner_paused_nick_sends_nothing(monkeypatch):
    state = await _setup(monkeypatch, spam="free")
    chat = await db.get_followup_chat(A, U)
    await db.set_pending_send(A, U, "x", ps.chat_marker(chat))
    await db.pause_account(A, "Tắt tay")
    assert await ps.flush() == {"sent": 0, "dropped": 0}
    assert state["sent"] == [] and state["spam_checks"] == 0              # SpamBot not even asked


async def test_cancel_returns_the_text(client, monkeypatch):
    await _setup(monkeypatch)
    chat = await db.get_followup_chat(A, U)
    await db.set_pending_send(A, U, "draft to reuse", ps.chat_marker(chat))
    r = client.delete(f"/api/ai-followup/chats/{A}/{U}/pending-send")
    assert r.status_code == 200 and r.json()["text"] == "draft to reuse"
    assert await db.get_pending_send(A, U) is None
    assert client.delete(f"/api/ai-followup/chats/{A}/{U}/pending-send").status_code == 404


async def test_successful_send_clears_an_older_waiting_reply(client, monkeypatch):
    state = await _setup(monkeypatch, spam="free")
    chat = await db.get_followup_chat(A, U)
    await db.set_pending_send(A, U, "old question", ps.chat_marker(chat))
    r = client.post(f"/api/ai-followup/chats/{A}/{U}/send", json={"text": "new message"})
    assert r.json() == {"ok": True} and state["sent"][-1][2] == "new message"
    assert await db.get_pending_send(A, U) is None
    # Telegram does not echo the app's own sends: the route records the message itself.
    chat = await db.get_followup_chat(A, U)
    assert chat["history"][-1]["content"] == "[Human Admin]: new message" and chat["status"] == "needs_human"


async def test_reply_cancelled_or_replaced_during_the_gap_is_not_sent(monkeypatch):
    """The flush works from a list read earlier; what it sends must still be what is stored."""
    state = await _setup(monkeypatch, spam="free")
    V, W = 778, 779
    for uid in (V, W):
        await db.get_or_create_followup_chat(account_id=A, user_id=uid, username=f"u{uid}", name="K")
        await db.append_followup_chat_message(A, uid, "user", "hi")
    for uid, text in ((U, "first"), (V, "second, will be cancelled"), (W, "third, will be replaced")):
        await db.set_pending_send(A, uid, text, ps.chat_marker(await db.get_followup_chat(A, uid)))

    async def operator_acts_during_the_gap(*a, **k):
        if not state.get("acted"):
            state["acted"] = True
            await db.delete_pending_send(A, V)                                           # operator pressed Huỷ
            await db.set_pending_send(A, W, "third, corrected", ps.chat_marker(await db.get_followup_chat(A, W)))
    monkeypatch.setattr(ps.asyncio, "sleep", operator_acts_during_the_gap)

    assert await ps.flush() == {"sent": 1, "dropped": 0}
    assert [t for _, _, t in state["sent"]] == ["first"]                  # neither the cancelled nor the old text
    assert await db.get_pending_send(A, V) is None
    row = await db.get_pending_send(A, W)
    assert row["text"] == "third, corrected" and row["status"] == "waiting"   # the correction survives for the next run

    assert await ps.flush() == {"sent": 1, "dropped": 0}
    assert [t for _, _, t in state["sent"]] == ["first", "third, corrected"]


async def test_interrupted_send_is_never_repeated(monkeypatch):
    """Telegram accepted the message but the app died before removing the row."""
    state = await _setup(monkeypatch, spam="free")
    await db.set_pending_send(A, U, "x", ps.chat_marker(await db.get_followup_chat(A, U)))

    async def send_then_crash(account_id, chat_id, text, parse_mode="html"):
        state["sent"].append((account_id, chat_id, text))
        raise KeyboardInterrupt  # process stops right after Telegram took the message
    monkeypatch.setattr(ps.tg, "send_text_message", send_then_crash)
    try:
        await ps._flush_account(A, await db.list_waiting_pending_sends())
    except KeyboardInterrupt:
        pass
    row = await db.get_pending_send(A, U)
    assert row["status"] == "dropped" and "gián đoạn" in row["reason"]     # shown to the owner, not waiting
    assert await db.list_waiting_pending_sends() == []
    assert await ps.flush() == {"sent": 0, "dropped": 0} and len(state["sent"]) == 1


async def test_flood_wait_keeps_everything_waiting_without_counting(monkeypatch):
    state = await _setup(monkeypatch, spam="free")
    await db.get_or_create_followup_chat(account_id=A, user_id=778, username="u778", name="K")
    for uid in (U, 778):
        await db.set_pending_send(A, uid, "x", ps.chat_marker(await db.get_followup_chat(A, uid)))

    class FloodWaitError(Exception):
        seconds = 300
    state["fail"] = FloodWaitError()
    for _ in range(4):                                                    # more rounds than MAX_ATTEMPTS
        assert await ps.flush() == {"sent": 0, "dropped": 0}
    rows = await db.list_waiting_pending_sends()
    assert len(rows) == 2 and all(r["attempts"] == 0 for r in rows)       # nothing burnt, nothing dropped
    assert state["scheduled"]                                             # retried after the wait, not blindly

    state["fail"] = None
    assert (await ps.flush())["sent"] == 2
