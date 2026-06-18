import json
import re
"""
Keyword Watcher Engine
Listens for new messages in configured groups, matches keywords,
and auto-DMs the sender via multi-account with fallback.
"""
import asyncio
import logging
import random
from telethon import events, errors as tg_errors

import database as db
import telegram_client as tg
import ai_remix as ai_rmx
from telegram_client import _get_entity_safe

logger = logging.getLogger("tg-scheduler.watcher")

def clean_id(telegram_id: int) -> int:
    s = str(abs(telegram_id))
    if len(s) > 10 and s.startswith("100"):
        return int(s[3:])
    return int(s)

def normalize_text(t: str) -> str:
    if not t:
        return ""
    # Remove zero-width spaces and formatting marks
    t = re.sub(r'[\u200b-\u200d\ufeff]', '', t)
    # Replace any sequence of whitespaces with a single space
    t = re.sub(r'\s+', ' ', t.lower())
    return t.strip()

async def _resolve_peer_for_client(client, user_id: int, username: str | None, event=None, input_peer=None):
    """
    Resolve a sendable peer for the given Telegram client.
    - For the PRIMARY account (that received the event): use pre-resolved input_peer directly.
    - For FALLBACK accounts: use InputPeerUser(id, access_hash) — only valid if access_hash > 0.
      If access_hash == 0 (min user), fallback accounts cannot DM; raises exception to skip them.
    """
    from telethon.tl.types import InputPeerUser, PeerUser

    # 1. PRIMARY account: use the pre-resolved input_peer (may be InputUserFromMessage)
    if input_peer is not None and event is not None and getattr(event, "client", None) == client:
        return input_peer

    # 2. FALLBACK accounts: use access_hash extracted during pre-resolve step
    if input_peer is not None:
        ah = getattr(input_peer, "access_hash", 0) or 0
        if ah > 0:
            return InputPeerUser(user_id=user_id, access_hash=ah)
        # access_hash == 0 means min user — fallback cannot resolve, skip
        if ah == 0:
            raise Exception(
                f"Min user {user_id}: access_hash=0, cannot DM from fallback account. "
                f"Ensure fallback accounts are also members of the same group."
            )

    # 3. Username resolution (if user has @username — works cross-account)
    uname = username or ""
    if uname and not uname.isdigit():
        try:
            return await client.get_entity(uname)
        except Exception:
            pass

    # 4. Session cache by numeric ID
    try:
        return await client.get_entity(user_id)
    except Exception:
        pass

    # 5. PeerUser last resort
    try:
        return await client.get_entity(PeerUser(user_id))
    except Exception:
        pass

    raise Exception(f"Cannot resolve Telegram entity for user_id={user_id}")

_handler_removers: dict[int, list] = {}
_background_tasks: set = set()

# ── PeerFlood & DM dedup cooldowns ────────────────────────────────────────────
import time as _time

# {account_id: timestamp_when_unblocked}  — PeerFlood blocks for 45 min
_peerflood_cooldown: dict = {}
PEERFLOOD_COOLDOWN_SECS = 45 * 60  # 45 minutes

# {user_id: timestamp_last_dm}  — prevent duplicate DMs to same user
_user_dm_sent: dict = {}
USER_DM_COOLDOWN_SECS = 24 * 60 * 60  # 24 hours

# {(chat_id, msg_id): timestamp}  — prevent 4-account handler race for SAME message
# Key expires after MSG_DEDUP_TTL_SECS seconds (messages older than this may re-fire)
_seen_msg_ids: dict[tuple, float] = {}
MSG_DEDUP_TTL_SECS = 60 * 10  # 10 minutes

# {user_id}  — users currently being processed (anti-race-condition lock)
_user_dm_in_progress: set = set()

# Global DM rate-limit: only 1 DM at a time, random 2-20 min delay between sends
import asyncio as _asyncio
_dm_global_lock = None   # initialized lazily (asyncio loop must be running)
_dm_last_send_time: float = 0.0
DM_DELAY_MIN_SECS = 2 * 60    # 2 minutes
DM_DELAY_MAX_SECS = 20 * 60   # 20 minutes


def _get_dm_lock():
    """Lazily create the global DM lock on the running event loop."""
    global _dm_global_lock
    if _dm_global_lock is None:
        _dm_global_lock = _asyncio.Lock()
    return _dm_global_lock


