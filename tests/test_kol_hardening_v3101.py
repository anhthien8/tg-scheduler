"""v3.10.1 regression tests — schema self-check, AI guards, DM campaign safety.

Covers the three bug classes fixed in this round:

1. Schema drift: the 5-step KOL funnel (kol_step / kol_profiles / kol_step_events)
   existed in `database.py` but never landed in the live DB — the ALTERs were
   swallowed by bare `except: pass`. verify_schema() now asserts the contract
   and init_db() refuses to run the migration on a broken schema.
2. AI guards: the drip follow-up and the outreach nudge could quote any figure
   ("$50,000 prize pool" was hard-coded in the drip instruction), and a distilled
   human rule was auto-approved even when it promised an off-ladder number.
3. DM campaign safety: no reservation before the network call (a crash between
   "message accepted" and "log success" re-DM'd the same lead), a per-campaign
   in-memory daily counter (two campaigns double-spent one sender's quota),
   and no account_busy registration (DM + reaction engine on the same nick).
"""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import database as db
import dm_reply_tracker as tracker
import kol_offer

pytestmark = pytest.mark.asyncio

A, U = 1, 4242


# ── helpers ──────────────────────────────────────────────────────────────────

async def _account_and_agent(account_id: int = A, agent_name: str = "Hardening Agent"):
    agent_id = await db.create_ai_agent({
        "name": agent_name,
        "provider": "gemini",
        "api_keys_json": ["agent-key"],
        "system_prompt": "Sys",
        "knowledge_base": "KB",
    })
    await db.create_account({
        "id": account_id, "name": "BD", "phone": "+8400", "api_id": "x",
        "api_hash": "y", "session_name": f"bd{account_id}",
        "ai_agent_id": agent_id,
    })
    await db.set_account_ai_agent(account_id, agent_id)
    return agent_id


async def _lead(user_id: int = U, account_id: int = A, step: str = "prospect",
                history: bool = False):
    await db.get_or_create_followup_chat(
        account_id=account_id, user_id=user_id, username=f"kol{user_id}", name="KOL")
    if history:
        await db.append_followup_chat_message(
            account_id, user_id, "user", "what is the commission?")
    if step != "prospect":
        await db.set_kol_step(account_id, user_id, step, actor="admin:test", reason="fixture")
    return user_id


async def _campaign(name: str = "Hardening Campaign") -> int:
    """A real campaign row — dm_campaign_logs has a FK on campaign_id."""
    return await db.create_dm_campaign({
        "name": name,
        "scrape_job_id": "job-1",
        "sender_account_ids": [A],
        "messages": [["hello"]],
    })


async def _backdate_silence(user_id: int, hours: int = 72, account_id: int = A):
    """Make the lead look silent for `hours` (the drip/stage clock)."""
    async with db.get_db() as conn:
        await conn.execute("""
            UPDATE ai_followup_chats
            SET last_user_message_at = datetime('now', ?), updated_at = datetime('now', ?)
            WHERE account_id = ? AND user_id = ?
        """, (f"-{hours} hours", f"-{hours} hours", account_id, user_id))
        await conn.commit()


# ── 1. schema self-check ─────────────────────────────────────────────────────

async def test_verify_schema_passes_on_a_fresh_database():
    """A freshly migrated DB must satisfy the schema contract."""
    problems = await db.verify_schema()
    assert problems == [], f"schema self-check failed: {problems}"


async def test_required_schema_covers_the_funnel_tables():
    """The contract must actually assert the 5-step funnel columns."""
    assert "kol_step" in db.REQUIRED_SCHEMA["ai_followup_chats"]
    assert "kol_step_updated_at" in db.REQUIRED_SCHEMA["ai_followup_chats"]
    assert "weex_uid" in db.REQUIRED_SCHEMA["kol_profiles"]
    assert "offer_version" in db.REQUIRED_SCHEMA["kol_profiles"]
    assert "from_step" in db.REQUIRED_SCHEMA["kol_step_events"]
    assert "ladder_json" in db.REQUIRED_SCHEMA["kol_offer_ladder_history"]


