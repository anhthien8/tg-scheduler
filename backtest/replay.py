"""Replay layer — read historical outreach data from SQLite into Funnels.

Read-only, stdlib sqlite3, no app imports: safe to run against a live DB copy
or a backup. Requires the schema created by database.py.

Attribution model (documented, not hidden):
  - An "attempt" is a successful send in dm_campaign_logs or watcher_dm_logs.
  - A "reply" is a dm_replies row whose sender_user_id matches an attempt's
    target_user_id on the SAME account_id.
  - "converted" = the lead's ai_followup_chats.intent_score >= threshold.
  - "onboarded" = the lead's ai_followup_chats.status == 'onboarded'.
  - reply latency = received_at - sent_at for the earliest matching reply.

Known limitation: attribution is by (account_id, user_id) with time ordering,
so a user DM'd by two campaigns is attributed to the most recent successful
send before the reply. This is the same rule the live analytics use.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from backtest.analytics import CONVERSION_INTENT_THRESHOLD, Funnel

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _attempt_rows(conn, campaign_id: int | None) -> list[sqlite3.Row]:
    """Successful sends, optionally scoped to one campaign."""
    if campaign_id is not None:
        sql = """
            SELECT account_id, target_user_id, template_variant_index, sent_at,
                   'campaign' AS source
            FROM dm_campaign_logs
            WHERE campaign_id = ? AND status = 'success'
        """
        return list(conn.execute(sql, (campaign_id,)))
    sql = """
        SELECT account_id, target_user_id, template_variant_index, sent_at,
               'campaign' AS source
        FROM dm_campaign_logs WHERE status = 'success'
        UNION ALL
        SELECT account_id, target_user_id, template_variant_index, sent_at,
               'watcher' AS source
        FROM watcher_dm_logs WHERE status = 'success'
    """
    return list(conn.execute(sql))


def _followup_index(conn, threshold: int) -> dict[tuple[int, int], dict]:
    """(account_id, user_id) -> {intent_score, status} from ai_followup_chats."""
    try:
        rows = conn.execute(
            "SELECT account_id, user_id, intent_score, status FROM ai_followup_chats"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    index = {}
    for r in rows:
        index[(r["account_id"], r["user_id"])] = {
            "intent_score": r["intent_score"] or 0,
            "status": (r["status"] or "").lower(),
        }
    return index


def _replies_by_key(conn) -> dict[tuple[int, int], list[str]]:
    """(account_id, sender_user_id) -> [received_at, ...] sorted ascending."""
    rows = conn.execute(
        "SELECT account_id, sender_user_id, received_at FROM dm_replies"
    ).fetchall()
    by_key: dict[tuple[int, int], list[str]] = {}
    for r in rows:
        by_key.setdefault((r["account_id"], r["sender_user_id"]), []).append(r["received_at"])
    for key in by_key:
        by_key[key].sort()
    return by_key


def build_funnels(db_path: str, campaign_id: int | None = None,
                  threshold: int = CONVERSION_INTENT_THRESHOLD) -> dict[int, Funnel]:
    """Build per-variant Funnels from historical data.

    Returns {variant_index: Funnel}. Variant index comes from
    template_variant_index on the send rows (0 when absent).
    """
    conn = _connect(db_path)
    try:
        attempts = _attempt_rows(conn, campaign_id)
        followups = _followup_index(conn, threshold)
        replies = _replies_by_key(conn)
    finally:
        conn.close()

    funnels: dict[int, Funnel] = {}
    counted_reply_keys: set[tuple[int, int]] = set()

    # Sort NEWEST-first: the most recent send before the reply must claim it
    # (matching the attribution model documented at the top of this module).
    # Processing oldest-first credited the reply to the earliest attempt.
    attempts.sort(key=lambda r: r["sent_at"] or "", reverse=True)

    for row in attempts:
        variant = row["template_variant_index"] if row["template_variant_index"] is not None else 0
        funnel = funnels.setdefault(variant, Funnel())
        funnel.sent += 1

        account_id = row["account_id"]
        user_id = row["target_user_id"]
        key = (account_id, user_id)

        # Reply attribution: earliest reply at/after this send, counted once.
        if key not in counted_reply_keys:
            sent_at = _parse_ts(row["sent_at"])
            reply_times = replies.get(key, [])
            match = _first_reply_after(reply_times, sent_at)
            if match is not None:
                counted_reply_keys.add(key)
                funnel.replied += 1
                if sent_at is not None:
                    delta = (match[1] - sent_at).total_seconds()
                    if delta >= 0:
                        funnel.latencies.append(delta)

                follow = followups.get(key)
                if follow:
                    if (follow["intent_score"] or 0) >= threshold:
                        funnel.converted += 1
                    if follow["status"] == "onboarded":
                        funnel.onboarded += 1

    return funnels


def _first_reply_after(reply_times: list[str], sent_at: datetime | None):
    """Return (index, datetime) of the earliest reply at/after sent_at (or the
    earliest reply if sent_at is unknown)."""
    if not reply_times:
        return None
    if sent_at is None:
        parsed = _parse_ts(reply_times[0])
        return (0, parsed) if parsed else None
    for i, ts in enumerate(reply_times):
        parsed = _parse_ts(ts)
        if parsed is None:
            continue
        if parsed >= sent_at:
            return (i, parsed)
    return None


def overall_funnel(funnels: dict[int, Funnel]) -> Funnel:
    """Sum per-variant funnels into a single overall funnel."""
    total = Funnel()
    for f in funnels.values():
        total.sent += f.sent
        total.replied += f.replied
        total.converted += f.converted
        total.onboarded += f.onboarded
        total.latencies.extend(f.latencies)
    return total
