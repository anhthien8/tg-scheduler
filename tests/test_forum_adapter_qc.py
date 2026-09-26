"""Offline callback regression; no real Telegram client or database."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import command_bot as cb
import telegram_forum_inbox as fx

@pytest.mark.asyncio
async def test_cancel_callback_cannot_cancel_another_admin_draft(monkeypatch):
    cfg = fx.ForumConfig(True, -1001, 1, {9, 10})
    monkeypatch.setattr(cb, '_load_forum_config', AsyncMock(return_value=cfg))
    database = SimpleNamespace(get_telegram_topic_by_thread=AsyncMock(return_value={"account_id": 3, "user_id": 4}))
    p = await fx.prepare_reply(database, cfg, 9, -1001, 77, 'draft')
    message = SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=77))
    event = SimpleNamespace(chat_id=-1001, sender_id=10, get_message=AsyncMock(return_value=message), answer=AsyncMock(), edit=AsyncMock())
    await cb._handle_forum_cancel(event, p.token)
    assert p.token in fx._PENDING
    event.edit.assert_not_awaited()
    event.sender_id = 9
    await cb._handle_forum_cancel(event, p.token)
    assert p.token not in fx._PENDING
    event.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_config_reloads_admin_allowlist(monkeypatch):
    settings = {"forum_inbox_enabled": "1", "forum_inbox_group_id": "-1001", "command_bot_admin_ids": "10"}
    async def setting(key, default=None): return settings.get(key, default)
    monkeypatch.setattr(cb.db, 'get_setting', setting)
    monkeypatch.setattr(cb, '_admin_ids', {9})
    assert (await cb._load_forum_config()).admin_ids == {10}


@pytest.mark.asyncio
async def test_callback_uses_get_message(monkeypatch):
    cfg = fx.ForumConfig(True, -1001, 1, {9})
    monkeypatch.setattr(cb, '_load_forum_config', AsyncMock(return_value=cfg))
    monkeypatch.setattr(fx, 'confirm_reply', AsyncMock(return_value='sent'))
    message = SimpleNamespace(reply_to=SimpleNamespace(reply_to_top_id=77), raw_text='preview')
    event = SimpleNamespace(chat_id=-1001, sender_id=9, get_message=AsyncMock(return_value=message),
                            answer=AsyncMock(), edit=AsyncMock())
    await cb._handle_forum_confirm(event, 'token')
    event.get_message.assert_awaited_once()
    assert fx.confirm_reply.await_args.args[4] == 77
    event.edit.assert_awaited_once()

@pytest.mark.asyncio
async def test_send_flow_text_handles_select_target_new_from_group(monkeypatch):
    """_handle_send_flow_text must advance select_target_new→type_message
    even when the event comes from a group/forum topic (not just private chat)."""
    import command_bot as cb
    accounts = [{"id": 1, "name": "TestAcc", "is_premium": False}]
    monkeypatch.setattr(cb, '_get_managed_accounts', AsyncMock(return_value=accounts))
    cb._STATES[9] = {"step": "select_target_new", "account_id": 1, "ts": __import__('time').time()}
    respond_calls = []
    event = SimpleNamespace(
        sender_id=9,
        raw_text="@targetkol",
        is_group=True,
        is_private=False,
        respond=AsyncMock(side_effect=lambda *a, **kw: respond_calls.append(kw))
    )
    await cb._handle_send_flow_text(event)
    assert cb._STATES[9]["step"] == "type_message"
    assert cb._STATES[9]["target"] == "@targetkol"
    assert len(respond_calls) == 1
    assert respond_calls[0]["buttons"] is cb._CANCEL_BUTTONS
    cb._STATES.pop(9, None)


@pytest.mark.asyncio
async def test_send_flow_text_handles_type_message_from_group(monkeypatch):
    """_handle_send_flow_text must collect message and advance to confirm step
    when admin types in a group/forum topic while in type_message state."""
    import command_bot as cb
    _show_confirm_calls = []
    async def fake_show_confirm(event, state): _show_confirm_calls.append(state)
    monkeypatch.setattr(cb, '_show_confirm', fake_show_confirm)
    cb._STATES[9] = {"step": "type_message", "account_id": 1, "target": "@kol", "ts": __import__('time').time()}
    event = SimpleNamespace(
        sender_id=9,
        raw_text="Xin chào KOL",
        is_group=True,
        is_private=False,
        respond=AsyncMock()
    )
    await cb._handle_send_flow_text(event)
    assert cb._STATES[9]["step"] == "confirm"
    assert cb._STATES[9]["message"] == "Xin chào KOL"
    assert len(_show_confirm_calls) == 1
    cb._STATES.pop(9, None)


@pytest.mark.asyncio
async def test_captioned_media_is_rejected(monkeypatch):
    monkeypatch.setattr(cb, '_load_forum_config', AsyncMock(return_value=fx.ForumConfig(True, -1001, 1, {9})))
    monkeypatch.setattr(fx, 'prepare_reply', AsyncMock())
    event = SimpleNamespace(chat_id=-1001, sender_id=9, raw_text='caption',
        message=SimpleNamespace(media=object(), reply_to=SimpleNamespace(reply_to_top_id=77)),
        get_sender=AsyncMock(return_value=SimpleNamespace(bot=False)), respond=AsyncMock())
    await cb._handle_forum_admin_message(event)
    fx.prepare_reply.assert_not_awaited()
    event.respond.assert_awaited_once()