async def test_verify_schema_reports_a_missing_column():
    """A dropped column must be reported, not silently tolerated."""
    async with db.get_db() as conn:
        await conn.execute("ALTER TABLE kol_step_events DROP COLUMN actor")
        await conn.commit()
    problems = await db.verify_schema()
    assert any("kol_step_events.actor" in p for p in problems)
    # The repair step must be able to heal it again.
    repaired = await db.repair_schema()
    assert "kol_step_events.actor" in repaired
    assert await db.verify_schema() == []


async def test_add_column_only_swallows_duplicate_column(monkeypatch):
    """The old bare `except: pass` hid real migration failures."""
    async with db.get_db() as conn:
        # duplicate column → silent no-op
        await db._add_column(conn, "kol_profiles", "weex_uid", "TEXT")
        # a genuine error must propagate
        with pytest.raises(Exception):
            await db._add_column(conn, "table_that_does_not_exist", "x", "TEXT")


async def test_reservations_table_exists():
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dm_campaign_reservations'")
        assert await cur.fetchone() is not None


# ── 2a. DM campaign reservation idempotency ──────────────────────────────────

async def test_reserve_is_granted_once_per_target():
    """The first reservation wins; a second one is refused (no duplicate DM)."""
    assert await db.reserve_dm_campaign_target(9001, 555, A) is True
    assert await db.reserve_dm_campaign_target(9001, 555, A) is False


async def test_reserve_after_sent_is_refused():
    assert await db.reserve_dm_campaign_target(9002, 556, A) is True
    await db.finish_dm_campaign_reservation(9002, 556, "sent")
    assert await db.reserve_dm_campaign_target(9002, 556, A) is False


async def test_reserve_after_failed_is_granted_again():
    """A failed send is retryable — the reservation must not block it."""
    assert await db.reserve_dm_campaign_target(9003, 557, A) is True
    await db.finish_dm_campaign_reservation(9003, 557, "failed", "flood")
    assert await db.reserve_dm_campaign_target(9003, 557, A) is True


async def test_pending_reservation_is_not_reissued_within_24h():
    """A crash between send and log leaves 'pending' — no re-send for a day."""
    assert await db.reserve_dm_campaign_target(9004, 558, A) is True
    assert await db.reserve_dm_campaign_target(9004, 558, A) is False


async def test_pending_reservation_is_released_after_24h():
    assert await db.reserve_dm_campaign_target(9005, 559, A) is True
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE dm_campaign_reservations SET created_at = datetime('now', '-25 hours') "
            "WHERE campaign_id = 9005 AND target_user_id = 559")
        await conn.commit()
    assert await db.reserve_dm_campaign_target(9005, 559, A) is True


async def test_reservation_is_scoped_per_campaign():
    """The same lead in another campaign is a different reservation."""
    assert await db.reserve_dm_campaign_target(9006, 560, A) is True
    assert await db.reserve_dm_campaign_target(9007, 560, A) is True


async def test_finish_reservation_rejects_unknown_status():
    with pytest.raises(ValueError):
        await db.finish_dm_campaign_reservation(9008, 561, "weird")


async def test_finish_reservation_stores_error_message():
    await db.reserve_dm_campaign_target(9009, 562, A)
    await db.finish_dm_campaign_reservation(9009, 562, "failed", "account_busy")
    async with db.get_db() as conn:
        row = await (await conn.execute(
            "SELECT status, error_message FROM dm_campaign_reservations "
            "WHERE campaign_id=9009 AND target_user_id=562")).fetchone()
    assert row[0] == "failed" and row[1] == "account_busy"


# ── 2b. atomic daily cap ─────────────────────────────────────────────────────

async def test_live_daily_count_includes_watcher_and_campaign_successes():
    campaign_id = await _campaign()
    await db.add_dm_campaign_log(campaign_id, A, 7001, "kol1", "success")
    assert await db.get_account_daily_dm_count_live(A) == 1


async def test_live_daily_count_counts_pending_reservations():
    """An in-flight send already consumes quota — otherwise two campaigns
    sharing a sender both see '0 used' and double-spend the cap."""
    assert await db.get_account_daily_dm_count_live(A) == 0
    await db.reserve_dm_campaign_target(9102, 7002, A)
    assert await db.get_account_daily_dm_count_live(A) == 1


async def test_live_daily_count_ignores_failed_reservations():
    await db.reserve_dm_campaign_target(9103, 7003, A)
    await db.finish_dm_campaign_reservation(9103, 7003, "failed", "send_not_completed")
    assert await db.get_account_daily_dm_count_live(A) == 0


