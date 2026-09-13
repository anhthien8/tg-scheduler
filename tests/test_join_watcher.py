"""
Tests for the Join Watcher feature (watch_type='join'):
- multi-account dedup key claimed BEFORE any await (race-safe)
- API payload validation for watch_type / join_dm_delay_min/max
- DB round-trip of the new columns
Telethon is mocked (conftest) — no real Telegram calls.
"""
import asyncio
import pytest
from unittest.mock import MagicMock

import database as db
import keyword_watcher as kw

pytestmark = pytest.mark.asyncio


def _make_watcher_dict(**overrides):
    w = {
        "id": 999,
        "name": "Join W",
        "sender_account_ids": [1],
        "keywords": [],
        "group_ids": [-1001234567890],
        "cooldown_hours": 24,
        "dm_once": False,
        "excluded_usernames": [],
        "messages": [{"msg_order": 0, "msg_type": "text", "content": "hi"}],
        "watch_type": "join",
        "join_dm_delay_min": 1,
        "join_dm_delay_max": 1,
    }
    w.update(overrides)
    return w


def _make_join_event(chat_id=-1001234567890, user_id=555, bot=True):
    """Fake ChatAction event. bot=True makes process_join exit fast (no DB)."""
    user = MagicMock()
    user.id = user_id
    user.bot = bot
    user.username = "someone"
    user.access_hash = 12345

    event = MagicMock()
    event.user_joined = True
    event.user_added = False
    event.chat_id = chat_id
    event.users = [user]
    return event


@pytest.fixture(autouse=True)
def _clean_dedup_state():
    kw._seen_msg_ids.clear()
    kw._user_dm_in_progress.clear()
    kw._user_dm_sent.clear()
    yield
    kw._seen_msg_ids.clear()
    kw._user_dm_in_progress.clear()
    kw._user_dm_sent.clear()


# ── (a) Dedup: claim key before await, only 1 task across accounts ────────────

class _SpawnCounter:
    """Wrap asyncio.create_task to count spawns from the join handler."""
    def __init__(self, monkeypatch):
        self.count = 0
        self.tasks = []
        real_create_task = asyncio.create_task

        def counting(coro, *a, **k):
            self.count += 1
            t = real_create_task(coro, *a, **k)
            self.tasks.append(t)
            return t

        monkeypatch.setattr(kw.asyncio, "create_task", counting)

    async def drain(self):
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def test_join_dedup_claims_key_and_spawns_once(monkeypatch):
    spawns = _SpawnCounter(monkeypatch)
    handler = kw._make_join_handler(_make_watcher_dict())
    event = _make_join_event(user_id=555)

    await handler(event)
    key = ("join", kw.clean_id(event.chat_id), 555)
    assert key in kw._seen_msg_ids  # claimed synchronously inside the handler
    assert spawns.count == 1

    # Second delivery of the same join (another account's handler) → no new task
    await handler(_make_join_event(user_id=555))
    assert spawns.count == 1
    await spawns.drain()


async def test_join_dedup_race_concurrent_handlers(monkeypatch):
    """Two accounts firing the SAME join concurrently → exactly one task."""
    spawns = _SpawnCounter(monkeypatch)
    handler_a = kw._make_join_handler(_make_watcher_dict())
    handler_b = kw._make_join_handler(_make_watcher_dict())

    await asyncio.gather(
        handler_a(_make_join_event(user_id=777, bot=False)),
        handler_b(_make_join_event(user_id=777, bot=False))
    )
    assert spawns.count == 1
    await spawns.drain()


async def test_join_handler_ignores_other_groups_and_non_join(monkeypatch):
    spawns = _SpawnCounter(monkeypatch)
    handler = kw._make_join_handler(_make_watcher_dict())

    # Wrong group
    await handler(_make_join_event(chat_id=-1009999999999, user_id=1))
    # Not a join/add action
    ev = _make_join_event(user_id=2)
    ev.user_joined = False
    ev.user_added = False
    await handler(ev)

    assert spawns.count == 0
    assert not kw._seen_msg_ids


# ── (d) Admin promoted during DM delay → skip ─────────────────────────────────

