"""Unit tests for alerts.py — dedup, flood auto-pause, backup retention.

Offline: database and telegram_client are monkeypatched; backup uses a real
temp sqlite file.
"""
import os
import sqlite3
import types
from unittest.mock import AsyncMock, patch

import pytest

import alerts


@pytest.fixture
def fake_db(tmp_path):
    db_path = str(tmp_path / "scheduler.db")
    # Real sqlite file so run_backup() has something to copy.
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()

    fake = types.ModuleType("database")
    fake.DB_PATH = db_path
    fake.get_setting = AsyncMock(return_value="1")
    fake.get_all_accounts = AsyncMock(return_value=[])
    fake.pause_account = AsyncMock()
    fake.get_db = AsyncMock()
    return fake


@pytest.fixture(autouse=True)
def clear_alert_log():
    alerts._alert_log.clear()
    yield
    alerts._alert_log.clear()


# ── send_alert ──────────────────────────────────────────────────────────────

async def test_send_alert_disabled_by_setting(fake_db, monkeypatch):
    fake_db.get_setting = AsyncMock(return_value="0")
    monkeypatch.setattr(alerts, "db", fake_db)
    with patch("telegram_client.get_client") as gc:
        await alerts.send_alert("k", "text")
        gc.assert_not_called()


async def test_send_alert_no_connected_account_is_noop(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_all_accounts = AsyncMock(return_value=[{"id": 1, "is_logged_in": True}])
    with patch("telegram_client.get_client", return_value=None):
        await alerts.send_alert("k", "text")  # must not raise


async def test_send_alert_sends_and_records_cooldown(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_all_accounts = AsyncMock(return_value=[{"id": 1, "is_logged_in": True}])
    client = types.SimpleNamespace(is_connected=lambda: True, send_message=AsyncMock())
    with patch("telegram_client.get_client", return_value=client):
        await alerts.send_alert("k", "hello")
    client.send_message.assert_awaited_once()
    assert "k" in alerts._alert_log


async def test_send_alert_dedups_within_cooldown(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_all_accounts = AsyncMock(return_value=[{"id": 1, "is_logged_in": True}])
    client = types.SimpleNamespace(is_connected=lambda: True, send_message=AsyncMock())
    with patch("telegram_client.get_client", return_value=client):
        await alerts.send_alert("dup", "first")
        await alerts.send_alert("dup", "second")
    assert client.send_message.await_count == 1


async def test_send_alert_never_raises(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_setting = AsyncMock(side_effect=RuntimeError("db down"))
    await alerts.send_alert("k", "text")  # swallowed


# ── flood_guard ─────────────────────────────────────────────────────────────

async def test_flood_guard_pauses_account(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_all_accounts = AsyncMock(return_value=[{"id": 5, "name": "nick", "is_paused": False}])
    with patch.object(alerts, "send_alert", AsyncMock()):
        paused = await alerts.flood_guard(5)
    assert paused is True
    fake_db.pause_account.assert_awaited_once()


async def test_flood_guard_skips_already_paused(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_all_accounts = AsyncMock(return_value=[{"id": 5, "name": "nick", "is_paused": True}])
    paused = await alerts.flood_guard(5)
    assert paused is False
    fake_db.pause_account.assert_not_awaited()


async def test_flood_guard_returns_false_on_error(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_all_accounts = AsyncMock(side_effect=RuntimeError("boom"))
    assert await alerts.flood_guard(1) is False


# ── run_backup ──────────────────────────────────────────────────────────────

def test_run_backup_creates_file_and_returns_path(fake_db, monkeypatch, tmp_path):
    monkeypatch.setattr(alerts, "db", fake_db)
    monkeypatch.setattr(alerts, "BACKUP_DIR", str(tmp_path / "backups"))
    dest = alerts.run_backup()
    assert dest is not None and os.path.exists(dest)


def test_run_backup_prunes_to_keep_limit(fake_db, monkeypatch, tmp_path):
    monkeypatch.setattr(alerts, "db", fake_db)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(alerts, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(alerts, "BACKUP_KEEP", 2)
    # Seed 5 stale backups with distinct mtimes via name sort.
    for i in range(5):
        (backup_dir / f"scheduler_2026010{i}_0000.db").write_bytes(b"x")
    alerts.run_backup()
    remaining = [f for f in os.listdir(backup_dir) if f.startswith("scheduler_")]
    assert len(remaining) <= alerts.BACKUP_KEEP


def test_run_backup_returns_none_on_error(fake_db, monkeypatch):
    fake_db.DB_PATH = "/nonexistent/path/db.sqlite"
    monkeypatch.setattr(alerts, "db", fake_db)
    assert alerts.run_backup() is None


# ── check_reply_sla ─────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.row_factory = None

    async def execute(self, *a, **k):
        return _FakeCursor(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _row(minutes_ago, last_role="user", tier="Tier A"):
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    import json
    return {
        "account_id": 1, "user_id": 100, "username": "hot", "name": "Hot Lead",
        "lead_tier": tier, "intent_score": 80, "updated_at": ts,
        "history_json": json.dumps([{"role": last_role, "content": "hi"}]),
    }


async def test_reply_sla_alerts_when_user_waiting(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_setting = AsyncMock(return_value="1")
    rows = [_row(minutes_ago=90)]
    fake_db.get_db = lambda: _FakeConn(rows)
    with patch.object(alerts, "send_alert", AsyncMock()) as sa:
        await alerts.check_reply_sla()
    sa.assert_awaited_once()


async def test_reply_sla_skips_when_we_replied_last(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_setting = AsyncMock(return_value="1")
    rows = [_row(minutes_ago=90, last_role="assistant")]
    fake_db.get_db = lambda: _FakeConn(rows)
    with patch.object(alerts, "send_alert", AsyncMock()) as sa:
        await alerts.check_reply_sla()
    sa.assert_not_awaited()


async def test_reply_sla_skips_within_threshold(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)

    async def _setting(key, default=""):
        if key == "reply_sla_minutes":
            return "30"
        return "1"  # enabled flags
    fake_db.get_setting = AsyncMock(side_effect=_setting)
    rows = [_row(minutes_ago=5)]  # 5 < 30 min threshold
    fake_db.get_db = lambda: _FakeConn(rows)
    with patch.object(alerts, "send_alert", AsyncMock()) as sa:
        await alerts.check_reply_sla()
    sa.assert_not_awaited()


async def test_reply_sla_disabled_by_setting(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_setting = AsyncMock(return_value="0")
    rows = [_row(minutes_ago=90)]
    fake_db.get_db = lambda: _FakeConn(rows)
    with patch.object(alerts, "send_alert", AsyncMock()) as sa:
        await alerts.check_reply_sla()
    sa.assert_not_awaited()


async def test_reply_sla_new_lead_not_suppressed_by_prior_alert(fake_db, monkeypatch):
    """Regression: a shared 'reply_sla' kind + 1h cooldown used to suppress the
    alert for a DIFFERENT hot lead that crosses the 30-min threshold later."""
    monkeypatch.setattr(alerts, "db", fake_db)

    async def _setting(key, default=""):
        if key == "reply_sla_minutes":
            return "30"
        return "1"
    fake_db.get_setting = AsyncMock(side_effect=_setting)

    # Lead A waiting.
    fake_db.get_db = lambda: _FakeConn([_row(minutes_ago=90)])
    sent_kinds = []

    async def capture(kind, text):
        sent_kinds.append(kind)
    monkeypatch.setattr(alerts, "send_alert", capture)

    await alerts.check_reply_sla()
    assert len(sent_kinds) == 1

    # Lead B (different user_id) waiting — must produce a NEW distinct alert.
    row_b = _row(minutes_ago=90)
    row_b["user_id"] = 200
    fake_db.get_db = lambda: _FakeConn([row_b])
    await alerts.check_reply_sla()
    assert len(sent_kinds) == 2
    assert sent_kinds[0] != sent_kinds[1]


async def test_flood_guard_rotates_proxy_when_enabled(fake_db, monkeypatch):
    monkeypatch.setattr(alerts, "db", fake_db)
    fake_db.get_all_accounts = AsyncMock(return_value=[{"id": 5, "name": "n", "is_paused": False, "proxy_url": "p1"}])
    fake_db.get_setting = AsyncMock(return_value="1")
    rotate = AsyncMock(return_value={"rotated": True, "new_proxy": "p2", "reason": "ok"})
    monkeypatch.setattr("proxy_manager.rotate_proxy_for_account", rotate)
    with patch.object(alerts, "send_alert", AsyncMock()):
        paused = await alerts.flood_guard(5)
    assert paused is True
    rotate.assert_awaited_once()
