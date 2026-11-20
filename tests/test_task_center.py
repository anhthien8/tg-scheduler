"""v3.40 Task center: GET /api/tasks/summary groups every background job into run/plan/warn/done."""
import asyncio
import time

import database as db
import routes.tasks as T


def _sql(stmts):
    async def go():
        async with db.get_db() as conn:
            for sql, params in stmts:
                await conn.execute(sql, params)
            await conn.commit()
    asyncio.run(go())


def _get(client):
    T._cache.update(at=0.0, val=None)          # each test starts from an empty cache
    r = client.get("/api/tasks/summary")
    assert r.status_code == 200
    return r.json()


def test_empty_db_returns_no_tasks(client):
    d = _get(client)
    assert d["tasks"] == []
    assert d["counts"] == {"run": 0, "plan": 0, "warn": 0, "done": 0}
    assert d["skipped"] == [] or "zalo" in d["skipped"]   # zalo tables are created lazily


def test_dm_campaign_buckets(client):
    ins = ("INSERT INTO dm_campaigns (name, scrape_job_id, status, total_targets, sent_count, failed_count) "
           "VALUES (?, 'j', ?, ?, ?, ?)")
    _sql([
        (ins, ("Chạy", "running", 100, 40, 2)),
        (ins, ("Nháp", "draft", 10, 0, 0)),                 # never a task
        (ins, ("Xong", "completed", 50, 50, 0)),
        (ins, ("Lỗi", "error", 5, 0, 0)),
        (ins, ("Dừng tay", "paused", 5, 1, 0)),
        (ins, ("Cũ", "completed", 5, 5, 0)),
        ("UPDATE dm_campaigns SET updated_at = datetime('now','-3 days') WHERE name = 'Cũ'", ()),
    ])
    d = _get(client)
    by = {t["name"]: t for t in d["tasks"] if t["kind"] == "dm"}
    assert set(by) == {"Chạy", "Xong", "Lỗi", "Dừng tay"}      # draft and >24h-old finished rows hidden
    assert by["Chạy"]["bucket"] == "run" and by["Chạy"]["done"] == 42 and by["Chạy"]["total"] == 100
    assert by["Xong"]["bucket"] == "done"
    assert by["Lỗi"]["bucket"] == "warn"
    assert by["Dừng tay"]["bucket"] == "done"                  # manual stop, no error note
    assert by["Chạy"]["page"] == "members" and by["Chạy"]["key"].startswith("dm:")


def test_dm_paused_with_error_note_needs_attention(client):
    _sql([
        ("INSERT INTO dm_campaigns (id, name, scrape_job_id, status) VALUES (7, 'Tự dừng', 'j', 'paused')", ()),
        ("INSERT INTO dm_campaign_logs (campaign_id, account_id, target_user_id, status, error_message) "
         "VALUES (7, 1, 1, 'failed', 'AI provider lỗi 3 lần liên tiếp')", ()),
    ])
    t = next(t for t in _get(client)["tasks"] if t["kind"] == "dm")
    assert t["bucket"] == "warn" and "AI provider" in t["note"]


def test_story_jobs_and_partial_failure(client):
    now = time.time()
    _sql([
        ("INSERT INTO story_jobs (id, name, media_file, scheduled_at, status) VALUES (1, 'Hẹn', 'a.png', ?, 'scheduled')", (now + 3600,)),
        ("INSERT INTO story_jobs (id, name, media_file, scheduled_at, status) VALUES (2, 'Một phần', 'a.png', ?, 'done')", (now - 600,)),
        ("INSERT INTO story_posts (job_id, account_id, post_at, status, posted_at) VALUES (2, 1, ?, 'posted', ?)", (now - 600, now - 590)),
        ("INSERT INTO story_posts (job_id, account_id, post_at, status) VALUES (2, 2, ?, 'failed')", (now - 300,)),
        ("INSERT INTO story_posts (job_id, account_id, post_at, status) VALUES (1, 1, ?, 'pending')", (now + 3600,)),
    ])
    by = {t["id"]: t for t in _get(client)["tasks"] if t["kind"] == "story"}
    assert by[1]["bucket"] == "plan" and by[1]["ts_scheduled"] == int(now + 3600)
    assert by[2]["bucket"] == "warn" and by[2]["status"] == "partial" and by[2]["done"] == 2


def test_once_schedule_expired_and_recurring_ignored(client):
    asyncio.run(db.create_account({"name": "A", "phone": "+1", "api_id": 1, "api_hash": "h", "session_name": "s1"}))
    _sql([
        ("INSERT INTO schedules (name, schedule_type, time_of_day, once_date, is_active) VALUES ('Quá giờ', 'once', '08:00', '2020-01-01', 1)", ()),
        ("INSERT INTO schedules (name, schedule_type, time_of_day, once_date, is_active) VALUES ('Sắp gửi', 'once', '08:00', '2099-01-01', 1)", ()),
        ("INSERT INTO schedules (name, schedule_type, time_of_day, is_active) VALUES ('Hằng ngày', 'daily', '08:00', 1)", ()),
    ])
    by = {t["name"]: t for t in _get(client)["tasks"] if t["kind"] == "schedule"}
    assert set(by) == {"Quá giờ", "Sắp gửi"}
    assert by["Quá giờ"]["bucket"] == "warn"
    assert by["Sắp gửi"]["bucket"] == "plan" and by["Sắp gửi"]["ts_scheduled"] > time.time()


def test_summary_is_cached_until_fresh(client):
    _get(client)
    _sql([("INSERT INTO dm_campaigns (name, scrape_job_id, status) VALUES ('Mới', 'j', 'running')", ())])
    assert client.get("/api/tasks/summary").json()["counts"]["run"] == 0      # served from the 30 s cache
    T._cache["at"] -= T.FRESH_MIN_GAP + 1
    assert client.get("/api/tasks/summary?fresh=1").json()["counts"]["run"] == 1