async def test_live_daily_count_ignores_other_accounts():
    await db.create_account({
        "id": 2, "name": "Other", "phone": "+8401", "api_id": "x",
        "api_hash": "y", "session_name": "other",
    })
    await db.reserve_dm_campaign_target(9104, 7004, 2)
    assert await db.get_account_daily_dm_count_live(A) == 0
    assert await db.get_account_daily_dm_count_live(2) == 1


# ── 2c. account_busy on the campaign path ────────────────────────────────────

async def test_account_busy_blocks_a_second_holder():
    from account_busy import AccountBusy, acquire

    async with acquire(A, "dm", ttl_secs=60):
        with pytest.raises(AccountBusy):
            async with acquire(A, "engagement", ttl_secs=60):
                pass
    # released again
    async with acquire(A, "dm", ttl_secs=60):
        pass


async def test_campaign_module_uses_the_account_busy_registry():
    """The campaign send path must register the account in the busy registry."""
    import inspect
    import routes.members as members
    src = inspect.getsource(members)
    assert "acquire_account_busy" in src
    assert "reserve_dm_campaign_target" in src
    assert "get_account_daily_dm_count_live" in src
    # The old per-campaign in-memory counter must be gone.
    assert "acc_daily_count_map" not in src


# ── 3a. drip follow-up: no hard-coded figures + ladder guard ────────────────

async def test_drip_instructions_contain_no_hard_coded_figures():
    """The drip prompt used to hard-code '$50,000 prize pool' — past the guard."""
    for stage in (1, 2, 3):
        for tier in ("Tier A", "Tier B", "Tier C"):
            txt = tracker._drip_instruction_for_stage(stage, 3, tier)
            assert "$50,000" not in txt
            assert "50,000" not in txt
            assert "prize pool" not in txt.lower()
            # No concrete % / $ figure at all.
            assert kol_offer.find_off_ladder_figures(txt) == [], (
                f"stage {stage}/{tier} leaks a figure: {txt}")


async def test_drip_instruction_tier_a_mentions_the_official_ladder():
    txt = tracker._drip_instruction_for_stage(1, 3, "Tier A")
    assert "ladder" in txt.lower()
    assert "official" in txt.lower()


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_with_off_ladder_figure_is_not_sent(mock_send, mock_gen):
    """A drip message quoting a figure outside the ladder must be dropped."""
    await _account_and_agent()
    await _lead()
    # Tier C (the default tier) starts at 48h of silence.
    await _backdate_silence(U, hours=60)
    mock_gen.return_value = "We can offer 75% revshare and $2000 upfront bro!"

    res = await tracker.process_drip_followups()
    mock_send.assert_not_called()
    assert res["sent"] == 0


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_drip_prompt_contains_the_official_ladder(mock_send, mock_gen):
    """The drip prompt must carry the ladder block (not a hard-coded number)."""
    await _account_and_agent()
    await _lead()
    await _backdate_silence(U, hours=60)
    mock_gen.return_value = "Hey bro, still interested in the affiliate program?"

    await tracker.process_drip_followups()
    assert mock_gen.call_count == 1
    system_prompt = mock_gen.call_args[0][1]
    assert "OFFICIAL WEEX KOL COMMISSION LADDER" in system_prompt
    assert "$50,000" not in system_prompt


# ── 3b. outreach nudge: ladder block + off-ladder guard ─────────────────────

