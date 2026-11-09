import asyncio
import json
import time
from datetime import datetime, timezone

import database as db
import telegram_client as tg
from routes import members as m


def _msg(ts):
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"Your account is limited until {d.day} {d.strftime('%b %Y, %H:%M')} UTC."


def test_spam_entry_view_marks_expired_limits():
    now = time.time()
    past = tg.spam_entry_view({"status": "limited", "message": _msg(now - 3600)}, now)
    future = tg.spam_entry_view({"status": "limited", "message": _msg(now + 7200)}, now)
    undated = tg.spam_entry_view({"status": "limited", "message": "some users reported you"}, now)
    assert past["expired"] is True and past["until"]
    assert future["expired"] is False
    assert undated["expired"] is False and undated["until"] is None
    assert "expired" not in tg.spam_entry_view({"status": "free"}, now)


def test_campaign_runs_reask_spambot_every_time():
    # 10-min sharing window only, not the old once-a-day reuse.
    assert m.SPAMBOT_RECHECK_HOURS <= 0.25


def test_status_and_availability_treat_expired_limit_as_stale(client):
    now = time.time()
    aid = asyncio.run(db.create_account({"name": "Will", "phone": "+100", "api_id": 1,
                                         "api_hash": "h", "session_name": "s100"}))
    cache = {str(aid): {"status": "limited", "message": _msg(now - 600),
                        "checked_at": datetime.now(timezone.utc).isoformat()}}
    asyncio.run(db.set_setting("spam_status_cache", json.dumps(cache)))

    st = client.get("/api/members/accounts/spam-status").json()["statuses"][str(aid)]
    assert st["expired"] is True
    avail = client.get("/api/members/accounts/availability").json()["accounts"]
    assert str(aid) not in avail  # no stale "SpamBot" restriction shown


def test_recheck_expired_only_asks_for_stale_connected_accounts(client, monkeypatch):
    now = time.time()
    cache = {
        "1": {"status": "limited", "message": _msg(now - 600), "checked_at": "2026-11-08T16:40:00+00:00"},
        "2": {"status": "limited", "message": _msg(now + 9000), "checked_at": "2026-11-08T16:40:00+00:00"},
        "3": {"status": "free", "message": "", "checked_at": "2026-11-08T16:40:00+00:00"},
    }
    asyncio.run(db.set_setting("spam_status_cache", json.dumps(cache)))

    class _C:
        def is_connected(self):
            return True
    asked = []

    async def fake_check(aid, max_age_hours=0):
        asked.append(aid)
        return {"status": "free", "message": "Good news", "checked_at": "2026-11-09T10:00:00+00:00"}
    monkeypatch.setattr(tg, "get_client", lambda aid: _C())
    monkeypatch.setattr(tg, "check_spam_status", fake_check)

    r = client.post("/api/members/accounts/spam-recheck-expired").json()
    assert asked == [1]
    assert r["checked"] == 1 and r["statuses"]["1"]["status"] == "free"
