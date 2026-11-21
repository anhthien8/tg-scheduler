"""
pending_sends.py
────────────────
A hand-typed reply that Telegram refused because the nick is spam-limited
(PeerFlood) is kept and sent by itself once the nick is usable again.

* One pending message per chat. It is sent ONLY when @SpamBot says the nick is
  free, never by blind retries (a failed send while limited makes the limit worse).
* It is dropped, not sent, when the chat changed while it waited (the KOL wrote
  again, someone answered, the lead was ignored / moved to another nick /
  blacklisted) or after EXPIRE_DAYS. A dropped message stays visible in the chat
  window with the reason until the owner dismisses it.
* The row is claimed in the database before Telegram is called and is never sent
  from a stale copy: a reply cancelled, replaced or interrupted mid-send (restart)
  is not sent again by itself.
* No polling: a one-shot job fires right after SpamBot's "limited until" date, and
  the existing auto-resume sweep is the fallback (unknown date, restart).
"""
import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

import database as db
import telegram_client as tg

logger = logging.getLogger("pending_sends")

EXPIRE_DAYS = 7
MAX_ATTEMPTS = 3
SEND_GAP_SECS = (120, 300)      # between two pending messages of one nick
FLUSH_AFTER_LIMIT_SECS = 120    # one-shot job runs this long after SpamBot's date
RETRY_NO_DATE_SECS = 3600       # SpamBot gave no date: look again once, an hour later

# A claimed row reads as "dropped" with this reason until the send is confirmed, so a
# crash between Telegram accepting the message and the row being removed can never
# turn into a second delivery.
SENDING_NOTE = "đang gửi thì bị gián đoạn, có thể đã tới lead: kiểm tra Telegram trước khi gửi lại"

_ACCOUNT_ERRORS = {"FloodWaitError", "AuthKeyUnregisteredError", "AuthKeyDuplicatedError", "UserDeactivatedError",
                   "UserDeactivatedBanError", "SessionRevokedError", "UnauthorizedError"}

_running: set[int] = set()      # account ids being flushed right now


def is_peer_flood(exc: BaseException) -> bool:
    return type(exc).__name__ == "PeerFloodError"


def _is_account_error(exc: BaseException) -> bool:
    """The nick (not this chat) cannot send right now: wait, do not count it against the reply."""
    return (type(exc).__name__ in _ACCOUNT_ERRORS or isinstance(exc, (ConnectionError, OSError, asyncio.TimeoutError))
            or str(exc) == "Account not connected")


def chat_marker(chat: dict | None) -> str:
    """Fingerprint of the transcript: any new message from either side changes it."""
    hist = (chat or {}).get("history") or []
    last = hist[-1] if hist and isinstance(hist[-1], dict) else {}
    return f"{len(hist)}|{last.get('timestamp', '')}"


async def record_human_send(account_id: int, user_id: int, text: str) -> None:
    """Write a hand-typed message the app itself sent into the transcript and hold the AI.

    Telegram does not echo a client's own sends back to its handlers, so the outgoing
    interceptor never sees them. Best-effort: bookkeeping must never look like a failed send.
    """
    try:
        content = f"[Human Admin]: {text}"
        chat = await db.get_followup_chat(account_id, user_id)
        if not chat:
            return
        hist = chat.get("history") or []
        if hist and hist[-1].get("role") == "assistant" and hist[-1].get("content") == content:
            return  # already recorded
        await db.set_human_takeover(account_id, user_id)
        await db.append_followup_chat_message(account_id, user_id, "assistant", content)
        import dm_reply_tracker
        dm_reply_tracker.note_admin_message(account_id, user_id, text)  # portal login → dashboard guide
    except Exception as e:
        logger.warning("[PendingSend] Sent, but could not record it for user %d on account %d: %s", user_id, account_id, e)


async def account_blocked(account_id: int) -> bool:
    """The nick is auto-paused for PeerFlood: do not even try to send."""
    acc = await db.get_account(account_id)
    return bool(acc and acc.get("is_paused") and str(acc.get("pause_reason") or "").startswith("PeerFlood"))


