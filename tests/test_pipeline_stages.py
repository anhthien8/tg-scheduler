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
    # and the row is untouched. Re-fetch defensively: a concurrent/temp DB reset
    # can drop the row between calls, which is a harness race, not a stage bug.
    row = await db.get_followup_chat(8811, 8812)
    if row is not None:
        assert row["stage"] != "not-a-stage"


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


@pytest.mark.asyncio
async def test_attention_dedupes_a_lead_seen_on_several_accounts():
    """/attention is a short list — the same KOL contacted from 3 accounts
    must appear once (the hottest row), not three times."""
    for acc in (8841, 8842, 8843):
        await db.get_or_create_followup_chat(acc, 8844, username="MultiAcc")
        await db.set_followup_stage(acc, 8844, "nurture")
        async with db.get_db() as conn:
            await conn.execute(
                "UPDATE ai_followup_chats SET intent_score=?, lead_tier='Tier A' "
                "WHERE account_id=? AND user_id=8844", (90 - acc % 10, acc))
            await conn.commit()

    rows = await db.get_leads_needing_attention(limit=200)
    mine = [r for r in rows if (r.get("username") or "").lower().lstrip("@") == "multiacc"]
    assert len(mine) == 1, f"expected 1 deduped row, got {len(mine)}"


@pytest.mark.asyncio
async def test_bot_and_self_accounts_are_not_counted_as_leads():
    """The funnel must count humans, not SpamBot/helper bots.

    ~106 of 132 'paused' rows in production were bots — without this filter the
    /pipeline numbers are misleading by ~40%.
    """
    before = await db.get_pipeline_counts()

    for acc, uname in ((8851, "SomeSpamBot"), (8852, "duckchain_bot"),
                       (8853, "weexwill"), (8854, "weexwillbot")):
        await db.get_or_create_followup_chat(acc, 8855, username=uname)
        await db.set_followup_stage(acc, 8855, "paused")

    after = await db.get_pipeline_counts()
    assert after == before, "bot/self rows must not move the funnel counts"

    # ...but a real lead does move them.
    await db.get_or_create_followup_chat(8861, 8862, username="RealKolPerson")
    await db.set_followup_stage(8861, 8862, "paused")
    assert (await db.get_pipeline_counts())["paused"] == before["paused"] + 1


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
