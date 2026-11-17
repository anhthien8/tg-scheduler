import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database as db
import dm_reply_tracker as tracker
import telegram_client as tg

pytestmark = pytest.mark.asyncio

async def test_is_bot_account():
    assert tracker.is_bot_account("help_bot", "Helper", "Hi") is True
    assert tracker.is_bot_account("robotics_trader", "Rob", "Hi") is False
    assert tracker.is_bot_account("helper", "Airdrop Bot Pro", "Hi") is False
    assert tracker.is_bot_account("SpamBotHelper", "x", "Hi") is False
    long_msg = "Earn money now! Join the official bot at t.me/free_casino_bot to start. Casino free spins and giveaway!" + "a"*150
    assert tracker.is_bot_account("user", "Name", long_msg) is False
    assert tracker.is_bot_account("user_name", "User Name", "Hi, how are you?") is False
    # Real KOL intro with a channel link must not be treated as a bot.
    kol = ("I'm Dave, founder of Luffy trading community, a trading KOL. "
           "https://t.me/tradeversehqs "
           "I work with crypto exchanges to increase visibility in my channel. Are you open to partnerships?")
    assert tracker.is_bot_account("LuffytradeverseHQ", "LUFFY TRADES", kol) is False
    assert tracker.is_bot_account("plainname", "Name", "Hi", is_bot=True) is True

async def test_sanitize_telegram_html():
    text = "Hello \\u0062\\u0072\\u006f!<br>Welcome **bold** and *italic*.<script>alert('xss')</script><b>open tag"
    sanitized = tracker.sanitize_telegram_html(text)
    assert "bro!" in sanitized
    assert "\n" in sanitized
    assert "<b>bold</b>" in sanitized
    assert "<i>italic</i>" in sanitized
    assert "<script>" not in sanitized
    assert sanitized.endswith("</b>")


# ── 1b. AI disclosure guard ───────────────────────────────────────────────────

async def test_contains_ai_disclosure_flags_confessions():
    leaked = (
        "Straight answer bro: I'm an AI assistant, not a WEEX employee. "
        "I was speaking in the voice of the role I was set up to play."
    )
    assert tracker.contains_ai_disclosure(leaked) is True
    assert tracker.contains_ai_disclosure("Tôi là một chatbot trợ lý ảo nhé") is True
    assert tracker.contains_ai_disclosure("Mình không phải người thật đâu") is True
    assert tracker.contains_ai_disclosure("As an AI language model, I can't do that") is True
    assert tracker.contains_ai_disclosure("I can't actually open or verify Telegram links — I don't have browsing") is True


async def test_contains_ai_disclosure_allows_normal_replies():
    assert tracker.contains_ai_disclosure("Got your UID bro! Will bind commission now 👍") is False
    assert tracker.contains_ai_disclosure("Bên mình hỗ trợ rút USDT hàng ngày, không KYC nhé") is False
    assert tracker.contains_ai_disclosure("Let me loop in our partnership manager for this") is False
    assert tracker.contains_ai_disclosure("") is False
    assert tracker.contains_ai_disclosure(None) is False


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_ai_disclosure_blocked_and_nothing_sent(mock_send, mock_chat):
    agent_id = await db.create_ai_agent({
        "name": "Guard Agent",
        "provider": "gemini",
        "api_keys_json": ["agent-key"],
        "system_prompt": "Sys",
        "knowledge_base": "KB",
    })
    await db.create_account({
        "id": 1,
        "name": "BD Acc",
        "phone": "+841****3456",
        "api_id": "api",
        "api_hash": "hash",
        "session_name": "bd_acc",
        "ai_agent_id": agent_id,
    })
    await db.set_account_ai_agent(1, agent_id)
    await db.get_or_create_followup_chat(account_id=1, user_id=202, username="lead", name="Lead")
    await db.append_followup_chat_message(1, 202, "user", "Are you a bot?")

    mock_chat.return_value = "Yes, I'm an AI assistant. I'm not a real WEEX employee."

    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock) as mock_notify:
        res = await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")
        mock_notify.assert_called_once()

    # No canned stand-in line either: the owner answers this one.
    assert res is False
    mock_send.assert_not_called()

    chat = await db.get_followup_chat(1, 202)
    assert chat["status"] == "needs_human"
    assert [m["role"] for m in chat["history"]] == ["user"]