async def _restore_peerflood_from_db() -> None:
    """On startup, restore PeerFlood cooldowns from DB so they survive restarts."""
    try:
        accounts = await db.get_accounts_with_peerflood()
        now = _time.time()
        for acc_id, until in accounts:
            if until > now:
                _peerflood_cooldown[acc_id] = until
                rem = int((until - now) // 60)
                logger.info(f"[CoolDown] Restored PeerFlood block: acc {acc_id}, {rem} min remaining")
    except Exception as e:
        logger.debug(f"[CoolDown] Could not restore PeerFlood from DB: {e}")


def _is_peerflood_blocked(acc_id: int) -> bool:
    until = _peerflood_cooldown.get(acc_id)
    if until and _time.time() < until:
        remaining = int(until - _time.time()) // 60
        return True
    return False


def _mark_peerflood(acc_id: int):
    until = _time.time() + PEERFLOOD_COOLDOWN_SECS
    _peerflood_cooldown[acc_id] = until
    # Persist to DB asynchronously so it survives server restarts
    import asyncio
    async def _persist():
        try:
            await db.set_account_peerflood_until(acc_id, until)
        except Exception:
            pass
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_persist())
    except Exception:
        pass
    logger.warning(
        f"[CoolDown] Account {acc_id} marked PeerFlood-blocked for {PEERFLOOD_COOLDOWN_SECS//60} min (persisted)"
    )


def _already_dmed(user_id: int) -> bool:
    # Check if another task is currently processing this user (race-condition guard)
    if user_id in _user_dm_in_progress:
        return True
    last = _user_dm_sent.get(user_id)
    if last and _time.time() - last < USER_DM_COOLDOWN_SECS:
        return True
    return False


def _mark_dmed(user_id: int):
    _user_dm_sent[user_id] = _time.time()

# Admin cache: group_id -> (set of admin user_ids, timestamp)
_admin_cache: dict[int, tuple[set, float]] = {}
_ADMIN_CACHE_TTL = 86400  # seconds (refresh every 1 day)

MIN_DELAY = 2.0
MAX_DELAY = 5.0


# ── Sending ──────────────────────────────────────────────────────────────────

async def _send_dm_with_fallback(
    account_ids: list[int],
    user_id: int,
    messages: list[dict],
    watcher_id: int,
    username: str | None,
    group_id: int | None,
    group_title: str | None,
    matched_keyword: str,
    event=None,
    input_peer=None,
):
    """
    Try each account in order (fallback). Send all messages in sequence.
    Returns (success: bool, used_account_id: int | None, error: str | None)
    """
    import random as _random
    import time as _time_module

    # Acquire global DM lock — ensures only 1 DM is in-flight at a time
    dm_lock = _get_dm_lock()
    async with dm_lock:
        # Random delay between sends (anti-spam / anti-PeerFlood)
        global _dm_last_send_time
        elapsed = _time_module.time() - _dm_last_send_time
        if _dm_last_send_time > 0 and elapsed < DM_DELAY_MIN_SECS:
            delay = _random.uniform(DM_DELAY_MIN_SECS, DM_DELAY_MAX_SECS)
            logger.info(
                "[DM Queue] Waiting %.0fs before next DM (anti-spam delay)", delay
            )
            await _asyncio.sleep(delay)

        _dm_last_send_time = _time_module.time()

        # ── Original send logic starts here ──
        return await _do_send_dm_with_fallback(
            account_ids, user_id, messages, watcher_id,
            username, group_id, group_title, matched_keyword, event, input_peer
        )


def _translate_dm_error(raw: str) -> str:
    """Translate raw Telegram error text to a short human-readable Vietnamese string."""
    r = str(raw).lower()
    if "privacy" in r:
        return "User bật quyền riêng tư – không nhận DM."
    if "floodwait" in r or "flood_wait" in r:
        import re
        m = re.search(r'(\d+)', raw)
        secs = m.group(1) if m else "?"
        return f"FloodWait {secs}s – Telegram chặn tạm."
    if "peerflood" in r or "peer_flood" in r:
        return "PeerFlood – tài khoản gửi quá nhiều DM."
    if "premium" in r:
        return "User này yêu cầu tài khoản Telegram Premium mới có thể nhận tin."
    if "peerinvalid" in r or ("peer" in r and "invalid" in r):
        return "Peer không hợp lệ – user không tồn tại hoặc không thể gửi DM."
    if "deactivated" in r or "deleted" in r:
        return "Tài khoản user đã bị xóa hoặc vô hiệu hóa."
    if "cannot" in r and "send" in r:
        return "Không thể gửi DM cho user này."
    if "banned" in r or "restricted" in r:
        return "Tài khoản gửi DM bị hạn chế."
    if "cannot resolve" in r or "min user" in r or "access_hash=0" in r:
        return "Không thể xác định user (min user / chưa tương tác trước). Bỏ qua."
    if "is_premium" in r or "no such column" in r:
        return "Lỗi cột DB is_premium – đã tự sửa ở lần khởi động tiếp theo."
    return raw[:120] if len(raw) > 120 else raw


