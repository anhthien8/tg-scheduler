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


async def test_no_scripts_runs_passive_actions_instead_of_stopping(fake_env):
    # Used to stop silently right after "Chạy" — day 1-2 warmup needs no scripts.
    db, tg = fake_env
    db.get_warmup_scripts = AsyncMock(return_value=[])
    seen = []

    async def fake_exec(acc_id, behavior, *a, **k):
        seen.append(behavior)
        we._active_jobs[1] = False
        return True, None

    with patch.object(we, "_execute_behavior", fake_exec), patch.object(we.asyncio, "sleep", AsyncMock()):
        await we.run_warmup_job(1)
    db.update_warmup_job_status.assert_any_await(1, "running")
    assert seen and seen[0] in we.PASSIVE_WEIGHTS
    tg.send_text_message.assert_not_awaited()


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


def test_in_window_handles_midnight():
    assert we.in_window("10:00", "09:00", "22:00")
    assert not we.in_window("22:30", "09:00", "22:00")
    assert we.in_window("23:30", "22:00", "02:00") and we.in_window("01:00", "22:00", "02:00")
    assert not we.in_window("12:00", "22:00", "02:00")


async def test_post_counter_resets_on_new_day(fake_env):
    db, tg = fake_env
    stale = _job(posts_today=5, daily_post_limit=5, last_post_at="2020-01-01T10:00:00")

    async def stop_after_first(*a, **k):
        we._active_jobs[1] = False

    tg.send_text_message = AsyncMock(side_effect=stop_after_first)
    db.get_warmup_job = AsyncMock(side_effect=[stale, dict(stale)])
    with patch.object(we.asyncio, "sleep", AsyncMock()):
        await we.run_warmup_job(1)
    db.update_warmup_job_status.assert_any_await(1, "running", posts_today=0)
    tg.send_text_message.assert_awaited_once()  # limit no longer blocks the new day


async def test_resume_only_restarts_running_jobs(monkeypatch):
    db = types.ModuleType("database")
    db.get_warmup_jobs = AsyncMock(return_value=[{"id": 1, "status": "running"}, {"id": 2, "status": "stopped"}])
    monkeypatch.setattr(we, "db", db)
    started = []

    async def fake_run(job_id, initial_delay=0):
        started.append((job_id, initial_delay))

    monkeypatch.setattr(we, "run_warmup_job", fake_run)
    assert await we.resume_running_jobs(initial_delay=5) == 1
    await asyncio.gather(*we._tasks.values())
    assert started == [(1, 5)]
    we._tasks.clear()


async def test_ensure_member_joins_when_not_participant(monkeypatch):
    from telethon import errors as tl_errors
    from telethon.tl.functions.channels import GetParticipantRequest, JoinChannelRequest

    calls = []

    class FakeClient:
        async def get_input_entity(self, peer):
            return "ENT"

        async def __call__(self, req):
            calls.append(type(req))
            if isinstance(req, GetParticipantRequest):
                raise tl_errors.UserNotParticipantError(request=None)
            return True

    tg = types.ModuleType("telegram_client")
    tg.get_client = lambda acc_id: FakeClient()
    monkeypatch.setattr(we, "tg", tg)
    assert await we._ensure_member(7, "SomeGroup") == (True, "joined")
    assert calls == [GetParticipantRequest, JoinChannelRequest]


def test_peer_prefers_username():
    assert we._peer({"chat_id": "-1001816192742", "chat_username": "BloFin_Official"}) == "BloFin_Official"
    assert we._peer({"chat_id": "-1001816192742", "chat_username": ""}) == -1001816192742


def test_start_route_warns_about_missing_scripts(client, monkeypatch):
    gid = client.post("/api/warmup/groups", json={"name": "G", "chat_id": "-100555", "chat_username": "g"}).json()["id"]
    jid = client.post("/api/warmup/jobs", json={"group_id": gid, "account_ids": [991], "ai_write": False}).json()["id"]
    started = []
    monkeypatch.setattr(we, "start_job", lambda job_id, initial_delay=0: started.append(job_id) or True)
    import telegram_client
    monkeypatch.setattr(telegram_client, "get_client", lambda acc_id: None)
    r = client.post(f"/api/warmup/jobs/{jid}/start")
    assert r.status_code == 200 and started == [jid]
    w = " ".join(r.json()["warnings"])
    assert "kịch bản" in w and "chưa kết nối" in w
    jobs = client.get("/api/warmup/jobs").json()["jobs"]
    assert jobs[0]["scripts_count"] == 0 and "state" in jobs[0]


