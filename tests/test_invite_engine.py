"""Unit tests for invite_engine.py — guards, stop signal, daily limit, modes.

Offline: database + telegram_client monkeypatched; asyncio.sleep stubbed and
the campaign stopped after the intended number of iterations.
"""
import types
from unittest.mock import AsyncMock, patch

import pytest

import invite_engine as ie


@pytest.fixture(autouse=True)
def clean_campaigns():
    ie._active_invite_campaigns.clear()
    yield
    ie._active_invite_campaigns.clear()


def _campaign(**over):
    base = {
        "id": 1, "target_chat": "@chan", "invite_mode": "direct",
        "invite_link": "https://t.me/+abc", "dm_message": "Hi {name} {invite_link}",
        "sender_account_ids": [11, 12], "daily_limit": 50,
        "delay_min": 0, "delay_max": 0, "target_chat_title": "Chan",
        "scrape_job_id": 7, "invited_count": 0, "failed_count": 0, "skipped_count": 0,
    }
    base.update(over)
    return base


@pytest.fixture
def fake_env(monkeypatch):
    db = types.ModuleType("database")
    db.get_invite_campaign = AsyncMock(return_value=_campaign())
    db.get_scraped_members = AsyncMock(return_value=[
        {"user_id": 100, "username": "u1", "first_name": "A", "is_bot": False},
        {"user_id": 101, "username": "u2", "first_name": "B", "is_bot": False},
    ])
    db.get_invite_campaign_sent_user_ids = AsyncMock(return_value=set())
    db.update_invite_campaign_status = AsyncMock()
    db.add_invite_campaign_log = AsyncMock()
    monkeypatch.setattr(ie, "db", db)

    tg = types.ModuleType("telegram_client")
    tg.invite_to_channel = AsyncMock(return_value={"success": True})
    tg.send_text_message = AsyncMock(return_value=True)
    tg.is_bot_account = lambda sender, username=None: bool(username) and str(username).lower().endswith("bot")
    monkeypatch.setattr(ie, "tg", tg)
    return db, tg


async def test_missing_campaign_returns(fake_env):
    db, _ = fake_env
    db.get_invite_campaign = AsyncMock(return_value=None)
    await ie.run_invite_campaign(42)
    db.update_invite_campaign_status.assert_not_awaited()


async def test_no_senders_sets_error_status(fake_env):
    db, _ = fake_env
    db.get_invite_campaign = AsyncMock(return_value=_campaign(sender_account_ids=[]))
    await ie.run_invite_campaign(1)
    db.update_invite_campaign_status.assert_any_await(1, "error")


async def test_no_members_completes_with_zero(fake_env):
    db, _ = fake_env
    db.get_scraped_members = AsyncMock(return_value=[])
    await ie.run_invite_campaign(1)
    db.update_invite_campaign_status.assert_any_await(1, "completed", invited=0, failed=0, skipped=0)


async def test_stop_signal_pauses_campaign(fake_env):
    db, tg = fake_env
    # run_invite_campaign sets the flag True at entry, so trigger the stop via a
    # side effect on get_scraped_members (which runs right after that).
    def _stop_at_load(*a, **k):
        ie._active_invite_campaigns[1] = False
        return [
            {"user_id": 100, "username": "u1", "first_name": "A", "is_bot": False},
        ]
    db.get_scraped_members = AsyncMock(side_effect=_stop_at_load)
    with patch.object(ie.asyncio, "sleep", AsyncMock()):
        await ie.run_invite_campaign(1)
    db.update_invite_campaign_status.assert_any_await(1, "paused", invited=0, failed=0, skipped=0)


async def test_all_members_processed_completes(fake_env):
    db, tg = fake_env
    with patch.object(ie.asyncio, "sleep", AsyncMock()):
        await ie.run_invite_campaign(1)
    db.update_invite_campaign_status.assert_any_await(1, "completed", invited=2, failed=0, skipped=0)
    assert tg.invite_to_channel.await_count == 2


async def test_daily_limit_pauses(fake_env):
    db, tg = fake_env
    db.get_invite_campaign = AsyncMock(return_value=_campaign(daily_limit=1))
    with patch.object(ie.asyncio, "sleep", AsyncMock()):
        await ie.run_invite_campaign(1)
    # After 1 invite the limit is hit -> paused with invited=1.
    assert any(
        c.args[1] == "paused" and c.kwargs.get("invited") == 1
        for c in db.update_invite_campaign_status.await_args_list
    )


async def test_dm_link_mode_personalizes_message(fake_env):
    db, tg = fake_env
    db.get_invite_campaign = AsyncMock(return_value=_campaign(invite_mode="dm_link"))
    with patch.object(ie.asyncio, "sleep", AsyncMock()):
        await ie.run_invite_campaign(1)
    sent_text = tg.send_text_message.await_args.args[2]
    assert "{name}" not in sent_text
    assert "{invite_link}" not in sent_text
    assert "https://t.me/+abc" in sent_text


async def test_skips_bots(fake_env):
    db, tg = fake_env
    db.get_scraped_members = AsyncMock(return_value=[
        {"user_id": 100, "username": "spam_bot", "first_name": "B", "is_bot": False},
    ])
    with patch.object(ie.asyncio, "sleep", AsyncMock()):
        await ie.run_invite_campaign(1)
    tg.invite_to_channel.assert_not_awaited()
    assert db.add_invite_campaign_log.await_args.args[-2] == "skipped"


async def test_already_member_is_skipped_not_failed(fake_env):
    db, tg = fake_env
    tg.invite_to_channel = AsyncMock(return_value={"success": False, "error": "already participant"})
    db.get_scraped_members = AsyncMock(return_value=[
        {"user_id": 100, "username": "u1", "first_name": "A", "is_bot": False},
    ])
    with patch.object(ie.asyncio, "sleep", AsyncMock()):
        await ie.run_invite_campaign(1)
    assert any(c.args[-2] == "already_member" for c in db.add_invite_campaign_log.await_args_list)


def test_stop_helper_sets_flag():
    ie.stop_invite_campaign(5)
    assert ie._active_invite_campaigns[5] is False
