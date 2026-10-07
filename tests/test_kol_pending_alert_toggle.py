from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import dm_reply_tracker as tracker


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("setting, expected_saved_messages", [("0", 0), ("1", 1)])
async def test_kol_pending_saved_message_toggle(setting, expected_saved_messages):
    tracker._handover_alert_sent.clear()
    main_client = MagicMock()
    main_client.is_connected.return_value = True
    main_client.get_me = AsyncMock(return_value=SimpleNamespace(id=12345))
    main_client.send_message = AsyncMock()

    profile = {"weex_uid": "uid-1", "selected_campaign": "campaign-1"}
    with (
        patch.object(tracker.db, "get_kol_profile", AsyncMock(return_value=profile)),
        patch.object(tracker.db, "get_account", AsyncMock(return_value={"name": "Account 1"})),
        patch.object(tracker.db, "get_setting", AsyncMock(return_value=setting)) as get_setting,
        patch.object(tracker.tg, "get_client", return_value=main_client),
        patch("command_bot.send_handover_alert", new_callable=AsyncMock) as command_alert,
        patch("command_bot.send_forum_general_alert", new_callable=AsyncMock) as forum_alert,
    ):
        await tracker._notify_main_account_handover(1, 99, "kol_user", "Needs review")

    get_setting.assert_awaited_once_with("kol_pending_saved_msg_enabled", "0")
    command_alert.assert_awaited_once()
    forum_alert.assert_awaited_once()
    assert main_client.send_message.await_count == expected_saved_messages
    if expected_saved_messages:
        assert "🚨 KOL CAMPAIGN PENDING" in main_client.send_message.await_args.args[1]
