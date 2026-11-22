"""v3.38 — nobody gets a message they cannot read, and old / admin-owned chats are left alone.

Three incidents (2026-11-10):
1. A Chinese campaign template went to English speakers (AI Remix was off, so
   nothing looked at the recipient's language).
2. The drip engine sent an English pitch into a Vietnamese chat.
3. The drip engine revived chats that were 7-11 days old, on an account with AI
   switched off, in conversations our human admin had already handled.
"""
from unittest.mock import AsyncMock, patch

import ai_remix
import database as db
import dm_reply_tracker as tracker
import lang_guard
import telegram_client as tg

from tests.test_drip_queue_and_owed_answers import A, U, _account_and_agent, _row, _silent_lead, _set

EN_PITCH = "Hey bro, still interested in the affiliate program? We can offer a strong revshare deal."
ZH_TEMPLATE = "嗨 Rizwan，我是 WEEX 的 Will。想直接和你聊三个重点：返佣比例、社群推广预算、交易量奖励。"


# ── language detection ───────────────────────────────────────────────────────

def test_text_script_recognises_languages():
    assert lang_guard.text_script(ZH_TEMPLATE) == "zh"          # Latin brand words do not hide Chinese
    assert lang_guard.text_script("Привет! Какие условия по WEEX?") == "cyr"
    assert lang_guard.text_script("Setup villa xong. Trade lại. Volume tháng bao nhiêu?") == "vi"
    assert lang_guard.text_script(EN_PITCH) == "latin"
    assert lang_guard.text_script("[HANDOVER] https://t.me/x @abc") == ""


def test_conversation_scripts_follow_what_the_person_wrote():
    vi_chat = [
        {"role": "user", "content": "mình làm cho binance và bybit"},
        {"role": "assistant", "content": "[Human Admin]: ok bạn, để mình gửi thông tin nhé"},
        {"role": "user", "content": "Okie"},
    ]
    assert lang_guard.conversation_scripts(vi_chat) == ["vi"]      # "Okie" does not make it English
    both = [{"role": "user", "content": "Привет, какие условия?"},
            {"role": "user", "content": "I can also speak English if that is easier"}]
    assert set(lang_guard.conversation_scripts(both)) == {"cyr", "latin"}
    assert lang_guard.conversation_scripts([{"role": "user", "content": "[Sent a sticker]"}]) == []


def test_wrong_language_verdicts():
    assert lang_guard.wrong_language(EN_PITCH, ["vi"], strict=True)          # English into a Vietnamese chat
    assert lang_guard.wrong_language(ZH_TEMPLATE, "latin")                   # Chinese to an English reader
    assert not lang_guard.wrong_language(EN_PITCH, "cyr")                    # weak evidence: English passes
    assert not lang_guard.wrong_language("Ok bro 👍", ["vi"], strict=True)   # too short to judge
    assert not lang_guard.wrong_language(EN_PITCH, [], strict=True)          # no evidence, no verdict
    assert not lang_guard.wrong_language("Chào bạn, mình gửi thông tin nhé", ["vi"], strict=True)


# ── campaign / Keyword DM template gate ──────────────────────────────────────

async def test_chinese_template_is_not_sent_to_an_english_speaker_without_ai():
    with patch.object(ai_remix, "get_provider_config", AsyncMock(return_value=([], {}))):
        text, why = await ai_remix.fit_recipient_language(ZH_TEMPLATE, {"first_name": "M Rizwan"}, True)
    assert why.startswith("language_mismatch") and "Chinese" in why


async def test_chinese_template_is_translated_when_ai_is_available():
    with patch.object(ai_remix, "remix_message", AsyncMock(return_value="Hi Rizwan, Will from WEEX here.")) as rm:
        text, why = await ai_remix.fit_recipient_language(
            ZH_TEMPLATE, {"lang_code": "en"}, True, provider="gemini", api_keys=["k"])
    assert why == "" and text.startswith("Hi Rizwan")
    assert rm.call_args.kwargs["auto_translate_native"] is True


async def test_failed_translation_blocks_the_send():
    # remix_message returns the original text when every AI key fails.
    with patch.object(ai_remix, "remix_message", AsyncMock(return_value=ZH_TEMPLATE)):
        text, why = await ai_remix.fit_recipient_language(
            ZH_TEMPLATE, {"lang_code": "ru"}, True, provider="gemini", api_keys=["k"])
    assert why and "Russian" in why


async def test_template_passes_for_matching_or_unjudged_recipients():
    rm = AsyncMock()
    with patch.object(ai_remix, "remix_message", rm):
        assert (await ai_remix.fit_recipient_language(ZH_TEMPLATE, {"lang_code": "zh-hans"}, True))[1] == ""
        assert (await ai_remix.fit_recipient_language(ZH_TEMPLATE, {"first_name": "王伟"}, True))[1] == ""
        # English template: always fine. Unknown recipient with translation switched off: sent as written.
        assert (await ai_remix.fit_recipient_language(EN_PITCH, {"lang_code": "ru"}, True))[1] == ""
        assert (await ai_remix.fit_recipient_language(ZH_TEMPLATE, {"first_name": "Bob"}, False))[1] == ""
    rm.assert_not_called()


