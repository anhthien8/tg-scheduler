"""
claimer.py — Atomic rate-limit claim system for the Community Engagement Engine.

Implements the claim/reserve-before-send pattern from design doc §4.1.1:
- claim_limit(): Counter-based atomic conditional increment for limit-type windows
- claim_gap(): Timestamp-based gap enforcement for gap-type windows
- release_claims(): Rollback by claim_id (never by window_key)
- build_windows(): Generate all 5 window checks for a single reply
"""
import logging
import time as _time
from datetime import datetime, timezone

import database as db
from database import get_db

logger = logging.getLogger("tg-scheduler.claimer")


def build_windows(campaign: dict, group_id: int, account_id: int, now: float | None = None):
    """Return list of (window_key, kind, scope, limit, min_gap) — in check order."""
    now = now or _time.time()
    hour = _time.strftime("%Y%m%dT%H", _time.gmtime(now))
    day = _time.strftime("%Y%m%d", _time.gmtime(now))
    return [
        (f"acct:{account_id}:{day}", "limit", "account", 25, 0),
        (f"groupacct:{group_id}:{account_id}:{day}", "limit", "account",
         campaign.get("max_replies_per_day_per_group", 12), 0),
        (f"group:{group_id}:{hour}", "limit", "group",
         campaign.get("max_replies_per_hour_per_group", 4), 0),
        (f"gap:{group_id}:{account_id}", "gap", "account", None,
         campaign.get("min_gap_seconds", 180)),
        (f"gapacct:{account_id}", "gap", "account", None, 120),
    ]


def _counter_key(scope: str, group_id: int, account_id: int, kind_prefix: str) -> dict:
    """Determine the counter row scope keys based on window type."""
    if scope == "group":
        return dict(window_kind="group", acct_key=-1, group_key=group_id)
    if kind_prefix == "acct":
        return dict(window_kind="acct", acct_key=account_id, group_key=-1)
    return dict(window_kind="groupacct", acct_key=account_id, group_key=group_id)


async def claim_limit(
    campaign_id: int, group_id: int, account_id: int,
    window_key: str, limit: int, scope: str
) -> tuple[bool, str, int | None, int | None]:
    """Claim a slot in a limit-type window using atomic counter increment.

    Returns (ok, reason, claim_id, counter_id).
    ok=True means slot was reserved; ok=False means rate limit hit.
    """
    key = _counter_key(scope, group_id, account_id, window_key.split(":")[0])

    async with get_db() as conn:
        # BEGIN IMMEDIATE for serialization
        await conn.execute("BEGIN IMMEDIATE")
        try:
            # 1) Ensure counter row exists (idempotent)
            await conn.execute(
                "INSERT OR IGNORE INTO engagement_window_counters"
                "(window_kind, acct_key, group_key, window_key, count, limit_) "
                "VALUES(?,?,?,?,0,?)",
                (key["window_kind"], key["acct_key"], key["group_key"], window_key, limit),
            )

            # 2) Atomic conditional increment
            cur = await conn.execute(
                "UPDATE engagement_window_counters SET count=count+1, updated_at=datetime('now') "
                "WHERE window_kind=? AND acct_key=? AND group_key=? AND window_key=? "
                "  AND count < ?",
                (key["window_kind"], key["acct_key"], key["group_key"], window_key, limit),
            )
            if cur.rowcount != 1:
                await conn.execute("COMMIT")
                return False, "rate_limit", None, None

            # Get counter row id for rollback
            counter_row = await (await conn.execute(
                "SELECT id FROM engagement_window_counters "
                "WHERE window_kind=? AND acct_key=? AND group_key=? AND window_key=?",
                (key["window_kind"], key["acct_key"], key["group_key"], window_key),
            )).fetchone()
            counter_id = counter_row[0] if counter_row else None

            # 3) Record in claims ledger
            claim_cur = await conn.execute(
                "INSERT INTO engagement_claims(campaign_id, group_id, account_id, window_key) "
                "VALUES(?,?,?,?)",
                (campaign_id, group_id, account_id, window_key),
            )
            claim_id = claim_cur.lastrowid

            await conn.execute("COMMIT")
            return True, "", claim_id, counter_id
        except Exception:
            try:
                await conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


async def claim_gap(
    campaign_id: int, group_id: int, account_id: int,
    window_key: str, min_gap: int
) -> tuple[bool, str, int | None]:
    """Claim a gap-type window. Returns (ok, reason, claim_id).

    Gap windows use max(created_at) to enforce minimum time between replies.
    """
    async with get_db() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            row = await (await conn.execute(
                "SELECT max(created_at) AS last FROM engagement_claims "
                "WHERE account_id=? AND window_key=?",
                (account_id, window_key),
            )).fetchone()

            now = _time.time()
            if row and row[0]:
                last_ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                last_ts = last_ts.replace(tzinfo=timezone.utc).timestamp()
                if now - last_ts < min_gap:
                    await conn.execute("COMMIT")
                    return False, "rate_limit", None

                # Past gap — delete old gap row, insert new one
                await conn.execute(
                    "DELETE FROM engagement_claims WHERE account_id=? AND window_key=?",
                    (account_id, window_key),
                )

            try:
                claim_cur = await conn.execute(
                    "INSERT INTO engagement_claims(campaign_id, group_id, account_id, window_key) "
                    "VALUES(?,?,?,?)",
                    (campaign_id, group_id, account_id, window_key),
                )
                claim_id = claim_cur.lastrowid
            except Exception:
                # UNIQUE constraint race
                await conn.execute("COMMIT")
                return False, "rate_limit", None

            await conn.execute("COMMIT")
            return True, "", claim_id
        except Exception:
            try:
                await conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


async def release_claims(claimed: list) -> None:
    """Release claims by ID (never by window_key). Only call before network send.

    claimed: list of (claim_id, counter_id, is_gap)
    """
    if not claimed:
        return
    async with get_db() as conn:
        for claim_id, counter_id, is_gap in claimed:
            if is_gap:
                if claim_id:
                    await conn.execute(
                        "DELETE FROM engagement_claims WHERE id=?", (claim_id,)
                    )
                continue
            # Limit type: delete claim record, decrement counter
            if claim_id:
                await conn.execute(
                    "DELETE FROM engagement_claims WHERE id=?", (claim_id,)
                )
            if counter_id:
                await conn.execute(
                    "UPDATE engagement_window_counters SET count=count-1, updated_at=datetime('now') "
                    "WHERE id=? AND count>0",
                    (counter_id,),
                )
        await conn.commit()


async def try_claim_all(campaign: dict, group_id: int, account_id: int) -> tuple[bool, str, list]:
    """Try to claim all 5 windows for a single reply.

    Returns (ok, skip_reason, claimed_list).
    If ok is False, all already-claimed slots are released.
    """
    claimed = []
    try:
        for wkey, kind, scope, lim, min_gap in build_windows(campaign, group_id, account_id):
            if kind == "gap":
                ok, reason, gap_id = await claim_gap(
                    campaign["id"], group_id, account_id, wkey, min_gap
                )
                if ok:
                    claimed.append((gap_id, None, True))
            else:
                ok, reason, claim_id, counter_id = await claim_limit(
                    campaign["id"], group_id, account_id, wkey, lim, scope
                )
                if ok:
                    claimed.append((claim_id, counter_id, False))
            if not ok:
                await release_claims(claimed)
                return False, reason, []
    except Exception as e:
        logger.error(f"[Claimer] Error during claim: {e}")
        await release_claims(claimed)
        return False, "claim_error", []
    return True, "", claimed
