"""Spec docs/spec-kol-starter-package.md §C.3 + §C.6 — 5-step KOL funnel memory.

3. Transitions: +1 is accepted, skipping is rejected for automation, admin may
   override (any direction, incl. out of a terminal step), every change is
   logged in kol_step_events; routes expose state / set / counts.
6. Migration v1 is idempotent and maps legacy stage/status/UID correctly.
"""
from unittest.mock import patch

import pytest

import database as db

A, U = 1, 777


async def _lead(user_id: int = U, username: str = "kol_lead"):
    if not await db.get_account(A):
        await db.create_account({
            "id": A, "name": "BD", "phone": "+8400", "api_id": "x", "api_hash": "y", "session_name": "bd",
        })
    await db.get_or_create_followup_chat(account_id=A, user_id=user_id, username=f"{username}_{user_id}", name="KOL")
    return user_id


async def _set_null_step(user_id: int):
    async with db.get_db() as conn:
        await conn.execute("UPDATE ai_followup_chats SET kol_step = NULL WHERE account_id = ? AND user_id = ?", (A, user_id))
        await conn.commit()


# ── constants / helpers ──────────────────────────────────────────────────────

def test_step_tables_are_closed_and_ordered():
    assert db.KOL_STEPS == ("prospect", "deal_agreed", "uid_received", "link_confirmed", "first_post", "active")
    assert db.KOL_STEP_TERMINAL == ("declined", "dormant")
    assert [db.kol_step_rank(s) for s in db.KOL_STEPS] == [0, 1, 2, 3, 4, 5]
    assert db.kol_step_rank("declined") == -1
    assert db.normalize_kol_step(" Deal_Agreed ") == "deal_agreed"
    assert db.normalize_kol_step("signed") is None
    assert db.normalize_kol_step(None) is None
    for s in db.KOL_STEP_ALL:
        assert s in db.KOL_STEP_LABELS and s in db.KOL_STEP_EMOJI


def test_missing_items_follow_the_step_and_profile():
    assert db.compute_missing_items("prospect", {}) == ["deal_agreement"]
    assert db.compute_missing_items("deal_agreed", {}) == ["weex_uid"]
    assert db.compute_missing_items("deal_agreed", {"weex_uid": "123456"}) == []
    assert db.compute_missing_items("uid_received", {}) == ["vip_code", "affiliate_link"]
    assert db.compute_missing_items("uid_received", {"vip_code": "x", "affiliate_link": "y"}) == ["link_confirmation"]
    assert db.compute_missing_items("link_confirmed", {}) == ["selected_campaign", "first_post_link"]
    assert db.compute_missing_items("first_post", {}) == ["volume_verified"]
    assert db.compute_missing_items("active", {}) == []
    assert db.compute_missing_items("declined", {}) == []
    # Only KOL-owned items are ever asked for by the AI / reminders.
    assert db.kol_missing_items_for_kol(["vip_code", "affiliate_link", "weex_uid"]) == ["weex_uid"]
    assert db.kol_missing_items_for_kol(["link_confirmation"]) == []


# ── C.3 transitions ──────────────────────────────────────────────────────────

async def test_new_lead_starts_at_prospect():
    await _lead()
    assert await db.get_kol_step(A, U) == "prospect"
    state = await db.get_kol_state(A, U)
    assert state["step"] == "prospect" and state["step_rank"] == 0
    assert state["missing_items"] == ["deal_agreement"]


async def test_automation_may_advance_exactly_one_step():
    await _lead()
    res = await db.set_kol_step(A, U, "deal_agreed", actor="ai", reason="[DEAL_AGREED]")
    assert res == {"changed": True, "from": "prospect", "to": "deal_agreed"}
    assert await db.get_kol_step(A, U) == "deal_agreed"

    res = await db.set_kol_step(A, U, "uid_received", actor="system:uid", reason="uid")
    assert res["changed"] is True and res["to"] == "uid_received"


async def test_automation_skip_and_backwards_are_rejected():
    await _lead()
    res = await db.set_kol_step(A, U, "link_confirmed", actor="ai")
    assert res["changed"] is False and res["error"] == "skip_not_allowed"
    assert await db.get_kol_step(A, U) == "prospect"

    await db.set_kol_step(A, U, "deal_agreed", actor="ai")
    res = await db.set_kol_step(A, U, "prospect", actor="ai")
    assert res["changed"] is False and res["error"] == "skip_not_allowed"
    assert await db.get_kol_step(A, U) == "deal_agreed"
    # Rejected moves leave no event behind.
    events = await db.get_kol_step_events(A, U)
    assert [e["to_step"] for e in events] == ["deal_agreed"]


async def test_automation_may_close_out_but_not_reopen():
    await _lead()
    res = await db.set_kol_step(A, U, "declined", actor="ai", reason="[STOP]")
    assert res["changed"] is True
    res = await db.set_kol_step(A, U, "deal_agreed", actor="ai")
    assert res["changed"] is False and res["error"] == "terminal_requires_admin"
    assert await db.get_kol_step(A, U) == "declined"