async def _do_send_dm_with_fallback(
    account_ids: list[int],
    user_id: int,
    messages: list[dict],
    watcher_id: int,
    username: str | None,
    group_id: int | None,
    group_title: str | None,
    matched_keyword: str,
    event=None,
    input_peer=None,
):
    """Internal: actual DM sending logic (called from _send_dm_with_fallback after lock/delay)."""
    import random as _random
    import time as _time_module
    # Load AI remix settings from DB (cached inside _send_dm_with_fallback scope)
    ai_provider = await db.get_setting("ai_provider", None)
    ai_enabled = ai_provider in ("gemini", "deepseek", "openai", "groq")
    ai_keys = []
    if ai_enabled:
        try:
            raw = await db.get_setting("ai_keys_" + ai_provider, "[]")
            ai_keys = json.loads(raw) if raw else []
        except Exception:
            ai_keys = []
        if not ai_keys:
            ai_enabled = False

    last_error = None

    # ── Pre-resolve: get real access_hash from the primary account ────────────
    # For "min" users (only visible in a group), InputUserFromMessage ONLY works
    # with the account that is a member of that group.
    # We try to get the REAL access_hash from the primary account first.
    # If access_hash > 0, all fallback accounts can use InputPeerUser(id, hash).
    # If access_hash == 0 (truly min user), only primary account can DM them.
    _resolved_input_peer = input_peer  # default
    _resolved_access_hash = 0

    if event is not None:
        primary_client = getattr(event, "client", None)
        if primary_client:
            from telethon.tl.functions.users import GetUsersRequest
            from telethon.tl.types import InputPeerUser as _IPU2

            # Strategy 1: GetUsersRequest with InputUserFromMessage (best for min users)
            if input_peer is not None:
                try:
                    _users = await primary_client(GetUsersRequest([input_peer]))
                    if _users:
                        _u = _users[0]
                        _ah = getattr(_u, "access_hash", 0) or 0
                        if _ah:
                            _resolved_input_peer = _IPU2(user_id=_u.id, access_hash=_ah)
                            _resolved_access_hash = _ah
                            logger.info(f"[Watcher {watcher_id}] Pre-resolve: access_hash={_ah} via GetUsersRequest")
                except Exception as _e1:
                    logger.debug(f"[Watcher {watcher_id}] Pre-resolve GetUsersRequest: {_e1}")

            # Strategy 2: get_entity by ID (uses session cache)
            if not _resolved_access_hash:
                try:
                    _entity = await primary_client.get_entity(user_id)
                    _ah = getattr(_entity, "access_hash", 0) or 0
                    if _ah:
                        _resolved_input_peer = _IPU2(user_id=user_id, access_hash=_ah)
                        _resolved_access_hash = _ah
                        logger.info(f"[Watcher {watcher_id}] Pre-resolve: access_hash={_ah} via get_entity")
                except Exception as _e2:
                    logger.debug(f"[Watcher {watcher_id}] Pre-resolve get_entity: {_e2}")

            # Strategy 3: get_input_sender
            if not _resolved_access_hash:
                try:
                    _inp_sender = await event.get_input_sender()
                    _ah = getattr(_inp_sender, "access_hash", 0) or 0
                    if _ah:
                        _resolved_input_peer = _IPU2(user_id=user_id, access_hash=_ah)
                        _resolved_access_hash = _ah
                        logger.info(f"[Watcher {watcher_id}] Pre-resolve: access_hash={_ah} via get_input_sender")
                except Exception as _e3:
                    logger.debug(f"[Watcher {watcher_id}] Pre-resolve get_input_sender: {_e3}")

    input_peer = _resolved_input_peer
    logger.info(
        f"[Watcher {watcher_id}] input_peer final: {type(input_peer).__name__}, "
        f"access_hash={_resolved_access_hash}"
    )

    # ── Early exit: unresolvable min user ──────────────────────────────────
    # If ALL pre-resolve strategies failed AND sender is a "min user" (access_hash=0),
    # we CANNOT DM this person from any account. Stop immediately, don't waste attempts.
    if _resolved_access_hash == 0 and event is not None:
        sender_obj = getattr(event, "sender", None)
        is_min = getattr(sender_obj, "min", False)
        if is_min:
            err_msg = (
                f"Min user {user_id}: access_hash=0 sau pre-resolve. "
                "Cần để tài khoản này trong cùng group mới DM được."
            )
            logger.warning(f"[Watcher {watcher_id}] {err_msg}")
            await db.add_watcher_dm_log(
                watcher_id, None, user_id, username,
                group_id, group_title, matched_keyword, "failed",
                _translate_dm_error(err_msg)
            )
            return False, None, _translate_dm_error(err_msg)

    for acc_id in account_ids:
        client = tg.get_client(acc_id)
        if not client:
            logger.warning(f"[Watcher {watcher_id}] Account {acc_id} client not found, trying next")
            continue
        if not client.is_connected():
            logger.warning(f"[Watcher {watcher_id}] Account {acc_id} disconnected, trying next")
            continue

        # Skip accounts that are in PeerFlood cooldown
        if _is_peerflood_blocked(acc_id):
            logger.warning(f"[Watcher {watcher_id}] Account {acc_id} in PeerFlood cooldown, skipping")
            continue

        # ── Daily DM limit check ─────────────────────────────────────────────
        limit_reached, dm_count, dm_limit = await db.is_account_dm_limit_reached(acc_id)
        if limit_reached:
            logger.warning(
                f"[Watcher {watcher_id}] Account {acc_id} reached daily DM limit "
                f"({dm_count}/{dm_limit}), skipping this account"
            )
            continue

        try:
            # Resolve the peer for this specific account client
            peer = await _resolve_peer_for_client(client, user_id, username, event, input_peer)

            # Send all messages sequentially
            for msg in sorted(messages, key=lambda m: m.get("msg_order", 0)):
                # AI remix text content if enabled
                msg_to_send = dict(msg)
                if ai_enabled and msg_to_send.get("msg_type") == "text" and msg_to_send.get("content"):
                    logger.info(f"[Watcher {watcher_id}] Requesting {ai_provider} AI remix...")
                    msg_to_send["content"] = await ai_rmx.remix_message(
                        original_text=msg_to_send["content"],
                        provider=ai_provider,
                        api_keys=ai_keys,
                        sender_name=username if username and not username.isdigit() else None
                    )
                elif ai_enabled and msg_to_send.get("content"):
                    # For media messages remix the caption too
                    logger.info(f"[Watcher {watcher_id}] Requesting {ai_provider} AI remix for caption...")
                    msg_to_send["content"] = await ai_rmx.remix_message(
                        original_text=msg_to_send["content"],
                        provider=ai_provider,
                        api_keys=ai_keys,
                        sender_name=username if username and not username.isdigit() else None
                    )
                await _send_one(client, peer, msg_to_send)
                await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

            logger.info(
                f"[Watcher {watcher_id}] \u2713 DM sent to {username or user_id} "
                f"via account {acc_id} (keyword: '{matched_keyword}')"
            )
            await db.add_watcher_dm_log(
                watcher_id, acc_id, user_id, username,
                group_id, group_title, matched_keyword, "success"
            )
            # Note: _mark_dmed(user_id) is called in process_msg after this returns
            return True, acc_id, None

        except tg_errors.UserPrivacyRestrictedError:
            # Privacy = USER-level block → auto-blacklist and stop immediately
            last_error = _translate_dm_error("UserPrivacyRestrictedError")
            logger.warning(
                f"[Watcher {watcher_id}] Account {acc_id} ✗ {user_id} has privacy on — "
                f"auto-blacklisting, will never DM again"
            )
            await db.add_watcher_dm_log(
                watcher_id, acc_id, user_id, username,
                group_id, group_title, matched_keyword, "failed", last_error
            )
            # Auto-blacklist: no account can DM privacy-restricted users
            await db.add_to_dm_blacklist(
                user_id, username, reason="privacy_restricted"
            )
            break  # Stop immediately — blacklisted forever

        except (tg_errors.UserDeactivatedError, tg_errors.UserDeactivatedBanError):
            # User account deleted/deactivated → USER-level, blacklist and stop
            last_error = "Tài khoản user đã bị xóa hoặc vô hiệu hóa."
            logger.warning(
                f"[Watcher {watcher_id}] Account {acc_id} ✗ user {user_id} is deactivated/deleted — "
                f"auto-blacklisting"
            )
            await db.add_watcher_dm_log(
                watcher_id, acc_id, user_id, username,
                group_id, group_title, matched_keyword, "failed", last_error
            )
            await db.add_to_dm_blacklist(user_id, username, reason="user_deactivated")
            break  # Stop immediately

        except tg_errors.PeerInvalidError:
            # PeerInvalidError = USER-level: peer ID/access_hash is invalid.
            # No other account can fix this — break immediately to save resources.
            last_error = _translate_dm_error("PeerInvalidError: peer invalid")
            logger.warning(
                f"[Watcher {watcher_id}] ✗ PeerInvalidError for {user_id} (user-level) — stopping all attempts"
            )
            await db.add_watcher_dm_log(
                watcher_id, acc_id, user_id, username,
                group_id, group_title, matched_keyword, "failed", last_error
            )
            break  # USER-level: no account can reach this peer

        except tg_errors.FloodWaitError as e:
            # FloodWait = ACCOUNT-level → try next account, don't penalise user
            last_error = _translate_dm_error(f"FloodWait {e.seconds}s")
            logger.warning(f"[Watcher {watcher_id}] Account {acc_id} ✗ FloodWait {e.seconds}s, trying next account")
            continue

        except tg_errors.PeerFloodError:
            # PeerFlood = ACCOUNT-level → mark account, try next, don't penalise user
            last_error = _translate_dm_error("PeerFlood too many DMs from this account")
            _mark_peerflood(acc_id)
            logger.warning(f"[Watcher {watcher_id}] Account {acc_id} ✗ PeerFlood, blocked for {PEERFLOOD_COOLDOWN_SECS//60} min, trying next")
            continue

        except ValueError as e:
            # ValueError from get_entity() = "Cannot find any entity corresponding to X"
            # This is USER-level: entity not in Telegram → no account can find them.
            # Break immediately to avoid wasting all account slots.
            err_str = str(e).lower()
            if "cannot find" in err_str or "could not find" in err_str or "entity" in err_str:
                last_error = f"Cannot resolve Telegram entity for user_id={user_id}"
                logger.warning(
                    f"[Watcher {watcher_id}] ✗ Entity not found for {user_id} (user-level) — stopping all attempts"
                )
                await db.add_watcher_dm_log(
                    watcher_id, acc_id, user_id, username,
                    group_id, group_title, matched_keyword, "failed", last_error
                )
                break  # USER-level: no account can resolve this user
            # Other ValueError → treat as generic
            last_error = _translate_dm_error(str(e))
            logger.warning(f"[Watcher {watcher_id}] Account {acc_id} ✗ {user_id}: ValueError {e}")
            await db.add_watcher_dm_log(
                watcher_id, acc_id, user_id, username,
                group_id, group_title, matched_keyword, "failed", last_error
            )
            _total_fails = await db.count_user_dm_failures(watcher_id, user_id, hours=24)
            if _total_fails >= 3:
                break
            continue

        except Exception as e:
            # Generic error — log individually and count toward daily failure limit
            last_error = _translate_dm_error(str(e))
            logger.warning(f"[Watcher {watcher_id}] Account {acc_id} ✗ {user_id}: {e}, trying next account")
            await db.add_watcher_dm_log(
                watcher_id, acc_id, user_id, username,
                group_id, group_title, matched_keyword, "failed", last_error
            )
            _total_fails = await db.count_user_dm_failures(watcher_id, user_id, hours=24)
            if _total_fails >= 3:
                logger.warning(
                    f"[Watcher {watcher_id}] User {user_id} hit {_total_fails} failures in 24h — stopping"
                )
                break
            continue

    # ── All accounts exhausted or broke early ────────────────────────────────
    # If only PeerFlood/FloodWait happened (no individual failures logged),
    # log a summary row so the user isn't immediately retried on next message.
    _logged_fails = await db.count_user_dm_failures(watcher_id, user_id, hours=1)
    if _logged_fails == 0 and last_error:
        await db.add_watcher_dm_log(
            watcher_id, None, user_id, username,
            group_id, group_title, matched_keyword, "failed",
            last_error or "All accounts rate-limited (PeerFlood/FloodWait)"
        )
    elif last_error:
        logger.error(f"[Watcher {watcher_id}] All accounts failed DM to {user_id}: {last_error}")

    return False, None, last_error