# ── v3.21: ✨ AI tự viết + ⚡ 1-click ─────────────────────────────────────

def test_check_ai_text_filters():
    assert we._check_ai_text("", []) == "AI không trả lời"
    assert "chưa có gì" in we._check_ai_text("<SKIP>", [])
    for bad in ("join t.me/xyz", "ask @admin", "#bitcoin to the moon", "use my referral code", "www.x.io"):
        assert "link" in we._check_ai_text(bad, []), bad
    assert "dài" in we._check_ai_text("word " * 40, [])
    assert "giống" in we._check_ai_text("gm everyone nice day", ["GM everyone, nice day!"])
    assert we._check_ai_text("anyone tried the new copy trading?", ["gm"]) is None


def _msg(sid, text, bot=False, action=None):
    return types.SimpleNamespace(message=text, sender_id=sid, action=action, via_bot_id=None,
                                 sender=types.SimpleNamespace(bot=bot))


async def test_group_context_skips_own_accounts_and_bots(monkeypatch):
    # Newest first, like Telethon get_messages.
    msgs = [_msg(5, "newest from real user"), _msg(777, "our own account talking"),
            _msg(6, "bot spam", bot=True), _msg(7, ""), _msg(8, "x joined", action=object()),
            _msg(9, "oldest real")]
    client = types.SimpleNamespace(get_messages=AsyncMock(return_value=msgs))
    tg = types.ModuleType("telegram_client")
    tg.get_client = lambda acc_id: client
    tg._me_cache = {}
    monkeypatch.setattr(we, "tg", tg)
    db = types.ModuleType("database")
    db.get_all_internal_telegram_user_ids = AsyncMock(return_value=[777])
    monkeypatch.setattr(we, "db", db)
    monkeypatch.setattr(we, "_self_ids", set())
    monkeypatch.setattr(we, "_self_ids_at", 0.0)
    assert await we._group_context(1, "g") == ["oldest real", "newest from real user"]


async def test_group_context_fails_closed_when_own_ids_unknown(monkeypatch):
    client = types.SimpleNamespace(get_messages=AsyncMock(return_value=[_msg(5, "hi")]))
    tg = types.ModuleType("telegram_client")
    tg.get_client = lambda acc_id: client
    tg._me_cache = {}
    monkeypatch.setattr(we, "tg", tg)
    db = types.ModuleType("database")
    db.get_all_internal_telegram_user_ids = AsyncMock(return_value=[])
    monkeypatch.setattr(we, "db", db)
    monkeypatch.setattr(we, "_self_ids", set())
    monkeypatch.setattr(we, "_self_ids_at", 0.0)
    assert await we._group_context(1, "g") == []
    client.get_messages.assert_not_awaited()


async def test_ai_group_message_needs_ai_config(monkeypatch):
    import message_queue
    monkeypatch.setattr(message_queue, "_remix_config", AsyncMock(return_value=None))
    text, why = await we._ai_group_message(1, "g", 1, {})
    assert text is None and "Cài đặt" in why


async def test_ai_group_message_cleans_and_never_repeats(monkeypatch):
    import ai_remix
    import message_queue
    monkeypatch.setattr(message_queue, "_remix_config", AsyncMock(
        return_value={"provider": "p", "keys": ["k"], "instruction": "", "kwargs": {}}))
    monkeypatch.setattr(we, "_group_context", AsyncMock(return_value=["is the app down?"]))
    gen = AsyncMock(return_value='"anyone else trading eth today"')
    monkeypatch.setattr(ai_remix, "_generate_chat_response_raw", gen)
    we._recent_ai.pop(77, None)
    text, why = await we._ai_group_message(1, "g", 77, {"chat_title": "BloFin"})
    assert text == "anyone else trading eth today" and why == ""
    prompt_user = gen.await_args.args[0][0]["content"]
    assert "is the app down?" in prompt_user and "BloFin" in gen.await_args.args[1]
    # Same line again -> rejected as a repeat, and the AI saw its own last post.
    text2, why2 = await we._ai_group_message(1, "g", 77, {"chat_title": "BloFin"})
    assert text2 is None and "giống" in why2
    assert "anyone else trading eth today" in gen.await_args.args[0][0]["content"]
    we._recent_ai.pop(77, None)


