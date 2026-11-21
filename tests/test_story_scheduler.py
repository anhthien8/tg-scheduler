"""v3.39 Story scheduler: bulk story posts + internal nicks viewing each other."""
import asyncio
import json
import io
import os
import random
import time

import pytest
from telethon.errors import FloodWaitError

import database as db
import story_engine as se


# ── Pure helpers ─────────────────────────────────────────────────────────────

def test_media_type_for():
    assert se.media_type_for("a.JPG") == "photo"
    assert se.media_type_for("b.webp") == "photo"
    assert se.media_type_for("c.mp4") == "video"
    assert se.media_type_for("d.pdf") is None
    assert se.media_type_for("") is None


def test_plan_post_times_staggers_each_nick():
    plan = se.plan_post_times(1000.0, [5, 6, 7], 10, rng=random.Random(1))
    assert [a for a, _ in plan] == [5, 6, 7]
    assert plan[0][1] == 1000.0                       # first nick exactly on time
    for i, (_, ts) in enumerate(plan[1:], start=1):
        assert 1000 + i * 600 <= ts <= 1000 + i * 600 + 120   # + up to 20% jitter


def test_plan_post_times_zero_stagger():
    plan = se.plan_post_times(50.0, [1, 2], 0)
    assert [ts for _, ts in plan] == [50.0, 50.0]


def test_plan_view_times_excludes_poster_and_stays_in_window():
    plan = se.plan_view_times(0.0, [1, 2, 3, 4], 2, 3, 45, rng=random.Random(7))
    assert sorted(v for v, _ in plan) == [1, 3, 4]
    assert all(180 <= ts <= 2700 for _, ts in plan)


def test_unsafe_reason():
    now = time.time()
    ok = {"is_logged_in": 1}
    assert se.unsafe_reason(ok, now) is None
    assert se.unsafe_reason(None) == "không tìm thấy nick"
    assert se.unsafe_reason({"is_logged_in": 0}) == "chưa đăng nhập"
    assert se.unsafe_reason({"is_logged_in": 1, "is_paused": 1}) == "đang tạm dừng"
    assert se.unsafe_reason({"is_logged_in": 1, "is_flagged": 1}) == "bị flag"
    assert se.unsafe_reason({"is_logged_in": 1, "peerflood_until": now + 60}, now) == "đang PeerFlood"


# ── Fixtures ─────────────────────────────────────────────────────────────────

async def _make_accounts(n=3):
    ids = []
    for i in range(n):
        aid = await db.create_account({"name": f"Nick {i}", "phone": f"+8400{i}", "api_id": "1",
                                       "api_hash": "h", "session_name": f"story_{i}_{time.time_ns()}"})
        await db.update_account_login_status(aid, True)
        ids.append(aid)
    return ids


@pytest.fixture
def media_file():
    os.makedirs(se.MEDIA_DIR, exist_ok=True)
    name = f"story_test_{time.time_ns()}.jpg"
    path = os.path.join(se.MEDIA_DIR, name)
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff fake jpg")
    yield name
    os.remove(path)


class _Updates:
    def __init__(self, sid):
        from telethon.tl.types import UpdateStoryID
        self.updates = [UpdateStoryID(id=sid, random_id=1)]


class FakeClient:
    """Records Telegram requests; SendStory returns a new story id."""
    _next = 100

    def __init__(self, aid, fail=None):
        self.aid, self.fail, self.calls = aid, fail, []

    async def upload_file(self, path):
        return object()

    async def get_input_entity(self, x):
        return ("peer", x)

    async def __call__(self, req):
        name = type(req).__name__
        self.calls.append(name)
        if self.fail and name in self.fail:
            raise self.fail[name]
        if name == "SendStoryRequest":
            FakeClient._next += 1
            return _Updates(FakeClient._next)
        return True


@pytest.fixture
def fake_tg(monkeypatch):
    import telegram_client as tg
    clients = {}

    def get_client(aid):
        return clients.setdefault(aid, FakeClient(aid))

    async def get_me(aid, *a, **k):
        return {"user_id": 5000 + aid, "username": f"nick{aid}"}

    async def no_pause(lo, hi):
        return None

    monkeypatch.setattr(tg, "get_client", get_client)
    monkeypatch.setattr(tg, "get_me", get_me)
    monkeypatch.setattr(se, "_pause", no_pause)
    se._last_view_at.clear()
    se._peer_cache.clear()
    return clients


async def _rows(sql, *args):
    async with db.get_db() as conn:
        return await (await conn.execute(sql, args)).fetchall()


# ── API ──────────────────────────────────────────────────────────────────────