async def _limit_until(account_id: int) -> int | None:
    """Unix time SpamBot says the limit lifts, or None (free / no date / cannot tell).

    Read-only: it must not lift the pause the flood guard has just set.
    """
    try:
        res = await tg.check_spam_status(account_id, max_age_hours=0.25, auto_unpause=False)
    except Exception as e:
        logger.debug("[PendingSend] SpamBot check failed for %s: %s", account_id, e)
        return None
    if res.get("status") != "limited":
        return None
    return tg.parse_spambot_until(res.get("message", ""))


def _schedule_flush(account_id: int, at: int | None) -> None:
    """One-shot flush of this nick at unix time `at` (+ a small margin)."""
    if not at:
        return
    try:
        from apscheduler.triggers.date import DateTrigger
        import scheduler as sch
        run_at = datetime.fromtimestamp(max(at, time.time()) + FLUSH_AFTER_LIMIT_SECS, tz=timezone.utc)
        sch.get_scheduler().add_job(
            flush, trigger=DateTrigger(run_date=run_at), args=[account_id],
            id=f"pending_send_flush_{account_id}", name=f"Send pending replies of account {account_id}",
            replace_existing=True, misfire_grace_time=3600)
    except Exception as e:  # the periodic sweep still sends it
        logger.debug("[PendingSend] Could not schedule flush for %s: %s", account_id, e)


async def queue(account_id: int, user_id: int, text: str) -> dict | None:
    """Keep `text` for this chat. Returns the API payload, or None when the chat is unknown."""
    chat = await db.get_followup_chat(account_id, user_id)
    if not chat:
        return None
    replaced = bool(await db.get_pending_send(account_id, user_id))
    await db.set_pending_send(account_id, user_id, text, chat_marker(chat))
    until = await _limit_until(account_id)
    _schedule_flush(account_id, until or int(time.time()) + RETRY_NO_DATE_SECS)
    logger.info("[PendingSend] Queued reply to user %d on account %d (limit until %s)", user_id, account_id, until)
    return {"ok": False, "queued": True, "until": until, "replaced": replaced}


async def view(account_id: int, user_id: int) -> dict | None:
    """What the chat window shows: the pending (or dropped) message of this chat."""
    row = await db.get_pending_send(account_id, user_id)
    if not row:
        return None
    out = {k: row.get(k) for k in ("text", "status", "reason", "created_at")}
    if row.get("status") == "waiting":
        try:
            cached = await tg.get_cached_spam_status(account_id, max_age_hours=24 * EXPIRE_DAYS)
            if cached and cached.get("status") == "limited":
                out["until"] = tg.parse_spambot_until(cached.get("message", ""))
        except Exception:
            pass
    return out


async def _drop(row: dict, reason: str) -> bool:
    """Mark the reply as not sent (only if it is still the waiting row we hold) and tell the owner."""
    if not await db.drop_pending_send(row["account_id"], row["user_id"], reason, row["text"]):
        return False  # cancelled or replaced meanwhile: nothing to report
    logger.info("[PendingSend] Dropped reply to user %d on account %d: %s", row["user_id"], row["account_id"], reason)
    try:
        import alerts
        await alerts.send_alert(
            f"pending_send_drop_{row['account_id']}_{row['user_id']}",
            f"✉️ Tin chờ gửi cho lead {row['user_id']} (account {row['account_id']}) KHÔNG được gửi: {reason}. "
            "Mở chat đó để xem lại nội dung.")
    except Exception:
        pass
    return True


def _expired(row: dict) -> bool:
    try:
        created = datetime.fromisoformat(str(row.get("created_at")).replace(" ", "T")).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - created >= timedelta(days=EXPIRE_DAYS)


