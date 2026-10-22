"""Pre-send lead scoring / ICP filtering  +  post-conversation dynamic re-scoring.

Goal: stop wasting sends (and warming up ban risk) on low-quality targets.

Phase 1 — STATIC (pre-send, pure, no DB):
  + has a username (reachable, survives peer changes, usually more engaged)
  + has a first name (real profile vs empty)
  + premium account (more likely a genuine customer, less likely a throwaway)
  + recent activity (was online recently — a dead account never replies)
  + profile completeness (first + last name + username + premium)
  + bio_keywords (trading / KOL-related terms in bio)
  - suspected throwaway (no username AND no first name)

Phase 2 — DYNAMIC (post-conversation, async, needs DB):
  + reply velocity / count
  + provided UID
  + community size
  + current exchange
  - silence penalty

The static module is pure (no DB, no I/O) so it is fully unit-testable.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import aiosqlite
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("tg-scheduler.lead_scoring")

# ── Phase 1: Static Signal Weights ──────────────────────────────────

W_USERNAME = 15          # reduced from 25 → 15 (bio_keywords takes weight)
W_FIRST_NAME = 10
W_PREMIUM = 15
W_ACTIVE_RECENT = 30
W_ACTIVE_WEEK = 15
W_COMPLETE_PROFILE = 15  # reduced from 20 → 15
W_BIO_KEYWORDS = 15      # NEW: crypto/KOL bio signals

P_THROWAWAY = -30         # penalty for blank/throwaway profile

# Bio keywords that indicate a crypto / KOL / trading profile
BIO_KEYWORDS_RE = re.compile(
    r'\b(?:trader|trading|analyst|kol|influencer|crypto|defi|dex|cex|exchange|'
    r'nft|blockchain|web3|btc|eth|futures|leverage|copy.?trade|signal)\b',
    re.IGNORECASE,
)

# ── Dataclasses ──────────────────────────────────────────────────────

@dataclass
class LeadScore:
    """Composite lead score with tier and signal breakdown."""
    score: int
    tier: str = "Cold"
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, int] = field(default_factory=dict)

    # Legacy compat — keep .static_score / .dynamic_score aliases
    static_score: int = 0
    dynamic_score: int = 0

    @property
    def total_score(self) -> int:
        return self.score

    @property
    def is_qualified(self) -> bool:
        return self.score > 0


# ── Tier Assignment ──────────────────────────────────────────────────

def assign_tier(total_score: int) -> str:
    """Deterministic tier assignment from composite score."""
    if total_score >= 70:
        return "Tier A"
    elif total_score >= 45:
        return "Tier B"
    elif total_score >= 20:
        return "Tier C"
    else:
        return "Cold"


# ── Helper ───────────────────────────────────────────────────────────

def _as_utc_naive(dt: datetime) -> datetime:
    """Normalise a datetime to naive UTC so naive/aware subtraction never raises.

    Telethon's `status.was_online.isoformat()` yields tz-AWARE strings while
    `datetime.utcnow()` is naive; subtracting the two raises TypeError, which
    silently dropped all activity points for offline users.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ── Phase 1: Static Score (pure, no I/O) ────────────────────────────

def score_lead(member: dict, now: datetime | None = None) -> LeadScore:
    """Score a scraped-member dict.  Returns LeadScore(score, tier, reasons, signals).

    Recognised keys: username, first_name, last_name, is_premium, last_seen, bio/about.
    `last_seen` may be an ISO timestamp (naive or tz-aware), or one of the
    coarse buckets 'recently' / 'last_week' / 'last_month' used by the scraper.

    Backward compatible — same signature as original, returns LeadScore with
    the new `tier` and `signals` fields.
    """
    now = _as_utc_naive(now or datetime.now(timezone.utc))
    score = 0
    reasons: list[str] = []
    signals: dict[str, int] = {}

    username = (member.get("username") or "").strip()
    first_name = (member.get("first_name") or "").strip()
    last_name = (member.get("last_name") or "").strip()
    is_premium = bool(member.get("is_premium"))
    last_seen = member.get("last_seen")
    bio = (member.get("bio") or member.get("about") or "").strip()

    if username:
        score += W_USERNAME
        signals["has_username"] = W_USERNAME
        reasons.append(f"+{W_USERNAME} has_username")
    if first_name:
        score += W_FIRST_NAME
        signals["has_first_name"] = W_FIRST_NAME
        reasons.append(f"+{W_FIRST_NAME} has_name")
    if is_premium:
        score += W_PREMIUM
        signals["is_premium"] = W_PREMIUM
        reasons.append(f"+{W_PREMIUM} premium")
    if first_name and last_name and username and is_premium:
        score += W_COMPLETE_PROFILE
        signals["complete_profile"] = W_COMPLETE_PROFILE
        reasons.append(f"+{W_COMPLETE_PROFILE} complete_profile")

    activity_points, activity_reason = _activity_points(last_seen, now)
    if activity_points:
        score += activity_points
        signals["activity"] = activity_points
        reasons.append(f"+{activity_points} {activity_reason}")

    # NEW: bio keyword signal
    if bio and BIO_KEYWORDS_RE.search(bio):
        score += W_BIO_KEYWORDS
        signals["bio_keywords"] = W_BIO_KEYWORDS
        reasons.append(f"+{W_BIO_KEYWORDS} bio_keywords")

    # Throwaway: no username and no name — basically unreachable/anonymous.
    if not username and not first_name:
        score += P_THROWAWAY
        signals["throwaway_penalty"] = P_THROWAWAY
        reasons.append(f"{P_THROWAWAY} throwaway_profile")

    final = max(0, min(100, score))
    tier = assign_tier(final)
    return LeadScore(score=final, tier=tier, reasons=reasons,
                     signals=signals, static_score=final, dynamic_score=0)