async def test_admin_override_any_direction_including_from_terminal():
    await _lead()
    res = await db.set_kol_step(A, U, "first_post", actor="admin:web", reason="legacy KOL")
    assert res["changed"] is True and res["from"] == "prospect"
    res = await db.set_kol_step(A, U, "deal_agreed", actor="admin:web", reason="oops, wrong")
    assert res["changed"] is True
    await db.set_kol_step(A, U, "declined", actor="ai")
    res = await db.set_kol_step(A, U, "link_confirmed", actor="admin:cmd", reason="came back")
    assert res["changed"] is True
    assert await db.get_kol_step(A, U) == "link_confirmed"


async def test_same_step_is_a_noop_and_invalid_step_raises():
    await _lead()
    res = await db.set_kol_step(A, U, "prospect", actor="admin")
    assert res["changed"] is False and "error" not in res
    with pytest.raises(ValueError):
        await db.set_kol_step(A, U, "signed", actor="admin")
    res = await db.set_kol_step(A, 999999, "deal_agreed", actor="admin")
    assert res["error"] == "not_found"


async def test_events_are_logged_completely():
    await _lead()
    await db.set_kol_step(A, U, "deal_agreed", actor="ai", reason="AI tag [DEAL_AGREED]")
    await db.set_kol_step(A, U, "uid_received", actor="system:uid", reason="UID 123456 confirmed")
    await db.set_kol_step(A, U, "prospect", actor="admin:web", reason="reset")
    events = await db.get_kol_step_events(A, U)
    assert [(e["from_step"], e["to_step"], e["actor"]) for e in events] == [
        ("uid_received", "prospect", "admin:web"),
        ("deal_agreed", "uid_received", "system:uid"),
        ("prospect", "deal_agreed", "ai"),
    ]
    assert events[1]["reason"] == "UID 123456 confirmed"
    assert all(e["created_at"] for e in events)


async def test_step_change_resets_reminder_counters_and_refreshes_missing_items():
    await _lead()
    await db.set_kol_step(A, U, "deal_agreed", actor="ai")
    await db.mark_step_reminder_sent(A, U)
    chat = await db.get_followup_chat(A, U)
    assert chat["step_reminders_sent"] == 1 and chat["last_step_reminder_at"]
    assert (await db.get_kol_profile(A, U)).get("missing_items") == ["weex_uid"]

    await db.upsert_kol_profile(A, U, {"weex_uid": "88888888"})
    await db.set_kol_step(A, U, "uid_received", actor="system:uid")
    chat = await db.get_followup_chat(A, U)
    assert chat["step_reminders_sent"] == 0 and chat["last_step_reminder_at"] is None
    assert (await db.get_kol_profile(A, U)).get("missing_items") == ["vip_code", "affiliate_link"]


async def test_step_counts_and_leads_by_step_ignore_bots():
    await _lead(U)
    await db.get_or_create_followup_chat(account_id=A, user_id=U + 1, username="helper_bot", name="Bot")
    await db.get_or_create_followup_chat(account_id=A, user_id=U + 2, username="other", name="Other")
    await db.set_kol_step(A, U + 2, "deal_agreed", actor="ai")
    counts = await db.get_kol_step_counts()
    assert set(counts) == set(db.KOL_STEP_ALL)
    assert counts["prospect"] == 1 and counts["deal_agreed"] == 1
    assert [r["user_id"] for r in await db.get_leads_by_kol_step("deal_agreed")] == [U + 2]


async def test_legacy_stage_signed_promotes_step_to_deal_agreed():
    await _lead()
    assert await db.set_followup_stage(A, U, "signed") is True
    assert await db.get_kol_step(A, U) == "deal_agreed"
    chat = await db.get_followup_chat(A, U)
    assert chat["stage"] == "signed" and chat["status"] == "needs_human"


# ── routes ───────────────────────────────────────────────────────────────────