async def test_join_dm_skipped_when_promoted_to_admin_during_delay(monkeypatch):
    """User is a plain member at join time but admin after the delay →
    the post-sleep get_permissions re-check must skip the DM."""
    from unittest.mock import AsyncMock

    spawns = _SpawnCounter(monkeypatch)
    monkeypatch.setattr(kw, "_get_group_admin_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(kw.db, "get_watcher", AsyncMock(return_value={"is_active": True}))
    monkeypatch.setattr(kw.db, "is_user_blacklisted", AsyncMock(return_value=False))
    monkeypatch.setattr(kw.db, "was_user_dmed_recently", AsyncMock(return_value=False))
    dm = AsyncMock(return_value=(True, 1, None))
    monkeypatch.setattr(kw, "_send_dm_with_fallback", dm)

    handler = kw._make_join_handler(_make_watcher_dict())

    # Promoted to admin during the delay → no DM
    ev = _make_join_event(user_id=60001, bot=False)
    ev.users[0].is_bot = False
    ev.client.get_permissions = AsyncMock(
        return_value=MagicMock(is_admin=True, is_creator=False)
    )
    await handler(ev)
    await spawns.drain()
    assert not dm.called

    # Control: still a plain member → DM goes out (proves the mocks reach the DM)
    ev2 = _make_join_event(user_id=60002, bot=False)
    ev2.users[0].is_bot = False
    ev2.client.get_permissions = AsyncMock(
        return_value=MagicMock(is_admin=False, is_creator=False)
    )
    await handler(ev2)
    await spawns.drain()
    assert dm.called


# ── (b) API payload validation ────────────────────────────────────────────────

def _payload(**overrides):
    p = {
        "name": "Join Watcher",
        "sender_account_ids": [1],
        "keywords": [],
        "group_ids": [123],
        "messages": [{"msg_order": 0, "msg_type": "text", "content": "welcome"}],
        "watch_type": "join",
        "join_dm_delay_min": 3,
        "join_dm_delay_max": 15,
    }
    p.update(overrides)
    return p


async def test_api_create_join_watcher_valid(client):
    r = client.post("/api/watchers", json=_payload())
    assert r.status_code == 200
    w = await db.get_watcher(r.json()["id"])
    assert w["watch_type"] == "join"
    assert w["join_dm_delay_min"] == 3
    assert w["join_dm_delay_max"] == 15


async def test_api_invalid_watch_type(client):
    r = client.post("/api/watchers", json=_payload(watch_type="bogus"))
    assert r.status_code == 400


async def test_api_delay_min_below_floor(client):
    r = client.post("/api/watchers", json=_payload(join_dm_delay_min=0))
    assert r.status_code == 400


async def test_api_delay_max_below_min(client):
    r = client.post("/api/watchers", json=_payload(join_dm_delay_min=10, join_dm_delay_max=5))
    assert r.status_code == 400


async def test_api_delay_max_over_cap(client):
    r = client.post("/api/watchers", json=_payload(join_dm_delay_max=121))
    assert r.status_code == 400


async def test_api_update_validates_too(client):
    r = client.post("/api/watchers", json=_payload())
    wid = r.json()["id"]
    r2 = client.put(f"/api/watchers/{wid}", json=_payload(watch_type="nope"))
    assert r2.status_code == 400
    r3 = client.put(f"/api/watchers/{wid}", json=_payload(join_dm_delay_min=5, join_dm_delay_max=30))
    assert r3.status_code == 200
    w = await db.get_watcher(wid)
    assert w["join_dm_delay_min"] == 5
    assert w["join_dm_delay_max"] == 30


async def test_api_keyword_watcher_defaults_preserved(client):
    """Old-style keyword payload (no new fields) still works, defaults applied."""
    p = _payload()
    p.pop("watch_type"); p.pop("join_dm_delay_min"); p.pop("join_dm_delay_max")
    p["keywords"] = ["hello"]
    r = client.post("/api/watchers", json=p)
    assert r.status_code == 200
    w = await db.get_watcher(r.json()["id"])
    assert w["watch_type"] == "keyword"
    assert w["join_dm_delay_min"] == 3
    assert w["join_dm_delay_max"] == 15


# ── (c) DB round-trip ─────────────────────────────────────────────────────────

async def test_db_create_get_roundtrip_join_fields():
    wid = await db.create_watcher({
        "name": "DB Join",
        "sender_account_ids": [1, 2],
        "keywords": [],
        "group_ids": [-100111],
        "watch_type": "join",
        "join_dm_delay_min": 2,
        "join_dm_delay_max": 45,
        "messages": [{"msg_order": 0, "msg_type": "text", "content": "yo"}],
    })
    w = await db.get_watcher(wid)
    assert w["watch_type"] == "join"
    assert w["join_dm_delay_min"] == 2
    assert w["join_dm_delay_max"] == 45

    # update_watcher writes the new fields too
    await db.update_watcher(wid, {
        "name": "DB Join 2",
        "sender_account_ids": [1],
        "keywords": [],
        "group_ids": [-100111],
        "watch_type": "join",
        "join_dm_delay_min": 4,
        "join_dm_delay_max": 60,
        "messages": [],
    })
    w2 = await db.get_watcher(wid)
    assert w2["join_dm_delay_min"] == 4
    assert w2["join_dm_delay_max"] == 60

    # default when omitted
    wid2 = await db.create_watcher({
        "name": "DB Kw",
        "sender_account_ids": [1],
        "keywords": ["x"],
        "group_ids": [-100111],
        "messages": [],
    })
    w3 = await db.get_watcher(wid2)
    assert w3["watch_type"] == "keyword"
    assert w3["join_dm_delay_min"] == 3
    assert w3["join_dm_delay_max"] == 15
