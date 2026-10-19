"""Tests for C1: Tier-Differentiated Drip Cadence."""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from dm_reply_tracker import (
    DEFAULT_DRIP_CADENCE_HOURS,
    DEFAULT_TIER_CADENCE,
    _MIN_FIRST_STAGE_HOURS,
    _validate_cadence,
    _load_drip_cadence,
    _load_drip_cadence_for_tier,
    _drip_instruction_for_stage,
)


# ── _validate_cadence ──

def test_validate_cadence_valid():
    assert _validate_cadence("[12, 24, 48]") == [12, 24, 48]

def test_validate_cadence_single():
    assert _validate_cadence("[24]") == [24]

def test_validate_cadence_not_increasing():
    assert _validate_cadence("[24, 24, 48]") is None

def test_validate_cadence_negative():
    assert _validate_cadence("[-1, 24]") is None

def test_validate_cadence_empty():
    assert _validate_cadence("[]") is None

def test_validate_cadence_garbage():
    assert _validate_cadence("not json") is None

def test_validate_cadence_zero():
    assert _validate_cadence("[0, 24]") is None


# ── _load_drip_cadence ──

@pytest.mark.asyncio
async def test_load_drip_cadence_default():
    with patch("dm_reply_tracker.db") as mock_db:
        mock_db.get_setting = AsyncMock(return_value="")
        result = await _load_drip_cadence()
        assert result == DEFAULT_DRIP_CADENCE_HOURS

@pytest.mark.asyncio
async def test_load_drip_cadence_custom():
    with patch("dm_reply_tracker.db") as mock_db:
        mock_db.get_setting = AsyncMock(return_value="[6, 12, 24]")
        result = await _load_drip_cadence()
        assert result == [6, 12, 24]

@pytest.mark.asyncio
async def test_load_drip_cadence_invalid_falls_back():
    with patch("dm_reply_tracker.db") as mock_db:
        mock_db.get_setting = AsyncMock(return_value="bad")
        result = await _load_drip_cadence()
        assert result == DEFAULT_DRIP_CADENCE_HOURS


# ── _load_drip_cadence_for_tier ──

@pytest.mark.asyncio
async def test_tier_a_default_cadence():
    with patch("dm_reply_tracker.db") as mock_db:
        mock_db.get_setting = AsyncMock(return_value="")
        result = await _load_drip_cadence_for_tier("Tier A")
        assert result == [12, 24, 48, 96]

@pytest.mark.asyncio
async def test_tier_b_default_cadence():
    with patch("dm_reply_tracker.db") as mock_db:
        mock_db.get_setting = AsyncMock(return_value="")
        result = await _load_drip_cadence_for_tier("Tier B")
        assert result == [24, 72, 168]

@pytest.mark.asyncio
async def test_tier_c_default_cadence():
    with patch("dm_reply_tracker.db") as mock_db:
        mock_db.get_setting = AsyncMock(return_value="")
        result = await _load_drip_cadence_for_tier("Tier C")
        assert result == [48, 168]

@pytest.mark.asyncio
async def test_tier_a_custom_from_settings():
    with patch("dm_reply_tracker.db") as mock_db:
        async def fake_setting(key, default=""):
            if key == "ai_followup_drip_cadence_tier_a":
                return "[6, 12, 24, 48]"
            return default
        mock_db.get_setting = AsyncMock(side_effect=fake_setting)
        result = await _load_drip_cadence_for_tier("Tier A")
        assert result == [6, 12, 24, 48]

@pytest.mark.asyncio
async def test_tier_invalid_setting_falls_to_tier_default():
    with patch("dm_reply_tracker.db") as mock_db:
        async def fake_setting(key, default=""):
            if key == "ai_followup_drip_cadence_tier_b":
                return "garbage"
            return default
        mock_db.get_setting = AsyncMock(side_effect=fake_setting)
        result = await _load_drip_cadence_for_tier("Tier B")
        assert result == [24, 72, 168]

@pytest.mark.asyncio
async def test_unknown_tier_falls_to_global():
    with patch("dm_reply_tracker.db") as mock_db:
        mock_db.get_setting = AsyncMock(return_value="")
        result = await _load_drip_cadence_for_tier("Unknown")
        assert result == DEFAULT_DRIP_CADENCE_HOURS


# ── Stage counts per tier ──

def test_tier_a_has_4_stages():
    assert len(DEFAULT_TIER_CADENCE["Tier A"]) == 4

def test_tier_b_has_3_stages():
    assert len(DEFAULT_TIER_CADENCE["Tier B"]) == 3

def test_tier_c_has_2_stages():
    assert len(DEFAULT_TIER_CADENCE["Tier C"]) == 2

def test_min_first_stage_is_12():
    assert _MIN_FIRST_STAGE_HOURS == 12
    # Verify it matches the minimum across all tiers
    min_h = min(cadence[0] for cadence in DEFAULT_TIER_CADENCE.values())
    assert _MIN_FIRST_STAGE_HOURS == min_h


# ── _drip_instruction_for_stage (tier-aware) ──

def test_tier_a_stage1_personalized():
    instr = _drip_instruction_for_stage(1, 4, "Tier A")
    assert "high-value KOL" in instr
    assert "personalized" in instr

def test_tier_b_stage1_default():
    instr = _drip_instruction_for_stage(1, 3, "Tier B")
    assert "competition" in instr or "prize pool" in instr

def test_tier_a_stage2_case_study():
    instr = _drip_instruction_for_stage(2, 4, "Tier A")
    assert "case study" in instr or "testimonial" in instr

def test_tier_a_stage3_demo():
    instr = _drip_instruction_for_stage(3, 4, "Tier A")
    assert "demo" in instr or "proposal" in instr

def test_final_stage_always_final_checkin():
    # Final stage for any tier should mention "last message"
    instr_a = _drip_instruction_for_stage(4, 4, "Tier A")
    instr_b = _drip_instruction_for_stage(3, 3, "Tier B")
    instr_c = _drip_instruction_for_stage(2, 2, "Tier C")
    assert "last message" in instr_a or "FINAL" in instr_a
    assert "last message" in instr_b or "FINAL" in instr_b
    assert "last message" in instr_c or "FINAL" in instr_c

def test_middle_stage_default_tier_b():
    instr = _drip_instruction_for_stage(2, 3, "Tier B")
    assert "different angle" in instr

def test_instruction_backward_compat_no_tier():
    """Old code calling without tier param should still work."""
    instr = _drip_instruction_for_stage(1, 3)
    assert isinstance(instr, str) and len(instr) > 10
