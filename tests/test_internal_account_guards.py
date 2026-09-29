"""Regression: outreach paths must never DM our OWN managed accounts.

Covers the shared self-id loader used by the keyword/join watcher, and the
DB helper that both the campaign and invite engines use to skip internal ids.
"""
from unittest.mock import AsyncMock, patch

import database as db
import keyword_watcher as kw


async def test_get_self_user_ids_includes_db_telegram_user_ids(monkeypatch):
    """The watcher guard must know our internal ids even before any client's
    me_cache is warm (durable telegram_user_id source)."""
    kw._self_user_ids.clear()
    kw._self_user_ids_loaded_at = 0.0
    monkeypatch.setattr(kw.db, "get_all_internal_telegram_user_ids",
                        AsyncMock(return_value={1001, 1002}))
    monkeypatch.setattr(kw.tg, "_me_cache", {}, raising=False)

    ids = await kw._get_self_user_ids()
    assert {1001, 1002} <= ids
    kw._self_user_ids.clear()
    kw._self_user_ids_loaded_at = 0.0


async def test_get_self_user_ids_merges_runtime_me_cache(monkeypatch):
    kw._self_user_ids.clear()
    kw._self_user_ids_loaded_at = 0.0
    monkeypatch.setattr(kw.db, "get_all_internal_telegram_user_ids",
                        AsyncMock(return_value={1001}))
    monkeypatch.setattr(kw.tg, "_me_cache", {1: {"user_id": 2002}}, raising=False)

    ids = await kw._get_self_user_ids()
    assert 1001 in ids and 2002 in ids
    kw._self_user_ids.clear()
    kw._self_user_ids_loaded_at = 0.0


async def test_internal_ids_helper_returns_managed_accounts():
    """End-to-end DB helper used by campaign/invite skip guards."""
    aid = await db.create_account({
        "name": "self1", "phone": "+84900000001", "api_id": "1",
        "api_hash": "h", "session_name": "self1_session",
    })
    await db.update_account_telegram_user_id(aid, 555001)

    ids = await db.get_all_internal_telegram_user_ids()
    assert 555001 in ids