async def _setup_agent_chat(user_msg: str):
    agent_id = await db.create_ai_agent({"name": "Agent", "provider": "gemini", "api_keys_json": ["key"]})
    await db.create_account({
        "id": 1, "name": "BD Acc", "phone": "+84123456", "api_id": "api",
        "api_hash": "hash", "session_name": "bd_acc", "ai_agent_id": agent_id,
    })
    await db.set_account_ai_agent(1, agent_id)
    await db.get_or_create_followup_chat(account_id=1, user_id=202, username="lead")
    await db.append_followup_chat_message(1, 202, "user", user_msg)


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_decline_tags_only_sends_polite_closing(mock_send, mock_chat):
    await _setup_agent_chat("Добрый день, спасибо, но у нас нет такого формата сотрудничества.")
    mock_chat.return_value = '[STOP]\n[METRICS: {"intent_score": 0, "lead_tier": "Tier C", "summary": "Declined"}]'

    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock):
        res = await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")

    assert res is True
    sent = mock_send.call_args[0][2]
    assert sent == tracker.get_decline_closing_reply([{"role": "user", "content": "нет"}])
    assert "Спасибо" in sent
    chat = await db.get_followup_chat(1, 202)
    assert chat["status"] == "needs_human"
    assert await db.get_kol_step(1, 202) == "declined"


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_tags_only_reply_without_decline_is_logged_not_sent(mock_send, mock_chat, caplog):
    await _setup_agent_chat("Help me")
    mock_chat.return_value = "[HANDOVER_REQUIRED]"

    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock):
        res = await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")

    assert res is False
    mock_send.assert_not_called()
    assert "empty after stripping tags" in caplog.text


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_prompt_includes_broker_program(mock_send, mock_chat):
    await _setup_agent_chat("We are a fintech platform looking for an exchange partner")
    mock_chat.return_value = "Sounds great"
    await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")
    prompt = mock_chat.call_args_list[0][0][1]
    assert "COMPANY/PLATFORM" in prompt and "Broker / B2B partnership model" in prompt
    assert "neobank" in prompt


async def test_decline_closing_reply_language():
    vi = tracker.get_decline_closing_reply([{"role": "user", "content": "Cảm ơn, bên mình không hợp tác"}])
    en = tracker.get_decline_closing_reply([{"role": "user", "content": "Not interested, thanks"}])
    assert vi.startswith("Cảm ơn") and en.startswith("Thanks")
    for text in (vi, en):
        assert "%" not in text and "$" not in text  # never quotes figures
        assert not tracker.contains_ai_disclosure(text)


# ── 2. AI Reply pipeline ──────────────────────────────────────────────────────

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_generate_and_send_ai_reply_success(mock_send, mock_chat):
    agent_id = await db.create_ai_agent({
        "name": "Test Agent",
        "provider": "gemini",
        "api_keys_json": ["agent-key"],
        "system_prompt": "Sys",
        "knowledge_base": "KB"
    })
    await db.create_account({
        "id": 1,
        "name": "BD Acc",
        "phone": "+84123456",
        "api_id": "api",
        "api_hash": "hash",
        "session_name": "bd_acc",
        "ai_agent_id": agent_id
    })
    await db.set_account_ai_agent(1, agent_id)
    
    await db.get_or_create_followup_chat(
        account_id=1, user_id=202, username="lead", name="Lead User"
    )
    await db.append_followup_chat_message(1, 202, "user", "I want to register")
    
    mock_chat.return_value = "Sure bro! Use this link.\n[METRICS: {\"intent_score\": 90, \"lead_tier\": \"Tier A\", \"summary\": \"Ready to join\"}]"
    
    res = await tracker.generate_and_send_ai_reply_for_chat(
        account_id=1,
        user_id=202,
        sender_username="lead",
        sender_name="Lead User"
    )
    assert res is True
    mock_send.assert_called_once_with(1, 202, "Sure bro! Use this link.")
    
    chat = await db.get_followup_chat(1, 202)
    assert chat["intent_score"] == 90
    assert chat["lead_tier"] == "Tier A"
    assert chat["summary"] == "Ready to join"

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
async def test_generate_and_send_ai_reply_handover(mock_chat):
    agent_id = await db.create_ai_agent({
        "name": "Agent",
        "provider": "gemini",
        "api_keys_json": ["key"]
    })
    await db.create_account({
        "id": 1,
        "name": "BD Acc",
        "phone": "+84123456",
        "api_id": "api",
        "api_hash": "hash",
        "session_name": "bd_acc",
        "ai_agent_id": agent_id
    })
    await db.set_account_ai_agent(1, agent_id)
    await db.get_or_create_followup_chat(
        account_id=1, user_id=202, username="lead"
    )
    await db.append_followup_chat_message(1, 202, "user", "Help me")
    
    mock_chat.return_value = "Please wait. [HANDOVER_REQUIRED]"
    
    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock) as mock_notify:
        await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")
        mock_notify.assert_called_once()
        
    chat = await db.get_followup_chat(1, 202)
    assert chat["status"] == "needs_human"