async def test_ai_write_posts_ai_text_without_scripts(fake_env, monkeypatch):
    db, tg = fake_env
    db.get_warmup_job = AsyncMock(side_effect=[_job(ai_write=1), _job(ai_write=1, posts_today=1)])
    db.get_warmup_scripts = AsyncMock(return_value=[])
    tg.get_client = lambda acc_id: object()

    async def stop_after_first(*a, **k):
        we._active_jobs[1] = False

    tg.send_text_message = AsyncMock(side_effect=stop_after_first)
    monkeypatch.setattr(we, "_ensure_member", AsyncMock(return_value=(True, "member")))
    ai = AsyncMock(return_value=("gm, anyone trading today?", ""))
    monkeypatch.setattr(we, "_ai_group_message", ai)
    with patch.object(we.asyncio, "sleep", AsyncMock()):
        await we.run_warmup_job(1)
    ai.assert_awaited()
    assert tg.send_text_message.await_args.args[2] == "gm, anyone trading today?"
    assert db.add_warmup_log.await_args.args[3] is None  # no script behind an AI post
    assert db.add_warmup_log.await_args.args[5] == "success"


async def test_ai_skip_without_scripts_falls_back_to_passive(fake_env, monkeypatch):
    db, tg = fake_env
    db.get_warmup_job = AsyncMock(return_value=_job(ai_write=1))
    db.get_warmup_scripts = AsyncMock(return_value=[])
    tg.get_client = lambda acc_id: object()
    monkeypatch.setattr(we, "_ensure_member", AsyncMock(return_value=(True, "member")))
    monkeypatch.setattr(we, "_ai_group_message", AsyncMock(return_value=(None, "AI thấy chưa có gì hợp để nói")))
    seen = []

    async def fake_exec(acc_id, behavior, *a, **k):
        seen.append((behavior, k.get("ai_text")))
        we._active_jobs[1] = False
        return True, None

    with patch.object(we, "_execute_behavior", fake_exec), patch.object(we.asyncio, "sleep", AsyncMock()):
        await we.run_warmup_job(1)
    assert seen and seen[0][0] in we.PASSIVE_WEIGHTS and seen[0][1] is None
    tg.send_text_message.assert_not_awaited()


async def test_ai_off_uses_scripts_and_never_calls_ai(fake_env, monkeypatch):
    db, tg = fake_env
    db.get_warmup_job = AsyncMock(side_effect=[_job(ai_write=0), _job(ai_write=0, posts_today=1)])
    tg.get_client = lambda acc_id: object()

    async def stop_after_first(*a, **k):
        we._active_jobs[1] = False

    tg.send_text_message = AsyncMock(side_effect=stop_after_first)
    monkeypatch.setattr(we, "_ensure_member", AsyncMock(return_value=(True, "member")))
    ai = AsyncMock(return_value=("never", ""))
    monkeypatch.setattr(we, "_ai_group_message", ai)
    with patch.object(we.asyncio, "sleep", AsyncMock()):
        await we.run_warmup_job(1)
    ai.assert_not_awaited()
    assert tg.send_text_message.await_args.args[2] == "hi"


# ── 1-click routes (real test DB, fake Telegram from conftest) ──

async def _acc(name):
    import database as dbm
    n = len(await dbm.get_all_accounts()) + 1
    aid = await dbm.create_account({"name": name, "phone": f"+8400000{n:05d}", "api_id": "1",
                                    "api_hash": "h", "session_name": f"wu_{n}"})
    await dbm.update_account_login_status(aid, True)
    return aid


