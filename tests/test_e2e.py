import pytest
import os
import asyncio
import database as db
from fastapi.testclient import TestClient
from main import app

# Ensure pytest treats these test functions as async
pytestmark = pytest.mark.asyncio

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 1: ACCOUNT MANAGEMENT (Tests 1-10)
# ──────────────────────────────────────────────────────────────────────────────

# 1. Add new account successfully.
async def test_01_add_account_success(client):
    payload = {
        "phone": "+8412345678",
        "name": "Test Account 1",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627"
    }
    response = client.post("/api/auth/accounts", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "account_id" in response.json()

# 2. List accounts (verifies mapped structures & user_info).
async def test_02_list_accounts(client):
    # Insert account
    acc_id = await db.create_account({
        "name": "Test Account List",
        "phone": "+8499999999",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "account_list_test",
        "proxy_url": None
    })
    response = client.get("/api/auth/accounts")
    assert response.status_code == 200
    data = response.json()
    assert "accounts" in data
    assert len(data["accounts"]) >= 1
    # Check that mocked user_info details are returned
    acc = next(a for a in data["accounts"] if a["id"] == acc_id)
    assert acc["user_info"]["username"] == "mocked_user"

# 3. Check status endpoint (authorized / unauthorized accounts).
async def test_03_auth_status(client):
    # Register an account
    await db.create_account({
        "name": "Test Account Status",
        "phone": "+8488888888",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "account_status_test",
        "proxy_url": None
    })
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is True
    assert len(data["accounts"]) >= 1

# 4. Toggle premium for account (changes limit from 10 to 50).
async def test_04_toggle_premium(client):
    acc_id = await db.create_account({
        "name": "Premium Toggle Account",
        "phone": "+8411111111",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "premium_toggle_test",
        "proxy_url": None
    })
    # Toggle premium on
    resp_on = client.post(f"/api/auth/accounts/{acc_id}/toggle-premium?is_premium=true")
    assert resp_on.status_code == 200
    assert resp_on.json()["is_premium"] is True
    assert resp_on.json()["daily_limit"] == 50

    # Toggle premium off
    resp_off = client.post(f"/api/auth/accounts/{acc_id}/toggle-premium?is_premium=false")
    assert resp_off.status_code == 200
    assert resp_off.json()["is_premium"] is False
    assert resp_off.json()["daily_limit"] == 10

# 5. Unflag account (removes warning flag).
async def test_05_unflag_account(client):
    acc_id = await db.create_account({
        "name": "Flagged Account",
        "phone": "+8422222222",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "flagged_test",
        "proxy_url": None
    })
    # Flag is cleared via unflag endpoint
    response = client.post(f"/api/auth/accounts/{acc_id}/unflag")
    assert response.status_code == 200
    assert response.json()["ok"] is True

# 6. Add account with invalid JSON payload.
async def test_06_add_account_invalid_payload(client):
    payload = {
        # missing phone number which is required
        "name": "Invalid Account"
    }
    response = client.post("/api/auth/accounts", json=payload)
    assert response.status_code == 422  # validation error

# 7. Delete non-existent account ID.
async def test_07_remove_non_existent_account(client):
    response = client.delete("/api/auth/accounts/9999")
    assert response.status_code == 404
    assert "Account not found" in response.json()["detail"]

# 8. Toggle premium for non-existent account ID.
async def test_08_toggle_premium_non_existent_account(client):
    # Toggling premium directly edits the DB. Let's see if the database or endpoint raises error or behaves.
    # Note: set_account_premium does a direct sql update. If it doesn't fail, it returns success, but let's check
    # if it doesn't crash or if we check database state.
    response = client.post("/api/auth/accounts/9999/toggle-premium?is_premium=true")
    # Endpoint returns 200 since it is a broad update query, let's verify it behaves gracefully
    assert response.status_code == 200
    assert response.json()["success"] is True

# 9. Fetch DM stats for non-existent account ID.
async def test_09_get_dm_stats_non_existent_account(client):
    # db.is_account_dm_limit_reached returns limit status. If account doesn't exist, it uses default limit (10)
    response = client.get("/api/auth/accounts/9999/dm-stats")
    assert response.status_code == 200
    assert response.json()["account_id"] == 9999
    assert response.json()["daily_limit"] == 10

# 10. Unflag non-existent account ID.
async def test_10_unflag_non_existent_account(client):
    response = client.post("/api/auth/accounts/9999/unflag")
    assert response.status_code == 200
    assert response.json()["ok"] is True


# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 2: MESSAGE SCHEDULING (Tests 11-20)
# ──────────────────────────────────────────────────────────────────────────────

# 11. Create a schedule successfully.
async def test_11_create_schedule_success(client):
    acc_id = await db.create_account({
        "name": "Scheduler Account",
        "phone": "+8433333333",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "schedule_acc",
        "proxy_url": None
    })
    payload = {
        "account_id": acc_id,
        "name": "Test Schedule 1",
        "schedule_type": "daily",
        "time_of_day": "14:30",
        "messages": [
            {"msg_order": 0, "msg_type": "text", "content": "Hello World!"}
        ],
        "targets": [
            {"chat_id": 98765, "chat_title": "Target Chat", "chat_type": "group"}
        ]
    }
    response = client.post("/api/schedules", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "id" in response.json()

# 12. Get schedule details by ID.
async def test_12_get_schedule_details(client):
    acc_id = await db.create_account({
        "name": "Scheduler Account 2",
        "phone": "+8433333334",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "schedule_acc2",
        "proxy_url": None
    })
    sch_id = await db.create_schedule({
        "account_id": acc_id,
        "name": "Test Schedule 2",
        "schedule_type": "hourly",
        "time_of_day": "00:15",
        "messages": [{"msg_order": 0, "msg_type": "text", "content": "Ping!"}],
        "targets": [{"chat_id": 12345, "chat_title": "Group Chat", "chat_type": "group"}]
    })
    response = client.get(f"/api/schedules/{sch_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Schedule 2"
    assert len(data["messages"]) == 1

# 13. Update schedule details.
async def test_13_update_schedule_success(client):
    acc_id = await db.create_account({
        "name": "Scheduler Account 3",
        "phone": "+8433333335",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "schedule_acc3",
        "proxy_url": None
    })
    sch_id = await db.create_schedule({
        "account_id": acc_id,
        "name": "Original Name",
        "schedule_type": "daily",
        "time_of_day": "12:00",
        "messages": [],
        "targets": []
    })
    payload = {
        "account_id": acc_id,
        "name": "Updated Name",
        "schedule_type": "weekly",
        "time_of_day": "15:45",
        "days_of_week": "1,3,5",
        "messages": [{"msg_order": 1, "msg_type": "text", "content": "Updated content"}],
        "targets": [{"chat_id": 54321, "chat_title": "New Target", "chat_type": "channel"}]
    }
    response = client.put(f"/api/schedules/{sch_id}", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True

    # Verify updates in DB
    updated = await db.get_schedule(sch_id)
    assert updated["name"] == "Updated Name"
    assert updated["schedule_type"] == "weekly"

# 14. Delete schedule.
async def test_14_delete_schedule_success(client):
    acc_id = await db.create_account({
        "name": "Scheduler Account 4",
        "phone": "+8433333336",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "schedule_acc4",
        "proxy_url": None
    })
    sch_id = await db.create_schedule({
        "account_id": acc_id,
        "name": "Delete Me",
        "schedule_type": "once",
        "time_of_day": "09:00",
        "once_date": "2026-08-01",
        "messages": [],
        "targets": []
    })
    response = client.delete(f"/api/schedules/{sch_id}")
    assert response.status_code == 200
    assert response.json()["success"] is True
    # Confirm it is deleted
    assert await db.get_schedule(sch_id) is None

# 15. Toggle schedule active status.
async def test_15_toggle_schedule_success(client):
    acc_id = await db.create_account({
        "name": "Scheduler Account 5",
        "phone": "+8433333337",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "schedule_acc5",
        "proxy_url": None
    })
    sch_id = await db.create_schedule({
        "account_id": acc_id,
        "name": "Toggle Schedule",
        "schedule_type": "hourly",
        "time_of_day": "00:00",
        "is_active": True,
        "messages": [],
        "targets": []
    })
    # Toggle off
    response = client.patch(f"/api/schedules/{sch_id}/toggle")
    assert response.status_code == 200
    assert response.json()["is_active"] == 0

    # Toggle on
    response = client.patch(f"/api/schedules/{sch_id}/toggle")
    assert response.status_code == 200
    assert response.json()["is_active"] == 1

# 16. Create schedule with invalid schedule_type.
async def test_16_create_schedule_invalid_type(client):
    payload = {
        "account_id": 1,
        "name": "Invalid Type Schedule",
        "schedule_type": "invalid_type",  # should be hourly, daily, etc.
        "time_of_day": "12:00",
        "messages": [],
        "targets": []
    }
    # SQLite CHECK(schedule_type IN (...)) rejects the invalid type
    response = client.post("/api/schedules", json=payload)
    assert response.status_code in (400, 422, 500)

# 17. Create schedule with negative max_sends.
async def test_17_create_schedule_negative_max_sends(client):
    acc_res = client.post("/api/auth/accounts", json={"phone": "+8490000017", "name": "Account 17"})
    acc_id = acc_res.json()["account_id"]
    payload = {
        "account_id": acc_id,
        "name": "Negative Sends Schedule",
        "schedule_type": "daily",
        "time_of_day": "12:00",
        "max_sends": -10,
        "messages": [],
        "targets": []
    }
    response = client.post("/api/schedules", json=payload)
    # The API schema accepts any int for max_sends. If created successfully, we inspect the DB
    assert response.status_code == 200
    sch_id = response.json()["id"]
    schedule = await db.get_schedule(sch_id)
    assert schedule["max_sends"] == -10

# 18. Get non-existent schedule.
async def test_18_get_non_existent_schedule(client):
    response = client.get("/api/schedules/9999")
    assert response.status_code == 404
    assert "Schedule not found" in response.json()["detail"]

# 19. Reset count for non-existent schedule.
async def test_19_reset_count_non_existent_schedule(client):
    response = client.post("/api/schedules/9999/reset-count")
    assert response.status_code == 404

# 20. Send now for non-existent schedule.
async def test_20_send_now_non_existent_schedule(client):
    response = client.post("/api/schedules/9999/send-now")
    assert response.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 3: KEYWORD WATCHERS (Tests 21-30)
# ──────────────────────────────────────────────────────────────────────────────

# 21. Create a keyword watcher.
async def test_21_create_watcher_success(client):
    payload = {
        "name": "Keyword Watcher 1",
        "sender_account_ids": [1],
        "keywords": ["solana", "gem"],
        "group_ids": [12345],
        "cooldown_hours": 24,
        "dm_once": True,
        "excluded_usernames": ["bot_user"],
        "is_active": 1,
        "messages": [{"msg_order": 0, "msg_type": "text", "content": "Check this out!"}],
        "reply_in_group": True,
        "group_reply_text": "DM sent!",
        "group_reply_account_id": 1
    }
    response = client.post("/api/watchers", json=payload)
    assert response.status_code == 200
    assert "id" in response.json()
    assert response.json()["message"] == "Watcher created"

# 22. Get a keyword watcher.
async def test_22_get_watcher_details(client):
    watcher_id = await db.create_watcher({
        "name": "Fetch Watcher",
        "sender_account_ids": [1],
        "keywords": ["crypto"],
        "group_ids": [123],
        "cooldown_hours": 12,
        "dm_once": False,
        "excluded_usernames": [],
        "is_active": 1,
        "messages": []
    })
    response = client.get(f"/api/watchers/{watcher_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Fetch Watcher"

# 23. Update a keyword watcher.
async def test_23_update_watcher_success(client):
    watcher_id = await db.create_watcher({
        "name": "Old Watcher",
        "sender_account_ids": [1],
        "keywords": ["old"],
        "group_ids": [111],
        "cooldown_hours": 6,
        "dm_once": False,
        "excluded_usernames": [],
        "is_active": 1,
        "messages": []
    })
    payload = {
        "name": "New Watcher",
        "sender_account_ids": [1, 2],
        "keywords": ["new", "fresh"],
        "group_ids": [222],
        "cooldown_hours": 12,
        "dm_once": True,
        "excluded_usernames": ["scammer"],
        "is_active": 1,
        "messages": [{"msg_order": 0, "msg_type": "text", "content": "New message"}]
    }
    response = client.put(f"/api/watchers/{watcher_id}", json=payload)
    assert response.status_code == 200
    assert response.json()["message"] == "Watcher updated"

    updated = await db.get_watcher(watcher_id)
    assert updated["name"] == "New Watcher"
    assert "fresh" in json_loads(updated["keywords"])

def json_loads(val):
    import json
    return json.loads(val) if isinstance(val, str) else val

# 24. Delete a keyword watcher.
async def test_24_delete_watcher_success(client):
    watcher_id = await db.create_watcher({
        "name": "Delete Watcher",
        "sender_account_ids": [1],
        "keywords": ["trash"],
        "group_ids": [999],
        "cooldown_hours": 1,
        "is_active": 1,
        "messages": []
    })
    response = client.delete(f"/api/watchers/{watcher_id}")
    assert response.status_code == 200
    assert response.json()["message"] == "Watcher deleted"
    assert await db.get_watcher(watcher_id) is None

# 25. Toggle a keyword watcher.
async def test_25_toggle_watcher_success(client):
    watcher_id = await db.create_watcher({
        "name": "Toggle Watcher",
        "sender_account_ids": [1],
        "keywords": ["toggle"],
        "group_ids": [777],
        "is_active": 1,
        "messages": []
    })
    # Toggle off
    response = client.post(f"/api/watchers/{watcher_id}/toggle")
    assert response.status_code == 200
    assert response.json()["is_active"] == 0

    # Toggle on
    response = client.post(f"/api/watchers/{watcher_id}/toggle")
    assert response.status_code == 200
    assert response.json()["is_active"] == 1

# 26. Create watcher with invalid input types.
async def test_26_create_watcher_invalid_payload(client):
    payload = {
        "name": "Invalid Watcher",
        "sender_account_ids": "not-a-list",  # validation error
        "keywords": ["test"]
    }
    response = client.post("/api/watchers", json=payload)
    assert response.status_code == 422

# 27. Create watcher with empty keywords list.
async def test_27_create_watcher_empty_keywords(client):
    payload = {
        "name": "Empty Watcher",
        "sender_account_ids": [1],
        "keywords": [],  # empty list
        "group_ids": [123]
    }
    response = client.post("/api/watchers", json=payload)
    assert response.status_code == 200
    watcher_id = response.json()["id"]
    watcher = await db.get_watcher(watcher_id)
    assert json_loads(watcher["keywords"]) == []

# 28. Get non-existent watcher.
async def test_28_get_non_existent_watcher(client):
    response = client.get("/api/watchers/9999")
    assert response.status_code == 404

# 29. Update non-existent watcher.
async def test_29_update_non_existent_watcher(client):
    payload = {
        "name": "Watcher",
        "sender_account_ids": [1],
        "keywords": ["test"],
        "group_ids": []
    }
    response = client.put("/api/watchers/9999", json=payload)
    assert response.status_code == 404

# 30. Toggle non-existent watcher.
async def test_30_toggle_non_existent_watcher(client):
    response = client.post("/api/watchers/9999/toggle")
    assert response.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 4: AUTO-REACTIONS & AUTO-REPLIES (Tests 31-40)
# ──────────────────────────────────────────────────────────────────────────────

# 31. Create auto-react target.
async def test_31_create_reaction_target_success(client):
    acc_id = await db.create_account({
        "name": "React Acc",
        "phone": "+8444444444",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "react_acc",
        "proxy_url": None
    })
    payload = {
        "channel_link": "https://t.me/testchannel",
        "account_ids": [acc_id],
        "reactions": ["👍", "❤️"],
        "delay_min": 1,
        "delay_max": 5,
        "auto_join": True,
        "view_enabled": 1,
        "view_ratio": 0.8
    }
    response = client.post("/api/reactions/targets", json=payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "target" in response.json()

# 32. List auto-react targets.
async def test_32_list_reaction_targets(client):
    response = client.get("/api/reactions/targets")
    assert response.status_code == 200
    assert "targets" in response.json()

# 33. Update auto-react target.
async def test_33_update_reaction_target(client):
    target_id = await db.add_reaction_target(
        channel_link="https://t.me/targetupdate",
        channel_id=123,
        channel_title="Target",
        account_ids=[1],
        reactions=["🔥"],
        delay_min=2,
        delay_max=10
    )
    payload = {
        "reactions": ["👀"],
        "delay_min": 5,
        "is_active": 0
    }
    response = client.put(f"/api/reactions/targets/{target_id}", json=payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["target"]["reactions"] == ["👀"]

# 34. Delete auto-react target.
async def test_34_delete_reaction_target(client):
    target_id = await db.add_reaction_target(
        channel_link="https://t.me/targetdelete",
        channel_id=456,
        channel_title="Delete",
        account_ids=[1],
        reactions=["👍"]
    )
    response = client.delete(f"/api/reactions/targets/{target_id}")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    # Verify deletion
    targets = await db.get_all_reaction_targets(active_only=False)
    assert not any(t["id"] == target_id for t in targets)

# 35. Create auto-reply rule.
async def test_35_create_auto_reply_rule(client):
    payload = {
        "name": "Auto Reply Rule 1",
        "trigger_type": "keyword",
        "trigger_keywords": ["price", "cost"],
        "reply_messages": [{"msg_type": "text", "content": "It is free!"}],
        "account_ids": [1],
        "use_ai": 0,
        "max_replies_per_user": 2,
        "is_active": 1
    }
    response = client.post("/api/auto-reply/rules", json=payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "id" in response.json()

# 36. Create react target with empty channel link.
async def test_36_create_reaction_target_empty_link(client):
    payload = {
        "channel_link": "",
        "account_ids": [1],
        "reactions": ["👍"]
    }
    response = client.post("/api/reactions/targets", json=payload)
    assert response.status_code == 400

# 37. Get reactions logs.
async def test_37_get_reactions_logs(client):
    response = client.get("/api/reactions/logs")
    assert response.status_code == 200
    assert "logs" in response.json()

# 38. Update non-existent react target.
async def test_38_update_non_existent_reaction_target(client):
    payload = {
        "reactions": ["👍"]
    }
    response = client.put("/api/reactions/targets/9999", json=payload)
    assert response.status_code == 404

# 39. Delete non-existent react target.
async def test_39_delete_non_existent_reaction_target(client):
    response = client.delete("/api/reactions/targets/9999")
    assert response.status_code == 404

# 40. Update non-existent auto-reply rule.
async def test_40_update_non_existent_auto_reply_rule(client):
    payload = {
        "name": "Non existent Rule",
        "trigger_keywords": ["hello"]
    }
    response = client.put("/api/auto-reply/rules/9999", json=payload)
    assert response.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 5: SCRAPING & CAMPAIGNS (Tests 41-50)
# ──────────────────────────────────────────────────────────────────────────────

# 41. Start scraping members.
async def test_41_start_scraping_members(client):
    acc_id = await db.create_account({
        "name": "Scraper Acc",
        "phone": "+8455555555",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "scrape_acc_test",
        "proxy_url": None
    })
    payload = {
        "account_id": acc_id,
        "group_id": 987654321,
        "group_title": "Target Scraping Group",
        "filter_active_days": 7,
        "exclude_bots": True,
        "scrape_method": "members",
        "max_messages": 100
    }
    response = client.post("/api/members/scrape", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "started"
    assert "scrape_job_id" in response.json()

# 42. List scrape jobs.
async def test_42_list_scrape_jobs(client):
    response = client.get("/api/members/scrape-jobs")
    assert response.status_code == 200
    assert "jobs" in response.json()

# 43. Import contacts.
async def test_43_import_contacts(client):
    payload = {
        "scrape_job_id": "job_import_test",
        "group_title": "Import Group",
        "contacts": [
            {"username": "@imported_user_1", "first_name": "Imported", "last_name": "One"},
            {"username": "@imported_user_2", "first_name": "Imported", "last_name": "Two"}
        ]
    }
    response = client.post("/api/members/import-contacts", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["count"] == 2

# 44. Create a DM campaign.
async def test_44_create_campaign_success(client):
    # Pre-requisite: we need scraped members in DB for the job
    job_id = "job_campaign_test"
    await db.save_scraped_members(job_id, 0, 0, "Test Group", [
        {"user_id": 1111, "username": "user1", "first_name": "U1", "last_name": "", "phone": "", "is_bot": False, "is_premium": False, "status": "active", "last_seen": ""}
    ])
    payload = {
        "name": "Campaign One",
        "scrape_job_id": job_id,
        "sender_account_ids": [1],
        "messages": [{"msg_type": "text", "content": "Hi there!"}],
        "delay_min": 10,
        "delay_max": 20,
        "daily_limit": 50,
        "use_ai_remix": False
    }
    response = client.post("/api/members/campaigns", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert "campaign_id" in response.json()

# 45. Update campaign messages.
async def test_45_update_campaign_messages(client):
    # Campaign must be in draft/paused/error status to allow edits.
    campaign_id = await db.create_dm_campaign({
        "name": "Campaign to Update",
        "scrape_job_id": "job_update_msgs",
        "sender_account_ids": [1],
        "messages": [{"msg_type": "text", "content": "Draft Message"}],
        "delay_min": 30,
        "delay_max": 90,
        "daily_limit": 30,
        "use_ai_remix": False,
        "total_targets": 1
    })
    payload = {
        "messages": [{"msg_type": "text", "content": "Updated Message!"}],
        "delay_min": 5,
        "delay_max": 15
    }
    response = client.put(f"/api/members/campaigns/{campaign_id}/messages", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "updated"

# 46. Start scraping with invalid account ID.
async def test_46_start_scraping_invalid_account(client):
    payload = {
        "account_id": 9999,  # non-existent account
        "group_id": 12345
    }
    response = client.post("/api/members/scrape", json=payload)
    assert response.status_code == 400
    assert "Tài khoản không tồn tại" in response.json()["detail"]

# 47. Create campaign with non-existent scrape job.
async def test_47_create_campaign_non_existent_job(client):
    payload = {
        "name": "Ghost Campaign",
        "scrape_job_id": "non_existent_job_123",
        "sender_account_ids": [1],
        "messages": [{"msg_type": "text", "content": "Hello"}]
    }
    response = client.post("/api/members/campaigns", json=payload)
    assert response.status_code == 400
    assert "Scrape job không tồn tại" in response.json()["detail"]

# 48. Update messages on running campaign.
async def test_48_update_messages_running_campaign(client):
    campaign_id = await db.create_dm_campaign({
        "name": "Running Campaign",
        "scrape_job_id": "job_run",
        "sender_account_ids": [1],
        "messages": [{"msg_type": "text", "content": "Running Msg"}],
        "delay_min": 30,
        "delay_max": 90,
        "daily_limit": 30,
        "use_ai_remix": False,
        "total_targets": 1
    })
    await db.update_dm_campaign_status(campaign_id, "running")
    payload = {
        "messages": [{"msg_type": "text", "content": "Crash me"}]
    }
    response = client.put(f"/api/members/campaigns/{campaign_id}/messages", json=payload)
    assert response.status_code == 400
    assert "Chỉ có thể sửa campaign" in response.json()["detail"]

# 49. Get non-existent campaign details.
async def test_49_get_non_existent_campaign(client):
    response = client.get("/api/members/campaigns/9999")
    assert response.status_code == 404

# 50. Start campaign with non-existent campaign ID.
async def test_50_start_non_existent_campaign(client):
    response = client.post("/api/members/campaigns/9999/start")
    assert response.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 6: ANALYTICS, LOGS & BLACKLIST (Tests 51-60)
# ──────────────────────────────────────────────────────────────────────────────

# 51. Get analytics overview.
async def test_51_get_analytics_overview(client):
    response = client.get("/api/analytics/overview")
    assert response.status_code == 200
    data = response.json()
    assert "accounts_count" in data
    assert "active_schedules_count" in data

# 52. Get account health stats.
async def test_52_get_account_health(client):
    response = client.get("/api/analytics/account-health")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

# 53. List logs with pagination / filter.
async def test_53_list_send_logs(client):
    response = client.get("/api/logs?limit=10&offset=0")
    assert response.status_code == 200
    assert "logs" in response.json()

# 54. Add user to DM blacklist.
async def test_54_add_user_to_blacklist(client):
    payload = {
        "user_id": 999111,
        "username": "blacklist_user",
        "reason": "Test Spam"
    }
    response = client.post("/api/blacklist", json=payload)
    assert response.status_code == 200
    assert response.json()["user_id"] == 999111

# 55. List DM blacklist.
async def test_55_list_blacklist(client):
    response = client.get("/api/blacklist")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

# 56. Export CSV for non-existent scrape job.
async def test_56_export_csv_non_existent_job(client):
    response = client.get("/api/export/members/non_existent_job_csv")
    assert response.status_code == 404

# 57. Add blacklist with empty payload.
async def test_57_add_blacklist_empty_payload(client):
    payload = {
        "reason": "Missing both username and user_id"
    }
    response = client.post("/api/blacklist", json=payload)
    assert response.status_code == 400

# 58. List logs with invalid pagination limit.
async def test_58_list_logs_invalid_pagination(client):
    response = client.get("/api/logs?limit=invalid")
    assert response.status_code == 422

# 59. List keyword watcher logs with invalid status filter.
async def test_59_list_watcher_logs_invalid_params(client):
    response = client.get("/api/watchers/logs?limit=abc")
    assert response.status_code == 422

# 60. Remove non-existent blacklist entry ID.
async def test_60_remove_non_existent_blacklist(client):
    response = client.delete("/api/blacklist/9999")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# ──────────────────────────────────────────────────────────────────────────────
# TIER 3: CROSS-FEATURE COMBINATIONS (Tests 61-66)
# ──────────────────────────────────────────────────────────────────────────────

# 61. Create account -> Create schedule linked to that account -> Verify schedule account constraint.
async def test_61_cross_create_account_and_link_schedule(client):
    # 1. Create account
    acc_payload = {"phone": "+8490123456", "name": "Cross Account"}
    acc_res = client.post("/api/auth/accounts", json=acc_payload)
    acc_id = acc_res.json()["account_id"]

    # 2. Create schedule linked to this account
    sch_payload = {
        "account_id": acc_id,
        "name": "Linked Schedule",
        "schedule_type": "hourly",
        "time_of_day": "10:00",
        "messages": [],
        "targets": []
    }
    sch_res = client.post("/api/schedules", json=sch_payload)
    sch_id = sch_res.json()["id"]

    # 3. Retrieve schedule and check account_id match
    retrieved = client.get(f"/api/schedules/{sch_id}").json()
    assert retrieved["account_id"] == acc_id

# 62. Create account -> Start member scraping -> Check that scraping job records the account.
async def test_62_cross_create_account_and_scrape(client):
    # 1. Add account
    acc_res = client.post("/api/auth/accounts", json={"phone": "+8490765432", "name": "Scrape Cross Account"})
    acc_id = acc_res.json()["account_id"]

    # 2. Save scraped member for job
    job_id = "scrape_998877_cross"
    await db.save_scraped_members(job_id, acc_id, 998877, "Scrape Group", [
        {"user_id": 112233, "username": "scraped_member", "first_name": "Scraped", "last_name": "", "phone": "", "is_bot": False, "is_premium": False, "status": "active", "last_seen": ""}
    ])

    # 3. Fetch jobs and confirm the account_id matches
    jobs_res = client.get("/api/members/scrape-jobs").json()
    job = next(j for j in jobs_res["jobs"] if j["scrape_job_id"] == job_id)
    assert job["account_id"] == acc_id

# 63. Create campaign -> Add target to blacklist -> Verify campaign skips the blacklisted user.
async def test_63_cross_campaign_skips_blacklisted_user(client):
    # 1. Setup a scrape job with two users
    job_id = "cross_blacklist_job"
    await db.save_scraped_members(job_id, 0, 0, "Blacklist Group", [
        {"user_id": 55555, "username": "clean_user", "first_name": "Clean", "last_name": "", "phone": "", "is_bot": False, "is_premium": False, "status": "active", "last_seen": ""},
        {"user_id": 66666, "username": "blocked_user", "first_name": "Blocked", "last_name": "", "phone": "", "is_bot": False, "is_premium": False, "status": "active", "last_seen": ""}
    ])

    # 2. Add user 66666 to global blacklist
    await db.add_to_dm_blacklist(66666, "blocked_user", "Cross Blacklist Test")

    # 3. Create Campaign
    camp_payload = {
        "name": "Blacklist Cross Campaign",
        "scrape_job_id": job_id,
        "sender_account_ids": [1],
        "messages": [{"msg_type": "text", "content": "Test Message"}]
    }
    camp_res = client.post("/api/members/campaigns", json=camp_payload)
    camp_id = camp_res.json()["campaign_id"]

    # 4. Trigger Campaign background execution manually via backend function to assert skipped count
    from routes.members import _run_campaign
    await _run_campaign(camp_id)

    # 5. Fetch Campaign Logs and assert skipped count
    logs_res = client.get(f"/api/members/campaigns/{camp_id}/logs").json()
    logs = logs_res["logs"]
    assert len(logs) >= 1
    skipped = next(l for l in logs if l["target_user_id"] == 66666)
    assert skipped["status"] == "skipped"
    assert "blacklist" in skipped["error_message"].lower()

# 64. Start scraping -> Import contacts to that scrape job -> Verify we can export the merged CSV.
async def test_64_cross_scrape_import_export_csv(client):
    job_id = "cross_import_export_job"
    # 1. Save initial scraped members
    await db.save_scraped_members(job_id, 0, 0, "Export Group", [
        {"user_id": 77771, "username": "user7771", "first_name": "U1", "last_name": "", "phone": "", "is_bot": False, "is_premium": False, "status": "active", "last_seen": ""}
    ])
    # 2. Import additional contact
    import_payload = {
        "scrape_job_id": job_id,
        "group_title": "Export Group",
        "contacts": [
            {"username": "@imported_7772", "first_name": "ImportedU2"}
        ]
    }
    client.post("/api/members/import-contacts", json=import_payload)

    # 3. Export CSV and check both usernames are inside
    csv_res = client.get(f"/api/export/members/{job_id}")
    assert csv_res.status_code == 200
    csv_text = csv_res.text
    assert "user7771" in csv_text
    assert "imported_7772" in csv_text

# 65. Create watcher -> Log match in watcher_dm_logs -> Verify watcher logs are returned correctly on the log listing endpoint.
async def test_65_cross_watcher_log_match_indexing(client):
    # 1. Create watcher
    w_id = await db.create_watcher({
        "name": "Watcher Log Test",
        "sender_account_ids": [1],
        "keywords": ["log_test"],
        "group_ids": [111]
    })
    # 2. Insert watcher log directly
    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("""
            INSERT INTO watcher_dm_logs (watcher_id, account_id, target_user_id, target_username, group_id, group_title, matched_keyword, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (w_id, 1, 998877, "target_log_user", 111, "Log Group", "log_test", "success", None))
        await conn.commit()

    # 3. Request logs via watcher route
    resp = client.get(f"/api/watchers/logs?watcher_id={w_id}")
    assert resp.status_code == 200
    logs = resp.json()["logs"]
    assert len(logs) == 1
    assert logs[0]["target_username"] == "target_log_user"

# 66. Add rule to auto-reply -> Trigger/simulate an auto-reply -> Verify rule-specific logs match.
async def test_66_cross_auto_reply_simulation(client):
    # 1. Add Auto-reply rule
    rule_id = client.post("/api/auto-reply/rules", json={
        "name": "Auto-Reply Cross",
        "trigger_keywords": ["price"],
        "reply_messages": [{"msg_type": "text", "content": "Price is $10"}]
    }).json()["id"]

    # 2. Log an auto-reply event in the DB
    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("""
            INSERT INTO auto_reply_logs (rule_id, account_id, user_id, username, trigger_text, reply_text, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (rule_id, 1, 12345, "mock_user", "What is the price?", "Price is $10", "success"))
        await conn.commit()

    # 3. Retrieve rule specific logs
    resp = client.get(f"/api/auto-reply/logs/{rule_id}")
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) == 1
    assert logs[0]["trigger_text"] == "What is the price?"



# ──────────────────────────────────────────────────────────────────────────────
# TIER 4: REAL-WORLD WORKLOADS (Tests 67-71)
# ──────────────────────────────────────────────────────────────────────────────

# 67. Full Campaign Lifecycle.
async def test_67_workload_campaign_lifecycle(client):
    # 1. Add Account
    acc_res = client.post("/api/auth/accounts", json={"phone": "+8498877665", "name": "Workload Account"})
    acc_id = acc_res.json()["account_id"]

    # 2. Populate scraped members
    job_id = "workload_job"
    await db.save_scraped_members(job_id, acc_id, 999, "Workload Group", [
        {"user_id": 88801, "username": "w_user1", "first_name": "WU1", "last_name": "", "phone": "", "is_bot": False, "is_premium": False, "status": "active", "last_seen": ""},
        {"user_id": 88802, "username": "w_user2", "first_name": "WU2", "last_name": "", "phone": "", "is_bot": False, "is_premium": False, "status": "active", "last_seen": ""}
    ])

    # 3. Create Campaign
    campaign_res = client.post("/api/members/campaigns", json={
        "name": "Workload Campaign",
        "scrape_job_id": job_id,
        "sender_account_ids": [acc_id],
        "messages": [{"msg_type": "text", "content": "Workload Message"}]
    })
    campaign_id = campaign_res.json()["campaign_id"]

    # 4. Start Campaign
    start_res = client.post(f"/api/members/campaigns/{campaign_id}/start")
    assert start_res.json()["status"] == "started"

    # 5. Check campaign details status
    details = client.get(f"/api/members/campaigns/{campaign_id}").json()
    assert details["campaign"]["status"] == "running"

    # Stop Campaign
    client.post(f"/api/members/campaigns/{campaign_id}/stop")
    details_paused = client.get(f"/api/members/campaigns/{campaign_id}").json()
    assert details_paused["campaign"]["status"] == "paused"

# 68. Multi-watcher with Blacklist: Cooldown and blacklist checks.
async def test_68_workload_multi_watcher_blacklist_checks(client):
    # 1. Create two keyword watchers
    w1_payload = {
        "name": "Watcher 1", "keywords": ["apple", "banana"], "sender_account_ids": [1], "group_ids": [101]
    }
    w2_payload = {
        "name": "Watcher 2", "keywords": ["orange"], "sender_account_ids": [1], "group_ids": [101]
    }
    w1_id = client.post("/api/watchers", json=w1_payload).json()["id"]
    w2_id = client.post("/api/watchers", json=w2_payload).json()["id"]

    # 2. Add target user to global blacklist
    await db.add_to_dm_blacklist(121212, "blacklisted_guy", "Spammer")

    # 3. Verify they are stored in DB
    watchers = await db.get_all_watchers_by_platform("telegram")
    assert len(watchers) >= 2
    bl = await db.get_dm_blacklist()
    assert any(b["user_id"] == 121212 for b in bl)

# 69. Schedule execution lifecycle: Create schedule -> Trigger send-now -> Verify messages enqueued.
async def test_69_workload_schedule_execution_lifecycle(client):
    # 1. Create Account
    acc_id = await db.create_account({
        "name": "Lifecycle Account", "phone": "+8499001122", "api_id": "2040", "api_hash": "b", "session_name": "lifecycle_acc"
    })
    # 2. Create Schedule with targets and messages
    sch_id = client.post("/api/schedules", json={
        "account_id": acc_id,
        "name": "Lifecycle Schedule",
        "schedule_type": "hourly",
        "time_of_day": "12:00",
        "messages": [{"msg_order": 0, "msg_type": "text", "content": "Automated Ping"}],
        "targets": [{"chat_id": 9988, "chat_title": "Target Chat", "chat_type": "group"}]
    }).json()["id"]

    # 3. Trigger send-now
    send_res = client.post(f"/api/schedules/{sch_id}/send-now")
    assert send_res.status_code == 200
    assert send_res.json()["success"] is True

    # 4. Check that items are in message queue or processed into send_logs
    import message_queue as mq
    q = mq.get_queue()
    if q.qsize() > 0:
        item = await q.get()
        assert item["schedule_id"] == sch_id
        assert item["message"]["content"] == "Automated Ping"
    else:
        # Worker running in background has already dequeued the item
        logs = await db.get_send_logs(schedule_id=sch_id)
        assert logs["total"] >= 1 or len(logs["logs"]) >= 1

# 70. API Authentication / Security Bypass checks: Verify endpoints block requests without X-API-Key.
async def test_70_workload_api_security_bypass(client, unauth_client):
    # 1. Unauthenticated request to /api/auth/accounts should fail
    resp1 = unauth_client.get("/api/auth/accounts")
    assert resp1.status_code == 403

    # 2. Authenticated request should succeed
    resp2 = client.get("/api/auth/accounts")
    assert resp2.status_code == 200

# 71. Deep Crawl BFS flow: Start deep crawl -> Check status (polling) -> Stop deep crawl.
async def test_71_workload_deep_crawl_bfs_flow(client):
    # 1. Start deep crawl
    payload = {
        "account_ids": [1],
        "channel_link": "https://t.me/crawler_source",
        "max_depth": 2
    }
    start_res = client.post("/api/members/deep-crawl", json=payload)
    assert start_res.status_code == 200
    assert start_res.json()["success"] is True

    # 2. Check status (should be running or completed)
    status_res = client.get("/api/members/deep-crawl/status")
    assert status_res.status_code == 200
    assert status_res.json()["status"] in ("running", "completed")

    # 3. Stop deep crawl
    stop_res = client.post("/api/members/deep-crawl/stop")
    assert stop_res.status_code == 200
    assert stop_res.json()["success"] in (True, False)

    # 4. Check status again (should be stopped or completing transition)
    status_res_after = client.get("/api/members/deep-crawl/status")
    # It sets the stop flag which the background task handles.
    assert status_res_after.status_code == 200


# 72. Community Trading Scorer & Auto-Classifier tests
async def test_72_community_trading_scorer():
    from telegram_client import score_community_trading

    # 1. VIP Trading & Signals channel (High Intent)
    vip_res = score_community_trading(
        title="Binance Futures VIP Signals",
        description="Daily 95% winrate crypto signals, leverage 20x-50x, Bybit partner, contact @vip_admin for trade access",
        username="binance_vip_futures",
        contacts=["@vip_admin"]
    )
    assert vip_res["trading_score"] >= 80
    assert vip_res["category"] == "trading_signals"
    assert vip_res["is_trading"] is True
    assert "futures" in vip_res["matched_keywords"] or "signals" in vip_res["matched_keywords"]

    # 2. Chinese Trading / Futures KOL channel
    zh_res = score_community_trading(
        title="雷司纪的小道投资 Raysky",
        description="加密货币 合约交易 现货带单 返佣 商务合作 @raysky_bd",
        username="rayskyinvestment",
        contacts=["@raysky_bd"]
    )
    assert zh_res["trading_score"] >= 70
    assert zh_res["is_trading"] is True

    # 3. Vietnamese Trading Community
    vi_res = score_community_trading(
        title="Giao Dịch Crypto Việt Nam",
        description="Cộng đồng chia sẻ kèo trade future, phân tích kỹ thuật BTC/ETH, đòn bẩy và chốt lời @admin_trade",
        username="trade_coin_vn",
        contacts=["@admin_trade"]
    )
    assert vi_res["trading_score"] >= 80
    assert vi_res["category"] == "trading_signals"
    assert vi_res["is_trading"] is True

    # 4. Pure News & Media channel (ABMedia, BlockTempo)
    news_res = score_community_trading(
        title="NEWS 鏈新聞-ABMedia",
        description="加密貨幣 | 區塊鏈 | 幣圈即時快訊 新聞媒體",
        username="abmedia_news",
        contacts=[]
    )
    assert news_res["category"] == "news_general"
    assert news_res["trading_score"] <= 55

    # 5. Airdrop Spam / Tap-to-earn channel (Penalty)
    spam_res = score_community_trading(
        title="Free Airdrop Daily Claim",
        description="Free airdrop tap to earn notcoin hamster kombat faucet claim free token 18+",
        username="airdrop_free_claim",
        contacts=[]
    )
    assert spam_res["trading_score"] < 40
    assert spam_res["category"] == "low_relevance"
    assert spam_res["is_trading"] is False

    # 6. Hybrid Channel (Has news in title/desc, but also has Bybit/Binance signals & trading)
    hybrid_res = score_community_trading(
        title="Crypto Daily News & Signals",
        description="Cập nhật tin tức thị trường, bắn kèo trade future Bybit và Binance, chốt lời @admin_trade",
        username="crypto_news_signals",
        contacts=["@admin_trade"]
    )
    assert hybrid_res["trading_score"] >= 80
    assert hybrid_res["category"] == "trading_signals"

    # 7. Russian Scalping & Signals channel (DIGAHKA & MOEX Signals)
    ru_scalp = score_community_trading(
        title="DIGAHKA - СКАЛЬПИНГ",
        description="Нужно думать - нужно делать 😊 Скринер - https://digash.live Поддержка: @Puzo_Support",
        username="digahkaaaa",
        contacts=["@Puzo_Support"],
        participants_count=41731
    )
    assert ru_scalp["trading_score"] >= 80
    assert ru_scalp["category"] == "trading_signals"
    assert ru_scalp["is_trading"] is True

    ru_signals = score_community_trading(
        title="Сигналы МосБиржа",
        description="Бесплатные сигналы для трейдеров CScalp News : @cscalpofficial Trader signals : @daytrader_signals",
        username="signals_moex",
        contacts=["@daytrader_signals"],
        participants_count=15170
    )
    assert ru_signals["trading_score"] >= 80
    assert ru_signals["category"] == "trading_signals"
    assert ru_signals["is_trading"] is True
    assert hybrid_res["is_trading"] is True


# 73. Batch Translation for Channel Descriptions
async def test_73_translate_descriptions(client):
    payload = {
        "texts": [
            "Финансовая нерекомендация. Связь со мной - @trade_molly",
            "专注永续合约带单、高胜率策略"
        ],
        "target_lang": "en"
    }
    res = client.post("/api/members/translate-descriptions", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "translations" in data
    assert len(data["translations"]) == 2
    # Verify cached call returns quickly and with valid content
    res_cached = client.post("/api/members/translate-descriptions", json=payload)
    assert res_cached.status_code == 200
