"""Spec docs/spec-kol-starter-package.md §C.4 + §C.5 — UID two-source rule and
step-based follow-ups.

4. UID: regex (KOL's own message) + LLM extraction agree → uid_received; only
   one source → no transition, `weex_uid` stays in missing_items.
5. Follow-up: one nudge about exactly the missing item, max 2 per step, >= 48h
   apart, never outside active hours, stops on declined / [STOP], and only for
   leads that already agreed (prospects are left to the tier drip).
"""
from unittest.mock import AsyncMock, patch

import pytest

import database as db
import dm_reply_tracker as tracker

A, U = 1, 303


async def _agent_and_account():
    agent_id = await db.create_ai_agent({
        "name": "Step Agent",
        "provider": "gemini",
        "api_keys_json": ["agent-key"],
        "system_prompt": "Sys",
        "knowledge_base": "KB",
    })
    await db.create_account({
        "id": A, "name": "BD", "phone": "+8400", "api_id": "x", "api_hash": "y", "session_name": "bd",
        "ai_agent_id": agent_id,
    })
    await db.set_account_ai_agent(A, agent_id)
    return agent_id


async def _lead(user_id: int = U, step: str = "deal_agreed", username: str = "kol"):
    await db.get_or_create_followup_chat(account_id=A, user_id=user_id, username=username, name="KOL")
    await db.append_followup_chat_message(A, user_id, "user", "ok deal, let's go")
    if step != "prospect":
        await db.set_kol_step(A, user_id, step, actor="admin:test", reason="fixture")
    return user_id


async def _backdate(user_id: int, hours: int = 72):
    """Pretend the lead went quiet `hours` ago (all timestamps the gap uses)."""
    async with db.get_db() as conn:
        await conn.execute("""
            UPDATE ai_followup_chats
            SET kol_step_updated_at = datetime('now', ?), updated_at = datetime('now', ?),
                last_user_message_at = datetime('now', ?),
                last_step_reminder_at = CASE WHEN last_step_reminder_at IS NULL THEN NULL ELSE datetime('now', ?) END
            WHERE account_id = ? AND user_id = ?
        """, (f"-{hours} hours",) * 4 + (A, user_id))
        await conn.commit()


# ── C.4 UID two-source rule ──────────────────────────────────────────────────

def test_regex_source_finds_uid_only_in_kol_messages():
    history = [
        {"role": "assistant", "content": "Could you send me your UID 10101010?"},  # ours → ignored
        {"role": "user", "content": "sure, my UID is 12345678 bro"},
        {"role": "user", "content": "87654321"},                                     # bare number
        {"role": "user", "content": "I have 5000 followers and did 2M last month"},  # not a UID
    ]
    assert tracker.extract_uid_candidates_from_messages(history) == {"12345678", "87654321"}
    assert tracker.extract_uid_candidates_from_messages([]) == set()
    assert tracker._normalize_uid(" 12-34-5678 ") == "12345678"


async def test_uid_from_both_sources_moves_lead_to_uid_received():
    await _agent_and_account()
    await _lead(step="deal_agreed")
    history = [{"role": "user", "content": "here you go, UID: 12345678"}]
    with patch("ai_remix.extract_kol_profile", new_callable=AsyncMock,
               return_value={"weex_uid": "12345678", "platform": "telegram"}):
        await tracker._async_update_kol_profile(A, U, history, "gemini", ["k"], {})
    assert await db.get_kol_step(A, U) == "uid_received"
    profile = await db.get_kol_profile(A, U)
    assert profile["weex_uid"] == "12345678" and "weex_uid_candidate" not in profile
    assert "weex_uid" not in profile["missing_items"]


async def test_uid_from_llm_only_is_kept_as_candidate():
    await _agent_and_account()
    await _lead(step="deal_agreed")
    history = [{"role": "user", "content": "I'll send the id later"}]
    with patch("ai_remix.extract_kol_profile", new_callable=AsyncMock, return_value={"weex_uid": "12345678"}):
        await tracker._async_update_kol_profile(A, U, history, "gemini", ["k"], {})
    assert await db.get_kol_step(A, U) == "deal_agreed"
    profile = await db.get_kol_profile(A, U)
    assert profile.get("weex_uid") is None
    assert profile["weex_uid_candidate"] == "12345678"
    assert profile["missing_items"] == ["weex_uid"]


async def test_uid_from_regex_only_does_not_advance():
    await _agent_and_account()
    await _lead(step="deal_agreed")
    history = [{"role": "user", "content": "my uid 12345678"}]
    with patch("ai_remix.extract_kol_profile", new_callable=AsyncMock, return_value={"platform": "telegram"}):
        await tracker._async_update_kol_profile(A, U, history, "gemini", ["k"], {})
    assert await db.get_kol_step(A, U) == "deal_agreed"
    assert (await db.get_kol_profile(A, U))["missing_items"] == ["weex_uid"]