async def _stale_reason(row: dict) -> str | None:
    chat = await db.get_followup_chat(row["account_id"], row["user_id"])
    if not chat:
        return "cuộc trò chuyện không còn"
    if chat.get("status") == "bot_ignored":
        return "lead đã bị bỏ qua hoặc đã chuyển sang nick khác"
    if await db.is_user_blacklisted(user_id=row["user_id"], username=chat.get("username")):
        return "lead đã vào blacklist"
    if chat_marker(chat) != (row.get("chat_marker") or ""):
        return "cuộc trò chuyện đã có tin mới trong lúc chờ"
    return None


async def _flush_account(account_id: int, rows: list[dict]) -> dict:
    out = {"sent": 0, "dropped": 0}
    live = []
    for row in rows:
        if _expired(row):
            out["dropped"] += await _drop(row, f"đã chờ quá {EXPIRE_DAYS} ngày, nick vẫn chưa mở khoá")
        else:
            live.append(row)
    if not live:
        return out
    client = tg.get_client(account_id)
    if not client or not client.is_connected():
        return out
    acc = await db.get_account(account_id)
    if not acc:
        return out
    if acc.get("is_paused") and not str(acc.get("pause_reason") or "").startswith("PeerFlood"):
        return out  # switched off by the owner: nothing goes out from this nick
    # Only @SpamBot saying "free" opens the gate (it also un-pauses a PeerFlood pause).
    spam = await tg.check_spam_status(account_id, max_age_hours=0.2)
    if spam.get("status") != "free":
        if spam.get("status") == "limited":
            _schedule_flush(account_id, tg.parse_spambot_until(spam.get("message", "")))
        return out
    for row in live:
        uid, text = row["user_id"], row["text"]
        if out["sent"]:
            await asyncio.sleep(random.uniform(*SEND_GAP_SECS))  # never a burst right after an unlock
        reason = await _stale_reason(row)                        # checked after the gap: the chat may have moved
        if reason:
            out["dropped"] += await _drop(row, reason)
            continue
        # Claim the row as stored right now. Fails when it was cancelled, replaced or
        # answered by hand while this flush was running: the copy we hold is never sent.
        if not await db.drop_pending_send(account_id, uid, SENDING_NOTE, text):
            continue
        try:
            await tg.send_text_message(account_id, uid, text, parse_mode=None)
        except Exception as e:
            nick_level = _is_account_error(e)
            attempts = await db.release_pending_send(account_id, uid, text, SENDING_NOTE, bump=not nick_level)
            logger.warning("[PendingSend] Send to user %d on account %d failed (attempt %d): %s", uid, account_id, attempts, e)
            if nick_level:
                secs = int(getattr(e, "seconds", 0) or 0)
                if secs:
                    _schedule_flush(account_id, int(time.time()) + secs)
                break  # the nick cannot send: the rest stays waiting, nothing is counted
            if attempts >= MAX_ATTEMPTS:
                out["dropped"] += await _drop(row, f"Telegram vẫn từ chối sau {attempts} lần thử ({e})")
            if is_peer_flood(e):
                try:
                    import alerts
                    await alerts.flood_guard(account_id)
                except Exception:
                    pass
                break  # still limited: leave the rest waiting
            continue
        await db.delete_pending_send(account_id, uid, text)
        out["sent"] += 1
        logger.info("[PendingSend] ✅ Sent the pending reply to user %d from account %d", uid, account_id)
        await record_human_send(account_id, uid, text)
    return out


async def flush(account_id: int | None = None) -> dict:
    """Send the waiting replies of nicks that are usable again. Cheap when nothing waits."""
    rows = await db.list_waiting_pending_sends(account_id)
    total = {"sent": 0, "dropped": 0}
    by_acc: dict[int, list[dict]] = {}
    for r in rows:
        by_acc.setdefault(r["account_id"], []).append(r)
    for aid, items in by_acc.items():
        if aid in _running:
            continue
        _running.add(aid)
        try:
            res = await _flush_account(aid, items)
            total["sent"] += res["sent"]
            total["dropped"] += res["dropped"]
        except Exception as e:
            logger.warning("[PendingSend] Flush failed for account %s: %s", aid, e)
        finally:
            _running.discard(aid)
    return total
