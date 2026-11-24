"""WhatsApp outreach: contact identity merging, and the multi-number campaign
worker (opener → reply → second message) against a fake bridge."""
import asyncio
import time
from datetime import datetime, timedelta

import pytest

import wa_outreach as wo
import wa_store as store


def _lid(n) -> str:
    return f"{n}@lid"


@pytest.fixture
def fake_bridge(monkeypatch):
    calls = []  # (method, path, json, account)
    state = {"nums": {"n1": True, "n2": True}, "fail_sends": False, "events": {}, "restricted": {}}

    async def fake_call(method, path, json=None, timeout=60, account=None):
        calls.append((method, path, json, account))
        if path == "/status":
            accs = [{"id": i, "logged_in": ok, "login": {"state": "logged_in" if ok else "idle"},
                     "me": {"jid": f"8490000{i[1:]}@s.whatsapp.net", "lid": _lid(f"own{i}"),
                            "phone": f"8490000{i[1:]}", "name": f"Số {i}"},
                     "restricted_until": state["restricted"].get(i)} for i, ok in state["nums"].items()]
            return {"logged_in": any(state["nums"].values()), "accounts": accs}
        if path == "/send":
            if state["fail_sends"]:
                raise wo.BridgeError("WhatsApp error")
            return {"ok": True, "msg_id": "m"}
        if path == "/events":
            ev, state["events"] = state["events"], {}
            return ev
        raise AssertionError(path)

    monkeypatch.setattr(wo, "call", fake_call)
    monkeypatch.setattr(wo, "_in_hours", lambda c: True)
    monkeypatch.setattr(wo, "FOLLOWUP_DELAY_SEC", (0, 0))
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(wo.asyncio, "sleep", lambda *_: _real_sleep(0))
    store._schema_ready = False  # conftest drops tables between tests
    return calls, state


def _campaign(**kw):
    base = dict(name="t", sources=[], accounts=[], countries=[], open_msg="Hi {name}, quick question?",
                reply_msg="Thanks {name}! Here it is.", daily_limit=100, min_delay_sec=60, max_delay_sec=60,
                start_hour=0, end_hour=24)
    base.update(kw)
    return base


def _sends(calls):
    return [c for c in calls if c[1] == "/send"]


async def _running(**kw) -> int:
    cid = await store.create_campaign(_campaign(**kw))
    await store.set_campaign_state(cid, status="running")
    return cid


async def _rearm():
    """Pretend every number's random delay has elapsed."""
    for a in await store.list_accounts():
        await store.set_account_state(a["id"], next_action_at=None)


def _people(n: int, prefix="63917") -> list[dict]:
    return [{"uid": _lid(i), "lid": _lid(i), "phone": f"{prefix}{i:04d}", "name": ""} for i in range(n)]


async def _contacts(n: int, **kw):
    await store.upsert_contacts(_people(n, **kw), "group", "g1", "Nhóm 1")


async def _all() -> dict:
    return {c["uid"]: c for c in (await store.list_contacts())["items"]}


def test_one_person_two_ids_is_one_contact(fake_bridge):
    async def run():
        pn = {"uid": "84901@s.whatsapp.net", "lid": "", "phone": "84901", "name": ""}
        hidden = {"uid": _lid("L1"), "lid": _lid("L1"), "phone": "", "name": ""}
        both = {"uid": _lid("L1"), "lid": _lid("L1"), "phone": "84901", "name": "An"}
        r1 = await store.upsert_contacts([pn], "group", "g1", "Nhóm 1")       # phone-addressed group
        r2 = await store.upsert_contacts([hidden], "group", "g2", "Nhóm 2")   # hides their number there
        assert (r1["new"], r2["new"]) == (1, 1)                                # can't know yet it's one person
        r3 = await store.upsert_contacts([both], "group", "g3", "Nhóm 3")     # both ids → the rows fold
        assert r3["new"] == 0
        rows = await _all()
        assert len(rows) == 1
        c = next(iter(rows.values()))
        assert (c["lid"], c["phone"], c["name"]) == (_lid("L1"), "84901", "An")
        assert {s["source_id"]: s["total"] for s in await store.list_sources()} == {"g1": 1, "g2": 1, "g3": 1}
    asyncio.run(run())


