"""v3.36 — the drip queue keeps moving, and promises to "get back" are tracked.

1. Drip queue. The old loop re-read the same 30 oldest chats every cycle; a chat
   that could not be sent never left that window (head-of-line blocking) and cost
   one AI call per cycle. Now: reachability is checked BEFORE the AI call, every
   chat that is looked at and not sent is deferred, sending is paced.
2. Owed answers. "Our team will review and get back to you" opens a debt on the
   chat; a human admin message (or the UI button) closes it; promises older than
   24h go into one digest for the admin.
"""
from unittest.mock import AsyncMock, patch

import pytest

import database as db
import dm_reply_tracker as tracker
import owed_answers
import telegram_client as tg

A, U = 1, 4242


# ── helpers ──────────────────────────────────────────────────────────────────

async def _account_and_agent(account_id: int = A):
    agent_id = await db.create_ai_agent({
        "name": f"Agent {account_id}", "provider": "gemini", "api_keys_json": ["agent-key"],
        "system_prompt": "Sys", "knowledge_base": "KB",
    })
    await db.create_account({
        "id": account_id, "name": f"BD{account_id}", "phone": f"+8400{account_id}", "api_id": "x",
        "api_hash": "y", "session_name": f"bd{account_id}", "ai_agent_id": agent_id,
    })
    await db.set_account_ai_agent(account_id, agent_id)


async def _silent_lead(user_id: int = U, account_id: int = A, hours: int = 60, tier: str | None = None):
    await db.get_or_create_followup_chat(account_id=account_id, user_id=user_id,
                                         username=f"kol{user_id}", name="KOL")
    await db.append_followup_chat_message(account_id, user_id, "user", "what is the commission?")
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE ai_followup_chats SET last_user_message_at = datetime('now', ?), "
            "updated_at = datetime('now', ?), lead_tier = COALESCE(?, lead_tier) "
            "WHERE account_id = ? AND user_id = ?",
            (f"-{hours} hours", f"-{hours} hours", tier, account_id, user_id))
        await conn.commit()


async def _row(user_id: int = U, account_id: int = A) -> dict:
    import aiosqlite
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM ai_followup_chats WHERE account_id = ? AND user_id = ?", (account_id, user_id))
        return dict(await cur.fetchone())


async def _set(user_id: int, sql_set: str, params: tuple = (), account_id: int = A):
    async with db.get_db() as conn:
        await conn.execute(
            f"UPDATE ai_followup_chats SET {sql_set} WHERE account_id = ? AND user_id = ?",
            (*params, account_id, user_id))
        await conn.commit()


