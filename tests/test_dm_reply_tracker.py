import json
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta
import database as db
import dm_reply_tracker as tracker
import telegram_client as tg

pytestmark = pytest.mark.asyncio

async def test_is_bot_account():
    assert tracker.is_bot_account("help_bot", "Helper", "Hi") is True
    assert tracker.is_bot_account("helper", "Airdrop Bot Pro", "Hi") is True
    long_msg = "Earn money now! Join the official bot at t.me/free_casino_bot to start. Casino free spins and giveaway!" + "a"*150
    assert tracker.is_bot_account("user", "Name", long_msg) is True
    assert tracker.is_bot_account("user_name", "User Name", "Hi, how are you?") is False

async def test_sanitize_telegram_html():
    text = "Hello \\u0062\\u0072\\u006f!<br>Welcome **bold** and *italic*.<script>alert('xss')</script><b>open tag"
    sanitized = tracker.sanitize_telegram_html(text)
    assert "bro!" in sanitized
    assert "\n" in sanitized
    assert "<b>bold</b>" in sanitized
    assert "<i>italic</i>" in sanitized
    assert "<script>" not in sanitized
    assert sanitized.endswith("</b>")


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
    await db.append_followup_chat_message(1, 202, "user", "what is pricing?")
    
    mock_distill.return_value = {
        "question": "what is pricing?",
        "answer": "pricing is 50 usd"
    }
    
    await tracker._async_distill_human_rule(
        account_id=1,
        user_id=202,
        message_text="pricing is 50 usd"
    )
    
    mock_distill.assert_called_once()
    args, kwargs = mock_distill.call_args
    assert kwargs["human_reply"] == "pricing is 50 usd"
    assert len(kwargs["history"]) == 1
    
    rules = await db.get_learned_knowledge_for_agent(agent_id)
    assert len(rules) == 1
    assert rules[0]["question_pattern"] == "what is pricing?"
    assert rules[0]["learned_answer"] == "pricing is 50 usd"