# ── 3. Incoming & Outgoing Telethon Events ───────────────────────────────────

@patch("dm_reply_tracker.generate_and_send_ai_reply_for_chat", new_callable=AsyncMock)
async def test_incoming_user_message_triggers_reply(mock_ai_reply):
    agent_id = await db.create_ai_agent({"name": "A", "provider": "gemini", "api_keys_json": ["k"]})
    await db.create_account({
        "id": 1, "name": "Acc", "phone": "+8411", "api_id": "a", "api_hash": "h", "session_name": "s", "ai_agent_id": agent_id
    })
    await db.set_account_ai_agent(1, agent_id)
    await db.set_setting("ai_followup_enabled", "true")
    
    mock_event = MagicMock()
    mock_event.is_private = True
    mock_event.out = False
    mock_event.message.id = 1234
    mock_event.message.video = None
    mock_event.message.video_note = None
    mock_event.message.gif = None
    mock_event.message.sticker = None
    mock_event.message.dice = None
    mock_event.message.photo = None
    mock_event.message.document = None
    mock_event.sender_id = 999
    
    class MockSender:
        username = "lead_sender"
        first_name = "Lead"
        last_name = "User"
    
    mock_event.get_sender = AsyncMock(return_value=MockSender())
    mock_event.raw_text = "Hello!"
    
    handler_fn = tracker._make_handler(account_id=1)
    await handler_fn(mock_event)
    
    chat = await db.get_followup_chat(1, 999)
    assert chat is not None
    assert len(chat["history"]) == 1
    assert chat["history"][0]["content"] == "Hello!"
    mock_ai_reply.assert_called_once()


@pytest.mark.parametrize("text,offline,via_bot,expected", [
    ("Добрый день, это автоответчик. Вот мои проекты @savin_all", None, None, True),
    ("This is an automatic reply, I'm away until Monday", None, None, True),
    ("Auto-reply: back soon", None, None, True),
    ("Xin chào", True, None, True),                     # Telegram Business away / greeting message
    ("Hello", None, 777000111, True),                   # sent by the account's connected business bot
    ("Hello! What commission do you offer?", None, None, False),
    ("I reply to everyone automatically? no, I'm here", None, None, False),
    ("check my bot @savin_hub_bot", False, None, False),
])
def test_is_auto_reply(text, offline, via_bot, expected):
    msg = MagicMock()                                   # unset attributes are Mocks, never a real flag
    if offline is not None:
        msg.offline = offline
    if via_bot is not None:
        msg.via_business_bot_id = via_bot
    assert tracker.is_auto_reply(msg, text) is expected


@patch("dm_reply_tracker.generate_and_send_ai_reply_for_chat", new_callable=AsyncMock)
async def test_incoming_auto_reply_is_recorded_but_not_answered(mock_ai_reply):
    agent_id = await db.create_ai_agent({"name": "A", "provider": "gemini", "api_keys_json": ["k"]})
    await db.create_account({"id": 1, "name": "Acc", "phone": "+8411", "api_id": "a", "api_hash": "h",
                             "session_name": "s", "ai_agent_id": agent_id})
    await db.set_account_ai_agent(1, agent_id)
    await db.set_setting("ai_followup_enabled", "true")

    ev = MagicMock()
    ev.is_private, ev.out, ev.sender_id = True, False, 998
    ev.message.id = 4321
    for attr in ("video", "video_note", "gif", "sticker", "dice", "photo", "document"):
        setattr(ev.message, attr, None)

    class Sender:
        username, first_name, last_name = "savin", "Danya", "Savin"

    ev.get_sender = AsyncMock(return_value=Sender())
    ev.raw_text = "Добрый день, это автоответчик. Вот мои проекты @savin_all"
    await tracker._make_handler(account_id=1)(ev)

    chat = await db.get_followup_chat(1, 998)
    assert [m["content"] for m in chat["history"]] == [ev.raw_text]   # kept for the human reader
    assert chat["status"] == "active" and not chat.get("owed_since")   # still a real lead, nothing promised
    mock_ai_reply.assert_not_called()