async def _send_one(client, peer, msg: dict):
    """Send a single message to a resolved peer."""
    msg_type = msg.get("msg_type", "text")
    content = msg.get("content", "") or ""
    media_path = msg.get("media_path")

    if msg_type == "text":
        await client.send_message(peer, content, parse_mode="html")
    elif msg_type in ("photo", "video", "document"):
        force_doc = msg_type == "document"
        stream = msg_type == "video"
        await client.send_file(
            peer, media_path, caption=content, parse_mode="html",
            force_document=force_doc, supports_streaming=stream
        )
    else:
        raise ValueError(f"Unknown msg_type: {msg_type}")


async def _send_group_reply(
    event,
    watcher_id: int,
    reply_text: str,
    account_ids: list,
    used_dm_acc_id: int | None,
    dedicated_acc_id: int | None,
):
    """
    Reply to the user's original group message with a short text (e.g. "Check my DM 😊").
    Uses a DIFFERENT account from the one that sent the DM to look more natural.
    Falls back through available accounts until one succeeds.
    """
    import random as _rnd, asyncio as _aio

    chat_id = event.chat_id
    msg_id  = event.id

    # Build ordered list: dedicated → accounts excluding DM sender → DM sender last
    candidates = []
    if dedicated_acc_id:
        candidates.append(dedicated_acc_id)
    for aid in account_ids:
        if aid != used_dm_acc_id and aid not in candidates:
            candidates.append(aid)
    if used_dm_acc_id and used_dm_acc_id not in candidates:
        candidates.append(used_dm_acc_id)

    # Small random delay so reply doesn't appear instantaneous
    await _aio.sleep(_rnd.uniform(1.5, 4.0))

    for acc_id in candidates:
        client = tg.get_client(acc_id)
        if not client or not client.is_connected():
            continue
        if _is_peerflood_blocked(acc_id):
            continue
        try:
            await client.send_message(
                entity=chat_id,
                message=reply_text,
                reply_to=msg_id,
                parse_mode="html",
            )
            logger.info(
                f"[Watcher {watcher_id}] ✓ Group reply sent via acc={acc_id} "
                f"(reply_to msg_id={msg_id})"
            )
            return
        except tg_errors.FloodWaitError as e:
            logger.warning(f"[Watcher {watcher_id}] Group reply acc={acc_id} FloodWait {e.seconds}s, trying next")
            continue
        except tg_errors.PeerFloodError:
            _mark_peerflood(acc_id)
            logger.warning(f"[Watcher {watcher_id}] Group reply acc={acc_id} PeerFlood, trying next")
            continue
        except Exception as e:
            logger.warning(f"[Watcher {watcher_id}] Group reply acc={acc_id} failed: {e}, trying next")
            continue

    logger.warning(f"[Watcher {watcher_id}] Group reply failed — all accounts exhausted")



