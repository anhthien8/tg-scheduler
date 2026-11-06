"""MCP server helpers + the two backend pieces it relies on:
- POST /api/members/scrape-jobs/{id}/subset (filtered copy of a scrape job)
- _do_scrape handles basic (non-super) groups.
"""
import sys
from pathlib import Path

import pytest

import database as db

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_server"))
import tgs_logic as L  # noqa: E402

NOW = 1_800_000_000


def test_health_rules_order():
    ok = {"is_logged_in": True}
    assert L.account_health(ok, now=NOW)["ok"]
    assert L.account_health({**ok, "session_error": "x"}, now=NOW)["label"] == "Lỗi session"
    assert L.account_health({"is_logged_in": False}, now=NOW)["label"] == "Offline"
    assert L.account_health({**ok, "is_paused": 1}, now=NOW)["label"] == "Tạm dừng"
    assert L.account_health({**ok, "is_flagged": 1}, now=NOW)["label"] == "Bị flag"
    assert L.account_health({**ok, "peerflood_until": NOW + 60}, now=NOW)["label"] == "PeerFlood"
    assert L.account_health({**ok, "peerflood_until": NOW - 60}, now=NOW)["ok"]
    assert L.account_health(ok, {"status": "limited"}, now=NOW)["label"] == "SpamBot giới hạn"


def test_public_account_hides_secrets():
    acc = {"id": 1, "name": "A", "api_hash": "h", "api_id": "1", "phone": "+1", "proxy_url": "p",
           "session_name": "s", "is_logged_in": True, "user_info": {"username": "a"}}
    out = L.public_account(acc, now=NOW)
    for k in ("api_hash", "api_id", "phone", "proxy_url", "session_name"):
        assert k not in out


def test_filter_members_reasons():
    members = [
        {"user_id": 1, "username": "alice", "first_name": "Alice"},
        {"user_id": 2, "username": "weex_bob", "first_name": "Bob"},
        {"user_id": 3, "username": None, "first_name": "Miracle | WEEX"},
        {"user_id": 4, "username": "ourself", "first_name": "Us"},
        {"user_id": 5, "username": "helperbot", "first_name": "B"},
        {"user_id": 6, "username": None, "first_name": "NoUser"},
        {"user_id": 7, "username": "manual", "first_name": "M"},
    ]
    kept, excl = L.filter_members(members, internal_ids={4}, exclude_keywords=["Weex"],
                                  exclude_usernames=["@manual"], require_username=True)
    assert [m["user_id"] for m in kept] == [1]
    reasons = {m["user_id"]: m["reason"] for m in excl}
    assert reasons[2].startswith("chứa từ khóa")
    assert reasons[3].startswith("chứa từ khóa")
    assert reasons[4] == "account nội bộ"
    assert reasons[5] == "bot"
    assert reasons[6] == "không có username"
    assert reasons[7] == "loại thủ công"


def test_internal_identity():
    ids, names = L.internal_identity([{"telegram_user_id": "42", "user_info": {"username": "Me"}}])
    assert ids == {42} and names == {"me"}


def test_server_has_no_start_tool():
    src = (ROOT / "mcp_server" / "server.py").read_text(encoding="utf-8")
    assert "/start" not in src
    assert "scheduled_at" not in src


def test_scrape_handles_basic_groups():
    src = (ROOT / "routes" / "members.py").read_text(encoding="utf-8")
    block = src.split("async def _do_scrape")[1].split("@router.get")[0]
    assert "InputPeerChat" in block and "ChatParticipantCreator" in block


@pytest.mark.asyncio
async def test_scrape_subset_endpoint(client):
    rows = [{"user_id": i, "username": f"u{i}", "first_name": f"U{i}", "last_name": "", "phone": "",
             "is_bot": False, "is_premium": i == 2, "status": "active", "last_seen": "recently",
             "lang_code": "vi"} for i in (1, 2, 3)]
    await db.save_scraped_members("scrape_src_job", 7, 99, "Src", rows)
    r = client.post("/api/members/scrape-jobs/scrape_src_job/subset",
                    json={"keep_user_ids": [2, 3], "group_title": "Lọc"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2 and body["source_count"] == 3 and body["group_title"] == "Lọc"
    new = await db.get_scraped_members(body["scrape_job_id"])
    assert {m["user_id"] for m in new} == {2, 3}
    assert next(m for m in new if m["user_id"] == 2)["is_premium"] == 1
    assert all(m["account_id"] == 7 and m["lang_code"] == "vi" for m in new)
    assert await db.count_scraped_members("scrape_src_job") == 3  # source untouched
    assert client.post("/api/members/scrape-jobs/scrape_src_job/subset",
                       json={"keep_user_ids": [999]}).status_code == 400
    assert client.post("/api/members/scrape-jobs/nope/subset",
                       json={"keep_user_ids": [1]}).status_code == 404