async def test_confirmed_uid_at_prospect_walks_two_single_steps():
    await _agent_and_account()
    await _lead(step="prospect")
    assert await tracker.maybe_advance_uid_received(A, U, "12345678") is True
    assert await db.get_kol_step(A, U) == "uid_received"
    events = await db.get_kol_step_events(A, U)
    assert [(e["from_step"], e["to_step"]) for e in events] == [
        ("deal_agreed", "uid_received"), ("prospect", "deal_agreed"),
    ]
    # Already past uid_received → no-op; declined → no-op.
    assert await tracker.maybe_advance_uid_received(A, U, "12345678") is False
    await db.set_kol_step(A, U, "declined", actor="ai")
    assert await tracker.maybe_advance_uid_received(A, U, "12345678") is False


# ── AI tags ──────────────────────────────────────────────────────────────────

async def test_ai_tags_advance_one_step_and_are_stripped():
    await _agent_and_account()
    await _lead(step="prospect")
    clean, res = await tracker.apply_kol_step_tags(A, U, "Awesome, welcome aboard!\n[DEAL_AGREED]")
    assert clean == "Awesome, welcome aboard!"
    assert res["advanced"] == "deal_agreed" and res["declined"] is False
    assert await db.get_kol_step(A, U) == "deal_agreed"

    # [FIRST_POST] is ignored unless the link is confirmed (no skipping).
    clean, res = await tracker.apply_kol_step_tags(A, U, "Nice post! [FIRST_POST]")
    assert clean == "Nice post!" and res["advanced"] is None
    assert await db.get_kol_step(A, U) == "deal_agreed"

    await db.set_kol_step(A, U, "link_confirmed", actor="admin:test")
    clean, res = await tracker.apply_kol_step_tags(A, U, "Nice post! [first_post]")
    assert res["advanced"] == "first_post"
    assert await db.get_kol_step(A, U) == "first_post"

    clean, res = await tracker.apply_kol_step_tags(A, U, "No tags here")
    assert clean == "No tags here" and res == {"declined": False, "advanced": None, "tags": []}


async def test_stop_tag_declines_lead():
    await _agent_and_account()
    await _lead(step="uid_received")
    clean, res = await tracker.apply_kol_step_tags(A, U, "No worries bro, all the best 🙏 [STOP]")
    assert "[STOP]" not in clean and res["declined"] is True and res["advanced"] == "declined"
    assert await db.get_kol_step(A, U) == "declined"
    assert tracker._step_holds_ai("declined") is True
    assert tracker._step_holds_ai("deal_agreed") is True
    assert tracker._step_holds_ai("prospect") is False
    assert tracker._step_holds_ai(None) is False


async def test_step_prompt_lists_have_and_missing():
    await _agent_and_account()
    await _lead(step="link_confirmed")
    await db.upsert_kol_profile(A, U, {"weex_uid": "12345678", "vip_code": "WILL", "affiliate_link": "https://x",
                                       "volume_claimed": "12M"})
    text = await tracker.render_kol_step_prompt(A, U)
    assert "CURRENT STEP: 3/5" in text
    assert "weex_uid=12345678" in text
    assert "selected_campaign" in text and "first_post_link" in text
    assert "PREDICTED TIER" in text and "tier 2" in text
    assert "[STOP]" in text
    assert tracker.render_kol_step_bar("uid_received").startswith("●●○○○ 2/5")
    assert "⛔" in tracker.render_kol_step_bar("declined")
    line = await tracker.render_kol_step_line(A, U)
    assert line.startswith("🪜 Bước: ●●●○○ 3/5") and "Còn thiếu" in line