# ── 1. drip queue ────────────────────────────────────────────────────────────

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_unreachable_user_costs_no_ai_call_and_is_deferred(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead()
    with patch.object(tg, "ensure_user_entity", AsyncMock(return_value=False), create=True):
        res = await tracker.process_drip_followups()
        assert res["sent"] == 0
        mock_gen.assert_not_called()
        mock_send.assert_not_called()
        row = await _row()
        assert row["drip_fail_count"] == 1 and row["drip_retry_after"]
        assert row["last_drip_stage"] in (0, None)
        # Deferred: the next cycle does not even look the user up again.
        tg.ensure_user_entity.reset_mock()
        await tracker.process_drip_followups()
        tg.ensure_user_entity.assert_not_called()


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_unreachable_user_backs_off_for_a_month_after_three_failures(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead()
    await _set(U, "drip_fail_count = 2")
    with patch.object(tg, "ensure_user_entity", AsyncMock(return_value=False), create=True):
        await tracker.process_drip_followups()
    row = await _row()
    assert row["drip_fail_count"] == 3
    assert tracker._silence_hours(row["drip_retry_after"]) == 0  # in the future
    import aiosqlite
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT datetime(drip_retry_after) > datetime('now', '+20 days') AS far "
            "FROM ai_followup_chats WHERE user_id = ?", (U,))
        assert (await cur.fetchone())["far"] == 1


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_queue_moves_past_a_blocked_chat(mock_send, mock_gen):
    """One unreachable lead must not stop the lead behind it (next cycle)."""
    await _account_and_agent()
    await _silent_lead(user_id=1001, hours=60)   # more recent → looked at first
    await _silent_lead(user_id=1002, hours=80)
    mock_gen.return_value = "Hey bro, still interested in the affiliate program?"

    async def reach(account_id, user_id, username=None):
        return user_id != 1001

    with patch.object(tg, "ensure_user_entity", AsyncMock(side_effect=reach), create=True):
        first = await tracker.process_drip_followups()
        second = await tracker.process_drip_followups()
    assert first["sent"] == 0 and second["sent"] == 1
    assert mock_gen.call_count == 1
    assert (await _row(1002))["last_drip_stage"] == 1
    assert (await _row(1002))["last_drip_at"]


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_one_drip_per_account_per_cycle(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead(user_id=1001)
    await _silent_lead(user_id=1002)
    mock_gen.return_value = "Hey bro, still interested in the affiliate program?"
    assert (await tracker.process_drip_followups())["sent"] == 1
    assert (await tracker.process_drip_followups())["sent"] == 1
    assert mock_send.call_count == 2


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_daily_cap_per_account(mock_send, mock_gen):
    await _account_and_agent()
    await db.set_setting("ai_followup_drip_daily_cap", "1")
    await _silent_lead(user_id=1001)
    await _silent_lead(user_id=1002)
    mock_gen.return_value = "Hey bro, still interested in the affiliate program?"
    assert (await tracker.process_drip_followups())["sent"] == 1
    assert (await tracker.process_drip_followups())["sent"] == 0
    assert mock_send.call_count == 1


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_long_silent_lead_does_not_get_every_stage_back_to_back(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead(hours=60, tier="Tier A")  # stages 1-3 (12h, 24h, 48h) are all "due"
    mock_gen.return_value = "Hey bro, still interested in the affiliate program?"
    assert (await tracker.process_drip_followups())["sent"] == 1
    assert (await tracker.process_drip_followups())["sent"] == 0
    row = await _row()
    assert row["last_drip_stage"] == 1 and row["drip_retry_after"]
    assert mock_send.call_count == 1


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_a_lead_that_is_not_due_is_deferred_not_rescanned(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead(hours=20, tier="Tier C")  # Tier C first stage = 48h
    assert (await tracker._process_drip_queue())[0] == 0
    mock_gen.assert_not_called()
    assert (await _row())["drip_retry_after"]


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_paused_account_sends_no_drip(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead()
    await db.pause_account(A, "PeerFlood")
    assert (await tracker.process_drip_followups())["sent"] == 0
    mock_gen.assert_not_called()
    mock_send.assert_not_called()


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_peerflood_on_a_drip_pauses_the_nick(mock_send, mock_gen):
    """PeerFlood while sending a drip must stop the nick, not retry with the next lead 5 minutes later."""
    from telethon import errors
    await _account_and_agent()
    await db.unpause_account(A)  # the pause cache is in memory and outlives the previous test
    await _silent_lead(user_id=1001)
    await _silent_lead(user_id=1002)
    mock_gen.return_value = "Hey bro, still interested in the affiliate program?"
    mock_send.side_effect = errors.PeerFloodError(request=None)
    with patch("alerts.send_alert", new_callable=AsyncMock),             patch("proxy_manager.rotate_proxy_for_account", new_callable=AsyncMock, return_value={}):
        assert (await tracker.process_drip_followups())["sent"] == 0
        assert await db.is_account_paused(A)
        # The paused nick sends nothing on the following passes.
        await tracker.process_drip_followups()
    assert mock_send.call_count == 1
    await db.unpause_account(A)


async def test_user_reply_restarts_the_drip_cadence():
    await _account_and_agent()
    await _silent_lead()
    await _set(U, "last_drip_stage = 3, drip_fail_count = 2, drip_retry_after = datetime('now', '+5 days')")
    await db.append_followup_chat_message(A, U, "user", "sorry, was busy. still there?")
    row = await _row()
    assert row["last_drip_stage"] == 0 and row["drip_fail_count"] == 0 and row["drip_retry_after"] is None


# ── 2. owed answers: detection ───────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Let me double-check the exact numbers with the team and get right back to you 🙏",
    "The team will review it internally.",
    "I’ll pass this along to the team for review.".replace("’", "'"),
    "Our manager is reviewing your deck now.",
    "Để mình kiểm tra qua số liệu rồi báo lại nhé 👍",
    "Bên mình sẽ báo lại cho bạn trong hôm nay.",
    "Передам ваше предложение команде для оценки.",
    "Команда посмотрит их и вернётся с ответом.",
])
def test_detect_promise_positive(text):
    assert owed_answers.detect_promise(text)


@pytest.mark.parametrize("text", [
    "Hey bro, still interested in the affiliate program?",
    "Let me know if you want the details 👍",
    "WEEX offers up to 80% revshare for top partners.",
    "Bạn gửi mình UID nhé.",
    "",
    None,
])
def test_detect_promise_negative(text):
    assert owed_answers.detect_promise(text) is None


def test_detect_promise_returns_the_sentence_without_app_tags():
    note = owed_answers.detect_promise(
        "Got it bro. Our team will review your current deal and get back to you. Talk soon! [HANDOVER_REQUIRED]")
    assert note == "Our team will review your current deal and get back to you."


def test_owed_from_history_is_closed_by_a_human_admin_message():
    promise = {"role": "assistant", "content": "The team will review it.", "timestamp": "2026-11-08T10:00:00"}
    user = {"role": "user", "content": "ok, any news?", "timestamp": "2026-11-09T10:00:00"}
    human = {"role": "assistant", "content": "[Human Admin]: approved, 70%", "timestamp": "2026-11-09T11:00:00"}
    assert owed_answers.owed_from_history([promise, user]) == {
        "since": "2026-11-08T10:00:00", "note": "The team will review it."}
    assert owed_answers.owed_from_history([promise, user, human]) is None
    assert owed_answers.owed_from_history([]) is None


# ── 2. owed answers: storage ─────────────────────────────────────────────────

async def test_ai_promise_opens_the_debt_and_admin_message_closes_it():
    await _account_and_agent()
    await _silent_lead()
    await db.append_followup_chat_message(A, U, "assistant", "Sure, 55% to start.")
    assert (await _row())["owed_since"] is None

    await db.append_followup_chat_message(A, U, "assistant", "The team will review your deal and get back to you.")
    row = await _row()
    assert row["owed_since"] and "team will review" in row["owed_note"]
    since = row["owed_since"]

    # The KOL asking again does not close it, and a second promise keeps the first date.
    await db.append_followup_chat_message(A, U, "user", "any update?")
    await db.append_followup_chat_message(A, U, "assistant", "I'll check with the team again.")
    assert (await _row())["owed_since"] == since

    chats = await db.get_all_followup_chats(limit=50, include_history=False)
    listed = next(c for c in chats if c["user_id"] == U)
    assert listed["owed_since"] == since and listed["owed_note"]

    await db.append_followup_chat_message(A, U, "assistant", "[Human Admin]: approved — 70% from month one")
    row = await _row()
    assert row["owed_since"] is None and row["owed_note"] is None


def test_owed_route_marks_and_resolves(client):
    import asyncio

    async def seed():
        await _account_and_agent()
        await _silent_lead()
    asyncio.run(seed())

    assert client.post(f"/api/ai-followup/chats/{A}/{U}/owed?owed=true").status_code == 200
    assert asyncio.run(_row())["owed_since"]
    r = client.post(f"/api/ai-followup/chats/{A}/{U}/owed?owed=false")
    assert r.status_code == 200 and r.json()["owed"] is False
    assert asyncio.run(_row())["owed_since"] is None
    assert client.post(f"/api/ai-followup/chats/{A}/999999/owed?owed=false").status_code == 404


# ── 2. owed answers: reminder digest + backfill ──────────────────────────────

async def test_reminder_digest_lists_only_promises_past_24h():
    await _account_and_agent()
    await db.set_setting("owed_answers_backfilled", "1")
    await _silent_lead(user_id=2001)
    await _silent_lead(user_id=2002)
    await db.set_owed_answer(A, 2001, "Team will review your deal", since=None)
    await db.set_owed_answer(A, 2002, "Team will check the numbers", since=None)
    await _set(2001, "owed_since = datetime('now', '-30 hours'), lead_tier = 'Tier A'")
    await _set(2002, "owed_since = datetime('now', '-3 hours')")

    alert = AsyncMock(return_value=True)
    with patch("command_bot.send_forum_general_alert", alert):
        res = await tracker.process_owed_answer_reminders()
        assert res == {"reminded": 1}
        text = alert.call_args[0][0]
        assert "@kol2001" in text and "kol2002" not in text
        assert "Team will review your deal" in text and "[A]" in text
        # Cooldown: no second digest right away.
        assert (await tracker.process_owed_answer_reminders()).get("skipped") == "cooldown"
        # Forced (manual button): this promise was reminded <24h ago → nothing to send.
        assert (await tracker.process_owed_answer_reminders(force=True))["reminded"] == 0
    assert alert.call_count == 1
    row = await _row(2001)
    assert row["owed_reminders_sent"] == 1 and row["owed_last_reminder_at"]


async def test_reminder_stops_after_three_and_is_not_counted_when_undelivered():
    await _account_and_agent()
    await db.set_setting("owed_answers_backfilled", "1")
    await _silent_lead()
    await db.set_owed_answer(A, U, "Team will review")
    await _set(U, "owed_since = datetime('now', '-5 days')")

    with patch("command_bot.send_forum_general_alert", AsyncMock(return_value=False)), \
            patch.object(tg, "get_client", lambda *_a, **_k: None):
        res = await tracker.process_owed_answer_reminders(force=True)
    assert res == {"reminded": 0, "undelivered": 1}
    assert (await _row())["owed_reminders_sent"] == 0

    await _set(U, "owed_reminders_sent = 3")
    alert = AsyncMock(return_value=True)
    with patch("command_bot.send_forum_general_alert", alert):
        assert (await tracker.process_owed_answer_reminders(force=True))["reminded"] == 0
    alert.assert_not_called()


async def test_backfill_finds_open_promises_once():
    from datetime import datetime, timedelta
    await _account_and_agent()
    await _silent_lead(user_id=3001)
    await _silent_lead(user_id=3002)
    await _silent_lead(user_id=3003)
    two_days = (datetime.now() - timedelta(days=2)).isoformat()
    old = (datetime.now() - timedelta(days=90)).isoformat()
    import json

    async def put(uid, msgs):
        await _set(uid, "history_json = ?, owed_since = NULL, owed_note = NULL", (json.dumps(msgs),))

    await put(3001, [{"role": "assistant", "content": "The team will review it.", "timestamp": two_days}])
    await put(3002, [{"role": "assistant", "content": "The team will review it.", "timestamp": two_days},
                     {"role": "assistant", "content": "[Human Admin]: done", "timestamp": two_days}])
    await put(3003, [{"role": "assistant", "content": "The team will review it.", "timestamp": old}])

    assert await tracker.backfill_owed_answers() == 1
    row = await _row(3001)
    assert row["owed_note"] == "The team will review it."
    assert 40 < tracker._silence_hours(row["owed_since"]) < 56  # local → UTC kept the real age
    assert (await _row(3002))["owed_since"] is None
    assert (await _row(3003))["owed_since"] is None  # older than the backfill window
    assert await tracker.backfill_owed_answers() == 0  # one-off
