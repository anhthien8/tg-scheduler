"""Zalo outreach: contact storage, source filtering and the multi-nick campaign
worker (friend request → accept → greeting) against a fake bridge."""
import asyncio
from datetime import datetime, timedelta

import pytest

import zalo_outreach as zo
import zalo_store as store


@pytest.fixture
def fake_bridge(monkeypatch):
    calls = []  # (method, path, json, account)
    state = {"friends": set(), "fail_requests": False, "nicks": {"n1": True, "n2": True}}

    async def fake_call(method, path, json=None, timeout=60, account=None):
        calls.append((method, path, json, account))
        if path == "/status":
            accs = [{"id": i, "logged_in": ok, "me": {"uid": f"u-{i}", "name": f"Nick {i}"} if ok else None,
                     "login": {"state": "idle"}} for i, ok in state["nicks"].items()]
            return {"logged_in": any(state["nicks"].values()), "accounts": accs}
        if path.startswith("/friend-status/"):
            uid = path.rsplit("/", 1)[1]
            return {"is_friend": int(uid in state["friends"]), "is_requested": 0, "is_requesting": 0}
        if path == "/friend-request":
            if state["fail_requests"]:
                raise zo.BridgeError("Zalo error 222")
            return {"ok": True}
        if path == "/send":
            return {"ok": True}
        if path == "/listen":
            state.setdefault("listening", set()).add(account)
            return {"ok": True, "listening": True}
        if path == "/joins":
            q, state["joins"] = state.get("joins", []), []
            return q
        raise AssertionError(path)

    monkeypatch.setattr(zo, "call", fake_call)
    monkeypatch.setattr(zo, "_in_hours", lambda c: True)
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(zo.asyncio, "sleep", lambda *_: _real_sleep(0))
    store._schema_ready = False  # conftest drops tables between tests
    return calls, state


def _campaign(**kw):
    base = dict(name="t", sources=[], accounts=[], friend_msg="Chào {name}", greet_msg="Cảm ơn {full_name}",
                daily_limit=100, min_delay_sec=60, max_delay_sec=60, start_hour=0, end_hour=24)
    base.update(kw)
    return base


def _requests(calls):
    return [c for c in calls if c[1] == "/friend-request"]


async def _running(**kw) -> int:
    cid = await store.create_campaign(_campaign(**kw))
    await store.set_campaign_state(cid, status="running")
    return cid


async def _rearm():
    """Pretend every nick's random delay has elapsed."""
    for a in await store.list_accounts():
        await store.set_account_state(a["id"], next_action_at=None)


async def _contacts(n: int):
    await store.upsert_contacts([{"uid": str(i), "name": f"Người {i}"} for i in range(n)], "group", "g1", "Nhóm 1")


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


def test_nicks_share_contacts_then_greet_from_pinned_nick(fake_bridge):
    calls, state = fake_bridge

    async def run():
        await store.upsert_contacts([{"uid": "1", "name": "Nguyễn Văn An"}, {"uid": "2", "name": "B"},
                                     {"uid": "3", "name": "C"}], "group", "g1", "Nhóm 1")
        await _running()

        await zo.tick(check_accepts=False)
        req = _requests(calls)
        # one request per nick, different people, different nicks
        assert len(req) == 2
        assert {r[3] for r in req} == {"n1", "n2"}
        assert len({r[2]["uid"] for r in req}) == 2

        # each nick's own delay hasn't elapsed → nothing more this round
        await zo.tick(check_accepts=False)
        assert len(_requests(calls)) == 2

        # contacts are pinned to the nick that invited them
        pinned = {c["uid"]: c["account_id"] for c in (await store.list_contacts())["items"] if c["account_id"]}
        assert pinned == {r[2]["uid"]: r[3] for r in req}

        # someone accepts → greeting goes out from the nick that invited them, once
        uid, nick = req[0][2]["uid"], req[0][3]
        state["friends"].add(uid)
        await zo.tick(check_accepts=True)
        await zo.tick(check_accepts=True)
        sends = [c for c in calls if c[1] == "/send"]
        assert len(sends) == 1 and sends[0][2]["uid"] == uid and sends[0][3] == nick
        assert [c[3] for c in calls if c[1] == f"/friend-status/{uid}"][-1] == nick
    asyncio.run(run())


def test_new_nick_warmup_cap(fake_bridge):
    calls, state = fake_bridge
    state["nicks"] = {"n1": True}

    async def run():
        await _contacts(20)
        await _running()
        for _ in range(8):
            await _rearm()
            await zo.tick(check_accepts=False)
        acc = await store.get_account("n1")
        assert acc["sent_today"] == zo.WARMUP_START == len(_requests(calls))
        assert acc["first_used"] == datetime.now().strftime("%Y-%m-%d")
    asyncio.run(run())