# 
def _make_handler(watcher: dict):
    """Create a Telethon event handler closure for a watcher config."""
    watcher_id = watcher["id"]
    keywords = [normalize_text(kw) for kw in watcher.get("keywords", [])]
    group_ids = watcher.get("group_ids", [])
    account_ids = watcher.get("sender_account_ids", [])
    cooldown_hours = watcher.get("cooldown_hours", 24)
    dm_once = bool(watcher.get("dm_once", False))
    excluded = {u.lstrip("@").lower() for u in watcher.get("excluded_usernames", [])}
    messages = watcher.get("messages", [])
    reply_in_group     = bool(watcher.get("reply_in_group", False))
    group_reply_text   = (watcher.get("group_reply_text") or "").strip()
    group_reply_acc_id = watcher.get("group_reply_account_id")  # optional dedicated account

    async def process_msg(event, matched):
        logger.debug(f"[DEBUG Watcher {watcher_id}] Background task started! matched={matched}")
        user_id = None  # init early so finally block doesn't UnboundLocalError
        try:
            # Try cached sender first, then API call
            sender = event.sender
            logger.debug(f"[DEBUG Watcher {watcher_id}] event.sender cached: {sender}")
            if not sender:
                try:
                    sender = await event.get_sender()
                except Exception as e:
                    logger.warning(f"[Watcher {watcher_id}] Failed to fetch sender from Telegram API: {e}")
            
            if not sender:
                logger.warning(f"[Watcher {watcher_id}] Could not resolve sender, skipping.")
                return

            if getattr(sender, "bot", False):
                return
            if not hasattr(sender, "first_name"):  # It's a Channel, not a User
                return

            user_id = sender.id
            username = getattr(sender, "username", None) or str(user_id)

            # Exclusion list check (by username or numeric id)
            uname_lower = (getattr(sender, "username", None) or "").lower()
            if uname_lower and uname_lower in excluded:
                logger.info(f"[Watcher {watcher_id}] Skipped - @{uname_lower} is in exclusion list")
                return
            if str(user_id) in excluded:
                logger.info(f"[Watcher {watcher_id}] Skipped - user_id {user_id} is in exclusion list")
                return

            # Auto-exclude group admins/bots (fetch from Telegram, cached 1h)
            try:
                admin_ids = await _get_group_admin_ids(
                    event.client, clean_id(event.chat_id)
                )
                if user_id in admin_ids:
                    logger.info(
                        f"[Watcher {watcher_id}] Skipped - user_id {user_id} (@{uname_lower or 'N/A'}) "
                        f"is a group admin/bot"
                    )
                    return
            except Exception as _e:
                logger.warning(f"[Watcher {watcher_id}] Admin check failed: {_e}")

            # Re-fetch watcher from DB to get latest is_active status
            w = await db.get_watcher(watcher_id)
            if not w or not w["is_active"]:
                return

            # Feature #6: Check global DM blacklist
            if await db.is_user_blacklisted(user_id):
                logger.info(f"[Watcher {watcher_id}] User {user_id} blacklisted, skip DM")
                return

            # Cooldown / dm_once check
            skip = await db.was_user_dmed_recently(
                watcher_id, user_id, cooldown_hours, dm_once=dm_once
            )
            if skip:
                reason = "đã DM vĩnh viễn" if dm_once else "still in cooldown"
                logger.info(
                    f"[Watcher {watcher_id}] Skipped DM to {username} - {reason}"
                )
                return

            if not messages:
                logger.warning(f"[Watcher {watcher_id}] No messages configured, skipping")
                return

            # Resolve group title
            try:
                chat = await event.get_chat()
                group_title = getattr(chat, "title", str(event.chat_id))
            except Exception:
                group_title = str(event.chat_id)

            logger.info(
                f"[Watcher {watcher_id}] Keyword '{matched}' matched in {group_title} "
                f"  DM to @{username} (user_id={user_id})"
            )

            # Resolve the correct input peer for sending DMs.
            # Min users (seen only in a group, never DM'd before) need InputUserFromMessage
            # which gives Telegram the group context to resolve their full access_hash.
            from telethon.tl.types import InputUserFromMessage, InputPeerUser as _IPU
            input_peer = None

            if getattr(sender, "min", False):
                # Min user — must reference them via the group message they sent
                try:
                    input_chat = await event.get_input_chat()
                    input_peer = InputUserFromMessage(
                        peer=input_chat,
                        msg_id=event.id,
                        user_id=user_id,
                    )
                    logger.info(f"[Watcher {watcher_id}] Min user — built InputUserFromMessage: {input_peer}")
                except Exception as e:
                    logger.warning(f"[Watcher {watcher_id}] InputUserFromMessage failed: {e}, falling back to access_hash")

            if input_peer is None:
                # Non-min user or fallback: use access_hash directly
                _ah = getattr(sender, "access_hash", 0) or 0
                input_peer = _IPU(user_id=user_id, access_hash=_ah) if _ah else None

            if input_peer is None:
                try:
                    input_peer = await event.get_input_sender()
                except Exception:
                    pass

            logger.info(f"[Watcher {watcher_id}] input_peer resolved: {input_peer}")

            # ── Race-condition safe dedup ─────────────────────────────────────────
            # Step 1: in-memory check (sync, no await gap — fast race guard)
            if _already_dmed(user_id):
                logger.info(f"[Watcher {watcher_id}] Already DM'd/processing user {user_id} (in-mem), skipping")
                return
            # Step 2: IMMEDIATELY claim the slot (before any await)
            _user_dm_in_progress.add(user_id)
            # Step 3: DB check is done above (was_user_dmed_recently checks success+recent-attempt)
            # _mark_dmed is called AFTER the send (success OR fail) in process_msg below

            # Send DM with account fallback
            success, used_acc, err = await _send_dm_with_fallback(
                account_ids=account_ids,
                user_id=user_id,
                messages=messages,
                watcher_id=watcher_id,
                username=username,
                group_id=clean_id(event.chat_id),
                group_title=group_title,
                matched_keyword=matched,
                event=event,
                input_peer=input_peer,
            )
            # ── Always mark in-memory after attempt (success OR fail) ────────
            # This prevents the same user from being retried within the cooldown
            # window when the event fires repeatedly (e.g. high-traffic groups).
            # DB-level dedup (was_user_dmed_recently) handles cross-restart persistence.
            _mark_dmed(user_id)
            if not success:
                logger.info(
                    f"[Watcher {watcher_id}] All accounts failed DM to {username} "
                    f"(user_id={user_id}). Marked in-mem to prevent retry. Error: {err}"
                )

            # ── Group Reply (optional) ────────────────────────────────────────
            if success and reply_in_group and group_reply_text:
                asyncio.create_task(
                    _send_group_reply(
                        event=event,
                        watcher_id=watcher_id,
                        reply_text=group_reply_text,
                        account_ids=account_ids,
                        used_dm_acc_id=used_acc,
                        dedicated_acc_id=group_reply_acc_id,
                    )
                )
        except Exception as e:
            logger.exception(f"[Watcher {watcher_id}] Error in process_msg background task: {e}")
            # On unexpected exception: still mark to prevent retry spam
            if user_id is not None:
                _mark_dmed(user_id)
        finally:
            if user_id is not None:
                _user_dm_in_progress.discard(user_id)

    async def handler(event):
        import threading
        # Only handle messages from configured groups
        chat_id = event.chat_id
        if clean_id(chat_id) not in [clean_id(gid) for gid in group_ids]:
            return

        text = normalize_text(event.raw_text)
        if not text:
            return

        matched = next((kw for kw in keywords if kw.lower() in text), None)
        logger.debug(f"[DEBUG Watcher {watcher_id}] text={repr(text)}, keywords={keywords}, matched={repr(matched)}, Loop: {id(asyncio.get_running_loop())}, Thread: {threading.current_thread().name}")
        if not matched:
            return

        # ── Per-message dedup: block duplicate handlers from 4 accounts ────────
        msg_key = (clean_id(chat_id), event.id)
        now_ts = _time.time()
        # Purge expired entries first (keep dict small)
        expired = [k for k, ts in _seen_msg_ids.items() if now_ts - ts > MSG_DEDUP_TTL_SECS]
        for k in expired:
            _seen_msg_ids.pop(k, None)
        # Check & claim atomically (no await → truly race-safe in asyncio)
        if msg_key in _seen_msg_ids:
            logger.debug(f"[DEBUG Watcher {watcher_id}] msg_key {msg_key} already dispatched, skipping duplicate handler")
            return
        _seen_msg_ids[msg_key] = now_ts
        # ─────────────────────────────────────────────────────────────────────
        logger.debug(f"[DEBUG Watcher {watcher_id}] Spawning background task...")
        task = asyncio.create_task(process_msg(event, matched))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return handler