def test_upload_rejects_non_media(client):
    r = client.post("/api/stories/upload", files={"file": ("x.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.status_code == 400


def test_upload_and_create_job_flow(client):
    ids = asyncio.run(_make_accounts(2))
    up = client.post("/api/stories/upload", files={"file": ("pic.png", io.BytesIO(b"png"), "image/png")})
    assert up.status_code == 200 and up.json()["media_type"] == "photo"
    fname = up.json()["filename"]
    try:
        r = client.post("/api/stories/jobs", json={"name": "T", "account_ids": ids, "media_file": fname,
                                                   "caption": "hi", "stagger_min": 3})
        assert r.status_code == 200, r.text
        jid = r.json()["id"]
        jobs = client.get("/api/stories/jobs").json()["jobs"]
        assert jobs[0]["id"] == jid and jobs[0]["posts"] == {"pending": 2}
        d = client.get(f"/api/stories/jobs/{jid}").json()
        assert [p["account_id"] for p in d["post_rows"]] == ids
        assert client.post(f"/api/stories/jobs/{jid}/cancel").status_code == 200
        assert client.get("/api/stories/jobs").json()["jobs"][0]["posts"] == {"cancelled": 2}
        assert client.post(f"/api/stories/jobs/{jid}/cancel").status_code == 400
        assert client.delete(f"/api/stories/jobs/{jid}").status_code == 200
        assert client.get("/api/stories/jobs").json()["jobs"] == []
    finally:
        os.remove(os.path.join(se.MEDIA_DIR, fname))


def test_create_job_validation(client, media_file):
    ids = asyncio.run(_make_accounts(1))
    base = {"account_ids": ids, "media_file": media_file}
    assert client.post("/api/stories/jobs", json={**base, "account_ids": []}).status_code == 400
    assert client.post("/api/stories/jobs", json={**base, "account_ids": [99999]}).status_code == 400
    assert client.post("/api/stories/jobs", json={**base, "media_file": "nope.jpg"}).status_code == 400
    assert client.post("/api/stories/jobs", json={**base, "scheduled_at": "2001-01-01T10:00"}).status_code == 400
    assert client.post("/api/stories/jobs", json={**base, "view_delay_min": 50, "view_delay_max": 10}).status_code == 400
    assert client.post("/api/stories/jobs", json={**base, "caption": "x" * 3000}).status_code == 400


def test_accounts_endpoint_marks_unsafe(client):
    ids = asyncio.run(_make_accounts(2))
    asyncio.run(db.pause_account(ids[1], "test"))
    accs = {a["id"]: a for a in client.get("/api/stories/accounts").json()["accounts"]}
    assert accs[ids[0]]["ok"] is True
    assert accs[ids[1]]["ok"] is False and accs[ids[1]]["why"] == "đang tạm dừng"


# ── Engine tick ──────────────────────────────────────────────────────────────

async def test_tick_posts_then_nicks_view_each_other(fake_tg, media_file):
    ids = await _make_accounts(3)
    jid = await se.create_job(name="", account_ids=ids, media_file=media_file, caption="c",
                              scheduled_at=time.time() - 5, stagger_min=0)
    await se.tick()
    posts = await _rows("SELECT account_id, status, story_id FROM story_posts WHERE job_id=?", jid)
    assert all(st == "posted" and sid for _, st, sid in posts)
    assert all("SendStoryRequest" in fake_tg[a].calls for a in ids)
    # each of 3 posts gets 2 viewers (the other nicks), never the poster itself
    views = await _rows("SELECT v.viewer_account_id, p.account_id FROM story_views v JOIN story_posts p ON p.id=v.post_id")
    assert len(views) == 6 and all(v != p for v, p in views)
    # nothing is due yet (3..45 min later), so no read happened
    assert not any("ReadStoriesRequest" in c.calls for c in fake_tg.values())

    await _set_all_views_due()
    for _ in range(3):              # per-tick cap + per-viewer gap spread views over ticks
        se._last_view_at.clear()
        await se.tick()
    done = await _rows("SELECT COUNT(*) FROM story_views WHERE status='done'")
    assert done[0][0] == 6
    assert all(c.calls.count("ReadStoriesRequest") == 2 for c in fake_tg.values())
    assert all("SendMessageRequest" not in c.calls for c in fake_tg.values())   # never messages
    job = await _rows("SELECT status FROM story_jobs WHERE id=?", jid)
    assert job[0][0] == "done"


async def _set_all_views_due():
    async with db.get_db() as conn:
        await conn.execute("UPDATE story_views SET view_at=?", (time.time() - 1,))
        await conn.commit()


async def test_viewer_gap_limits_one_view_per_nick_per_tick(fake_tg, media_file):
    ids = await _make_accounts(3)
    await se.create_job(name="", account_ids=ids, media_file=media_file, caption="",
                        scheduled_at=time.time() - 5, stagger_min=0)
    await se.tick()
    await _set_all_views_due()
    se._last_view_at.clear()
    await se.tick()
    reads = [c.calls.count("ReadStoriesRequest") for c in fake_tg.values()]
    assert max(reads) == 1          # a viewer never views twice within VIEWER_MIN_GAP


async def test_floodwait_on_post_pauses_nick_and_never_retries(fake_tg, media_file):
    ids = await _make_accounts(2)
    fake_tg[ids[0]] = FakeClient(ids[0], fail={"SendStoryRequest": FloodWaitError(request=None, capture=120)})
    jid = await se.create_job(name="", account_ids=ids, media_file=media_file, caption="",
                              scheduled_at=time.time() - 5, stagger_min=0)
    await se.tick()
    acc = await db.get_account(ids[0])
    assert acc["is_paused"] == 1 and "FloodWait" in acc["pause_reason"]
    rows = dict(await _rows("SELECT account_id, status FROM story_posts WHERE job_id=?", jid))
    assert rows[ids[0]] == "failed" and rows[ids[1]] == "posted"
    # the paused nick does not view the other story either
    v = await _rows("SELECT status FROM story_views WHERE viewer_account_id=?", ids[0])
    assert all(s == "skipped" for (s,) in v)
    calls_before = len(fake_tg[ids[0]].calls)
    await se.tick()
    assert len(fake_tg[ids[0]].calls) == calls_before


async def test_floodwait_on_view_pauses_viewer(fake_tg, media_file):
    ids = await _make_accounts(2)
    fake_tg[ids[1]] = FakeClient(ids[1], fail={"ReadStoriesRequest": FloodWaitError(request=None, capture=30)})
    await se.create_job(name="", account_ids=[ids[0]], media_file=media_file, caption="",
                        scheduled_at=time.time() - 5, stagger_min=0, viewer_scope="all")
    await se.tick()
    await _set_all_views_due()
    await se.tick()
    acc = await db.get_account(ids[1])
    assert acc["is_paused"] == 1
    v = await _rows("SELECT status FROM story_views")
    assert v[0][0] == "failed"


async def test_paused_nick_is_skipped_and_late_post_dropped(fake_tg, media_file):
    ids = await _make_accounts(2)
    await db.pause_account(ids[0], "manual")
    jid = await se.create_job(name="", account_ids=ids, media_file=media_file, caption="",
                              scheduled_at=time.time() - 3 * 3600, stagger_min=0)
    await se.tick()
    rows = dict(await _rows("SELECT account_id, status FROM story_posts WHERE job_id=?", jid))
    assert rows == {ids[0]: "skipped", ids[1]: "skipped"}    # paused / >2h overdue
    assert not any("SendStoryRequest" in c.calls for c in fake_tg.values())


async def test_tick_idle_does_nothing(fake_tg):
    await se.tick()
    assert fake_tg == {}


# ── v3.40 AI caption ─────────────────────────────────────────────────────────

def test_parse_captions_json_and_cleanup():
    raw = '```json\n["  \\"Chill cuối tuần ☕\\"  ", "1. Hôm nay đẹp trời [HANDOVER_REQUIRED]", "Chill cuối tuần ☕"]\n```'
    assert se._parse_captions(raw, 3) == ["Chill cuối tuần ☕", "Hôm nay đẹp trời"]


def test_parse_captions_plain_lines_and_length_cap():
    long = "word " * 80
    caps = se._parse_captions(f"Hello there\n{long}", 2)
    assert caps[0] == "Hello there"
    assert len(caps[1]) <= se.CAPTION_MAX and not caps[1].endswith(" ")


def test_caption_prompt_keeps_idea_and_language():
    p = se._caption_prompt("Join t.me/abc today", "en", 3)
    assert "t.me/abc" in p and "English" in p and "3" in p
    assert "Vietnamese" in se._caption_prompt("", "xx", 1)


@pytest.fixture
def fake_ai(monkeypatch):
    import ai_remix
    calls = []

    async def cfg(provider):
        return (["k"], {}) if provider == "gemini" else ([], {})

    async def gen(prompt, provider, keys, **kw):
        calls.append(prompt)
        import re as _re
        n = int(_re.search(r"Write (\d+) different", prompt).group(1))
        return json.dumps([f"Caption {len(calls)}-{i} 🌿" for i in range(n)], ensure_ascii=False)

    monkeypatch.setattr(ai_remix, "get_provider_config", cfg)
    monkeypatch.setattr(ai_remix, "generate_response", gen)
    return calls


def test_caption_endpoint_one_click(client, fake_ai):
    r = client.post("/api/stories/caption", json={"idea": "sự kiện tháng 11", "lang": "vi"})
    assert r.status_code == 200
    assert r.json()["captions"] == ["Caption 1-0 🌿"]
    assert "sự kiện tháng 11" in fake_ai[0]


def test_caption_endpoint_no_provider(client, monkeypatch):
    import ai_remix

    async def none(provider):
        return [], {}
    monkeypatch.setattr(ai_remix, "get_provider_config", none)
    r = client.post("/api/stories/caption", json={})
    assert r.status_code == 502 and "AI" in r.json()["detail"]


def test_vary_caption_gives_each_nick_its_own(client, fake_ai, media_file):
    ids = asyncio.run(_make_accounts(3))
    r = client.post("/api/stories/jobs", json={"account_ids": ids, "media_file": media_file,
                                               "caption": "Gốc", "vary_caption": True})
    assert r.status_code == 200 and r.json()["ai_captions"] == 2
    d = client.get(f"/api/stories/jobs/{r.json()['id']}").json()
    caps = {p["account_id"]: p["caption"] for p in d["post_rows"]}
    assert caps[ids[0]] is None                       # first nick keeps the typed caption
    assert caps[ids[1]] and caps[ids[2]] and caps[ids[1]] != caps[ids[2]]


async def test_tick_posts_per_nick_caption(fake_tg, media_file, monkeypatch):
    ids = await _make_accounts(2)
    sent = {}

    async def fake_send(client, path, media_type, caption, privacy):
        sent[client.aid] = caption
        return 900 + client.aid
    monkeypatch.setattr(se, "_send_story", fake_send)
    await se.create_job(name="", account_ids=ids, media_file=media_file, caption="base",
                        scheduled_at=time.time() - 5, stagger_min=0, cross_view=False,
                        captions={ids[1]: "riêng nick 2"})
    await se.tick()
    assert sent == {ids[0]: "base", ids[1]: "riêng nick 2"}


# ── A job where posts failed is reported as failed, and can be retried ───────

async def test_failed_job_is_failed_not_done_and_retry_requeues_it(fake_tg, media_file):
    from telethon.errors import BadRequestError
    ids = await _make_accounts(2)
    for a in ids:
        fake_tg[a] = FakeClient(a, fail={"SendStoryRequest": BadRequestError(request=None, message="MEDIA_FILE_INVALID")})
    jid = await se.create_job(name="", account_ids=ids, media_file=media_file, caption="hi",
                              scheduled_at=time.time() - 60, stagger_min=0, cross_view=False)
    await se.tick()
    assert (await _rows("SELECT status FROM story_jobs WHERE id=?", jid))[0][0] == "failed"
    assert [j for j in await se.list_jobs() if j["id"] == jid][0]["status"] == "failed"

    assert await se.retry_job(jid) == 2
    rows = await _rows("SELECT status, error, post_at FROM story_posts WHERE job_id=?", jid)
    assert all(r[0] == "pending" and r[1] is None and r[2] > time.time() for r in rows)
    assert (await _rows("SELECT status FROM story_jobs WHERE id=?", jid))[0][0] == "running"
    assert await se.retry_job(jid) == 0   # still running: nothing to retry

    for a in ids:
        fake_tg[a] = FakeClient(a)
    async with db.get_db() as conn:
        await conn.execute("UPDATE story_posts SET post_at=? WHERE job_id=?", (time.time() - 1, jid))
        await conn.commit()
    await se.tick()
    assert (await _rows("SELECT status FROM story_jobs WHERE id=?", jid))[0][0] == "done"


def test_final_status():
    assert se.final_status({"failed": 13}) == "failed"
    assert se.final_status({"skipped": 2}) == "failed"
    assert se.final_status({"posted": 3, "failed": 1}) == "partial"
    assert se.final_status({"posted": 3}) == "done"
    assert se.final_status({"cancelled": 2}) == "done"


def test_cut_off_ai_array_gives_clean_captions_and_saved_arrays_are_repaired():
    # The AI's JSON array was cut off at the token limit: keep the complete items, decode their escapes.
    raw = r'["WWFC starts Nov 26 🏆\nMain event Dec 3", "Mark Nov 26: WWFC goes live", "cut o'
    assert se._parse_captions(raw, 5) == ["WWFC starts Nov 26 🏆\nMain event Dec 3", "Mark Nov 26: WWFC goes live"]
    saved = '["one line", "two line", "thr'
    assert se.repair_caption(saved, 0) == "one line" and se.repair_caption(saved, 1) == "two line"
    assert se.repair_caption(saved, 2) == "one line"
    assert se.repair_caption("plain caption", 0) == "plain caption" and se.repair_caption(None) is None


def test_retry_endpoint_refuses_when_nothing_failed(client):
    r = client.post("/api/stories/jobs/999999/retry")
    assert r.status_code == 400