def test_account_cap_warmup_and_low_accept():
    day = lambda n: (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731
    assert zo.account_cap({"daily_cap": 25})[0] == 5                      # never used
    assert zo.account_cap({"daily_cap": 25, "first_used": day(3)})[0] == 14
    assert zo.account_cap({"daily_cap": 25, "first_used": day(30)}) == (25, "")
    cap, note = zo.account_cap({"daily_cap": 25, "first_used": day(30), "requested_total": 30, "accepted_total": 3})
    assert cap == 12 and "giảm" in note
    # small sample → no penalty yet
    assert zo.account_cap({"daily_cap": 25, "first_used": day(30), "requested_total": 10, "accepted_total": 0})[0] == 25


def test_nick_rests_after_errors_campaign_keeps_running(fake_bridge):
    calls, state = fake_bridge
    state["nicks"] = {"n1": True}
    state["fail_requests"] = True

    async def run():
        await _contacts(10)
        cid = await _running()
        for _ in range(3):
            await _rearm()
            await zo.tick(check_accepts=False)
        acc = await store.get_account("n1")
        assert acc["paused_until"] and "nghỉ 24h" in acc["last_error"]
        assert (await store.get_campaign(cid))["status"] == "running"

        await _rearm()
        await zo.tick(check_accepts=False)
        assert len(_requests(calls)) == 3  # resting nick sends nothing
    asyncio.run(run())


def test_campaign_nick_filter_and_logged_out_nick(fake_bridge):
    calls, state = fake_bridge
    state["nicks"] = {"n1": True, "n2": True, "n3": False}

    async def run():
        await _contacts(5)
        await _running(accounts=["n2", "n3"])
        await zo.tick(check_accepts=False)
        assert [r[3] for r in _requests(calls)] == ["n2"]
    asyncio.run(run())


def test_blocked_contact_never_contacted(fake_bridge):
    calls, state = fake_bridge

    async def run():
        await store.upsert_contacts([{"uid": "x", "name": "Chặn"}, {"uid": "y", "name": "OK"}], "group", "g1", "Nhóm")
        await store.block_contact("x")
        cid = await _running()
        for _ in range(3):
            await _rearm()
            await zo.tick(check_accepts=False)
        assert [r[2]["uid"] for r in _requests(calls)] == ["y"]
        assert (await store.get_campaign(cid))["status"] == "done"
        assert (await store.list_contacts(status="blocked"))["total"] == 1
        await store.block_contact("x", False)
        assert (await store.list_contacts(status="new"))["total"] == 1
    asyncio.run(run())


def test_disabled_nick_sends_nothing(fake_bridge):
    calls, state = fake_bridge

    async def run():
        await store.sync_accounts([{"id": "n1"}, {"id": "n2"}])
        await store.set_account_state("n2", enabled=0)
        await _contacts(10)
        await _running()
        for _ in range(3):
            await _rearm()
            await zo.tick(check_accepts=False)
        assert [r[3] for r in _requests(calls)] == ["n1"] * 3
    asyncio.run(run())


def test_campaign_done_when_no_contacts(fake_bridge):
    async def run():
        cid = await _running(sources=["group:none"])
        await zo.tick(check_accepts=False)
        assert (await store.get_campaign(cid))["status"] == "done"
    asyncio.run(run())


def test_skip_admins_only_touches_uncontacted(fake_bridge):
    async def run():
        await store.upsert_contacts([{"uid": "a1", "name": "Admin"}, {"uid": "a2", "name": "Đã mời"},
                                     {"uid": "m1", "name": "Member"}], "group", "g1", "Nhóm")
        await store.set_contact_status("a2", "requested")
        assert await store.skip_admins(["a1", "a2"]) == 1
        stats = await store.contact_stats()
        assert stats == {"skipped": 1, "requested": 1, "new": 1}
        # campaigns never pick a skipped admin
        nxt = await store.next_new_contact([])
        assert nxt["uid"] == "m1"
    asyncio.run(run())


def test_join_watch_queues_newcomers_first_and_wakes_done_campaign(fake_bridge):
    calls, state = fake_bridge
    state["nicks"] = {"n1": True}

    async def run():
        await _contacts(3)  # older scraped contacts already waiting
        cid = await _running(sources=["group:g1"])
        await store.set_campaign_state(cid, status="done")
        await store.save_watch("g9", "Nhóm 9", "n1", cid)
        # route behaviour: a source-limited campaign gets the newcomers source
        c = await store.get_campaign(cid)
        await store.update_campaign(cid, {**c, "sources": c["sources"] + ["join:g9"]})

        state["joins"] = [{"account": "n1", "group_id": "g9", "group_name": "Nhóm 9",
                           "members": [{"uid": "new1", "name": "Người mới"}]},
                          {"account": "n1", "group_id": "other", "members": [{"uid": "x", "name": "X"}]}]
        await zo.tick(check_accepts=False)

        assert "n1" in state["listening"]
        assert (await store.get_campaign(cid))["status"] == "running"
        req = _requests(calls)
        assert len(req) == 1 and req[0][2]["uid"] == "new1"   # newcomer jumps the queue
        w = (await store.list_watches())[0]
        assert w["joined_count"] == 1 and w["last_join_at"]
        uids = {c["uid"] for c in (await store.list_contacts())["items"]}
        assert "x" not in uids                                  # unwatched group ignored

        # paused campaigns are left alone
        await store.set_campaign_state(cid, status="paused")
        state["joins"] = [{"group_id": "g9", "members": [{"uid": "new2", "name": "B"}]}]
        await zo.tick(check_accepts=False)
        assert (await store.get_campaign(cid))["status"] == "paused"
    asyncio.run(run())


def test_watch_alone_keeps_worker_listening(fake_bridge):
    calls, state = fake_bridge
    state["nicks"] = {"n1": True}

    async def run():
        cid = await store.create_campaign(_campaign())  # never started
        await store.save_watch("g9", "Nhóm 9", None, cid)
        await zo.tick(check_accepts=False)
        assert any(c[1] == "/listen" for c in calls) and any(c[1] == "/joins" for c in calls)
        await store.save_watch("g9", "Nhóm 9", None, cid, enabled=False)
        calls.clear()
        await zo.tick(check_accepts=False)
        assert not any(c[1] in ("/listen", "/joins") for c in calls)
    asyncio.run(run())
