"""Unit tests for warmup_engine.py — job lifecycle, guards, stop signal.

Offline: database, telegram_client, ai_remix monkeypatched; the run loop is
driven one iteration then stopped via the module stop flag.
"""
import asyncio
import types
from unittest.mock import AsyncMock, patch

import pytest

import warmup_engine as we


@pytest.fixture(autouse=True)
def clean_jobs():
    we._active_jobs.clear()
    yield
    we._active_jobs.clear()


def _job(**over):
    base = {
        "id": 1, "group_id": 10, "account_ids": "[11, 12]",
        "schedule_start": "00:00", "schedule_end": "23:59",
        "status": "running", "posts_today": 0, "daily_post_limit": 5,
        "interval_min": 1, "interval_max": 1,
    }
    base.update(over)
    return base


@pytest.fixture
def fake_env(monkeypatch):
    db = types.ModuleType("database")
    db.get_warmup_job = AsyncMock(return_value=_job())
    db.get_warmup_group = AsyncMock(return_value={"id": 10, "chat_id": "-100123", "chat_title": "G"})
    db.get_warmup_scripts = AsyncMock(return_value=[{"id": 1, "content": "hi", "use_ai_remix": 0}])
    db.get_setting = AsyncMock(return_value="")
    db.add_warmup_log = AsyncMock()
    db.update_warmup_job_status = AsyncMock()
    monkeypatch.setattr(we, "db", db)

    tg = types.ModuleType("telegram_client")
    tg.send_text_message = AsyncMock()
    monkeypatch.setattr(we, "tg", tg)
    return db, tg


async def test_missing_job_returns_without_status_write(fake_env):
    db, _ = fake_env
    db.get_warmup_job = AsyncMock(return_value=None)
    await we.run_warmup_job(99)
    db.update_warmup_job_status.assert_not_awaited()


async def test_no_scripts_stops_job(fake_env):
    db, _ = fake_env
    db.get_warmup_scripts = AsyncMock(return_value=[])
    await we.run_warmup_job(1)
    db.update_warmup_job_status.assert_any_await(1, "stopped")


async def test_no_accounts_stops_job(fake_env):
    db, _ = fake_env
    db.get_warmup_job = AsyncMock(return_value=_job(account_ids="[]"))
    await we.run_warmup_job(1)
    db.update_warmup_job_status.assert_any_await(1, "stopped")


async def test_posts_one_message_then_stops(fake_env):
    db, tg = fake_env
    # After the first successful post, flip the run flag off so the loop exits.
    async def stop_after_first(*a, **k):
        we._active_jobs[1] = False

    tg.send_text_message = AsyncMock(side_effect=stop_after_first)
    db.get_warmup_job = AsyncMock(side_effect=[_job(), _job(posts_today=1)])

    # Avoid the real per-second sleep chunks.
    with patch.object(we.asyncio, "sleep", AsyncMock()):
        await we.run_warmup_job(1)

    tg.send_text_message.assert_awaited_once()
    db.add_warmup_log.assert_awaited()
    assert db.add_warmup_log.await_args.args[5] == "success"


async def test_send_failure_logs_failed(fake_env):
    db, tg = fake_env

    async def fail_and_stop(*a, **k):
        we._active_jobs[1] = False  # end the loop after first attempt
        raise RuntimeError("blocked")

    tg.send_text_message = AsyncMock(side_effect=fail_and_stop)
    db.get_warmup_job = AsyncMock(side_effect=[_job(), _job()])

    with patch.object(we.asyncio, "sleep", AsyncMock()):
        await we.run_warmup_job(1)

    db.add_warmup_log.assert_awaited()
    # args: (job_id, group_id, acc_id, script_id, content, status, [error])
    assert db.add_warmup_log.await_args.args[5] == "failed"


async def test_status_not_running_breaks_loop(fake_env):
    db, _ = fake_env
    db.get_warmup_job = AsyncMock(return_value=_job(status="stopped"))
    with patch.object(we.asyncio, "sleep", AsyncMock()):
        await we.run_warmup_job(1)
    assert db.update_warmup_job_status.await_args.args[-1] == "stopped"


def test_stop_and_is_running_helpers():
    we.stop_warmup_job(1)
    assert we.is_job_running(1) is False
    we._active_jobs[1] = True
    assert we.is_job_running(1) is True
    assert we.is_job_running(999) is False
