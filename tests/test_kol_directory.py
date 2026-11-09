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


def test_import_skips_only_contacts_in_lists_a_campaign_uses(client):
    import asyncio

    def save(job, names, **extra):
        return client.post("/api/members/import-contacts", json={
            "scrape_job_id": job, "group_title": job, "contacts": [{"username": n} for n in names], **extra}).json()

    assert save("kol_job_1", ["@dupe_user", "only_first"])["count"] == 2

    # kol_job_1 has no campaign yet -> its contacts can be saved again
    d = save("kol_job_2", ["DUPE_USER", "fresh_one", "@fresh_one"],
             channels=[_lead("kolchan", contacts=["@fresh_one"])])
    assert d["count"] == 2 and d["resaved_unused"] == 1
    assert d["skipped_existing"] == 0 and d["skipped_dupe"] == 1
    assert d["kol_upserted"] == 1

    # once a campaign targets kol_job_1, its contacts are skipped elsewhere
    asyncio.run(db.create_dm_campaign({"name": "c", "scrape_job_id": "kol_job_1", "sender_account_ids": [1],
                                       "messages": [{"msg_type": "text", "content": "hi"}]}))
    d = save("kol_job_3", ["dupe_user", "only_first", "new_guy"])
    assert d["count"] == 1 and d["resaved_unused"] == 0
    assert d["skipped_existing"] == 2 and d["existing_jobs"][0][0] == "kol_job_1"

    # saving into the same (unused) list twice never duplicates
    d = save("kol_job_3", ["new_guy"])
    assert d["count"] == 0 and d["skipped_existing"] == 1

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


@pytest.mark.asyncio
async def test_kind_and_country_filters_after_rescore():
    from channel_country import guess_country
    from telegram_client import score_community_trading
    await db.upsert_kol_channels([
        {**_lead("idfut1"), "title": "Sinyal Futures Indonesia",
         "description": "Sinyal futures leverage x20 untuk member dan VIP chat @adm"},
        {**_lead("drop1"), "title": "Airdrop Finder", "description": "Get free crypto airdrop everyday @adm"},
    ])
    await db.rescore_kol_channels(score_community_trading, guess_country)
    fut = await db.list_kol_channels(kind="futures", country="ID", q="idfut1")
    assert [i["username"] for i in fut["items"]] == ["idfut1"]
    drop = await db.list_kol_channels(kind="airdrop", q="drop1")
    assert [i["username"] for i in drop["items"]] == ["drop1"]
    assert drop["kind_counts"]["airdrop"] >= 1 and "ID" in fut["country_counts"]