def _register_watcher(watcher: dict):
    """Register Telethon handlers for all sender accounts of a watcher."""
    watcher_id = watcher["id"]
    account_ids = watcher.get("sender_account_ids", [])
    group_ids = watcher.get("group_ids", [])

    if not group_ids or not account_ids:
        logger.warning(f"[Watcher {watcher_id}] No groups or accounts – skipping registration")
        return

    _unregister_watcher(watcher_id)  # clean up old handlers first

    removers = []
    handler_fn = _make_handler(watcher)

    # Register on ALL configured sender accounts so any active one can catch events
    # (events are per-client, so all accounts in the group will receive them)
    for acc_id in account_ids:
        client = tg.get_client(acc_id)
        if not client:
            logger.warning(f"[Watcher {watcher_id}] Account {acc_id} not found, skipping")
            continue
        client.add_event_handler(handler_fn, events.NewMessage(incoming=True))
        removers.append((client, handler_fn))
        logger.info(
            f"[Watcher {watcher_id}] Registered on account {acc_id} "
            f"– groups: {group_ids}"
        )

    _handler_removers[watcher_id] = removers


def _unregister_watcher(watcher_id: int):
    """Remove all event handlers for a watcher."""
    if watcher_id not in _handler_removers:
        return
    for client, fn in _handler_removers[watcher_id]:
        try:
            client.remove_event_handler(fn)
        except Exception:
            pass
    del _handler_removers[watcher_id]
    logger.info(f"[Watcher {watcher_id}] Unregistered handlers")