def test_own_numbers_are_never_contacts(fake_bridge):
    calls, state = fake_bridge

    async def run():
        await wo._sync_accounts()
        got = await store.upsert_contacts(
            [{"uid": _lid("ownn1"), "lid": _lid("ownn1"), "phone": ""},      # our n1, number hidden
             {"uid": "84900002@s.whatsapp.net", "lid": "", "phone": "84900002"},  # our n2 by phone
             *_people(1)], "group", "g1", "Nhóm 1")
        assert got == {"total": 1, "new": 1}
    asyncio.run(run())


def test_numbers_share_contacts_then_followup_from_pinned_number(fake_bridge):
    calls, state = fake_bridge

    async def run():
        await _contacts(3)
        await _running()
        await wo.tick()
        sent = _sends(calls)
        # one opener per number, different people; sent to the LID, with an empty name leaving no gap
        assert len(sent) == 2 and {s[3] for s in sent} == {"n1", "n2"}
        assert len({s[2]["jid"] for s in sent}) == 2
        assert all(s[2]["message"] == "Hi, quick question?" for s in sent)

        await wo.tick()  # each number's own delay hasn't elapsed
        assert len(_sends(calls)) == 2

        jid, num = sent[0][2]["jid"], sent[0][3]
        other = "n2" if num == "n1" else "n1"
        # a reply seen by the wrong number is not ours to answer; the right one arrives by phone jid
        phone_jid = f"{(await _all())[jid]['phone']}@s.whatsapp.net"
        state["events"] = {"replies": [{"account": other, "jid": jid, "alt_jid": "", "name": "X", "ts": int(time.time())}]}
        await wo.tick()
        assert (await _all())[jid]["status"] == "sent"
        state["events"] = {"replies": [{"account": num, "jid": phone_jid, "alt_jid": jid, "name": "Maria Cruz",
                                        "ts": int(time.time())}]}
        await wo.tick()
        await wo.tick()
        follow = [s for s in _sends(calls) if s[2]["message"].startswith("Thanks")]
        assert len(follow) == 1 and follow[0][3] == num and follow[0][2] == {"jid": jid, "message": "Thanks Maria! Here it is."}
        assert (await _all())[jid]["status"] == "followed"
    asyncio.run(run())


def test_owner_answering_by_hand_stops_the_second_message(fake_bridge):
    calls, state = fake_bridge
    state["nums"] = {"n1": True}

    async def run():
        await _contacts(1)
        await _running()
        await wo.tick()
        jid = _sends(calls)[0][2]["jid"]
        now = int(time.time())
        state["events"] = {"replies": [{"account": "n1", "jid": jid, "alt_jid": "", "name": "A", "ts": now}],
                           "manual": [{"account": "n1", "jid": jid, "alt_jid": ""}]}
        await wo.tick()
        await wo.tick()
        assert (await _all())[jid]["status"] == "handled"
        assert len(_sends(calls)) == 1
    asyncio.run(run())


def test_stale_reply_gets_no_second_message(fake_bridge):
    calls, state = fake_bridge
    state["nums"] = {"n1": True}

    async def run():
        await _contacts(2)
        await _running()
        await wo.tick()
        await _rearm()
        await wo.tick()
        a, b = [s[2]["jid"] for s in _sends(calls)]
        # a: the bridge was offline, the reply is three days old when it finally arrives
        old = int((datetime.now() - timedelta(days=3)).timestamp())
        # b: replied in time, but the number then sat disconnected for longer than the window
        state["events"] = {"replies": [{"account": "n1", "jid": a, "alt_jid": "", "name": "", "ts": old},
                                       {"account": "n1", "jid": b, "alt_jid": "", "name": "", "ts": int(time.time())}]}
        state["nums"] = {"n1": True}
        monkey_followups = wo._followups

        async def no_followups(connected):  # b stays 'replied' this round
            return None
        wo._followups = no_followups
        try:
            await wo.tick()
        finally:
            wo._followups = monkey_followups
        await store.set_contact_status(b, "replied")
        async with store.db.get_db() as conn:
            await conn.execute("UPDATE wa_contacts SET replied_at = ? WHERE uid = ?",
                               ((datetime.now() - timedelta(hours=50)).strftime("%Y-%m-%d %H:%M:%S"), b))
            await conn.commit()
        await wo.tick()
        rows = await _all()
        assert rows[a]["status"] == rows[b]["status"] == "handled"
        assert len(_sends(calls)) == 2  # only the two openers
    asyncio.run(run())


