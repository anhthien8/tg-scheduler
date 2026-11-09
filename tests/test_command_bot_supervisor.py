"""v3.33.0 — the Command Bot must never be silently dead again.

06–09/11/2026 the bot was off for 3 days while the Forum Inbox card said
"Đang bật": every lead message returned "bot_offline" and every HANDOVER alert
was dropped, with zero log lines. These tests pin the three fixes:
rate-limited warnings, the reconnect supervisor, and the honest status API.
"""
import inspect
import logging
from unittest.mock import AsyncMock

import pytest

import command_bot as cb
import telegram_forum_inbox as fx


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(cb, "_drop_counts", {})
    monkeypatch.setattr(cb, "_drop_logged_at", {})
    monkeypatch.setattr(cb, "_down_checks", 0)
    monkeypatch.setattr(cb, "_restart_failures", 0)
    monkeypatch.setattr(cb, "_next_restart_at", 0.0)
    monkeypatch.setattr(cb, "_bot", None)


def _enabled_cfg():
    return fx.ForumConfig(True, -1001, 1, {9})


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING and "OFFLINE" in r.getMessage()]


# ── Silent drops now leave a trace ────────────────────────────────────────

@pytest.mark.asyncio
async def test_offline_relay_warns_once_per_interval(monkeypatch, caplog):
    monkeypatch.setattr(cb, "_load_forum_config", AsyncMock(return_value=_enabled_cfg()))
    caplog.set_level(logging.WARNING, logger="tg-scheduler.command_bot")

    for user in (1, 2, 3):
        assert await cb.relay_inbound_to_forum(3, user, "lead", "Lead", "hi") == "bot_offline"

    warns = _warnings(caplog)
    assert len(warns) == 1, "a burst of lead messages must not flood the log"
    assert "forum_inbound" in warns[0].getMessage()
    assert cb._drop_counts["forum_inbound"] == 2  # counted for the next warning


@pytest.mark.asyncio
async def test_offline_warning_repeats_after_interval_with_count(monkeypatch, caplog):
    monkeypatch.setattr(cb, "_load_forum_config", AsyncMock(return_value=_enabled_cfg()))
    caplog.set_level(logging.WARNING, logger="tg-scheduler.command_bot")
    clock = [1000.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: clock[0])

    await cb.relay_inbound_to_forum(3, 1, None, None, "a")
    await cb.relay_inbound_to_forum(3, 2, None, None, "b")
    await cb.relay_inbound_to_forum(3, 3, None, None, "c")
    clock[0] += cb._DROP_LOG_INTERVAL + 1
    await cb.relay_inbound_to_forum(3, 4, None, None, "d")

    warns = _warnings(caplog)
    assert len(warns) == 2
    assert "(3 lần" in warns[1].getMessage()  # 2 suppressed + this one


@pytest.mark.asyncio
async def test_deliberately_disabled_inbox_stays_quiet(monkeypatch, caplog):
    off = fx.ForumConfig(False, -1001, 1, {9})
    monkeypatch.setattr(cb, "_load_forum_config", AsyncMock(return_value=off))
    caplog.set_level(logging.WARNING, logger="tg-scheduler.command_bot")

    assert await cb.relay_inbound_to_forum(3, 1, None, None, "hi") == "disabled"
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_handover_alert_dropped_while_offline_is_logged(caplog):
    caplog.set_level(logging.WARNING, logger="tg-scheduler.command_bot")
    sent = await cb.send_handover_alert("Nick", "kol_user", "KOL", "needs human")
    assert sent is False
    warns = _warnings(caplog)
    assert len(warns) == 1 and "handover_alert" in warns[0].getMessage()


# ── Reconnect supervisor ──────────────────────────────────────────────────

class _LiveBot:
    def is_connected(self):
        return True


@pytest.mark.asyncio
async def test_supervisor_healthy_path_skips_db(monkeypatch):
    monkeypatch.setattr(cb, "_bot", _LiveBot())
    get_setting = AsyncMock(return_value="1")
    monkeypatch.setattr(cb.db, "get_setting", get_setting)
    assert await cb.supervise_command_bot() == "ok"
    get_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_supervisor_leaves_disabled_bot_alone(monkeypatch):
    monkeypatch.setattr(cb.db, "get_setting", AsyncMock(return_value="0"))
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(cb, "_start_command_bot_locked", start)
    for _ in range(3):
        assert await cb.supervise_command_bot() == "disabled"
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_supervisor_restarts_after_two_failed_checks(monkeypatch):
    monkeypatch.setattr(cb.db, "get_setting", AsyncMock(return_value="1"))

    async def fake_start():
        cb._bot = _LiveBot()
        return True

    start = AsyncMock(side_effect=fake_start)
    monkeypatch.setattr(cb, "_start_command_bot_locked", start)

    assert await cb.supervise_command_bot() == "waiting"  # give Telethon's reconnect a chance
    start.assert_not_awaited()
    assert await cb.supervise_command_bot() == "restarted"
    start.assert_awaited_once()
    assert await cb.supervise_command_bot() == "ok"


@pytest.mark.asyncio
async def test_supervisor_backs_off_after_failed_restart(monkeypatch):
    monkeypatch.setattr(cb.db, "get_setting", AsyncMock(return_value="1"))
    start = AsyncMock(return_value=False)
    monkeypatch.setattr(cb, "_start_command_bot_locked", start)
    clock = [5000.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: clock[0])

    await cb.supervise_command_bot()
    assert await cb.supervise_command_bot() == "failed"
    assert await cb.supervise_command_bot() == "backoff"
    assert start.await_count == 1
    clock[0] += cb.SUPERVISOR_INTERVAL * 2 + 1
    assert await cb.supervise_command_bot() == "failed"
    assert start.await_count == 2
    # second failure doubles the wait, capped at 30 minutes
    assert cb._next_restart_at - clock[0] == cb.SUPERVISOR_INTERVAL * 4


@pytest.mark.asyncio
async def test_start_is_idempotent_when_bot_is_live(monkeypatch):
    """The settings route and the supervisor may both call start; never open a 2nd client."""
    monkeypatch.setattr(cb, "_bot", _LiveBot())
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(cb, "_start_command_bot_locked", start)
    assert await cb.start_command_bot() is True
    start.assert_not_awaited()


def test_supervisor_is_scheduled_at_engine_start():
    import engine_lifecycle

    src = inspect.getsource(engine_lifecycle.start_engines)
    assert "command_bot.supervise_command_bot" in src
    assert 'id="command_bot_supervisor"' in src
    assert "max_instances=1" in src


# ── Honest status for the Forum Inbox card ────────────────────────────────

@pytest.mark.asyncio
async def test_forum_config_reports_bot_state(monkeypatch):
    from routes import settings as settings_routes

    async def bulk(defaults):
        values = {"forum_inbox_enabled": "1", "forum_inbox_group_id": "-1004352266125",
                  "forum_inbox_general_topic_id": "1", "command_bot_enabled": "0"}
        return {k: values.get(k, v) for k, v in defaults.items()}

    monkeypatch.setattr(settings_routes.db, "get_settings_bulk", bulk)
    monkeypatch.setattr(cb, "is_running", lambda: False)
    data = await settings_routes.get_forum_inbox_config()
    assert data["enabled"] is True
    assert data["bot_running"] is False
    assert data["bot_enabled"] is False
