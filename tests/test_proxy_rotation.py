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


async def test_reconnect_failure_still_rotates_db(monkeypatch):
    db = FakeDB(pool=["p1", "p2"], accounts=[{"id": 1, "proxy_url": "p1"}])
    monkeypatch.setattr("database.get_setting", db.get_setting)
    monkeypatch.setattr("database.get_all_accounts", db.get_all_accounts)
    monkeypatch.setattr("database.update_account_proxy", db.update_account_proxy)
    with patch("telegram_client.reconnect_with_proxy", AsyncMock(side_effect=RuntimeError("boom")), create=True):
        res = await pm.rotate_proxy_for_account(1, current_proxy="p1")
    assert res["rotated"] is True
    assert "reconnect" in res["reason"]


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
