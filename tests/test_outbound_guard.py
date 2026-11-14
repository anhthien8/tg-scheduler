"""Internal AI control markers must never reach a real chat.

Incident 10/11/2026: the drip engine sent the bare text "[HANDOVER_REQUIRED]" to
KOLs. Only the interactive reply path stripped the marker. Now every AI send
goes through outbound_guard.clean_ai, and the lowest Telegram send layer
refuses markers too.
"""
import importlib.util
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database as db
import dm_reply_tracker as tracker
import outbound_guard as og

from tests.test_drip_queue_and_owed_answers import A, _account_and_agent, _row, _silent_lead


# ── the cleaner ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "[HANDOVER_REQUIRED]", " [handover_required] \n", "HANDOVER_REQUIRED", "[ HANDOVER REQUIRED ]",
    "[HANDOVER_REQUIRED]\n[METRICS: {\"intent_score\": 60}]", "<b>[HANDOVER_REQUIRED]</b>",
    "METRICS: {\"intent_score\": 60, \"lead_tier\": \"Tier B\"}", "[META: {\"intent_score\":85}]",
    "[DEAL_AGREED]", "[NEEDS_REVIEW]", "[STEP REMINDER INSTRUCTION]:",
])
def test_marker_only_output_leaves_nothing(raw):
    text, _ = og.clean_ai(raw)
    assert text == ""


def test_text_is_kept_and_handover_is_reported():
    text, handover = og.clean_ai("Our team will review and get back to you.\n\n[HANDOVER_REQUIRED]\n"
                                 "[METRICS: {\"intent_score\": 70}]")
    assert text == "Our team will review and get back to you." and handover is True
    assert og.clean_ai("See you [Monday] at 10 — BTC/USDT [VIP] list attached") == (
        "See you [Monday] at 10 — BTC/USDT [VIP] list attached", False)


def test_low_level_scrub_leaves_normal_posts_untouched():
    post = "🚀 [WEEX] New listing!\n\n\n\nTrade [BTC_USDT] now  \n[STOP] loss tips inside"
    assert og.scrub(post) is post
    assert og.guard_outgoing("Hi\n[HANDOVER_REQUIRED]") == "Hi"
    assert og.guard_outgoing("[HANDOVER_REQUIRED]") is None
    with pytest.raises(og.BlockedOutbound):
        og.require("[HANDOVER_REQUIRED]")


# ── lowest send layer ────────────────────────────────────────────────────────

def _load_real_tg():
    """conftest replaces telegram_client with a mock; load the real file."""
    path = Path(__file__).resolve().parent.parent / "telegram_client.py"
    spec = importlib.util.spec_from_file_location("real_telegram_client_outbound_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_send_text_message_never_sends_a_bare_marker():
    tg = _load_real_tg()
    client = MagicMock()
    client.send_message = AsyncMock()
    with patch.dict(tg._clients, {991: client}), \
            patch.object(tg, "_get_entity_safe", new_callable=AsyncMock, return_value="peer"):
        assert await tg.send_text_message(991, 5, "[HANDOVER_REQUIRED]") is False
        client.send_message.assert_not_called()
        assert await tg.send_text_message(991, 5, "Deal details soon. [HANDOVER_REQUIRED]") is True
        assert client.send_message.call_args.args[1] == "Deal details soon."


async def test_photo_caption_is_scrubbed():
    tg = _load_real_tg()
    client = MagicMock()
    client.send_file = AsyncMock()
    with patch.dict(tg._clients, {991: client}), \
            patch.object(tg, "_get_entity_safe", new_callable=AsyncMock, return_value="peer"):
        await tg.send_photo_message(991, 5, "x.png", caption="[HANDOVER_REQUIRED]")
        assert client.send_file.call_args.kwargs["caption"] == ""


# ── the incident: drip engine ────────────────────────────────────────────────

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_with_only_the_marker_sends_nothing_and_hands_over(mock_send, mock_gen):
    await _account_and_agent()
    await db.unpause_account(A)
    await _silent_lead(user_id=7001)
    mock_gen.return_value = "[HANDOVER_REQUIRED]"
    with patch.object(tracker, "_notify_main_account_handover", new_callable=AsyncMock) as notify:
        assert (await tracker.process_drip_followups())["sent"] == 0
        await tracker.process_drip_followups()  # a handed-over chat is not retried
    mock_send.assert_not_called()
    row = await _row(7001)
    assert row["status"] == "needs_human"
    assert "HANDOVER_REQUIRED" not in (row["history_json"] or "")
    assert notify.call_count == 1 and mock_gen.call_count == 1


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_with_text_and_marker_sends_only_the_text(mock_send, mock_gen):
    await _account_and_agent()
    await db.unpause_account(A)
    await _silent_lead(user_id=7002)
    mock_gen.return_value = "Hey, the team is checking your request now.\n\n[HANDOVER_REQUIRED]"
    with patch.object(tracker, "_notify_main_account_handover", new_callable=AsyncMock):
        assert (await tracker.process_drip_followups())["sent"] == 1
    assert mock_send.call_args.args[2] == "Hey, the team is checking your request now."
    row = await _row(7002)
    assert row["status"] == "needs_human"
    assert "HANDOVER_REQUIRED" not in row["history_json"]


# ── nobody adds a new unguarded send path ────────────────────────────────────

def test_every_module_that_sends_to_chats_uses_the_guard():
    """A module that calls Telethon's send_message/send_file with outside content must use outbound_guard.

    Allowed without it: modules that only message ourselves (alerts, admin bot, Saved Messages).
    """
    root = Path(__file__).resolve().parent.parent
    internal_only = {"alerts.py", "command_bot.py", "daily_summary.py"}
    offenders = []
    for path in list(root.glob("*.py")) + list(root.glob("routes/*.py")) + list(root.glob("platforms/*.py")):
        src = path.read_text(encoding="utf-8")
        if path.name in internal_only or path.name == "outbound_guard.py":
            continue
        raw_sends = re.findall(r"client\.send_(?:message|file)\(", src)
        if raw_sends and "outbound_guard" not in src:
            offenders.append(path.name)
    assert offenders == [], f"raw Telegram sends without outbound_guard: {offenders}"


def test_one_question_per_message():
    import outbound_guard as g
    msg = ("Thanks, noted. How has your volume looked over the last 3 months? A screenshot is enough. "
           "Also, where are you based?")
    assert g.one_question(msg) == "Thanks, noted. How has your volume looked over the last 3 months? A screenshot is enough."
    assert g.one_question("Where are you based?") == "Where are you based?"
    assert g.one_question("No question here.") == "No question here."
