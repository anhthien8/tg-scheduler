"""Tests for C6: Warmup Engine v2 — stage determination, behavior weights, graduation."""
import pytest
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from warmup_engine import (
    BEHAVIOR_WEIGHTS,
    determine_stage,
    pick_behavior,
    should_graduate,
    GRAD_MIN_DAYS,
    GRAD_MIN_MESSAGES,
    GRAD_MIN_REACTIONS,
    GRAD_MIN_CHANNELS,
)


# ── Stage Determination Tests ────────────────────────────────────────

class TestDetermineStage:
    def test_none_started_at(self):
        assert determine_stage(None) == 1

    def test_day_0(self):
        now = datetime.now(timezone.utc).isoformat()
        assert determine_stage(now) == 1

    def test_day_1(self):
        started = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
        assert determine_stage(started) == 1

    def test_day_3(self):
        started = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        assert determine_stage(started) == 2

    def test_day_5(self):
        started = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        assert determine_stage(started) == 3

    def test_day_10(self):
        started = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        assert determine_stage(started) == 4

    def test_invalid_date(self):
        assert determine_stage("not-a-date") == 1


# ── Behavior Weight Tests ────────────────────────────────────────────

class TestPickBehavior:
    def test_stage_1_no_posting(self):
        """Stage 1 should never select post_message."""
        random.seed(42)
        behaviors = [pick_behavior(1) for _ in range(200)]
        assert "post_message" not in behaviors
        assert "forward_message" not in behaviors
        assert "join_channel" not in behaviors

    def test_stage_1_only_passive(self):
        """Stage 1 should only select passive behaviors."""
        random.seed(42)
        behaviors = set(pick_behavior(1) for _ in range(200))
        allowed = {"read_history", "react_emoji", "view_profile"}
        assert behaviors.issubset(allowed)

    def test_stage_2_includes_posting(self):
        """Stage 2 should sometimes select post_message."""
        random.seed(42)
        behaviors = [pick_behavior(2) for _ in range(500)]
        assert "post_message" in behaviors

    def test_stage_3_includes_forward(self):
        """Stage 3 should sometimes select forward_message."""
        random.seed(42)
        behaviors = [pick_behavior(3) for _ in range(500)]
        assert "forward_message" in behaviors

    def test_stage_4_heavy_posting(self):
        """Stage 4 should select post_message most often."""
        random.seed(42)
        behaviors = [pick_behavior(4) for _ in range(1000)]
        post_count = behaviors.count("post_message")
        assert post_count > 250  # 40% weight ≈ 400 out of 1000

    def test_all_stages_have_weights(self):
        """All 4 stages should be defined in BEHAVIOR_WEIGHTS."""
        for s in [1, 2, 3, 4]:
            assert s in BEHAVIOR_WEIGHTS
            total_weight = sum(BEHAVIOR_WEIGHTS[s].values())
            assert total_weight == 100, f"Stage {s} weights sum to {total_weight}, not 100"

    def test_unknown_stage_falls_back_to_4(self):
        """Unknown stage should fallback to stage 4."""
        random.seed(42)
        behavior = pick_behavior(99)
        assert behavior in BEHAVIOR_WEIGHTS[4]


# ── Graduation Tests ─────────────────────────────────────────────────

class TestShouldGraduate:
    def test_none_progress(self):
        assert not should_graduate(None)

    def test_already_graduated(self):
        progress = {"graduated_at": "2026-01-01T00:00:00", "started_at": "2025-01-01T00:00:00"}
        assert should_graduate(progress)

    def test_not_enough_days(self):
        started = datetime.now(timezone.utc).isoformat()
        progress = {
            "started_at": started,
            "messages_sent": 100,
            "reactions_sent": 100,
            "channels_joined": 100,
            "graduated_at": None,
        }
        assert not should_graduate(progress)  # not 7 days yet

    def test_not_enough_messages(self):
        started = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        progress = {
            "started_at": started,
            "messages_sent": 2,  # too few
            "reactions_sent": 100,
            "channels_joined": 100,
            "graduated_at": None,
        }
        assert not should_graduate(progress)

    def test_not_enough_reactions(self):
        started = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        progress = {
            "started_at": started,
            "messages_sent": 100,
            "reactions_sent": 5,  # too few
            "channels_joined": 100,
            "graduated_at": None,
        }
        assert not should_graduate(progress)

    def test_not_enough_channels(self):
        started = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        progress = {
            "started_at": started,
            "messages_sent": 100,
            "reactions_sent": 100,
            "channels_joined": 1,  # too few
            "graduated_at": None,
        }
        assert not should_graduate(progress)

    def test_all_criteria_met(self):
        started = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        progress = {
            "started_at": started,
            "messages_sent": GRAD_MIN_MESSAGES,
            "reactions_sent": GRAD_MIN_REACTIONS,
            "channels_joined": GRAD_MIN_CHANNELS,
            "graduated_at": None,
        }
        assert should_graduate(progress)

    def test_graduation_constants(self):
        """Verify graduation constants are reasonable."""
        assert GRAD_MIN_DAYS >= 5
        assert GRAD_MIN_MESSAGES >= 5
        assert GRAD_MIN_REACTIONS >= 5
        assert GRAD_MIN_CHANNELS >= 2

    def test_missing_started_at(self):
        progress = {
            "started_at": None,
            "messages_sent": 100,
            "reactions_sent": 100,
            "channels_joined": 100,
            "graduated_at": None,
        }
        assert not should_graduate(progress)
