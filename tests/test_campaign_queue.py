"""DM campaign queue: a campaign sharing a sender with a live one waits, then auto-starts."""
import asyncio

import routes.members as rm


def _fake_db(monkeypatch, campaigns):
    async def get(cid):
        return campaigns.get(cid)

    async def get_all():
        return list(campaigns.values())

    async def set_status(cid, status, **_):
        campaigns[cid]["status"] = status
        campaigns[cid]["updated_at"] = str(len(campaigns[cid].setdefault("hist", [])))
        campaigns[cid]["hist"].append(status)

    monkeypatch.setattr(rm.db, "get_dm_campaign", get)
    monkeypatch.setattr(rm.db, "get_all_dm_campaigns", get_all)
    monkeypatch.setattr(rm.db, "update_dm_campaign_status", set_status)


def test_shared_sender_waits_then_autostarts(monkeypatch):
    campaigns = {
        1: {"id": 1, "status": "draft", "sender_account_ids": [10, 11]},
        2: {"id": 2, "status": "draft", "sender_account_ids": [11]},   # shares 11 with #1
        3: {"id": 3, "status": "draft", "sender_account_ids": [12]},   # independent
    }
    _fake_db(monkeypatch, campaigns)
    gates = {cid: asyncio.Event() for cid in campaigns}

    async def fake_run(cid):
        asyncio.current_task().add_done_callback(rm._kick_campaign_queue)
        await gates[cid].wait()
        campaigns[cid]["status"] = "completed"

    monkeypatch.setattr(rm, "_run_campaign", fake_run)
    monkeypatch.setattr(rm, "_active_campaigns", {})

    async def scenario():
        async with rm._active_campaign_lock:
            assert await rm.launch_campaign(1) == "running"
            assert await rm.launch_campaign(2) == "waiting"
            assert await rm.launch_campaign(3) == "running"
        assert campaigns[2]["status"] == "waiting"

        gates[1].set()                       # campaign 1 finishes → queue kicks
        for _ in range(10):
            await asyncio.sleep(0)
        assert campaigns[2]["status"] == "running"

        gates[2].set(); gates[3].set()
        await asyncio.gather(*[t for t in rm._active_campaigns.values()])

    asyncio.run(scenario())
