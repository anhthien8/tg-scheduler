"""v3.10.2 — 4 lỗi mở + chính sách needs_human.

1. lead_scoring.rescore_and_update từng chết: gọi get_followup_chat trên raw
   connection (AttributeError nuốt im lặng) → lead_score_history mãi 0 row.
2. distribution_enabled không ai set → get_kols_for_distribution trả rỗng.
3. offer_version chưa bao giờ được ghi khi KOL chốt deal.
4. verification_status ghi qua API nhưng không ai đọc.
Policy: needs_human chỉ auto-resume khi kol_step='prospect' (_step_holds_ai).
"""
import json

import pytest

import database as db
import dm_reply_tracker as tracker
import kol_offer
import lead_scoring

A = 1  # account_id dùng chung


async def _mk_account():
    await db.create_account({"id": A, "name": "acc-v3102", "phone": "+8400000002",
                             "api_id": "111", "api_hash": "hash", "session_name": "s-v3102"})


async def _mk_chat(user_id: int, reply_count: int = 2):
    await db.get_or_create_followup_chat(A, user_id, username=f"u{user_id}", name="KOL")
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE ai_followup_chats SET reply_count = ?, status = 'active' WHERE account_id = ? AND user_id = ?",
            (reply_count, A, user_id),
        )
        await conn.commit()


# ── 1. rescore_and_update sống lại ───────────────────────────────────────────

async def test_rescore_writes_lead_score_history_with_real_db():
    """Chạy trên DB thật (không mock): phải ghi được 1 row lead_score_history."""
    await _mk_account()
    await _mk_chat(9001, reply_count=4)
    result = await lead_scoring.rescore_and_update(user_id=9001, account_id=A)
    assert result.score > 0
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT total_score, tier FROM lead_score_history WHERE account_id = ? AND user_id = ?",
            (A, 9001),
        )
        rows = await cur.fetchall()
    assert len(rows) == 1, "rescore_and_update phải ghi đúng 1 row lịch sử"
    assert rows[0][0] == result.score


async def test_rescore_updates_intent_score_on_chat():
    await _mk_account()
    await _mk_chat(9002, reply_count=5)
    result = await lead_scoring.rescore_and_update(user_id=9002, account_id=A)
    chat = await db.get_followup_chat(A, 9002)
    assert chat["intent_score"] == result.score
    assert chat["lead_tier"] == result.tier


async def test_fetch_followup_chat_raw_connection():
    """_fetch_followup_chat phải đọc được qua raw pooled connection."""
    await _mk_account()
    await _mk_chat(9003)
    async with db.get_db() as conn:
        chat = await lead_scoring._fetch_followup_chat(conn, A, 9003)
    assert chat is not None and chat["user_id"] == 9003


# ── 2. distribution_enabled default-on tại link_confirmed ───────────────────

async def test_link_confirmed_defaults_distribution_enabled():
    await _mk_account()
    await _mk_chat(9010)
    for step in ("deal_agreed", "uid_received", "link_confirmed"):
        await db.set_kol_step(A, 9010, step, actor="admin:test")
    profile = await db.get_kol_profile(A, 9010)
    assert profile.get("distribution_enabled") is True


async def test_link_confirmed_respects_explicit_opt_out():
    await _mk_account()
    await _mk_chat(9011)
    await db.upsert_kol_profile(A, 9011, {"distribution_enabled": False})
    for step in ("deal_agreed", "uid_received", "link_confirmed"):
        await db.set_kol_step(A, 9011, step, actor="admin:test")
    profile = await db.get_kol_profile(A, 9011)
    assert profile.get("distribution_enabled") is False, "opt-out tay không được ghi đè"


async def test_distribution_list_includes_defaulted_kol():
    await _mk_account()
    await _mk_chat(9012)
    for step in ("deal_agreed", "uid_received", "link_confirmed"):
        await db.set_kol_step(A, 9012, step, actor="admin:test")
    kols = await db.get_kols_for_distribution()
    assert any(k["user_id"] == 9012 for k in kols)


# ── 3. offer_version ghi tại deal_agreed ─────────────────────────────────────

async def test_deal_agreed_pins_offer_version():
    await _mk_account()
    await _mk_chat(9020)
    ladder = await kol_offer.get_ladder()
    await db.set_kol_step(A, 9020, "deal_agreed", actor="admin:test")
    profile = await db.get_kol_profile(A, 9020)
    assert profile.get("offer_version") == ladder.get("version")


async def test_deal_agreed_does_not_overwrite_existing_pin():
    await _mk_account()
    await _mk_chat(9021)
    await db.upsert_kol_profile(A, 9021, {"offer_version": 777})
    await db.set_kol_step(A, 9021, "deal_agreed", actor="admin:test")
    profile = await db.get_kol_profile(A, 9021)
    assert profile.get("offer_version") == 777


# ── 4. verification_status được đọc ──────────────────────────────────────────

async def test_kol_state_exposes_verification_status():
    await _mk_account()
    await _mk_chat(9030)
    await db.update_followup_verification(A, 9030, "submitted")
    state = await db.get_kol_state(A, 9030)
    assert state["verification_status"] == "submitted"


async def test_kol_state_verification_defaults_none():
    await _mk_account()
    await _mk_chat(9031)
    state = await db.get_kol_state(A, 9031)
    assert state["verification_status"] == "none"


async def test_step_prompt_mentions_verification():
    await _mk_account()
    await _mk_chat(9032)
    await db.update_followup_verification(A, 9032, "verified")
    prompt = await tracker.render_kol_step_prompt(A, 9032)
    assert "VERIFICATION: verified" in prompt


async def test_step_prompt_silent_when_verification_none():
    await _mk_account()
    await _mk_chat(9033)
    prompt = await tracker.render_kol_step_prompt(A, 9033)
    assert "VERIFICATION:" not in prompt


# ── Policy: needs_human auto-resume chỉ cho prospect ─────────────────────────

async def test_step_holds_ai_policy_matrix():
    assert tracker._step_holds_ai("prospect") is False
    assert tracker._step_holds_ai(None) is False
    for held in ("deal_agreed", "uid_received", "link_confirmed",
                 "first_post", "active", "declined", "dormant"):
        assert tracker._step_holds_ai(held) is True, held


# ── Env-first command bot credentials ────────────────────────────────────────

async def test_command_bot_token_prefers_env(monkeypatch):
    import command_bot
    monkeypatch.setenv("COMMAND_BOT_TOKEN", "env-token-123")
    await db.set_setting("command_bot_token", "db-token-456")
    assert await command_bot.get_command_bot_token() == "env-token-123"


async def test_command_bot_token_falls_back_to_setting(monkeypatch):
    import command_bot
    monkeypatch.delenv("COMMAND_BOT_TOKEN", raising=False)
    await db.set_setting("command_bot_token", "db-token-456")
    assert await command_bot.get_command_bot_token() == "db-token-456"


async def test_command_bot_admin_ids_prefers_env(monkeypatch):
    import command_bot
    monkeypatch.setenv("COMMAND_BOT_ADMIN_IDS", "1,2,3")
    await db.set_setting("command_bot_admin_ids", "9,9")
    assert await command_bot.get_command_bot_admin_ids_str() == "1,2,3"
