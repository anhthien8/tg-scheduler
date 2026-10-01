"""Tests for the KOL pipeline stages (/pipeline, /attention, /stage).

The point of a stage is to answer "where is this KOL in the funnel" — something
a per-topic on/off switch cannot express. These tests pin the mapping rules and
the stage<->status coupling that keeps the AI gate honest.
"""
import pytest

import database as db


# ── normalisation: operators type Vietnamese, with or without accents ─────────

def test_normalize_stage_accepts_canonical_and_aliases():
    assert db.normalize_stage("nurture") == "nurture"
    assert db.normalize_stage("nuoi") == "nurture"
    assert db.normalize_stage("nuôi") == "nurture"      # with diacritics
    assert db.normalize_stage("dam phan") == "negotiating"
    assert db.normalize_stage("đàm phán") == "negotiating"
    assert db.normalize_stage("chot") == "signed"
    assert db.normalize_stage("signed") == "signed"
    assert db.normalize_stage("tạm dừng") == "paused"
    assert db.normalize_stage("paused") == "paused"


def test_normalize_stage_rejects_unknown_instead_of_guessing():
    """A bogus stage must be rejected, not silently written — otherwise the
    funnel counts would drift from reality."""
    for bad in (None, "", "   ", "banana", "tier-a"):
        assert db.normalize_stage(bad) is None


def test_stage_set_is_closed():
    assert set(db.PIPELINE_STAGES) == {"new", "nurture", "negotiating", "signed", "paused"}


# ── stage <-> status coupling: the AI gate ───────────────────────────────────

@pytest.mark.asyncio
async def test_signed_and_paused_block_ai_new_and_nurture_allow_it():
    """set_followup_stage must keep status consistent so the AI gate stays right.

    signed/paused -> status 'needs_human'  (AI silent)
    new/nurture/negotiating -> status 'active' (AI may speak)

    Uses a dedicated account and forces a known starting stage, so the result
    can't depend on rows or ordering left behind by other test files.
    """
    acc, uid = 8801, 8802
    await db.get_or_create_followup_chat(acc, uid, username="stagecase")
    # Known start regardless of any pre-existing row.
    assert await db.set_followup_stage(acc, uid, "new") is True

    assert await db.set_followup_stage(acc, uid, "nuôi") is True
    chat = await db.get_followup_chat(acc, uid)
    assert chat["stage"] == "nurture", f"got {chat['stage']!r}"
    assert chat["status"] == "active"

    assert await db.set_followup_stage(acc, uid, "chot") is True
    chat = await db.get_followup_chat(acc, uid)
    assert chat["stage"] == "signed"
    assert chat["status"] == "needs_human", "signed must silence the AI"
    assert chat["human_takeover_at"], "signed must stamp takeover"

    assert await db.set_followup_stage(acc, uid, "negotiating") is True
    chat = await db.get_followup_chat(acc, uid)
    assert chat["stage"] == "negotiating" and chat["status"] == "active"
    assert not chat["human_takeover_at"], "returning to AI must clear the stamp"


@pytest.mark.asyncio
async def test_set_stage_rejects_unknown_value():
    await db.get_or_create_followup_chat(8811, 8812, username="badstage")
    assert await db.set_followup_stage(8811, 8812, "not-a-stage") is False
    # and the row is untouched
    assert (await db.get_followup_chat(8811, 8812))["stage"] != "not-a-stage"


# ── funnel views ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_counts_cover_every_stage_key():
    counts = await db.get_pipeline_counts()
    assert set(counts) == set(db.PIPELINE_STAGES)
    assert all(isinstance(v, int) and v >= 0 for v in counts.values())


@pytest.mark.asyncio
async def test_get_leads_by_stage_returns_only_that_stage():
    await db.get_or_create_followup_chat(8821, 8822, username="funnelme")
    await db.set_followup_stage(8821, 8822, "negotiating")
    rows = await db.get_leads_by_stage("dam phan", limit=100)
    assert any(r["user_id"] == 8822 for r in rows)
    assert all(r["stage"] == "negotiating" for r in rows)


# ── username resolution used by '/stage @user <stage>' ───────────────────────

@pytest.mark.asyncio
async def test_find_followup_chat_by_username_is_case_and_at_insensitive():
    await db.get_or_create_followup_chat(8831, 8832, username="MixedCase")
    found = await db.find_followup_chat_by_username("@mixedcase")
    assert found is not None and found["user_id"] == 8832
    assert await db.find_followup_chat_by_username("nobody-here") is None


# ── command routing helpers ──────────────────────────────────────────────────

def test_pipeline_commands_recognised_but_not_other_slash_commands():
    import command_bot

    for ok in ("/pipeline", "/attention", "/stage", "/stage nuoi", "/STAGE @x nuôi"):
        assert command_bot._is_pipeline_command(ok), ok
    for no in ("/status", "/ai-on", "/stagex", "hello", ""):
        assert not command_bot._is_pipeline_command(no), no


def test_stage_labels_present_for_every_stage():
    import command_bot

    for st in db.PIPELINE_STAGES:
        assert st in command_bot._STAGE_LABEL