async def _blofin_setup(monkeypatch):
    import database as dbm
    import message_queue
    started = []
    monkeypatch.setattr(we, "start_job", lambda job_id, initial_delay=0: started.append(job_id) or True)
    monkeypatch.setattr(message_queue, "_remix_config", AsyncMock(
        return_value={"provider": "p", "keys": ["k"], "instruction": "", "kwargs": {}}))
    a1 = await _acc("Linh | BloFin")
    a2 = await _acc("Coy | Weex")
    a3 = await _acc("Plain")
    a4 = await _acc("Napped")
    await dbm.pause_account(a4, "test")
    gid = await dbm.create_warmup_group({"name": "BloFin Exchange", "chat_id": "-100777",
                                         "chat_title": "BloFin Exchange", "chat_username": "BloFin_Official"})
    return started, gid, (a1, a2, a3, a4)


async def test_eligible_accounts_skip_paused_and_other_brands(monkeypatch):
    from routes import warmup as rw
    _, gid, (a1, a2, a3, a4) = await _blofin_setup(monkeypatch)
    r = await rw.eligible_accounts(group_id=None, link="https://t.me/BloFin_Official")
    by = {a["id"]: a for a in r["accounts"]}
    assert r["picked"] == [a1, a3]
    assert by[a1]["brand_match"] and by[a2]["other_brand"] and not by[a2]["pick"]
    assert not by[a4]["ok"] and by[a4]["why"] == "tạm dừng"


async def test_one_click_creates_job_then_reuses_it(monkeypatch):
    import database as dbm
    from routes import warmup as rw
    started, gid, (a1, a2, a3, a4) = await _blofin_setup(monkeypatch)
    r = await rw.one_click(rw.OneClick(link="@BloFin_Official"))
    assert r["group"]["id"] == gid and r["group_created"] is False
    assert [a["id"] for a in r["accounts"]] == [a1, a3] and started == [r["job_id"]]
    assert (await dbm.get_warmup_job(r["job_id"]))["ai_write"] == 1
    assert not any("AI" in w for w in r["warnings"])
    # ⚡ on the group row: same job, no duplicate.
    r2 = await rw.one_click(rw.OneClick(group_id=gid))
    assert r2["job_id"] == r["job_id"] and len(await dbm.get_warmup_jobs(group_id=gid)) == 1
    # Hand-picked list: unhealthy accounts are skipped with a reason.
    r3 = await rw.one_click(rw.OneClick(group_id=gid, account_ids=[a2, a4]))
    assert [a["id"] for a in r3["accounts"]] == [a2]
    assert r3["skipped"][0]["id"] == a4 and r3["skipped"][0]["why"] == "tạm dừng"
    assert json_ids(await dbm.get_warmup_job(r["job_id"])) == [a2]


def json_ids(job):
    import json
    return json.loads(job["account_ids"])


async def test_one_click_without_healthy_accounts_is_400(monkeypatch):
    import database as dbm
    from fastapi import HTTPException
    from routes import warmup as rw
    monkeypatch.setattr(we, "start_job", lambda job_id, initial_delay=0: True)
    a = await _acc("Only One")
    await dbm.pause_account(a, "test")
    gid = await dbm.create_warmup_group({"name": "G", "chat_id": "-100888", "chat_username": "g888"})
    with pytest.raises(HTTPException) as ei:
        await rw.one_click(rw.OneClick(group_id=gid))
    assert ei.value.status_code == 400
    assert await dbm.get_warmup_jobs(group_id=gid) == []


async def test_patch_job_toggles_ai_and_start_warns_without_ai(monkeypatch):
    import database as dbm
    import message_queue
    from routes import warmup as rw
    gid = await dbm.create_warmup_group({"name": "G", "chat_id": "-100999", "chat_username": "g999"})
    jid = await dbm.create_warmup_job({"group_id": gid, "account_ids": [1]})
    assert (await dbm.get_warmup_job(jid))["ai_write"] == 1  # AI writes by default
    monkeypatch.setattr(message_queue, "_remix_config", AsyncMock(return_value=None))
    w = " ".join(await rw._start_warnings(await dbm.get_warmup_job(jid)))
    assert "Chưa cấu hình AI" in w and "kịch bản" not in w
    await rw.patch_job(jid, rw.JobPatch(ai_write=False))
    assert (await dbm.get_warmup_job(jid))["ai_write"] == 0
    w = " ".join(await rw._start_warnings(await dbm.get_warmup_job(jid)))
    assert "kịch bản" in w and "Chưa cấu hình AI" not in w
