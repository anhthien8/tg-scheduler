"""KOL directory + Deep Crawl save dedup (v3.20.0)."""
import pytest

import database as db


def _lead(u, score=70, contacts=None, subs=1000):
    return {"channel_id": f"9{len(u)}{score}", "username": u, "title": f"T {u}",
            "participants_count": subs, "trading_score": score, "category": "trading",
            "category_label": "Trading", "contacts": contacts if contacts is not None else [f"@adm_{u}"],
            "description": "d", "parent_channel": "P", "depth": 1}


@pytest.mark.asyncio
async def test_upsert_merges_contacts_and_keeps_booking_fields():
    await db.upsert_kol_channels([_lead("chanA", contacts=["@a1"])], source="https://t.me/src")
    d = await db.list_kol_channels(q="chana")
    item = d["items"][0]
    assert await db.update_kol_channel(item["id"], {"status": "booked", "price": "150$", "note": "ok"})

    await db.upsert_kol_channels([_lead("ChanA", score=85, contacts=["@a2", "@A1"])], source="x")
    item = (await db.list_kol_channels(q="chana"))["items"][0]
    assert item["contacts"] == ["@a1", "@a2"]          # merged, case-insensitive
    assert item["trading_score"] == 85                  # refreshed
    assert (item["status"], item["price"], item["note"]) == ("booked", "150$", "ok")
    assert item["source"] == "https://t.me/src"         # first source kept


@pytest.mark.asyncio
async def test_list_filters_and_bad_status():
    await db.upsert_kol_channels([_lead("vip1", 90), _lead("low1", 30), _lead("nocon", 70, contacts=[])])
    res = await db.list_kol_channels(min_score=80)
    assert {i["username"] for i in res["items"]} >= {"vip1"} and all(i["trading_score"] >= 80 for i in res["items"])
    res = await db.list_kol_channels(has_contact=True)
    assert "nocon" not in {i["username"] for i in res["items"]}
    with pytest.raises(ValueError):
        await db.update_kol_channel(res["items"][0]["id"], {"status": "hacked"})


def test_import_skips_contacts_already_in_other_lists(client):
    r = client.post("/api/members/import-contacts", json={
        "scrape_job_id": "kol_job_1", "group_title": "L1",
        "contacts": [{"username": "@dupe_user"}, {"username": "only_first"}]})
    assert r.status_code == 200 and r.json()["count"] == 2

    r = client.post("/api/members/import-contacts", json={
        "scrape_job_id": "kol_job_2", "group_title": "L2",
        "contacts": [{"username": "DUPE_USER"}, {"username": "fresh_one"}, {"username": "@fresh_one"}],
        "channels": [_lead("kolchan", contacts=["@fresh_one"])]})
    d = r.json()
    assert d["count"] == 1
    assert d["skipped_existing"] == 1 and d["existing_jobs"][0][0] == "kol_job_1"
    assert d["skipped_dupe"] == 1
    assert d["kol_upserted"] == 1

    r = client.get("/api/members/kol-directory", params={"q": "kolchan"})
    assert r.json()["items"][0]["saved_job"] == "kol_job_2"

    raw = client.get("/api/members/kol-directory/export.csv").content
    assert raw.startswith(b"\xef\xbb\xbf") and b"https://t.me/kolchan" in raw


def test_rename_scrape_job_keeps_id(client):
    client.post("/api/members/import-contacts", json={
        "scrape_job_id": "deep_https___t_me_xyz", "group_title": "Deep Crawl Contacts (deep_https___t_me_xyz)",
        "contacts": [{"username": "rename_me_user"}]})
    r = client.post("/api/members/scrape-jobs/deep_https___t_me_xyz/rename", json={"group_title": "xyz · Deep Crawl"})
    assert r.status_code == 200
    jobs = client.get("/api/members/scrape-jobs").json()["jobs"]
    assert any(j["scrape_job_id"] == "deep_https___t_me_xyz" and j["group_title"] == "xyz · Deep Crawl" for j in jobs)
    assert client.post("/api/members/scrape-jobs/nope/rename", json={"group_title": "a"}).status_code == 404