async def test_step_routes(client):
    await _lead()
    r = client.get(f"/api/ai-followup/chats/{A}/{U}/step")
    assert r.status_code == 200
    body = r.json()
    assert body["step"] == "prospect" and body["offer_version"] == 1
    assert body["predicted_tier"] is None and body["events"] == []

    r = client.post(f"/api/ai-followup/chats/{A}/{U}/step", json={"step": "link_confirmed", "reason": "manual"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] is True and r.json()["state"]["step"] == "link_confirmed"
    events = (await db.get_kol_step_events(A, U))
    assert events[0]["actor"] == "admin:web" and events[0]["reason"] == "manual"

    r = client.post(f"/api/ai-followup/chats/{A}/{U}/step", json={"step": "signed"})
    assert r.status_code == 400
    r = client.post(f"/api/ai-followup/chats/{A}/{U + 50}/step", json={"step": "deal_agreed"})
    assert r.status_code == 404
    r = client.get(f"/api/ai-followup/chats/{A}/{U + 50}/step")
    assert r.status_code == 404

    r = client.get("/api/ai-followup/step-counts")
    assert r.status_code == 200
    assert r.json()["counts"]["link_confirmed"] == 1
    assert r.json()["steps"] == list(db.KOL_STEPS)

    r = client.get(f"/api/ai-followup/chats/{A}/{U}/step-events")
    assert [e["to_step"] for e in r.json()["events"]] == ["link_confirmed"]


async def test_admin_uid_in_profile_advances_deal_agreed_to_uid_received(client):
    await _lead()
    await db.set_kol_step(A, U, "deal_agreed", actor="ai")
    r = client.post(f"/api/ai-followup/chats/{A}/{U}/profile", json={"weex_uid": "12345678", "volume_claimed": "30M"})
    assert r.status_code == 200, r.text
    assert await db.get_kol_step(A, U) == "uid_received"
    profile = await db.get_kol_profile(A, U)
    assert profile["weex_uid"] == "12345678"
    assert profile["missing_items"] == ["vip_code", "affiliate_link"]
    state = client.get(f"/api/ai-followup/chats/{A}/{U}/step").json()
    assert state["predicted_tier"]["tier"] == 3

    # A UID typed while still at prospect does NOT jump two steps.
    await _lead(U + 1)
    client.post(f"/api/ai-followup/chats/{A}/{U + 1}/profile", json={"weex_uid": "555555"})
    assert await db.get_kol_step(A, U + 1) == "prospect"


async def test_status_onboarded_is_a_legacy_alias(client):
    await _lead()
    r = client.post(f"/api/ai-followup/chats/{A}/{U}/status", json={"status": "onboarded"})
    assert r.status_code == 200, r.text
    assert await db.get_kol_step(A, U) == "link_confirmed"
    assert (await db.get_followup_chat(A, U))["status"] == "paused_admin"


# ── C.6 migration ────────────────────────────────────────────────────────────

async def test_migration_maps_legacy_rows_and_is_idempotent():
    await _lead(U)                                   # plain → prospect
    await _lead(U + 1)
    await db.set_followup_stage(A, U + 1, "signed")  # → deal_agreed
    await _lead(U + 2)
    await db.update_followup_chat_status(A, U + 2, "onboarded")  # → link_confirmed + paused_admin
    await _lead(U + 3)
    await db.upsert_kol_profile(A, U + 3, {"weex_uid": "424242"})  # → uid_received
    await _lead(U + 4)                               # already stepped: must NOT be touched
    await db.set_kol_step(A, U + 4, "first_post", actor="admin")
    for uid in (U, U + 1, U + 2, U + 3):
        await _set_null_step(uid)

    with patch("alerts.run_backup", return_value="backup.db"):
        res = await db.migrate_kol_steps_v1(force=True)
    assert res["skipped"] is False and res["migrated"] == 4
    assert res["by_step"] == {"prospect": 1, "deal_agreed": 1, "link_confirmed": 1, "uid_received": 1}

    assert await db.get_kol_step(A, U) == "prospect"
    assert await db.get_kol_step(A, U + 1) == "deal_agreed"
    assert await db.get_kol_step(A, U + 2) == "link_confirmed"
    assert (await db.get_followup_chat(A, U + 2))["status"] == "paused_admin"
    assert await db.get_kol_step(A, U + 3) == "uid_received"
    assert await db.get_kol_step(A, U + 4) == "first_post"
    ev = await db.get_kol_step_events(A, U + 2)
    assert ev[0]["actor"] == "migration:v1" and ev[0]["from_step"] is None

    # Idempotent: flag set → skipped; forced re-run → nothing left to migrate, no new events.
    assert await db.get_setting("kol_steps_migrated_v1")
    assert (await db.migrate_kol_steps_v1())["reason"] == "already_migrated"
    with patch("alerts.run_backup", return_value="backup.db") as backup:
        res = await db.migrate_kol_steps_v1(force=True)
        backup.assert_not_called()
    assert res["skipped"] is True and res["reason"] == "nothing_to_migrate"
    assert len(await db.get_kol_step_events(A, U + 2)) == 1


async def test_migration_mirrors_legacy_profile_json_into_columns():
    await _lead()
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO kol_profiles (account_id, user_id, profile_json, updated_at) VALUES (?, ?, ?, datetime('now'))",
            (A, U, '{"weex_uid": "909090", "vip_code": "WILL10"}'),
        )
        await conn.commit()
    await _set_null_step(U)
    with patch("alerts.run_backup", return_value=None):
        res = await db.migrate_kol_steps_v1(force=True)
    assert res["mirrored_profiles"] == 1
    assert await db.get_kol_step(A, U) == "uid_received"
    async with db.get_db() as conn:
        cur = await conn.execute("SELECT weex_uid, vip_code FROM kol_profiles WHERE account_id = ? AND user_id = ?", (A, U))
        assert tuple(await cur.fetchone()) == ("909090", "WILL10")