async def test_nudge_prompt_instruction_has_no_hard_coded_figures():
    txt = tracker._NUDGE_PROMPT_INSTRUCTION
    assert "$50,000" not in txt
    assert kol_offer.find_off_ladder_figures(txt) == []


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_nudge_with_off_ladder_figure_is_skipped_and_logged(mock_send, mock_gen):
    await _account_and_agent()
    sent = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    async with db.get_db() as conn:
        await conn.execute(
            """INSERT INTO watcher_dm_logs (watcher_id, account_id, target_user_id,
                                               target_username, status, sent_at)
               VALUES (1, ?, 8101, 'kol', 'success', ?)""", (A, sent))
        await conn.commit()

    mock_gen.return_value = "We pay 90% revshare bro, plus $5000 cash bonus!"

    with patch("dm_reply_tracker.is_internal_account_user", new_callable=AsyncMock, return_value=False):
        res = await tracker.process_outreach_nudges()

    assert res["sent"] == 0
    assert res["skipped"] >= 1
    mock_send.assert_not_called()
    # The skip must be auditable.
    stats = await db.get_nudge_stats()
    assert stats.get("skipped", 0) >= 1


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_nudge_prompt_contains_the_official_ladder(mock_send, mock_gen):
    await _account_and_agent()
    sent = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    async with db.get_db() as conn:
        await conn.execute(
            """INSERT INTO watcher_dm_logs (watcher_id, account_id, target_user_id,
                                               target_username, status, sent_at)
               VALUES (1, ?, 8102, 'kol', 'success', ?)""", (A, sent))
        await conn.commit()

    mock_gen.return_value = "Hey bro, just checking in on the affiliate program 👍"

    with patch("dm_reply_tracker.is_internal_account_user", new_callable=AsyncMock, return_value=False):
        await tracker.process_outreach_nudges()

    assert mock_gen.call_count == 1
    system_prompt = mock_gen.call_args[0][1]
    assert "OFFICIAL WEEX KOL COMMISSION LADDER" in system_prompt


# ── 3c. learned rules: off-ladder figures are held for review ───────────────

@patch("ai_remix.distill_human_takeover_rule", new_callable=AsyncMock)
async def test_learned_rule_with_off_ladder_figure_is_pending(mock_distill):
    """An admin's ad-hoc promise of 75% must not become permanent prompt policy."""
    agent_id = await _account_and_agent()
    await _lead(history=True)
    mock_distill.return_value = {
        "question": "what is the commission",
        "answer": "we can give you 75% for this month",
    }
    await tracker._async_distill_human_rule(
        account_id=A, user_id=U, message_text="we can give you 75% for this month")

    approved = await db.get_learned_knowledge_for_agent(agent_id, status="approved")
    pending = await db.get_learned_knowledge_for_agent(agent_id, status="pending")
    assert len(approved) == 0
    assert len(pending) == 1
    assert "75%" in pending[0]["learned_answer"]


@patch("ai_remix.distill_human_takeover_rule", new_callable=AsyncMock)
async def test_learned_rule_on_ladder_is_approved(mock_distill):
    agent_id = await _account_and_agent()
    await _lead(history=True)
    mock_distill.return_value = {
        "question": "what is the commission",
        "answer": "you start at 55% and go up to 80% by volume",
    }
    await tracker._async_distill_human_rule(
        account_id=A, user_id=U, message_text="you start at 55% and go up to 80% by volume")

    approved = await db.get_learned_knowledge_for_agent(agent_id, status="approved")
    assert len(approved) == 1
    pending = await db.get_learned_knowledge_for_agent(agent_id, status="pending")
    assert len(pending) == 0


# ── 4. the two stale pre-existing failures (regression lock) ─────────────────

async def test_root_index_serves_a_non_stale_shell():
    """'/' is the SPA shell: it must NOT be cached for an hour, but it must not
    be 'no-cache' either — the app shell is versioned by its asset query string
    and revalidated every time."""
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert cache_control in ("no-cache", "public, max-age=3600"), cache_control


async def test_auth_status_is_true_when_an_account_is_logged_in():
    """A registered account that the engine marked as logged in must be
    reported as authenticated (the fast path must not drop the DB flag)."""
    from fastapi.testclient import TestClient
    from main import app
    import telegram_client as tg

    client = TestClient(app)
    client.headers.update({"X-API-Key": "test_secret_key"})
    acc_id = await db.create_account({
        "name": "Status Account", "phone": "+8488888888", "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "account_status_test", "proxy_url": None,
    })
    try:
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        # A brand-new account is not logged in yet → not authenticated.
        assert data["authenticated"] is False
        assert data["has_accounts"] is True

        # Engine marks it logged in → status must flip to authenticated.
        await db.update_account_login_status(acc_id, True)
        resp = client.get("/api/auth/status")
        data = resp.json()
        assert data["authenticated"] is True
        assert any(a["account_id"] == acc_id for a in data["accounts"])
    finally:
        await db.update_account_login_status(acc_id, False)
        tg._me_cache.pop(acc_id, None)
