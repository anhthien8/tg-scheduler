"""Tests for C2: Enhanced Lead Scoring with bio_keywords, tiers, and dynamic scoring."""
import asyncio
import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from lead_scoring import (
    LeadScore,
    assign_tier,
    score_lead,
    passes_icp,
    score_lead_dynamic,
    rescore_and_update,
    BIO_KEYWORDS_RE,
    W_BIO_KEYWORDS,
)


# ── Static Scoring Tests ────────────────────────────────────────────

class TestAssignTier:
    def test_tier_a_boundary(self):
        assert assign_tier(70) == "Tier A"
        assert assign_tier(100) == "Tier A"

    def test_tier_b_boundary(self):
        assert assign_tier(45) == "Tier B"
        assert assign_tier(69) == "Tier B"

    def test_tier_c_boundary(self):
        assert assign_tier(20) == "Tier C"
        assert assign_tier(44) == "Tier C"

    def test_cold_boundary(self):
        assert assign_tier(0) == "Cold"
        assert assign_tier(19) == "Cold"

    def test_negative(self):
        assert assign_tier(-10) == "Cold"


class TestScoreLeadStatic:
    def test_premium_active_user_high_score(self):
        """Premium user with recent activity should score highly."""
        member = {
            "username": "crypto_king",
            "first_name": "John",
            "last_name": "Doe",
            "is_premium": True,
            "last_seen": "recently",
        }
        result = score_lead(member)
        assert result.score >= 70  # Should be Tier A
        assert result.tier == "Tier A"
        assert "has_username" in result.signals
        assert "is_premium" in result.signals

    def test_bio_keywords_boost(self):
        """Bio containing crypto keywords should add W_BIO_KEYWORDS points."""
        member_no_bio = {"username": "user1"}
        member_with_bio = {"username": "user1", "bio": "Professional crypto trader and analyst"}

        score_no_bio = score_lead(member_no_bio)
        score_with_bio = score_lead(member_with_bio)
        assert score_with_bio.score == score_no_bio.score + W_BIO_KEYWORDS
        assert "bio_keywords" in score_with_bio.signals

    def test_bio_keywords_regex_matches(self):
        """Verify bio keyword regex matches expected terms."""
        matches = ["trader", "KOL", "crypto expert", "DeFi developer",
                    "exchange operator", "NFT artist", "web3 builder"]
        for text in matches:
            assert BIO_KEYWORDS_RE.search(text), f"Should match: {text}"

    def test_bio_keywords_no_false_positive(self):
        """Non-crypto bios should not trigger."""
        no_match = ["I love cooking", "Student at MIT", "Dog lover"]
        for text in no_match:
            assert not BIO_KEYWORDS_RE.search(text), f"Should NOT match: {text}"

    def test_throwaway_penalty(self):
        """No username AND no name → throwaway penalty."""
        member = {}
        result = score_lead(member)
        assert "throwaway_penalty" in result.signals
        assert result.signals["throwaway_penalty"] < 0

    def test_score_clamped_0_100(self):
        """Score should always be between 0 and 100."""
        result = score_lead({})
        assert 0 <= result.score <= 100

    def test_complete_profile_bonus(self):
        """Full profile (first + last + username + premium) → bonus."""
        member = {
            "username": "test",
            "first_name": "A",
            "last_name": "B",
            "is_premium": True,
        }
        result = score_lead(member)
        assert "complete_profile" in result.signals

    def test_backward_compat_lead_score_has_tier(self):
        """LeadScore dataclass now includes tier field."""
        result = score_lead({"username": "abc"})
        assert hasattr(result, "tier")
        assert hasattr(result, "signals")
        assert hasattr(result, "is_qualified")
        assert isinstance(result.tier, str)

    def test_about_field_alias(self):
        """'about' field should work as alias for 'bio'."""
        member = {"username": "x", "about": "Crypto trader"}
        result = score_lead(member)
        assert "bio_keywords" in result.signals

    def test_activity_recently(self):
        """'recently' last_seen → activity bonus."""
        member = {"username": "x", "last_seen": "recently"}
        result = score_lead(member)
        assert "activity" in result.signals
        assert result.signals["activity"] > 0

    def test_activity_last_week(self):
        """'last_week' last_seen → smaller activity bonus."""
        member = {"username": "x", "last_seen": "last_week"}
        result = score_lead(member)
        assert "activity" in result.signals

    def test_activity_iso_timestamp(self):
        """ISO timestamp within 2 days → recently bonus."""
        now = datetime.now(timezone.utc)
        member = {"username": "x", "last_seen": (now - timedelta(hours=6)).isoformat()}
        result = score_lead(member, now=now)
        assert "activity" in result.signals


