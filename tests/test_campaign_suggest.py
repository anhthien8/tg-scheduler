import asyncio
import json

import campaign_suggest as cs
import database as db


def test_pure_helpers():
    assert cs.strip_name_vars("Hey {first_name}, let's talk") == "Hey, let's talk"
    assert cs.strip_name_vars("Hello {full_name} 👋\nI'm from WEEX") == "Hello 👋\nI'm from WEEX"
    assert cs.strip_name_vars("{Hi|Hey} there") == "{Hi|Hey} there"  # spintax untouched
    assert cs.text_lang("Mình phụ trách Partnership của WEEX, thấy network của bạn khá mạnh") == "vi"
    assert cs.text_lang("Hey, let's discuss a better commission package") == "en"
    assert cs.mentioned_brand("I'm from Weex.com") == "weex"
    assert cs.smoothed_rate(13, 52) > cs.smoothed_rate(76, 613)


def test_pick_best_respects_language_and_brand():
    camps = [
        {"id": 1, "lang": "en", "audience": "kol", "brand": "weex", "replied": 13, "sent": 52},
        {"id": 2, "lang": "en", "audience": "kol", "brand": "blofin", "replied": 30, "sent": 60},
        {"id": 3, "lang": "vi", "audience": "kol", "brand": "weex", "replied": 9, "sent": 26},
    ]
    assert cs.pick_best(camps, "en", "kol", "weex")["id"] == 1
    assert cs.pick_best(camps, "en", "kol", None)["id"] == 2
    assert cs.pick_best(camps, "vi", "kol", "weex")["id"] == 3
    assert cs.pick_best(camps, "zh", "kol", None) is None


def test_pick_agent_order():
    agents = [{"id": 2, "name": "WEEX Negotiator", "active": True},
              {"id": 3, "name": "BloFin Negotiator", "active": True},
              {"id": 1, "name": "Old", "active": False}]
    assert cs.pick_agent(agents, 3, "weex")[0]["id"] == 3       # source campaign's agent wins
    assert cs.pick_agent(agents, 1, "blofin")[0]["id"] == 3     # inactive -> brand match
    assert cs.pick_agent(agents, None, None)[0]["id"] == 2      # most used (list order)


async def _seed():
    members = [{"user_id": -i, "username": f"kol{i}", "first_name": f"@kol{i}", "last_name": ""}
               for i in range(1, 31)]
    await db.save_scraped_members("Owen_Contacts_0911", 1, 1, "Owen contacts", members)
    async with db.get_db() as conn:
        for i, country in enumerate(["EN"] * 18 + ["CN"] * 9 + ["AR"] * 3):
            await conn.execute(
                "INSERT INTO kol_channels (ukey, username, title, saved_job, country) VALUES (?,?,?,?,?)",
                (f"u{i}", f"ch{i}", f"Channel {i}", "Owen_Contacts_0911", country))
        await conn.commit()
    msg = [{"msg_order": 0, "msg_type": "text",
            "content": "Hey {first_name}, let's discuss a better commission package + marketing support. WEEX has an 8/10 Trust Score. Want the details?"}]
    cid = await db.create_dm_campaign({"name": "VIP", "scrape_job_id": "x_VIP_0711",
                                       "sender_account_ids": [1], "messages": msg, "use_ai_remix": True})
    async with db.get_db() as conn:
        await conn.execute("UPDATE dm_campaigns SET sent_count = 52 WHERE id = ?", (cid,))
        for u in range(13):
            await conn.execute(
                "INSERT INTO ai_followup_chats (account_id, user_id, campaign_id, reply_count) VALUES (1, ?, ?, 2)",
                (1000 + u, cid))
        await conn.execute(
            "INSERT INTO ai_agents (name, provider, model, is_active, api_keys_json) VALUES (?,?,?,?,?)",
            ("WEEX Negotiator", "gemini", "m", 1, json.dumps(["k"])))
        await conn.commit()
    return cid


def test_suggest_route_fills_message_and_settings(client):
    cid = asyncio.run(_seed())
    r = client.get("/api/members/campaigns/suggest", params={"job_id": "Owen_Contacts_0911"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["source_campaign_id"] == cid
    assert s["language"] == "en" and s["audience"] == "kol"
    # contact list has only @usernames -> no "Hey @kol1" greeting
    assert s["messages"][0]["content"].startswith("Hey, let's discuss")
    assert s["use_ai_remix"] is True and s["auto_translate_native"] is True
    assert s["ai_agent_id"] is not None
    assert any("13/52" in x for x in s["reasons"])

    assert client.get("/api/members/campaigns/suggest", params={"job_id": "nope"}).status_code == 404


def test_create_campaign_keeps_agent_and_translate(client):
    asyncio.run(db.save_scraped_members("job_a", 1, 1, "G", [{"user_id": 5, "username": "a", "first_name": "A"}]))
    r = client.post("/api/members/campaigns", json={
        "name": "c", "scrape_job_id": "job_a", "sender_account_ids": [1],
        "messages": [{"msg_type": "text", "content": "hi"}],
        "use_ai_remix": True, "ai_agent_id": 7, "auto_translate_native": 0,
    })
    assert r.status_code == 200, r.text
    c = asyncio.run(db.get_dm_campaign(r.json()["campaign_id"]))
    assert c["ai_agent_id"] == 7
    assert c["auto_translate_native"] == 0