# ── C.5 step reminders ───────────────────────────────────────────────────────

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock, return_value="")
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_reminder_asks_only_for_the_missing_item(mock_send, _gen):
    await _agent_and_account()
    await _lead(step="deal_agreed")
    await _backdate(U)
    with patch("database.check_account_active_hours", return_value=True):
        res = await tracker.process_step_reminders()
    assert res["sent"] == 1 and res["errors"] == []
    mock_send.assert_called_once()
    text = mock_send.call_args[0][2]
    assert "UID" in text and "%" not in text
    chat = await db.get_followup_chat(A, U)
    assert chat["step_reminders_sent"] == 1 and chat["last_step_reminder_at"]
    assert chat["status"] == "active"


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock, return_value="")
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_reminder_respects_gap_and_cap(mock_send, _gen):
    await _agent_and_account()
    await _lead(step="deal_agreed")
    await _backdate(U)
    with patch("database.check_account_active_hours", return_value=True):
        assert (await tracker.process_step_reminders())["sent"] == 1
        # Just sent → inside the 48h gap → nothing.
        assert (await tracker.process_step_reminders())["sent"] == 0
        # 47h later still inside the gap; 49h later → second (and last) nudge.
        await _backdate(U, hours=47)
        assert (await tracker.process_step_reminders())["sent"] == 0
        await _backdate(U, hours=49)
        assert (await tracker.process_step_reminders())["sent"] == 1
        # Cap = 2 per step: even after a long silence, no third message.
        await _backdate(U, hours=500)
        assert (await tracker.process_step_reminders())["sent"] == 0
    assert mock_send.call_count == 2
    assert (await db.get_followup_chat(A, U))["step_reminders_sent"] == 2

    # A new step resets the counter → reminders resume for the next item.
    await db.upsert_kol_profile(A, U, {"weex_uid": "12345678", "vip_code": "W", "affiliate_link": "https://x"})
    await db.set_kol_step(A, U, "uid_received", actor="system:uid")
    await db.set_kol_step(A, U, "link_confirmed", actor="admin:test")
    await _backdate(U, hours=72)
    with patch("database.check_account_active_hours", return_value=True):
        assert (await tracker.process_step_reminders())["sent"] == 1
    assert "campaign" in mock_send.call_args[0][2].lower()


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock, return_value="")
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_reminder_not_sent_outside_active_hours(mock_send, _gen):
    await _agent_and_account()
    await _lead(step="deal_agreed")
    await _backdate(U)
    with patch("database.check_account_active_hours", return_value=False):
        res = await tracker.process_step_reminders()
    assert res["sent"] == 0 and res["skipped"] == 1
    mock_send.assert_not_called()
    assert (await db.get_followup_chat(A, U))["step_reminders_sent"] == 0


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock, return_value="")
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_reminder_stops_on_declined_needs_human_and_prospect(mock_send, _gen):
    await _agent_and_account()
    declined = await _lead(U, step="deal_agreed")
    await tracker.apply_kol_step_tags(A, declined, "ok bye [STOP]")
    human = await _lead(U + 1, step="deal_agreed")
    await db.update_followup_chat_status(A, human, "needs_human")
    prospect = await _lead(U + 2, step="prospect")          # tier drip owns prospects
    waiting_on_ops = await _lead(U + 3, step="uid_received")  # vip_code/link are ours to do
    for uid in (declined, human, prospect, waiting_on_ops):
        await _backdate(uid)
    with patch("database.check_account_active_hours", return_value=True):
        res = await tracker.process_step_reminders()
    assert res["sent"] == 0
    mock_send.assert_not_called()


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_reminder_uses_ai_text_only_when_it_is_safe(mock_send, mock_gen):
    await _agent_and_account()
    await _lead(step="deal_agreed")
    await _backdate(U)
    mock_gen.return_value = "Hey bro, drop me your WEEX UID when you can and I'll bump you to 75% 😉"
    with patch("database.check_account_active_hours", return_value=True):
        assert (await tracker.process_step_reminders())["sent"] == 1
    # Off-ladder figure → template instead of the AI text.
    assert mock_send.call_args[0][2] == tracker._step_reminder_template("weex_uid", "en")

    await _backdate(U, hours=72)
    mock_gen.return_value = "Bro ơi, rảnh thì gửi mình UID WEEX nha 👍"
    with patch("database.check_account_active_hours", return_value=True):
        assert (await tracker.process_step_reminders())["sent"] == 1
    assert mock_send.call_args[0][2] == mock_gen.return_value


async def test_reminder_language_follows_the_kol():
    assert tracker._detect_reply_lang([{"role": "user", "content": "ok anh ơi, em gửi sau nhé"}]) == "vi"
    assert tracker._detect_reply_lang([{"role": "user", "content": "sure bro"}]) == "en"
    assert tracker._detect_reply_lang([]) == "en"
    assert "UID" in tracker._step_reminder_template("weex_uid", "vi")
    assert tracker._step_reminder_template("unknown_item", "en") == tracker._step_reminder_template("weex_uid", "en")


async def test_trigger_step_reminders_route(client):
    await _agent_and_account()
    with patch("database.check_account_active_hours", return_value=True):
        r = client.post("/api/ai-followup/trigger-step-reminders")
    assert r.status_code == 200
    assert r.json()["result"] == {"sent": 0, "skipped": 0, "errors": []}


# ── 12h auto-resume is disabled once the KOL agreed (spec B3) ────────────────

async def test_followup_candidates_query_excludes_held_and_prospect_rows():
    await _agent_and_account()
    agreed = await _lead(U, step="deal_agreed")
    prospect = await _lead(U + 1, step="prospect")
    for uid in (agreed, prospect):
        await _backdate(uid)
    rows = await db.get_step_reminder_candidates(min_gap_hours=48, max_per_step=2)
    assert [r["user_id"] for r in rows] == [agreed]
    rows = await db.get_step_reminder_candidates(min_gap_hours=100, max_per_step=2)
    assert rows == []
    rows = await db.get_step_reminder_candidates(min_gap_hours=48, max_per_step=0)
    assert rows == []