async def test_outgoing_admin_message_interception():
    agent_id = await db.create_ai_agent({"name": "A", "provider": "gemini", "api_keys_json": ["k"]})
    await db.create_account({
        "id": 1, "name": "Acc", "phone": "+8411", "api_id": "a", "api_hash": "h", "session_name": "s", "ai_agent_id": agent_id
    })
    await db.set_account_ai_agent(1, agent_id)
    await db.get_or_create_followup_chat(
        account_id=1, user_id=999, username="lead"
    )
    
    mock_event = MagicMock()
    mock_event.is_private = True
    mock_event.out = True
    mock_event.chat_id = 999
    mock_event.message.message = "I am an admin answering manually"
    mock_event.raw_text = "I am an admin answering manually"
    
    handler_fn = tracker._make_handler(account_id=1)
    with patch("dm_reply_tracker._async_distill_human_rule", new_callable=AsyncMock) as mock_distill:
        await handler_fn(mock_event)
        mock_distill.assert_called_once_with(1, 999, "I am an admin answering manually")
        
    chat = await db.get_followup_chat(1, 999)
    assert chat["status"] == "needs_human"
    assert chat["human_takeover_at"] is not None


# ── 4. Periodic Workers & Drip Workflows ──────────────────────────────────────

@patch("dm_reply_tracker.generate_and_send_ai_reply_for_chat", new_callable=AsyncMock)
async def test_process_drip_followups_workflows(mock_reply):
    agent_id = await db.create_ai_agent({"name": "Agent", "provider": "gemini", "api_keys_json": ["key"]})
    await db.create_account({
        "id": 1, "name": "Acc", "phone": "+8411", "api_id": "a", "api_hash": "h", "session_name": "s", "ai_agent_id": agent_id
    })
    await db.set_account_ai_agent(1, agent_id)
    
    # 24h human silent takeover resume
    past_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    await db.get_or_create_followup_chat(
        account_id=1, user_id=101, username="lead1", name="Lead 1"
    )
    await db.append_followup_chat_message(1, 101, "user", "Are you there?")
    async with db.get_db() as database:
        await database.execute(
            "UPDATE ai_followup_chats SET status = 'needs_human', human_takeover_at = ?, updated_at = ? WHERE user_id = 101",
            (past_time, past_time)
        )
        await database.commit()
        
    # Stuck chat recovery (>2m)
    stuck_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    await db.get_or_create_followup_chat(
        account_id=1, user_id=102, username="lead2", name="Lead 2"
    )
    await db.append_followup_chat_message(1, 102, "user", "I need pricing")
    async with db.get_db() as database:
        await database.execute(
            "UPDATE ai_followup_chats SET status = 'active', updated_at = ? WHERE user_id = 102",
            (stuck_time,)
        )
        await database.commit()
        
    res = await tracker.process_drip_followups()
    assert res["sent"] >= 2
    assert mock_reply.call_count >= 2


# ── 5. AI Self-Learning Rule Distillation ──────────────────────────────────────

@patch("ai_remix.distill_human_takeover_rule", new_callable=AsyncMock)
async def test_async_distill_human_rule_success(mock_distill):
    agent_id = await db.create_ai_agent({
        "name": "Test Agent",
        "provider": "gemini",
        "api_keys_json": '["agent-key"]'
    })
    await db.create_account({
        "id": 1,
        "name": "BD Acc",
        "phone": "+84123456",
        "api_id": "api",
        "api_hash": "hash",
        "session_name": "bd_acc",
        "ai_agent_id": agent_id
    })
    await db.set_account_ai_agent(1, agent_id)
    
    await db.get_or_create_followup_chat(
        account_id=1, user_id=202, username="lead", name="Lead User"
    )
    await db.append_followup_chat_message(1, 202, "user", "how do I get my referral link?")
    
    mock_distill.return_value = {
        "question": "how do I get my referral link?",
        "answer": "Send me your UID and the team will create your referral link today"
    }
    
    await tracker._async_distill_human_rule(
        account_id=1,
        user_id=202,
        message_text="Send me your UID and the team will create your referral link today"
    )
    
    mock_distill.assert_called_once()
    args, kwargs = mock_distill.call_args
    assert kwargs["human_reply"] == "Send me your UID and the team will create your referral link today"
    assert len(kwargs["history"]) == 1
    
    rules = await db.get_learned_knowledge_for_agent(agent_id)
    assert len(rules) == 1
    assert rules[0]["question_pattern"] == "how do I get my referral link?"
    assert rules[0]["learned_answer"] == "Send me your UID and the team will create your referral link today"



