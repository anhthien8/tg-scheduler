"""Zalo outreach: contact storage, source filtering and the campaign worker step
(friend request → accept → greeting) against a fake bridge."""
import asyncio

import pytest

import zalo_outreach as zo
import zalo_store as store


@pytest.fixture
def fake_bridge(monkeypatch):
    calls = []
    state = {"friends": set(), "fail_requests": False}

    async def fake_call(method, path, json=None, timeout=60):
        calls.append((method, path, json))
        if path == "/status":
            return {"logged_in": True}
        if path.startswith("/friend-status/"):
            uid = path.rsplit("/", 1)[1]
            return {"is_friend": int(uid in state["friends"]), "is_requested": 0, "is_requesting": 0}
        if path == "/friend-request":
            if state["fail_requests"]:
                raise zo.BridgeError("Zalo error 222")
            return {"ok": True}
        if path == "/send":
            return {"ok": True}
        raise AssertionError(path)

    monkeypatch.setattr(zo, "call", fake_call)
    monkeypatch.setattr(zo, "_in_hours", lambda c: True)
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(zo.asyncio, "sleep", lambda *_: _real_sleep(0))
    store._schema_ready = False  # conftest drops tables between tests
    return calls, state


def _campaign(**kw):
    base = dict(name="t", sources=[], friend_msg="Chào {name}", greet_msg="Cảm ơn {full_name}",
                daily_limit=2, min_delay_sec=60, max_delay_sec=60, start_hour=0, end_hour=24)
    base.update(kw)
    return base


def test_scrape_dedup_and_sources(fake_bridge):
    async def run():
        r1 = await store.upsert_contacts([{"uid": "1", "name": "Nguyễn Văn An"}, {"uid": "2", "name": "B"}], "group", "g1", "Nhóm 1")
        r2 = await store.upsert_contacts([{"uid": "2", "name": "B"}, {"uid": "3", "name": "C"}], "event", "e1", "📅 Event")
        assert (r1["new"], r2["new"]) == (2, 1)
        srcs = {(s["source_type"], s["source_id"]): s["total"] for s in await store.list_sources()}
        assert srcs == {("group", "g1"): 2, ("event", "e1"): 2}
        only_event = await store.list_contacts(source="event:e1")
        assert {c["uid"] for c in only_event["items"]} == {"2", "3"}
    asyncio.run(run())


def test_campaign_requests_then_greets(fake_bridge):
    calls, state = fake_bridge

    async def run():
        await store.upsert_contacts([{"uid": "1", "name": "Nguyễn Văn An"}, {"uid": "2", "name": "B"},
                                     {"uid": "3", "name": "C"}], "group", "g1", "Nhóm 1")
        cid = await store.create_campaign(_campaign())
        await store.set_campaign_state(cid, status="running")

        await zo._step_campaign(await store.get_campaign(cid))
        req = [c for c in calls if c[1] == "/friend-request"]
        assert req == [("POST", "/friend-request", {"uid": "1", "message": "Chào An"})]

        # delay not elapsed → no second request
        await zo._step_campaign(await store.get_campaign(cid))
        assert len([c for c in calls if c[1] == "/friend-request"]) == 1

        # daily limit 2 is respected
        for _ in range(3):
            await store.set_campaign_state(cid, next_action_at=None)
            await zo._step_campaign(await store.get_campaign(cid))
        assert await store.sent_today(cid) == 2

        # uid 1 accepts → greeting sent once
        state["friends"].add("1")
        await zo._check_accepts()
        await zo._check_accepts()
        sends = [c for c in calls if c[1] == "/send"]
        assert sends == [("POST", "/send", {"uid": "1", "message": "Cảm ơn Nguyễn Văn An"})]
        stats = await store.contact_stats()
        assert stats == {"greeted": 1, "requested": 1, "new": 1}
    asyncio.run(run())


def test_auto_pause_after_errors(fake_bridge):
    calls, state = fake_bridge
    state["fail_requests"] = True

    async def run():
        await store.upsert_contacts([{"uid": str(i), "name": f"U{i}"} for i in range(5)], "group", "g1", "Nhóm")
        cid = await store.create_campaign(_campaign(daily_limit=10))
        await store.set_campaign_state(cid, status="running")
        for _ in range(3):
            await store.set_campaign_state(cid, next_action_at=None)
            await zo._step_campaign(await store.get_campaign(cid))
        c = await store.get_campaign(cid)
        assert c["status"] == "paused" and "lỗi liên tiếp" in c["last_error"]
    asyncio.run(run())


def test_campaign_done_when_no_contacts(fake_bridge):
    async def run():
        cid = await store.create_campaign(_campaign(sources=["group:none"]))
        await store.set_campaign_state(cid, status="running")
        await zo._step_campaign(await store.get_campaign(cid))
        assert (await store.get_campaign(cid))["status"] == "done"
    asyncio.run(run())