def test_warmup_cap_and_account_cap_maths(fake_bridge):
    calls, state = fake_bridge
    state["nums"] = {"n1": True}
    day = lambda n: (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731
    assert wo.account_cap({"daily_cap": 20})[0] == 5
    assert wo.account_cap({"daily_cap": 20, "first_used": day(3)})[0] == 11
    assert wo.account_cap({"daily_cap": 20, "first_used": day(30)}) == (20, "")

    async def run():
        await _contacts(20)
        await _running()
        for _ in range(8):
            await _rearm()
            await wo.tick()
        acc = await store.get_account("n1")
        assert acc["sent_today"] == wo.WARMUP_START == len(_sends(calls))
    asyncio.run(run())


def test_number_rests_after_errors_and_after_a_refused_send(fake_bridge):
    calls, state = fake_bridge

    async def run():
        await _contacts(10)
        cid = await _running(accounts=["n1"])
        state["fail_sends"] = True
        for _ in range(3):
            await _rearm()
            await wo.tick()
        acc = await store.get_account("n1")
        assert acc["paused_until"] and "nghỉ 24h" in acc["last_error"]
        assert (await store.get_campaign(cid))["status"] == "running"

        # n2 sends fine, then WhatsApp refuses that message after the fact (restricted number)
        state["fail_sends"] = False
        await store.update_campaign(cid, _campaign(accounts=["n2"]))
        await wo.tick()
        jid = _sends(calls)[-1][2]["jid"]
        assert (await _all())[jid]["status"] == "sent"
        state["events"] = {"failed": [{"account": "n2", "jid": jid, "code": "463"}]}
        await _rearm()
        await wo.tick()
        assert (await _all())[jid]["status"] == "failed"
        n2 = await store.get_account("n2")
        assert n2["paused_until"] and "463" in n2["last_error"]
        before = len(_sends(calls))
        await _rearm()
        await wo.tick()
        assert len(_sends(calls)) == before  # both numbers resting
    asyncio.run(run())


def test_restricted_number_rests_until_whatsapp_lifts_it(fake_bridge):
    calls, state = fake_bridge
    state["nums"] = {"n1": True}
    state["restricted"] = {"n1": (datetime.now().astimezone() + timedelta(hours=6)).isoformat()}

    async def run():
        await _contacts(3)
        await _running()
        await wo.tick()
        assert not _sends(calls)
        acc = await store.get_account("n1")
        assert acc["paused_until"] > datetime.now().strftime("%Y-%m-%d %H:%M:%S") and "hạn chế" in acc["last_error"]
    asyncio.run(run())


def test_country_filter_skips_other_countries_and_hidden_numbers(fake_bridge):
    calls, state = fake_bridge
    state["nums"] = {"n1": True}

    async def run():
        await store.upsert_contacts(
            [{"uid": _lid("vn"), "lid": _lid("vn"), "phone": "84911000001"},
             {"uid": _lid("ph"), "lid": _lid("ph"), "phone": "639170000001"},
             {"uid": _lid("hid"), "lid": _lid("hid"), "phone": ""}], "group", "g1", "Nhóm 1")
        cid = await _running(countries=["63"])
        for _ in range(3):
            await _rearm()
            await wo.tick()
        assert [s[2]["jid"] for s in _sends(calls)] == [_lid("ph")]
        assert (await store.get_campaign(cid))["status"] == "done"
    asyncio.run(run())


def test_blocked_admin_disabled_and_disconnected(fake_bridge):
    calls, state = fake_bridge
    state["nums"] = {"n1": True, "n2": False}

    async def run():
        await _contacts(3)
        await store.block_contact(_lid(0))
        assert await store.skip_admins([{"uid": "x", "lid": "", "phone": "639170001"}]) == 1  # matched by phone
        await _running(accounts=["n2"])     # only the disconnected number is allowed
        await wo.tick()
        assert not _sends(calls)
        await store.update_campaign(1, _campaign())
        await wo._sync_accounts()
        await store.set_account_state("n1", enabled=0)
        await wo.tick()
        assert not _sends(calls)            # disabled
        await store.set_account_state("n1", enabled=1)
        await wo.tick()
        assert [s[2]["jid"] for s in _sends(calls)] == [_lid(2)]
        rows = await _all()
        assert (rows[_lid(0)]["status"], rows[_lid(1)]["status"]) == ("blocked", "skipped")
    asyncio.run(run())


def test_internal_marker_never_goes_out(fake_bridge):
    calls, state = fake_bridge
    state["nums"] = {"n1": True}

    async def run():
        await _contacts(2)
        cid = await _running(open_msg="[HANDOVER_REQUIRED]")
        await wo.tick()
        c = await store.get_campaign(cid)
        assert not _sends(calls) and c["status"] == "paused" and "chặn" in c["last_error"]
    asyncio.run(run())


def test_watch_queues_newcomers_first_and_collects_active_speakers(fake_bridge):
    calls, state = fake_bridge
    state["nums"] = {"n1": True}

    async def run():
        await _contacts(3)
        cid = await _running(sources=["group:g1", "join:g9"])
        await store.set_campaign_state(cid, status="done")
        await store.save_watch("g9", "Nhóm 9", "n1", cid, watch_joins=True, track_active=False)
        await store.save_watch("g8", "Nhóm 8", "n1", None, watch_joins=False, track_active=True)
        new = {"uid": _lid("new1"), "lid": _lid("new1"), "phone": "63900", "name": ""}
        state["events"] = {
            "joins": [{"account": "n1", "group_id": "g9", "members": [new]},
                      {"account": "n1", "group_id": "g8", "members": [_people(1, prefix="1")[0] | {"uid": _lid("j8"), "lid": _lid("j8")}]}],
            "speakers": [{"account": "n1", "group_id": "g8", "uid": _lid("sp"), "lid": _lid("sp"), "phone": "", "name": "Sp"},
                         {"account": "n1", "group_id": "g9", "uid": _lid("sp9"), "lid": _lid("sp9"), "phone": "", "name": ""}],
        }
        await wo.tick()

        assert [c for c in calls if c[1] == "/events"][0][2] == {"tracked": {"g9": "n1", "g8": "n1"}}
        assert (await store.get_campaign(cid))["status"] == "running"
        assert [s[2]["jid"] for s in _sends(calls)] == [_lid("new1")]            # newcomer jumps the queue
        rows = await _all()
        assert _lid("j8") not in rows and _lid("sp9") not in rows                # g8 isn't watching joins, g9 isn't tracking
        watches = {w["group_id"]: w for w in await store.list_watches()}
        assert watches["g9"]["joined_count"] == 1 and watches["g8"]["active_count"] == 1
        assert {(s["source_type"], s["source_id"]) for s in await store.list_sources()} >= {("join", "g9"), ("active", "g8")}

        # a paused campaign is left alone
        await store.set_campaign_state(cid, status="paused")
        state["events"] = {"joins": [{"account": "n1", "group_id": "g9", "members": [new | {"uid": _lid("n2"), "lid": _lid("n2")}]}]}
        await wo.tick()
        assert (await store.get_campaign(cid))["status"] == "paused"
    asyncio.run(run())


def test_idle_worker_does_not_touch_the_bridge(fake_bridge):
    calls, state = fake_bridge

    async def run():
        await _contacts(2)
        await store.create_campaign(_campaign())  # never started
        await wo.tick()
        assert not calls
    asyncio.run(run())


def test_render_handles_missing_names():
    assert wo._render("Hi {name}, ok?", {"name": ""}) == "Hi, ok?"
    assert wo._render("Hi {name}, ok?", {"name": "Maria Cruz"}) == "Hi Maria, ok?"
    assert wo._render("{full_name} !", {"name": "Maria Cruz"}) == "Maria Cruz!"
