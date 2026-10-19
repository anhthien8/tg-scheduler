"""Unit tests for lead_scoring — pure, no DB."""
from datetime import datetime, timedelta

from lead_scoring import passes_icp, score_lead

NOW = datetime(2026, 9, 28, 12, 0, 0)


def test_full_profile_scores_high():
    lead = score_lead({
        "username": "trader", "first_name": "Al", "last_name": "Ice",
        "is_premium": True, "last_seen": "recently",
    }, now=NOW)
    assert lead.score >= 80
    assert any("complete_profile" in r for r in lead.reasons)


def test_throwaway_profile_scores_low():
    lead = score_lead({"username": None, "first_name": None}, now=NOW)
    assert lead.score == 0
    assert any("throwaway" in r for r in lead.reasons)


def test_username_only_is_modest():
    lead = score_lead({"username": "someone"}, now=NOW)
    assert 10 <= lead.score <= 20


def test_recent_activity_beats_stale():
    recent = score_lead({"username": "u", "last_seen": "recently"}, now=NOW)
    stale = score_lead({"username": "u", "last_seen": "last_month"}, now=NOW)
    assert recent.score > stale.score


def test_iso_timestamp_within_two_days_counts_recent():
    ts = (NOW - timedelta(days=1)).isoformat()
    lead = score_lead({"username": "u", "last_seen": ts}, now=NOW)
    assert any("active_recently" in r for r in lead.reasons)


def test_iso_timestamp_older_than_week_no_activity_points():
    ts = (NOW - timedelta(days=30)).isoformat()
    lead = score_lead({"username": "u", "last_seen": ts}, now=NOW)
    assert not any("active" in r for r in lead.reasons)


def test_invalid_timestamp_is_ignored():
    lead = score_lead({"username": "u", "last_seen": "not-a-date"}, now=NOW)
    assert lead.score > 0
    assert not any("active" in r for r in lead.reasons)


def test_score_is_clamped_0_100():
    lead = score_lead({
        "username": "u", "first_name": "A", "last_name": "B",
        "is_premium": True, "last_seen": "recently",
    }, now=NOW)
    assert 0 <= lead.score <= 100


def test_passes_icp_disabled_when_threshold_zero():
    ok, lead = passes_icp({"username": None, "first_name": None}, min_score=0, now=NOW)
    assert ok is True
    assert lead.score == 0


def test_passes_icp_rejects_below_threshold():
    ok, _ = passes_icp({"username": None, "first_name": None}, min_score=50, now=NOW)
    assert ok is False


def test_passes_icp_accepts_above_threshold():
    member = {"username": "u", "first_name": "A", "last_name": "B", "is_premium": True}
    ok, _ = passes_icp(member, min_score=50, now=NOW)
    assert ok is True


def test_lead_score_is_qualified_truthy():
    assert score_lead({"username": "u"}, now=NOW).is_qualified
    assert not score_lead({}, now=NOW).is_qualified


def test_is_qualified_returns_bool_not_int():
    assert score_lead({"username": "u"}, now=NOW).is_qualified is True
    assert score_lead({}, now=NOW).is_qualified is False


def test_tz_aware_last_seen_still_awards_activity_points():
    """Telethon emits tz-aware ISO (e.g. '+00:00'); must not lose the points."""
    aware_ts = "2026-09-27T12:00:00+00:00"  # 1 day before NOW (2026-09-28 12:00)
    lead = score_lead({"username": "u", "last_seen": aware_ts}, now=NOW)
    assert any("active_recently" in r for r in lead.reasons)


def test_tz_aware_now_does_not_crash():
    from datetime import timezone
    aware_now = NOW.replace(tzinfo=timezone.utc)
    lead = score_lead({"username": "u", "last_seen": "2026-09-25T12:00:00+00:00"}, now=aware_now)
    assert lead.score > 0
