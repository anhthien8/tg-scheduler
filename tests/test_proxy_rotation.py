"""Tests for ban-triggered proxy rotation (proxy_manager.rotate_proxy_for_account)."""
import json
from unittest.mock import AsyncMock, patch

import proxy_manager as pm


class FakeDB:
    def __init__(self, pool, accounts, current=None):
        self._pool = json.dumps(pool)
        self._accounts = accounts
        self.current = current
        self.updated = None
        self.get_setting = AsyncMock(side_effect=self._get_setting)
        self.get_all_accounts = AsyncMock(return_value=accounts)
        self.update_account_proxy = AsyncMock(side_effect=self._update)

    async def _get_setting(self, key, default=""):
        return self._pool if key == "proxy_pool" else default

    async def _update(self, account_id, proxy):
        self.updated = (account_id, proxy)


async def test_rotates_to_different_proxy(monkeypatch):
    db = FakeDB(pool=["p1", "p2", "p3"],
                accounts=[{"id": 1, "proxy_url": "p1"}])
    monkeypatch.setattr("database.get_setting", db.get_setting)
    monkeypatch.setattr("database.get_all_accounts", db.get_all_accounts)
    monkeypatch.setattr("database.update_account_proxy", db.update_account_proxy)
    with patch("telegram_client.reconnect_with_proxy", AsyncMock(return_value=True), create=True):
        res = await pm.rotate_proxy_for_account(1, current_proxy="p1")
    assert res["rotated"] is True
    assert res["new_proxy"] != "p1"
    assert db.updated[0] == 1


async def test_no_rotation_when_pool_empty(monkeypatch):
    db = FakeDB(pool=[], accounts=[{"id": 1, "proxy_url": "p1"}])
    monkeypatch.setattr("database.get_setting", db.get_setting)
    monkeypatch.setattr("database.get_all_accounts", db.get_all_accounts)
    monkeypatch.setattr("database.update_account_proxy", db.update_account_proxy)
    res = await pm.rotate_proxy_for_account(1, current_proxy="p1")
    assert res["rotated"] is False
    assert "trống" in res["reason"]


async def test_no_rotation_when_only_same_proxy(monkeypatch):
    db = FakeDB(pool=["p1"], accounts=[{"id": 1, "proxy_url": "p1"}])
    monkeypatch.setattr("database.get_setting", db.get_setting)
    monkeypatch.setattr("database.get_all_accounts", db.get_all_accounts)
    monkeypatch.setattr("database.update_account_proxy", db.update_account_proxy)
    res = await pm.rotate_proxy_for_account(1, current_proxy="p1")
    assert res["rotated"] is False


async def test_picks_least_used_proxy(monkeypatch):
    # p2 used by 2 accounts, p3 used by 0 -> should pick p3.
    db = FakeDB(
        pool=["p1", "p2", "p3"],
        accounts=[
            {"id": 1, "proxy_url": "p1"},
            {"id": 2, "proxy_url": "p2"},
            {"id": 3, "proxy_url": "p2"},
        ],
    )
    monkeypatch.setattr("database.get_setting", db.get_setting)
    monkeypatch.setattr("database.get_all_accounts", db.get_all_accounts)
    monkeypatch.setattr("database.update_account_proxy", db.update_account_proxy)
    with patch("telegram_client.reconnect_with_proxy", AsyncMock(return_value=True), create=True):
        res = await pm.rotate_proxy_for_account(1, current_proxy="p1")
    assert res["new_proxy"] == "p3"


async def test_reconnect_failure_does_not_rotate(monkeypatch):
    """If the new proxy can't connect, we must NOT commit it (account would be
    left offline). DB proxy stays unchanged and rotated is False."""
    db = FakeDB(pool=["p1", "p2"], accounts=[{"id": 1, "proxy_url": "p1"}])
    monkeypatch.setattr("database.get_setting", db.get_setting)
    monkeypatch.setattr("database.get_all_accounts", db.get_all_accounts)
    monkeypatch.setattr("database.update_account_proxy", db.update_account_proxy)
    with patch("telegram_client.reconnect_with_proxy", AsyncMock(return_value=False), create=True):
        res = await pm.rotate_proxy_for_account(1, current_proxy="p1")
    assert res["rotated"] is False
    assert "thất bại" in res["reason"]
    assert db.updated is None  # DB proxy untouched


async def test_reconnect_returns_false_even_after_successful_rollback(monkeypatch):
    """Regression: reconnect_with_proxy must NOT return True when it fell back to
    the old proxy — otherwise rotate_proxy_for_account persists the dead proxy.

    conftest mocks `telegram_client`, so load the REAL module from disk to test
    its actual reconnect contract.
    """
    import importlib.util
    import sys
    import types
    from pathlib import Path
    from unittest.mock import patch

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("real_tg_reconnect", root / "telegram_client.py")
    real = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "database": types.ModuleType("database"),
    }):
        spec.loader.exec_module(real)

    calls = {"n": 0}

    async def fake_create_client(account_id, api_id, api_hash, session_name, proxy_url=None):
        calls["n"] += 1

    async def fake_start_client(account_id):
        return calls["n"] > 1  # new proxy fails, rollback succeeds

    monkeypatch.setattr(real, "create_client", fake_create_client)
    monkeypatch.setattr(real, "start_client", fake_start_client)
    real._clients = {}

    fake_db = types.ModuleType("database")
    fake_db.get_accounts = AsyncMock(return_value=[{"id": 1, "api_id": 1, "api_hash": "h", "session_name": "s"}])
    with patch.dict(sys.modules, {"database": fake_db}):
        result = await real.reconnect_with_proxy(1, "socks5://dead:1080")
    assert result is False  # rolled back, but must report failure for the swap


async def test_handles_malformed_pool(monkeypatch):
    class BadDB(FakeDB):
        async def _get_setting(self, key, default=""):
            return "not json" if key == "proxy_pool" else default
    db = BadDB(pool=[], accounts=[])
    monkeypatch.setattr("database.get_setting", db.get_setting)
    monkeypatch.setattr("database.get_all_accounts", db.get_all_accounts)
    monkeypatch.setattr("database.update_account_proxy", db.update_account_proxy)
    res = await pm.rotate_proxy_for_account(1, current_proxy="p1")
    assert res["rotated"] is False