# ── Public API ────────────────────────────────────────────────────────────────

async def start_all_watchers():
    """Load all active watchers from DB and register handlers. Call on startup."""
    # Preload user DM history so dedup survives server restarts
    await _preload_dm_history()
    # Restore PeerFlood cooldowns from DB (survives server restarts)
    await _restore_peerflood_from_db()
    watchers = await db.get_active_watchers()
    for w in watchers:
        _register_watcher(w)
    logger.info(f"Keyword Watcher: {len(watchers)} active rule(s) registered")


async def _preload_dm_history():
    """Load recent successful DMs from DB into _user_dm_sent to persist across restarts."""
    try:
        import aiosqlite, os
        db_path = os.path.join(os.path.dirname(__file__), "data", "scheduler.db")
        cutoff_secs = USER_DM_COOLDOWN_SECS
        async with aiosqlite.connect(db_path) as conn:
            rows = await conn.execute_fetchall(
                """SELECT target_user_id, MAX(sent_at) as last_dm
                   FROM watcher_dm_logs
                   WHERE status = 'success'
                   AND sent_at >= datetime('now', '-' || ? || ' seconds')
                   GROUP BY target_user_id""",
                (str(cutoff_secs),)
            )
        loaded = 0
        for row in rows:
            uid_val = row[0]
            last_str = row[1] or ''
            try:
                from datetime import datetime, timezone
                # Parse datetime and convert to timestamp
                dt = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                _user_dm_sent[int(uid_val)] = dt.timestamp()
                loaded += 1
            except Exception:
                pass
        logger.info(f"[DM Dedup] Preloaded {loaded} users from DB history (24h window)")
    except Exception as e:
        logger.warning(f"[DM Dedup] Could not preload history: {e}")