async def test_source_leak_detected():
    leaks = [
        "According to the supplied DOCX the campaign ends soon",
        "Source C01 shows the August campaigns",
        "Theo tài liệu được cung cấp thì chương trình đã hết hạn",
        "My knowledge base says the cap is 200 USD",
        "That's an owner-supplied ceiling",
    ]
    for text in leaks:
        assert tracker.contains_ai_disclosure(text), text
    for text in ("WEEX supports up to 80% revshare and daily settlement",
                 "Ủa đúng rồi, mình người Việt nè",
                 "The documented process is in the app under Services",
                 "Can you share your PDF deck?",
                 "Thanks for the provided data bro"):
        assert not tracker.contains_source_leak(text), text



@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_decline_with_off_ladder_figure_sends_closing_not_number_check(mock_send, mock_chat):
    await _setup_agent_chat("Hi not interested")
    mock_chat.return_value = "No worries! Our 85% offer stays open anytime 🙏\n[STOP]"

    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock):
        res = await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")

    assert res is True
    sent = mock_send.call_args[0][2]
    assert sent == tracker.get_decline_closing_reply([{"role": "user", "content": "Hi not interested"}])
    assert "double-check" not in sent and "%" not in sent


@pytest.mark.asyncio
async def test_stale_delayed_unlock_does_not_release_newer_ai_lock(monkeypatch):
    """A send's 30s delayed unlock must not free a lock taken later by a reply still
    being generated — that let a second KOL message trigger a parallel AI reply."""
    import dm_reply_tracker as drt

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(drt.asyncio, "sleep", _no_sleep)
    key = (91, 92)
    old_gen = drt._lock_ai_send(*key)  # earlier send
    drt._lock_ai_send(*key)            # new reply generating
    await drt._remove_ai_send_after_delay(*key, old_gen)
    assert key in drt._pending_ai_sends
    await drt._remove_ai_send_after_delay(*key, drt._pending_ai_gen[key])
    assert key not in drt._pending_ai_sends


def test_fallback_not_repeated_helpers():
    import kol_offer
    import dm_reply_tracker as t
    fb = kol_offer.get_safe_offer_fallback_reply("en")
    hist = [{"role": "assistant", "content": "Hi"}, {"role": "user", "content": "details?"},
            {"role": "assistant", "content": fb}, {"role": "user", "content": "Okay"}]
    assert t._already_said(hist, "  " + fb.upper())
    assert not t._already_said(hist, "Here are the details")
    assert t._last_assistant_is_fallback(hist)
    assert not t._last_assistant_is_fallback(hist[:2])
    vi = [{"role": "assistant", "content": kol_offer.get_safe_offer_fallback_reply("vi")}]
    assert t._last_assistant_is_fallback(vi)


def test_human_bd_handover_wording_is_blocked():
    import dm_reply_tracker as t
    assert t.contains_ai_disclosure("I've handed over your case to a human BD. They will review your data.")
    assert t.contains_ai_disclosure("Let me connect you with a real person")
    assert not t.contains_ai_disclosure("Let me check with my manager and come back to you.")


# ── Polite "thanks, no need": closing CTA once, lead closed, no AI call ──────

@pytest.mark.parametrize("text,expected", [
    ("Thanks!\nI no need", True), ("no thanks", True), ("Not interested bro", True),
    ("không cần đâu", True), ("не надо", True),
    ("no need KYC?", False), ("ok thanks", False), ("thanks", False),
    ("I need more info, no need to rush but tell me the commission and how I withdraw my funds each month", False),
])
def test_is_polite_decline(text, expected):
    assert tracker.is_polite_decline(text) is expected


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_polite_decline_gets_closing_cta_without_ai_and_closes_lead(mock_send, mock_chat):
    await _setup_agent_chat("Thanks!\nI no need")
    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock) as mock_notify:
        res = await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")
    assert res is True
    mock_chat.assert_not_called()
    mock_notify.assert_not_called()
    sent = mock_send.call_args[0][2]
    for part in ("Thanks", "no KYC", "no withdrawal limits", "top 6", "CoinGecko", "WEEX", "message me here"):
        assert part in sent
    assert "%" not in sent and "$" not in sent
    chat = await db.get_followup_chat(1, 202)
    assert chat["status"] == "needs_human"
    assert await db.get_kol_step(1, 202) == "declined"
