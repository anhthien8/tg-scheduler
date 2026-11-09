"""Deep Crawl queue + per-run results opened from the history (v3.24.0).

A crawl started while another one is live goes to the queue and starts on its own
when the current one ends. Every run keeps its leads in its own file under the
history id, so a new crawl no longer overwrites the previous results: the history
opens them again (`/deep-crawl/results?hid=`).
"""
import asyncio
import os

import pytest

import database as db
import routes.members as m


def _leads(tag, n=3):
    return [{"title": f"{tag} c{i}", "username": f"{tag}_u{i}", "channel_id": f"{abs(hash(tag)) % 9000}{i}",
             "description": "d", "contacts": [f"@{tag}_adm{i}"], "participants_count": 100 + i,
             "trading_score": 70, "category": "trading", "depth": 1} for i in range(n)]


class _AliveTask:
    def done(self):
        return False


async def _noop(*a, **k):
    return None


@pytest.fixture
def iso(monkeypatch, tmp_path):
    """Isolate the module-level crawl state and the run-file directory."""
    monkeypatch.setattr(db, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_viewed_run", {"hid": None, "run": None})
    monkeypatch.setattr(m, "_deep_crawl_state", {"status": "idle", "results": []})
    monkeypatch.setattr(m, "_deep_crawl_queue", [])
    monkeypatch.setattr(m, "_deep_crawl_task", None)
    monkeypatch.setattr(m, "_deep_crawl_history_id", None)
    monkeypatch.setattr(m, "_deep_crawl_recovered", True)
    monkeypatch.setattr(m, "_deep_crawl_stop_flag", {"stopped": False})
    monkeypatch.setattr(m, "_contact_cache_loaded", True)
    monkeypatch.setattr(m, "_results_cache_key", None)
    monkeypatch.setattr(m, "_kol_rescored", True)  # one-time KOL rescore is not under test here
    monkeypatch.setattr(m.tg, "is_bot_account", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(m.tg, "score_community_trading", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(m.tg, "seed_channel_about_cache", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(m.tg, "export_contact_kind_cache", lambda: {}, raising=False)
    monkeypatch.setattr(m.tg, "import_contact_kind_cache", lambda *a, **k: None, raising=False)
    return tmp_path


def _run_file(tmp_path, hid):
    return os.path.join(str(tmp_path), m._RUNS_DIRNAME, f"{hid}.json.gz")


# ── Queue ──

def test_start_while_task_alive_is_queued_with_its_settings(iso, client, monkeypatch):
    # The crawl is between states (FloodWait wait / pause before the next queued link):
    # status is no longer "running" but the task is alive → must queue, not start a 2nd crawl.
    monkeypatch.setattr(m, "_deep_crawl_state", {"status": "completed", "results": [], "source_url": "https://t.me/a"})
    monkeypatch.setattr(m, "_deep_crawl_task", _AliveTask())
    monkeypatch.setattr(m, "_deep_crawl_history_id", "abcdef0123")

    r = client.post("/api/members/deep-crawl", json={"account_ids": [1], "channel_link": "https://t.me/b",
                                                      "max_depth": 3, "min_expand_score": 55})
    d = r.json()
    assert r.status_code == 200 and d["queued"] is True and d["position"] == 1

    s = client.get("/api/members/deep-crawl/status").json()
    assert s["busy"] is True and s["history_id"] == "abcdef0123"
    assert s["queue_count"] == 1
    q = s["queue"][0]
    assert (q["channel_link"], q["max_depth"], q["min_expand_score"], q["retry"]) == ("https://t.me/b", 3, 55, False)


def test_status_not_busy_without_task(iso, client, monkeypatch):
    monkeypatch.setattr(m, "_deep_crawl_state", {"status": "completed", "results": _leads("x")})
    s = client.get("/api/members/deep-crawl/status").json()
    assert s["busy"] is False and s["history_id"] is None and s["results_count"] == 3


async def test_queued_crawl_runs_next_and_each_run_keeps_its_results(iso, monkeypatch):
    calls = []

    async def fake_crawl(account_ids, channel_link, max_depth, progress_callback, stop_flag, min_expand_score):
        calls.append((channel_link, max_depth, min_expand_score))
        tag = channel_link.rsplit("/", 1)[-1]
        return _leads(tag, 2 if tag == "a" else 4)

    monkeypatch.setattr(m.tg, "deep_crawl_similar_channels", fake_crawl, raising=False)
    m._deep_crawl_queue.append({"account_ids": [1], "channel_link": "https://t.me/b", "max_depth": 3,
                                "min_expand_score": 55, "added_at": ""})
    hid_a = await m._history_add({"channel_link": "https://t.me/a", "max_depth": 2, "status": "running"})
    m._deep_crawl_state.update(status="running", source_url="https://t.me/a", errors=[])

    await m._do_deep_crawl([1], "https://t.me/a", 2, hid_a, 40)
    assert m._deep_crawl_queue == [], "the queued link must start on its own"
    await m._deep_crawl_task  # the queued crawl (b) started by the first one

    assert calls == [("https://t.me/a", 2, 40), ("https://t.me/b", 3, 55)]
    hist = await m._history_load()
    by_link = {h["channel_link"]: h for h in hist}
    a, b = by_link["https://t.me/a"], by_link["https://t.me/b"]
    assert a["status"] == b["status"] == "completed"
    assert a.get("has_results") and b.get("has_results")
    assert m._deep_crawl_history_id == b["id"]

    run_a = await m._load_run(a["id"])
    run_b = await m._load_run(b["id"])
    assert [l["title"] for l in run_a["results"]] == ["a c0", "a c1"], "b must not overwrite a's results"
    assert len(run_b["results"]) == 4
    assert run_a["meta"]["channel_link"] == "https://t.me/a" and run_a["meta"]["max_depth"] == 2


# ── Results of an earlier run ──

def test_results_by_hid_returns_that_run_and_its_source(iso, client, monkeypatch):
    monkeypatch.setattr(m, "_deep_crawl_state", {"status": "running", "results": [], "source_url": "https://t.me/now"})
    meta = {"channel_link": "https://t.me/old", "max_depth": 2, "status": "completed"}
    assert asyncio.run(m._save_run_results("0123abcdef", _leads("old", 5), meta))

    d = client.get("/api/members/deep-crawl/results?slim=1&page=0&limit=2&hid=0123abcdef").json()
    assert d["total"] == 5 and len(d["leads"]) == 2
    assert d["source_url"] == "https://t.me/old"
    p1 = client.get("/api/members/deep-crawl/results?slim=1&page=1&limit=2&hid=0123abcdef").json()
    assert p1["leads"][0]["title"] == "old c2"

    full = client.get("/api/members/deep-crawl/results?hid=0123abcdef").json()
    assert full["total"] == 5 and full["status"] == "completed" and full["source_url"] == "https://t.me/old"

    # Without hid: the current crawl (still running, nothing yet)
    cur = client.get("/api/members/deep-crawl/results?slim=1&page=0&limit=2").json()
    assert cur["total"] == 0 and cur["source_url"] == "https://t.me/now"


def test_results_by_missing_or_bad_hid_is_404(iso, client):
    assert client.get("/api/members/deep-crawl/results?slim=1&hid=0123456789").status_code == 404
    assert client.get("/api/members/deep-crawl/results?slim=1&hid=..%2F..%2Fscheduler").status_code == 404
    assert m._run_path("../x") is None and m._run_path("ABCDEF1234") is None and m._run_path("") is None


def test_empty_run_is_not_written(iso):
    assert asyncio.run(m._save_run_results("0123abcdef", [], {})) is False
    assert asyncio.run(m._save_run_results("bad/id", _leads("z"), {})) is False
    assert not os.path.exists(_run_file(iso, "0123abcdef"))


# ── History ──

def test_history_delete_removes_the_run_file(iso, client):
    keep = asyncio.run(m._history_add({"channel_link": "https://t.me/k", "max_depth": 2, "status": "completed"}))
    gone = asyncio.run(m._history_add({"channel_link": "https://t.me/g", "max_depth": 2, "status": "completed"}))
    for hid in (keep, gone):
        assert asyncio.run(m._save_run_results(hid, _leads(hid), {"channel_link": hid}))

    assert client.delete(f"/api/members/deep-crawl/history/{gone}").status_code == 200
    assert not os.path.exists(_run_file(iso, gone))
    assert os.path.exists(_run_file(iso, keep))
    assert client.get(f"/api/members/deep-crawl/results?slim=1&hid={gone}").status_code == 404


def test_history_trim_prunes_old_run_files(iso, monkeypatch):
    monkeypatch.setattr(m, "_HISTORY_MAX", 2)
    ids = []
    for i in range(3):
        hid = asyncio.run(m._history_add({"channel_link": f"https://t.me/t{i}", "max_depth": 2, "status": "completed"}))
        if i < 2:
            asyncio.run(m._save_run_results(hid, _leads(f"t{i}"), {}))
        ids.append(hid)
    assert not os.path.exists(_run_file(iso, ids[0])), "trimmed entry must lose its file"
    assert os.path.exists(_run_file(iso, ids[1]))


def test_history_running_row_shows_live_counts(iso, client, monkeypatch):
    hid = asyncio.run(m._history_add({"channel_link": "https://t.me/live", "max_depth": 2, "status": "running"}))
    monkeypatch.setattr(m, "_deep_crawl_history_id", hid)
    monkeypatch.setattr(m, "_deep_crawl_state", {"status": "running", "channels_found": 12, "contacts_found": 5,
                                                 "results": []})
    row = next(h for h in client.get("/api/members/deep-crawl/history").json()["history"] if h["id"] == hid)
    assert (row["channels_found"], row["contacts_found"]) == (12, 5)
    assert "account_ids" not in row


# ── Upgrade + restart ──

async def test_snapshot_results_from_before_the_upgrade_are_adopted(iso, monkeypatch):
    hid = await m._history_add({"channel_link": "https://t.me/src", "max_depth": 2, "status": "completed"})
    await db.set_setting("deep_crawl_state", __import__("json").dumps(
        {"status": "completed", "source_url": "https://t.me/src", "results": _leads("src", 4)}))
    await db.set_setting("deep_crawl_queue", "[]")
    monkeypatch.setattr(m, "_deep_crawl_recovered", False)

    await m._load_deep_crawl_snapshot()

    h = next(x for x in await m._history_load() if x["id"] == hid)
    assert h.get("has_results") is True
    run = await m._load_run(hid)
    assert len(run["results"]) == 4 and run["meta"]["channel_link"] == "https://t.me/src"
    assert m._deep_crawl_state["status"] == "completed" and len(m._deep_crawl_state["results"]) == 4

    # Adopting again never overwrites the stored run
    m._viewed_run.update(hid=None, run=None)
    await m._adopt_state_results({"status": "completed", "source_url": "https://t.me/src", "results": _leads("x", 1)})
    assert len((await m._load_run(hid))["results"]) == 4


async def test_adopt_skips_live_or_unknown_source(iso):
    hid = await m._history_add({"channel_link": "https://t.me/src", "max_depth": 2, "status": "running"})
    await m._adopt_state_results({"status": "running", "source_url": "https://t.me/src", "results": _leads("s")})
    await m._adopt_state_results({"status": "completed", "source_url": "https://t.me/other", "results": _leads("s")})
    assert not os.path.exists(_run_file(iso, hid))


async def test_restart_keeps_partial_results_of_the_interrupted_run(iso):
    hid = await m._history_add({"channel_link": "https://t.me/r", "max_depth": 3, "status": "running"})
    await m._recover_after_restart({"status": "running", "source_url": "https://t.me/r", "max_depth": 3,
                                    "results": _leads("r", 2), "contacts_found": 2})
    h = next(x for x in await m._history_load() if x["id"] == hid)
    assert h["status"] == "interrupted" and h.get("has_results") is True and h["channels_found"] == 2
    assert len((await m._load_run(hid))["results"]) == 2


# ── Saving contacts from an opened run ──

def test_import_from_an_older_run_records_its_source(iso, client, monkeypatch):
    monkeypatch.setattr(m, "_deep_crawl_state", {"status": "running", "results": [], "source_url": "https://t.me/now"})
    lead = _leads("srcold", 1)[0]
    r = client.post("/api/members/import-contacts", json={
        "scrape_job_id": "deep_old_1", "group_title": "old", "contacts": [{"username": "srcold_adm0"}],
        "channels": [lead], "source": "https://t.me/old"})
    assert r.status_code == 200 and r.json()["kol_upserted"] == 1
    item = client.get("/api/members/kol-directory", params={"q": "srcold_u0"}).json()["items"][0]
    assert item["source"] == "https://t.me/old"