def _activity_points(last_seen, now: datetime) -> tuple[int, str]:
    if not last_seen:
        return 0, ""
    if last_seen == "recently":
        return W_ACTIVE_RECENT, "active_recently"
    if last_seen == "last_week":
        return W_ACTIVE_WEEK, "active_last_week"
    if last_seen == "last_month":
        return 0, ""  # too stale to be worth points
    # ISO timestamp (may be tz-aware from Telethon or naive from scrape)
    try:
        seen = _as_utc_naive(datetime.fromisoformat(str(last_seen)))
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


# ── Phase 2: Dynamic Score (needs DB) ───────────────────────────────

async def _fetch_followup_chat(db_conn, account_id: int, user_id: int) -> dict | None:
    """Read the chat row through whatever `get_db()` yielded.

    Live code yields a raw pooled aiosqlite.Connection (NO get_followup_chat
    method — calling it raised AttributeError and silently killed every
    rescore, which is why lead_score_history stayed at 0 rows). Tests hand in
    mocks that DO expose get_followup_chat; keep honoring those.
    """
    helper = getattr(db_conn, "get_followup_chat", None)
    if callable(helper):
        return await helper(account_id, user_id)
    db_conn.row_factory = aiosqlite.Row
    cursor = await db_conn.execute(
        "SELECT * FROM ai_followup_chats WHERE account_id = ? AND user_id = ?",
        (account_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _fetch_kol_profile(db_conn, account_id: int, user_id: int) -> dict | None:
    """Same raw-connection fallback as _fetch_followup_chat, for kol_profiles."""
    helper = getattr(db_conn, "get_kol_profile", None)
    if callable(helper):
        return await helper(account_id, user_id)
    db_conn.row_factory = aiosqlite.Row
    cursor = await db_conn.execute(
        "SELECT * FROM kol_profiles WHERE account_id = ? AND user_id = ?",
        (account_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def score_lead_dynamic(user_id: int, account_id: int,
                             static_score: int = 0) -> LeadScore:
    """Post-conversation dynamic scoring — combines static + conversational signals.

    Requires DB access (non-pure). Call after lead has interacted.
    """
    from database import get_db   # local import to avoid circular at module level

    dynamic = 0
    reasons: list[str] = []
    signals: dict[str, int] = {}

    try:
        async with get_db() as db_conn:
            # ── Reply signals from ai_followup_chats ──
            chat = await _fetch_followup_chat(db_conn, account_id, user_id)
            if not chat:
                tier = assign_tier(static_score)
                return LeadScore(score=static_score, tier=tier,
                                 static_score=static_score, dynamic_score=0)

            reply_count = chat.get("reply_count", 0) or 0
            if reply_count >= 4:
                dynamic += 15; signals["reply_count"] = 15
                reasons.append("+15 reply_count≥4")
            elif reply_count >= 2:
                dynamic += 8; signals["reply_count"] = 8
                reasons.append("+8 reply_count≥2")
            elif reply_count >= 1:
                dynamic += 3; signals["reply_count"] = 3
                reasons.append("+3 reply_count=1")

            # ── Reply velocity (first_user_message_at vs created_at) ──
            first_reply = chat.get("first_user_message_at")
            outreach_at = chat.get("created_at")
            if first_reply and outreach_at:
                try:
                    ts_reply = datetime.fromisoformat(str(first_reply))
                    ts_out = datetime.fromisoformat(str(outreach_at))
                    # make both naive UTC for safe subtraction
                    ts_reply = _as_utc_naive(ts_reply) if ts_reply.tzinfo else ts_reply
                    ts_out = _as_utc_naive(ts_out) if ts_out.tzinfo else ts_out
                    hours = (ts_reply - ts_out).total_seconds() / 3600
                    if hours < 1:
                        dynamic += 20; signals["reply_velocity"] = 20
                        reasons.append("+20 reply<1h")
                    elif hours < 6:
                        dynamic += 15; signals["reply_velocity"] = 15
                        reasons.append("+15 reply<6h")
                    elif hours < 24:
                        dynamic += 10; signals["reply_velocity"] = 10
                        reasons.append("+10 reply<24h")
                    elif hours < 72:
                        dynamic += 5; signals["reply_velocity"] = 5
                        reasons.append("+5 reply<72h")
                except Exception:
                    pass

            # ── KOL profile signals ──
            try:
                profile = await _fetch_kol_profile(db_conn, account_id, user_id)
            except Exception:
                profile = None
            if profile:
                try:
                    profile_data = json.loads(profile.get("profile_json", "{}") or "{}")
                except (json.JSONDecodeError, TypeError):
                    profile_data = {}

                if profile_data.get("weex_uid"):
                    dynamic += 20; signals["provided_uid"] = 20
                    reasons.append("+20 provided_uid")
                community = profile_data.get("community_name") or ""
                if community:
                    dynamic += 10; signals["has_community"] = 10
                    reasons.append("+10 has_community")
                if profile_data.get("current_cex"):
                    dynamic += 5; signals["current_exchange"] = 5
                    reasons.append("+5 current_exchange")

            # ── Silence penalty ──
            last_msg = chat.get("last_user_message_at")
            if last_msg:
                try:
                    ts = datetime.fromisoformat(str(last_msg))
                    ts = _as_utc_naive(ts) if ts.tzinfo else ts
                    now = _as_utc_naive(datetime.now(timezone.utc))
                    silence_days = (now - ts).total_seconds() / 86400
                    if silence_days > 14:
                        dynamic -= 15; signals["silence_penalty"] = -15
                        reasons.append("-15 silence>14d")
                    elif silence_days > 7:
                        dynamic -= 10; signals["silence_penalty"] = -10
                        reasons.append("-10 silence>7d")
                    elif silence_days > 3:
                        dynamic -= 5; signals["silence_penalty"] = -5
                        reasons.append("-5 silence>3d")
                except Exception:
                    pass

    except Exception as exc:
        logger.warning("[LeadScoring] Dynamic scoring failed: %s", exc)
        tier = assign_tier(static_score)
        return LeadScore(score=static_score, tier=tier,
                         static_score=static_score, dynamic_score=0)

    total = max(0, min(100, static_score + dynamic))
    tier = assign_tier(total)
    return LeadScore(
        score=total,
        tier=tier,
        reasons=reasons,
        signals=signals,
        static_score=static_score,
        dynamic_score=dynamic,
    )


async def rescore_and_update(user_id: int, account_id: int) -> LeadScore:
    """Re-score a lead (static + dynamic), update DB, log to history.

    Call after lead replies, drip sent, or on periodic cron.
    """
    from database import get_db

    # 1. Get current static score from existing chat data
    static_score = 30  # default
    existing_summary = ""
    try:
        async with get_db() as db_conn:
            chat = await _fetch_followup_chat(db_conn, account_id, user_id)
            if chat:
                # Use existing intent_score as base if available. Preserve the
                # LLM's human-readable summary; rescoring changes the numeric
                # signal, not the operator-facing explanation.
                static_score = chat.get("intent_score", 30) or 30
                existing_summary = str(chat.get("summary") or "")
    except Exception:
        pass

    # 2. Run dynamic scoring
    result = await score_lead_dynamic(user_id, account_id, static_score=0)

    # 3. Merge: keep the better of (LLM-assigned score) vs (dynamic-computed score)
    final_score = max(static_score, result.score)
    final_tier = assign_tier(final_score)
    result = LeadScore(
        score=final_score,
        tier=final_tier,
        reasons=result.reasons,
        signals=result.signals,
        static_score=static_score,
        dynamic_score=result.dynamic_score,
    )

    # 4. Update DB
    try:
        async with get_db() as db_conn:
            update_metrics = getattr(db_conn, "update_followup_lead_metrics", None)
            summary = existing_summary or "; ".join(result.reasons[:5])
            if callable(update_metrics):
                await update_metrics(
                    account_id=account_id,
                    user_id=user_id,
                    intent_score=result.score,
                    lead_tier=result.tier,
                    summary=summary,
                )
            else:
                await db_conn.execute(
                    """UPDATE ai_followup_chats
                       SET intent_score = ?, lead_tier = ?, summary = ?, updated_at = datetime('now')
                       WHERE account_id = ? AND user_id = ?""",
                    (result.score, result.tier, summary, account_id, user_id),
                )
                await db_conn.commit()
            # Log to history
            try:
                await db_conn.execute(
                    """INSERT INTO lead_score_history
                       (account_id, user_id, static_score, dynamic_score,
                        total_score, tier, signals_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (account_id, user_id, result.static_score,
                     result.dynamic_score, result.score, result.tier,
                     json.dumps(result.signals, ensure_ascii=False)),
                )
                await db_conn.commit()
            except Exception as hist_exc:
                # Schema self-check (v3.10.1) guarantees the table; a failure
                # here is a REAL bug — log it instead of silently dropping it.
                logger.warning("[LeadScoring] history insert failed: %s", hist_exc)
    except Exception as exc:
        logger.warning("[LeadScoring] rescore_and_update DB error: %s", exc)

    return result