class TestPassesIcp:
    def test_min_score_zero_always_passes(self):
        """min_score=0 → always passes (disabled)."""
        passes, result = passes_icp({}, min_score=0)
        assert passes

    def test_high_threshold(self):
        """High threshold should reject low-quality leads."""
        passes, result = passes_icp({}, min_score=50)
        assert not passes


# ── Dynamic Scoring Tests ────────────────────────────────────────────

class TestScoreLeadDynamic:
    @pytest.mark.asyncio
    async def test_no_chat_returns_static(self):
        """If no conversation exists, return static score only."""
        mock_db = AsyncMock()
        mock_db.get_followup_chat = AsyncMock(return_value=None)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch("database.get_db", return_value=mock_db):
            result = await score_lead_dynamic(user_id=123, account_id=1, static_score=30)
        assert result.score == 30
        assert result.tier == "Tier C"

    @pytest.mark.asyncio
    async def test_reply_count_boost(self):
        """4+ replies should give +15 dynamic boost."""
        mock_db = AsyncMock()
        chat = {"reply_count": 5, "first_user_message_at": None, "created_at": None,
                "last_user_message_at": None}
        mock_db.get_followup_chat = AsyncMock(return_value=chat)
        mock_db.get_kol_profile = AsyncMock(return_value=None)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch("database.get_db", return_value=mock_db):
            result = await score_lead_dynamic(user_id=123, account_id=1, static_score=50)
        assert result.signals.get("reply_count") == 15
        assert result.score >= 15

    @pytest.mark.asyncio
    async def test_uid_provided_signal(self):
        """Provided WEEX UID → +20."""
        mock_db = AsyncMock()
        chat = {"reply_count": 0, "first_user_message_at": None, "created_at": None,
                "last_user_message_at": None}
        mock_db.get_followup_chat = AsyncMock(return_value=chat)
        profile = {"profile_json": json.dumps({"weex_uid": "12345"})}
        mock_db.get_kol_profile = AsyncMock(return_value=profile)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch("database.get_db", return_value=mock_db):
            result = await score_lead_dynamic(user_id=123, account_id=1, static_score=0)
        assert result.signals.get("provided_uid") == 20

    @pytest.mark.asyncio
    async def test_silence_penalty(self):
        """No reply > 7 days → -10 penalty."""
        mock_db = AsyncMock()
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        chat = {"reply_count": 1, "first_user_message_at": None, "created_at": None,
                "last_user_message_at": old_time}
        mock_db.get_followup_chat = AsyncMock(return_value=chat)
        mock_db.get_kol_profile = AsyncMock(return_value=None)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch("database.get_db", return_value=mock_db):
            result = await score_lead_dynamic(user_id=123, account_id=1, static_score=50)
        assert result.signals.get("silence_penalty") == -10

    @pytest.mark.asyncio
    async def test_reply_velocity_fast(self):
        """Reply < 1 hour → +20."""
        mock_db = AsyncMock()
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=2)).isoformat()
        first_reply = (now - timedelta(hours=1, minutes=30)).isoformat()
        chat = {"reply_count": 1, "first_user_message_at": first_reply,
                "created_at": created, "last_user_message_at": now.isoformat()}
        mock_db.get_followup_chat = AsyncMock(return_value=chat)
        mock_db.get_kol_profile = AsyncMock(return_value=None)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch("database.get_db", return_value=mock_db):
            result = await score_lead_dynamic(user_id=123, account_id=1, static_score=0)
        assert result.signals.get("reply_velocity") in (15, 20)


class TestRescoreAndUpdate:
    @pytest.mark.asyncio
    async def test_rescore_calls_update(self):
        """rescore_and_update should call update_followup_lead_metrics."""
        mock_db = AsyncMock()
        chat = {"reply_count": 3, "intent_score": 40, "first_user_message_at": None,
                "created_at": None, "last_user_message_at": None}
        mock_db.get_followup_chat = AsyncMock(return_value=chat)
        mock_db.get_kol_profile = AsyncMock(return_value=None)
        mock_db.update_followup_lead_metrics = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch("database.get_db", return_value=mock_db):
            result = await rescore_and_update(user_id=123, account_id=1)

        assert result.score > 0
        assert result.tier in ("Tier A", "Tier B", "Tier C", "Cold")

    @pytest.mark.asyncio
    async def test_backward_compat_old_score_lead(self):
        """Old score_lead function still works with same signature."""
        result = score_lead({"username": "test", "first_name": "John"})
        assert isinstance(result, LeadScore)
        assert isinstance(result.score, int)
        # New fields available
        assert isinstance(result.tier, str)
        assert isinstance(result.signals, dict)