async def reload_watcher(watcher_id: int):
    """Re-register a single watcher (call after create/update/toggle)."""
    w = await db.get_watcher(watcher_id)
    if not w:
        _unregister_watcher(watcher_id)
        return
    if w["is_active"]:
        _register_watcher(w)
    else:
        _unregister_watcher(watcher_id)


def remove_watcher(watcher_id: int):
    """Remove handlers when a watcher is deleted."""
    _unregister_watcher(watcher_id)


async def test_dm(watcher_id: int, target: str) -> dict:
    """
    Manually test a watcher rule by sending DM to a specific user.
    `target` can be a username (with or without @) or a numeric user_id string.
    Bypasses keyword detection and cooldown – logs with matched_keyword='[TEST]'.
    """
    w = await db.get_watcher(watcher_id)
    if not w:
        return {"success": False, "error": "Watcher not found"}

    account_ids = w.get("sender_account_ids", [])
    messages = w.get("messages", [])

    if not account_ids:
        return {"success": False, "error": "No sender accounts configured"}
    if not messages:
        return {"success": False, "error": "No DM messages configured"}

    # Resolve target to user_id (username or numeric id)
    target_clean = target.lstrip("@").strip()
    resolved_user_id = None
    resolved_username = None

    for acc_id in account_ids:
        client = tg.get_client(acc_id)
        if not client or not client.is_connected():
            continue
        try:
            # Try numeric id first
            lookup = int(target_clean) if target_clean.isdigit() else target_clean
            entity = await client.get_entity(lookup)
            resolved_user_id = entity.id
            resolved_username = getattr(entity, "username", None) or target_clean
            break
        except Exception as e:
            logger.warning(
                f"[Watcher {watcher_id}] test_dm: could not resolve '{target_clean}' "
                f"via account {acc_id}: {e}"
            )

    if not resolved_user_id:
        return {
            "success": False,
            "error": (
                f"Cannot resolve user '{target_clean}'. "
                "Use a @username or numeric user_id that the account has in its contact list."
            )
        }

    logger.info(f"[Watcher {watcher_id}] 🧪 TEST DM → @{resolved_username} (id={resolved_user_id})")

    ok, used_acc, err = await _send_dm_with_fallback(
        account_ids=account_ids,
        user_id=resolved_user_id,
        messages=messages,
        watcher_id=watcher_id,
        username=resolved_username,
        group_id=None,
        group_title=None,
        matched_keyword="[TEST]",
        event=None,
        input_peer=None,
    )

    if ok:
        return {"success": True, "message": f"DM sent to @{resolved_username} via account {used_acc}"}
    return {"success": False, "error": err or "All accounts failed"}