def test_campaign_and_keyword_dm_use_the_gate():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    assert "fit_recipient_language" in (root / "routes" / "members.py").read_text(encoding="utf-8")
    assert "fit_recipient_language" in (root / "keyword_watcher.py").read_text(encoding="utf-8")


# ── AI send path ─────────────────────────────────────────────────────────────

async def _vi_chat(user_id: int = U):
    await db.get_or_create_followup_chat(account_id=A, user_id=user_id, username=f"kol{user_id}", name="KOL")
    await db.append_followup_chat_message(A, user_id, "user", "mình đang làm cho binance và bybit, bên bạn thế nào?")


@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_english_reply_into_vietnamese_chat_is_blocked_and_handed_over(mock_send):
    await _account_and_agent()
    await _vi_chat()
    with patch.object(tracker, "_notify_main_account_handover", AsyncMock()) as notify:
        assert await tracker._send_ai_message(A, U, EN_PITCH) is False
    mock_send.assert_not_called()
    assert (await _row())["status"] == "needs_human"     # someone was waiting → a person answers
    notify.assert_called_once()


@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_reply_in_the_persons_language_is_sent(mock_send):
    await _account_and_agent()
    await _vi_chat()
    mock_send.return_value = True
    assert await tracker._send_ai_message(A, U, "Bên mình hỗ trợ revshare tốt, bạn cho mình xin volume nhé") is True
    mock_send.assert_called_once()


# ── drip eligibility ─────────────────────────────────────────────────────────

async def _run_drip(mock_gen, text=EN_PITCH):
    mock_gen.return_value = text
    with patch.object(tg, "ensure_user_entity", AsyncMock(return_value=True), create=True):
        return await tracker.process_drip_followups()


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_never_revives_a_long_silent_lead(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead(hours=11 * 24)                     # silent for 11 days
    res = await _run_drip(mock_gen)
    assert res["sent"] == 0
    mock_gen.assert_not_called()
    mock_send.assert_not_called()


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_drops_a_stage_that_is_more_than_three_days_overdue(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead(hours=130, tier="Tier A")           # stage 1 was due at 12h
    res = await _run_drip(mock_gen)
    assert res["sent"] == 0
    mock_gen.assert_not_called()
    assert (await _row())["drip_retry_after"]              # parked, not re-read every cycle


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_skips_chats_a_human_admin_handled(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead(user_id=2001)
    await db.append_followup_chat_message(A, 2001, "assistant", "[Human Admin]: ok bro, talk tomorrow")
    await _silent_lead(user_id=2002)
    await _set(2002, "human_takeover_at = datetime('now', '-3 days')")
    await _set(2001, "last_user_message_at = datetime('now', '-60 hours')")
    res = await _run_drip(mock_gen)
    assert res["sent"] == 0
    mock_gen.assert_not_called()
    mock_send.assert_not_called()


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_sends_nothing_from_an_account_with_ai_off(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead()
    await db.set_account_ai_agent(A, None)
    res = await _run_drip(mock_gen)
    assert res["sent"] == 0
    mock_gen.assert_not_called()
    mock_send.assert_not_called()
    assert (await _row())["drip_retry_after"]


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_in_the_wrong_language_is_not_sent(mock_send, mock_gen):
    await _account_and_agent()
    await _vi_chat()
    await _set(U, "last_user_message_at = datetime('now', '-60 hours'), updated_at = datetime('now', '-60 hours')")
    res = await _run_drip(mock_gen, EN_PITCH)
    assert res["sent"] == 0
    mock_gen.assert_called_once()
    assert "Vietnamese" in mock_gen.call_args.args[1]      # the prompt names the language
    mock_send.assert_not_called()
    assert (await _row())["status"] == "active"            # a drip is not an owed answer: no handover


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_still_reaches_a_fresh_lead(mock_send, mock_gen):
    await _account_and_agent()
    await _silent_lead()
    mock_send.return_value = True
    res = await _run_drip(mock_gen)
    assert res["sent"] == 1
    mock_send.assert_called_once()


def test_app_list_label_does_not_make_a_list_vietnamese():
    """2026-11-21: a list saved as 'globalrashidfamily · Tất cả có contact (10/11)' sent ~105 Indian /
    English KOLs a Vietnamese opener: the app's own Vietnamese tab label was read as the group's language."""
    pick = lambda g: ai_remix.detect_user_language({"community_hint": g, "first_name": "@x"}, with_source=True)
    assert pick("globalrashidfamily · Tất cả có contact (10/11)") == ("en", "default")
    assert pick("Crypto_Owen111 · Tất cả có contact (09/11)") == ("en", "default")
    assert pick("H2Capital · Cộng đồng Crypto (06/11)") == ("en", "default")
    assert pick("Hội Trader Sài Gòn") == ("vi", "group")          # a real Vietnamese group still counts
    assert pick("Binance Vietnam · Tiềm năng BD (01/11)") == ("vi", "group")
    # The member's own profile still wins over everything.
    assert ai_remix.detect_user_language({"community_hint": "x · Tất cả (10/11)", "bio": "مرحبا"}) == "ar"
