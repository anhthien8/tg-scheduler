"""Pre-send lead scoring / ICP filtering.

Goal: stop wasting sends (and warming up ban risk) on low-quality targets.
A score of 0-100 is computed from signals already available BEFORE sending:

  + has a username (reachable, survives peer changes, usually more engaged)
  + has a first name (real profile vs empty)
  + premium account (more likely a genuine customer, less likely a throwaway)
  + recent activity (was online recently — a dead account never replies)
  + profile completeness (first + last name + username + premium)
  - suspected throwaway (no username AND no first name)

This is deliberately conservative and explainable. It is OFF by default
(min_score=0) so existing campaigns are unaffected until an operator opts in
by setting an ICP threshold on the campaign.

The module is pure (no DB, no I/O) so it is fully unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Signal weights (sum to 100 max).
W_USERNAME = 25
W_FIRST_NAME = 10
W_PREMIUM = 15
W_ACTIVE_RECENT = 30
W_ACTIVE_WEEK = 15
W_COMPLETE_PROFILE = 20

# Penalty for a blank/throwaway profile.
P_THROWAWAY = -30


@dataclass
class LeadScore:
    score: int
    reasons: list[str] = field(default_factory=list)

    @property
    def is_qualified(self):
        return self.score


def score_lead(member: dict, now: datetime | None = None) -> LeadScore:
    """Score a scraped-member dict. Returns LeadScore(score, reasons).

    Recognised keys: username, first_name, last_name, is_premium, last_seen.
    `last_seen` may be an ISO timestamp, or one of the coarse buckets
    'recently' / 'last_week' / 'last_month' used by the scraper.
    """
    now = now or datetime.utcnow()
    score = 0
    reasons: list[str] = []

    username = (member.get("username") or "").strip()
    first_name = (member.get("first_name") or "").strip()
    last_name = (member.get("last_name") or "").strip()
    is_premium = bool(member.get("is_premium"))
    last_seen = member.get("last_seen")

    if username:
        score += W_USERNAME
        reasons.append(f"+{W_USERNAME} has_username")
    if first_name:
        score += W_FIRST_NAME
        reasons.append(f"+{W_FIRST_NAME} has_name")
    if is_premium:
        score += W_PREMIUM
        reasons.append(f"+{W_PREMIUM} premium")
    if first_name and last_name and username and is_premium:
        score += W_COMPLETE_PROFILE
        reasons.append(f"+{W_COMPLETE_PROFILE} complete_profile")

    activity_points, activity_reason = _activity_points(last_seen, now)
    if activity_points:
        score += activity_points
        reasons.append(f"+{activity_points} {activity_reason}")

    # Throwaway: no username and no name — basically unreachable/anonymous.
    if not username and not first_name:
        score += P_THROWAWAY
        reasons.append(f"{P_THROWAWAY} throwaway_profile")

    return LeadScore(score=max(0, min(100, score)), reasons=reasons)


def _activity_points(last_seen, now: datetime) -> tuple[int, str]:
    if not last_seen:
        return 0, ""
    if last_seen == "recently":
        return W_ACTIVE_RECENT, "active_recently"
    if last_seen == "last_week":
        return W_ACTIVE_WEEK, "active_last_week"
    if last_seen == "last_month":
        return 0, ""  # too stale to be worth points
    # ISO timestamp
    try:
        seen = datetime.fromisoformat(str(last_seen))
    except (ValueError, TypeError):
        return 0, ""
    delta = now - seen
    if delta <= timedelta(days=2):
        return W_ACTIVE_RECENT, "active_recently"
    if delta <= timedelta(days=7):
        return W_ACTIVE_WEEK, "active_last_week"
    return 0, ""


def passes_icp(member: dict, min_score: int, now: datetime | None = None) -> tuple[bool, LeadScore]:
    """Return (passes, lead_score). min_score<=0 disables filtering."""
    result = score_lead(member, now=now)
    if min_score <= 0:
        return True, result
    return result.score >= min_score, result
