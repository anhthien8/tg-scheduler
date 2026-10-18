"""
engagement_engine.py — Community Engagement Engine (REACTIVE mode only).

Monitors target groups for keyword triggers, generates AI replies, and sends
them as group replies. Implements the full flow from design doc §2a:
  Events → Dedup → Filter → Claim → AI → Delay → Send → Log

This module handles handler registration, message processing, and lifecycle.
The rate-limit claim system is in claimer.py; busy registry in account_busy.py.
"""
import asyncio
import hashlib
import logging
import random
import re
import time as _time
from datetime import datetime, timezone
from difflib import SequenceMatcher

from telethon import errors as tg_errors
from telethon import events

import account_busy
import alerts
import claimer
import database as db
import telegram_client as tg

logger = logging.getLogger("tg-scheduler.engagement")

# ── In-memory state ───────────────────────────────────────────────────────────

# Message dedup: (chat_id, msg_id) → timestamp — reuses pattern from keyword_watcher
_seen_engagement_msgs: dict[tuple, float] = {}
_ENGAGEMENT_DEDUP_TTL = 600  # 10 min

# Recent replies per group for L3 similarity check — group_id → [reply_text, ...]
_recent_replies: dict[int, list[str]] = {}
_RECENT_REPLIES_MAX = 5

# Last reply time per account (RAM cache for nick selection — NOT source of truth)
_last_reply_at: dict[int, float] = {}

# Muted/kicked nicks per campaign — campaign_id → {account_id: mute_until}
_muted_nicks: dict[int, dict[int, float]] = {}

# Engagement send lock — serialize sends to prevent collisions
_engagement_send_lock: asyncio.Lock | None = None

# Handler removers — campaign_id → [(client, handler_fn)]
_engagement_handlers: dict[int, list] = {}

# Self user IDs cache
_self_user_ids: set[int] = set()
_self_ids_loaded_at: float = 0.0


def _get_engagement_send_lock():
    """Lazily create the engagement send lock."""
    global _engagement_send_lock
    if _engagement_send_lock is None:
        _engagement_send_lock = asyncio.Lock()
    return _engagement_send_lock


# ── Helper: self user IDs ─────────────────────────────────────────────────────

async def _get_self_user_ids() -> set:
    """Return cached set of our own Telegram user IDs."""
    global _self_ids_loaded_at
    now = _time.time()
    if _self_user_ids and now - _self_ids_loaded_at < 3600:
        return _self_user_ids
    try:
        db_ids = await db.get_all_internal_telegram_user_ids()
        _self_user_ids.update(int(x) for x in db_ids if x)
    except Exception:
        pass
    for me in getattr(tg, "_me_cache", {}).values():
        if me and me.get("user_id"):
            _self_user_ids.add(int(me["user_id"]))
    _self_ids_loaded_at = now
    return _self_user_ids


# ── Helper: admin cache ───────────────────────────────────────────────────────

_admin_cache: dict[int, tuple[set, float]] = {}  # group_id → (admin_ids, loaded_at)
_ADMIN_CACHE_TTL = 86400  # 24h


async def _get_group_admin_ids(client, group_id: int) -> set:
    """Get admin/bot IDs for a group (cached 24h)."""
    cached = _admin_cache.get(group_id)
    if cached and _time.time() - cached[1] < _ADMIN_CACHE_TTL:
        return cached[0]
    admin_ids = set()
    try:
        from telethon.tl.functions.channels import GetParticipantsRequest
        from telethon.tl.types import ChannelParticipantsAdmins
        result = await client(GetParticipantsRequest(
            channel=group_id, filter=ChannelParticipantsAdmins(),
            offset=0, limit=100, hash=0
        ))
        for u in result.users:
            admin_ids.add(u.id)
    except Exception as e:
        logger.debug(f"[Engagement] Could not get admins for {group_id}: {e}")
    _admin_cache[group_id] = (admin_ids, _time.time())
    return admin_ids


# ── L3: Similarity check ─────────────────────────────────────────────────────

def _is_too_similar(text: str, group_id: int, threshold: float = 0.65) -> bool:
    """Check if text is too similar to recent replies in the same group."""
    recent = _recent_replies.get(group_id, [])
    for prev in recent:
        ratio = SequenceMatcher(None, text.lower(), prev.lower()).ratio()
        if ratio >= threshold:
            return True
    return False


def _record_reply(text: str, group_id: int):
    """Record a reply for L3 similarity tracking."""
    if group_id not in _recent_replies:
        _recent_replies[group_id] = []
    _recent_replies[group_id].append(text)
    # Keep only the last N
    if len(_recent_replies[group_id]) > _RECENT_REPLIES_MAX:
        _recent_replies[group_id] = _recent_replies[group_id][-_RECENT_REPLIES_MAX:]


# ── Schedule check ────────────────────────────────────────────────────────────

def _is_within_schedule(campaign: dict) -> bool:
    """Check if current time (VN timezone) is within campaign schedule."""
    # Get current VN time (UTC+7)
    from datetime import timedelta
    now_utc = datetime.now(timezone.utc)
    vn_tz = timezone(timedelta(hours=7))
    now_vn = now_utc.astimezone(vn_tz)

    # Check day of week (1=Mon..7=Sun, isoweekday() returns same)
    active_days = campaign.get("active_days", [1, 2, 3, 4, 5, 6, 7])
    if now_vn.isoweekday() not in active_days:
        return False

    # Check time window
    start_str = campaign.get("schedule_start", "13:00")
    end_str = campaign.get("schedule_end", "23:00")
    try:
        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))
        current_minutes = now_vn.hour * 60 + now_vn.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes <= end_minutes
        else:
            # Overnight schedule (e.g., 22:00 - 06:00)
            return current_minutes >= start_minutes or current_minutes <= end_minutes
    except (ValueError, AttributeError):
        return True  # default allow if parsing fails


# ── Shill detection ───────────────────────────────────────────────────────────

_SHILL_PATTERNS = [
    r"cam kết", r"chắc chắn", r"x100", r"all[\-\s]?in",
    r"lợi nhuận.*%", r"ref(?:erral)?[\s\-]?(?:code|link|mã)",
    r"(?:binance|okx|bybit|gate\.io|mexc)\.com",
    r"(?:t\.me|telegram\.me)/\w+",
]
_shill_re = re.compile("|".join(_SHILL_PATTERNS), re.IGNORECASE)


def _contains_shill(text: str) -> bool:
    """Check if AI output contains shill/promotion content."""
    return bool(_shill_re.search(text))


# ── Auto-pause hook ──────────────────────────────────────────────────────────

async def _on_group_send_failure(campaign_id: int, account_id: int, kind: str):
    """Handle send failures — track muted/kicked nicks, auto-pause if too many."""
    if campaign_id not in _muted_nicks:
        _muted_nicks[campaign_id] = {}

    # Mark nick as muted/kicked for 24h
    _muted_nicks[campaign_id][account_id] = _time.time() + 86400

    # Check if enough nicks are muted to warrant auto-pause
    now = _time.time()
    active_mutes = {a: t for a, t in _muted_nicks.get(campaign_id, {}).items() if t > now}

    if len(active_mutes) >= 2:
        # Auto-pause campaign
        await db.update_engagement_campaign(campaign_id, {
            "status": "auto_paused",
            "pause_reason": f"mute_ban: {len(active_mutes)} nicks affected",
        })
        # Unregister handlers
        unregister_engagement_campaign(campaign_id)
        # Send alert
        try:
            await alerts.send_alert(
                "engagement",
                f"⚠️ Engagement campaign {campaign_id} auto-paused: "
                f"{len(active_mutes)} nicks muted/kicked ({kind})"
            )
        except Exception as e:
            logger.warning(f"[Engagement] Could not send alert: {e}")

        logger.warning(
            f"[Engagement] Campaign {campaign_id} auto-paused: "
            f"{len(active_mutes)} nicks muted/kicked"
        )


# ── Core: _send_group_message (§4.1.3) ───────────────────────────────────────

async def _send_group_message(
    event,
    text: str,
    account_id: int,
    reply_to: int | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Send a public message to a group as account_id. Returns {ok, msg_id, error, kind}.

    8 Acceptance Criteria from design doc §4.1.3:
    AC1: reply_to uses trigger_msg_id
    AC2: typing + delay (20-90s delay, typing simulation)
    AC3: PeerFlood handling
    AC4: mute/ban classification
    AC5: auto-pause hook (returns kind for caller)
    AC6: busy registry (account_busy.acquire)
    AC7: strip/guard output (shill detection)
    AC8: dry_run support
    """
    chat_id = event.chat_id

    # AC7: Guard output — check for shill content
    if _contains_shill(text):
        return {"ok": False, "msg_id": None, "error": "shill_detected", "kind": "shill"}

    # AC8: Dry run
    if dry_run:
        return {"ok": True, "msg_id": None, "error": None, "kind": "dry_run", "dry_run": True}

    # AC3: PeerFlood check (reuse keyword_watcher pattern)
    try:
        from keyword_watcher import _is_peerflood_blocked, _mark_peerflood
    except ImportError:
        _is_peerflood_blocked = lambda a: False
        _mark_peerflood = lambda a: None

    if _is_peerflood_blocked(account_id):
        return {"ok": False, "msg_id": None, "error": "peerflood_blocked", "kind": "peerflood"}

    client = tg.get_client(account_id)
    if not client:
        return {"ok": False, "msg_id": None, "error": "client_not_found", "kind": "offline"}

    if not await tg.ensure_connected(client, account_id):
        return {"ok": False, "msg_id": None, "error": "client_offline", "kind": "offline"}

    # AC6: Busy registry
    try:
        async with account_busy.acquire(account_id, "engagement", ttl_secs=90):
            # AC2: Delay 20-90s before replying
            delay = random.uniform(20, 90)
            await asyncio.sleep(delay)

            # Typing simulation: min(len(text)*0.012, 3.5)s
            typing_duration = min(len(text) * 0.012, 3.5)
            try:
                async with client.action(chat_id, "typing"):
                    await asyncio.sleep(typing_duration)
            except Exception:
                pass  # typing failure is non-fatal

            # Send message
            try:
                kwargs = {
                    "entity": chat_id,
                    "message": text,
                    "parse_mode": "html",
                }
                if reply_to:
                    kwargs["reply_to"] = reply_to

                result = await client.send_message(**kwargs)
                msg_id = result.id if result else None
                return {"ok": True, "msg_id": msg_id, "error": None, "kind": "reply"}

            except tg_errors.FloodWaitError as e:
                # AC3: FloodWait → mark peerflood
                _mark_peerflood(account_id)
                return {"ok": False, "msg_id": None, "error": str(e), "kind": "peerflood"}
            except (tg_errors.ChatWriteForbiddenError,
                    tg_errors.UserBannedInChannelError) as e:
                # AC4: mute classification
                return {"ok": False, "msg_id": None, "error": str(e), "kind": "muted"}
            except (tg_errors.UserNotParticipantError,
                    tg_errors.ChannelPrivateError) as e:
                # AC4: kicked classification
                return {"ok": False, "msg_id": None, "error": str(e), "kind": "kicked"}
            except Exception as e:
                return {"ok": False, "msg_id": None, "error": str(e), "kind": "error"}

    except account_busy.AccountBusy:
        # AC6: Account busy
        return {"ok": False, "msg_id": None, "error": "account_busy", "kind": "busy"}


# ── AI Reply Generation (§3.2) ───────────────────────────────────────────────

COMMUNITY_TA_PROMPT = """Bạn là trader phân tích kỹ thuật (TA) có kinh nghiệm, đang trả lời trong một nhóm
cộng đồng crypto công khai. Người đọc là trader bình thường, không phải khách hàng.

QUY TẮC BẮT BUỘC:
1. Trả lời ĐÚNG câu hỏi được nêu. Không mở bài dài dòng, không chào hỏi xã giao.
2. Đưa NHẬN ĐỊNH có thể kiểm chứng: vùng hỗ trợ/kháng cự cụ thể, khung thời gian
   (4H/1D), xu hướng, mốc invalidation. Nếu thiếu dữ liệu thì nói rõ "cần xem
   lại khung X" chứ KHÔNG bịa số.
3. TUYỆT ĐỐI KHÔNG: quảng cáo sàn/broker, xin link, mời vào nhóm, khoe lợi nhuận,
   hứa hẹn lợi nhuận, dùng từ "cam kết", "chắc chắn", "x100", "all-in".
4. KHÔNG nêu tên sàn, mã giới thiệu, link affiliate, hay link nhóm riêng trong
   câu trả lời trong nhóm công khai.
5. Luôn đóng bằng 1 câu rủi ro ngắn, khô: "Đây là góc nhìn kỹ thuật, không phải
   lời khuyên đầu tư."  (biến thể ngẫu nhiên, không lặp y hệt cùng câu chữ)
6. Trả lời NGẮN: tối đa 3-5 câu, không markdown nặng, không bullet dài.
7. Ngôn ngữ: khớp ngôn ngữ câu hỏi (Việt → Việt, Anh → Anh).
8. Nếu câu hỏi không liên quan crypto/TA (chào hỏi, drama, hỏi giá, hỏi admin):
   KHÔNG trả lời (trả về đúng token <SKIP>)."""


async def _generate_engagement_reply(
    campaign: dict,
    trigger_text: str,
    context_messages: list[str] | None = None,
) -> str | None:
    """Generate AI reply for engagement. Returns None if <SKIP> or error."""
    try:
        import ai_remix as ai_rmx
    except ImportError:
        logger.error("[Engagement] ai_remix not available")
        return None

    # Load AI agent config
    agent_id = campaign.get("ai_agent_id")
    if not agent_id:
        return None

    agent = await db.get_ai_agent(agent_id)
    if not agent:
        logger.warning(f"[Engagement] AI agent {agent_id} not found")
        return None

    # Build system prompt
    knowledge = agent.get("knowledge_base", "")
    knowledge_extra = campaign.get("knowledge_extra", "")
    system_prompt = COMMUNITY_TA_PROMPT
    if knowledge:
        system_prompt += f"\n\nKIẾN THỨC THAM KHẢO:\n{knowledge}"
    if knowledge_extra:
        system_prompt += f"\n\nKIẾN THỨC BỔ SUNG:\n{knowledge_extra}"

    # Build user message with context
    user_msg = f"Câu hỏi trong nhóm:\n{trigger_text}"
    if context_messages:
        context_text = "\n".join(f"- {m}" for m in context_messages[-5:])
        user_msg = f"5 tin gần nhất trong nhóm:\n{context_text}\n\nCâu hỏi cần trả lời:\n{trigger_text}"

    # Call AI
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        provider = agent.get("provider", "")
        api_keys = agent.get("api_keys_json", "[]")
        if isinstance(api_keys, str):
            import json
            api_keys = json.loads(api_keys)

        reply = await ai_rmx.generate_chat_response(messages, provider, api_keys)
        if not reply:
            return None

        # Parse <SKIP> token
        if "<SKIP>" in reply or "<skip>" in reply.lower():
            return None

        # Strip any markdown code blocks
        reply = reply.strip()
        if reply.startswith("```"):
            reply = re.sub(r"^```\w*\n?", "", reply)
            reply = re.sub(r"\n?```$", "", reply)

        # Final shill check
        if _contains_shill(reply):
            logger.warning("[Engagement] AI output contains shill content, retrying...")
            # One retry
            reply2 = await ai_rmx.generate_chat_response(messages, provider, api_keys)
            if not reply2 or "<SKIP>" in reply2 or _contains_shill(reply2):
                return None
            reply = reply2.strip()

        return reply

    except Exception as e:
        logger.error(f"[Engagement] AI generation failed: {e}")
        return None


# ── Keyword matching (reuse from keyword_watcher) ────────────────────────────

def _match_keywords(text: str, keywords: list[str]) -> str | None:
    """Check if text matches any keyword. Returns matched keyword or None."""
    if not text or not keywords:
        return None
    text_lower = text.lower()
    for kw in keywords:
        kw_stripped = kw.strip().lower()
        if not kw_stripped:
            continue
        # Regex match
        if kw_stripped.startswith("re:"):
            pattern = kw_stripped[3:].strip()
            try:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    return kw
            except re.error:
                pass
        # Plain substring
        elif kw_stripped in text_lower:
            return kw
    return None


# ── Core handler factory ─────────────────────────────────────────────────────

def _make_engagement_handler(campaign: dict):
    """Create a Telethon event handler closure for an engagement campaign."""
    campaign_id = campaign["id"]
    group_ids = campaign.get("group_ids", [])
    keywords = campaign.get("trigger_keywords", [])
    reply_account_ids = campaign.get("reply_account_ids", [])
    observer_account_ids = campaign.get("observer_account_ids", [])

    async def _process_engagement(event):
        """Process a new message event for engagement."""
        try:
            chat_id = event.chat_id
            msg_id = event.id

            # 1. Check if this chat is in our target groups
            if chat_id not in group_ids and abs(chat_id) not in group_ids:
                return

            # 2. Dedup (chat_id, msg_id) — atomic, before any await
            now = _time.time()
            key = (chat_id, msg_id)
            if key in _seen_engagement_msgs:
                return
            _seen_engagement_msgs[key] = now

            # Clean old dedup entries periodically
            if len(_seen_engagement_msgs) > 5000:
                cutoff = now - _ENGAGEMENT_DEDUP_TTL
                expired = [k for k, t in _seen_engagement_msgs.items() if t < cutoff]
                for k in expired:
                    _seen_engagement_msgs.pop(k, None)

            # 3. Get message text
            text = getattr(event, "raw_text", "") or getattr(event, "text", "") or ""
            if not text or len(text) < 3:
                return

            # 4. Check schedule
            # Reload campaign from DB for fresh data
            fresh_campaign = await db.get_engagement_campaign(campaign_id)
            if not fresh_campaign or fresh_campaign.get("status") != "active":
                return

            if not _is_within_schedule(fresh_campaign):
                return

            # 5. Get sender info
            sender = getattr(event, "sender", None)
            sender_id = getattr(event, "sender_id", None) or (sender.id if sender else None)
            if not sender_id:
                return

            # 6. L2: Filter out own accounts
            self_ids = await _get_self_user_ids()
            if sender_id in self_ids:
                return

            # 7. Filter bots
            sender_username = ""
            if sender:
                if getattr(sender, "bot", False):
                    return
                sender_username = getattr(sender, "username", "") or ""

            # 8. Filter admins
            try:
                observer_client = None
                for acc_id in (observer_account_ids or reply_account_ids):
                    c = tg.get_client(acc_id)
                    if c:
                        observer_client = c
                        break
                if observer_client:
                    admin_ids = await _get_group_admin_ids(observer_client, chat_id)
                    if sender_id in admin_ids:
                        return
            except Exception:
                pass

            # 9. Match keywords
            matched_kw = _match_keywords(text, keywords)
            if not matched_kw:
                return

            # 10. L3b: Don't reply if trigger is a reply to our own message
            if hasattr(event, "reply_to") and event.reply_to:
                try:
                    reply_msg_id = event.reply_to.reply_to_msg_id
                    if reply_msg_id:
                        # Check if the original message was from one of our accounts
                        async with db.get_db() as conn:
                            row = await (await conn.execute(
                                "SELECT 1 FROM engagement_logs WHERE reply_msg_id=? AND status='success' LIMIT 1",
                                (reply_msg_id,)
                            )).fetchone()
                            if row:
                                return  # Don't self-reply
                except Exception:
                    pass

            # 11. Choose 1 reply account (§4.3)
            candidates = []
            now = _time.time()
            for acc_id in reply_account_ids:
                if account_busy.is_busy(acc_id):
                    continue
                try:
                    from keyword_watcher import _is_peerflood_blocked
                    if _is_peerflood_blocked(acc_id):
                        continue
                except ImportError:
                    pass
                # Check if nick is muted for this campaign
                mute_until = _muted_nicks.get(campaign_id, {}).get(acc_id, 0)
                if mute_until > now:
                    continue
                # RAM-level gap check (not source of truth, just optimization)
                if now - _last_reply_at.get(acc_id, 0) < 120:
                    continue
                client = tg.get_client(acc_id)
                if client:
                    candidates.append(acc_id)

            if not candidates:
                await db.create_engagement_log({
                    "campaign_id": campaign_id,
                    "group_id": chat_id,
                    "trigger_user_id": sender_id,
                    "trigger_msg_id": msg_id,
                    "trigger_text": text[:500],
                    "kind": "reply",
                    "status": "skipped",
                    "skip_reason": "no_available_account",
                })
                return

            chosen_acc = random.choice(candidates)

            # 12. Claim all 5 rate-limit windows (§4.1.1)
            ok, skip_reason, claimed = await claimer.try_claim_all(
                fresh_campaign, chat_id, chosen_acc
            )
            if not ok:
                await db.create_engagement_log({
                    "campaign_id": campaign_id,
                    "group_id": chat_id,
                    "account_id": chosen_acc,
                    "trigger_user_id": sender_id,
                    "trigger_msg_id": msg_id,
                    "trigger_text": text[:500],
                    "kind": "reply",
                    "status": "skipped",
                    "skip_reason": skip_reason,
                })
                return

            # 13. Generate AI reply (§3.2)
            reply_text = await _generate_engagement_reply(
                fresh_campaign, text
            )

            if not reply_text:
                # Release claims since we won't send
                await claimer.release_claims(claimed)
                await db.create_engagement_log({
                    "campaign_id": campaign_id,
                    "group_id": chat_id,
                    "account_id": chosen_acc,
                    "trigger_user_id": sender_id,
                    "trigger_msg_id": msg_id,
                    "trigger_text": text[:500],
                    "kind": "reply",
                    "status": "skipped",
                    "skip_reason": "ai_skip",
                })
                return

            # 14. L3: Similarity check
            if _is_too_similar(reply_text, chat_id):
                # Try regenerate once
                reply_text2 = await _generate_engagement_reply(fresh_campaign, text)
                if not reply_text2 or _is_too_similar(reply_text2, chat_id):
                    await claimer.release_claims(claimed)
                    await db.create_engagement_log({
                        "campaign_id": campaign_id,
                        "group_id": chat_id,
                        "account_id": chosen_acc,
                        "trigger_user_id": sender_id,
                        "trigger_msg_id": msg_id,
                        "trigger_text": text[:500],
                        "kind": "reply",
                        "status": "skipped",
                        "skip_reason": "similarity",
                    })
                    return
                reply_text = reply_text2

            # 15. Send group message (§4.1.3) — claims already reserved
            start_ts = _time.time()
            async with _get_engagement_send_lock():
                result = await _send_group_message(
                    event, reply_text, chosen_acc,
                    reply_to=msg_id,
                )
            latency_ms = int((_time.time() - start_ts) * 1000)

            # 16. Log result
            if result["ok"]:
                _record_reply(reply_text, chat_id)
                _last_reply_at[chosen_acc] = _time.time()

                await db.create_engagement_log({
                    "campaign_id": campaign_id,
                    "group_id": chat_id,
                    "account_id": chosen_acc,
                    "trigger_user_id": sender_id,
                    "trigger_msg_id": msg_id,
                    "trigger_text": text[:500],
                    "reply_text": reply_text[:2000],
                    "reply_msg_id": result.get("msg_id"),
                    "kind": "reply",
                    "status": "success",
                    "latency_ms": latency_ms,
                })

                # Track for DM attribution (§5.2)
                try:
                    await db.create_engagement_dm_map(
                        campaign_id, chosen_acc, sender_id
                    )
                except Exception:
                    pass  # DM map is best-effort

                logger.info(
                    f"[Engagement] ✓ Campaign {campaign_id} replied in "
                    f"group {chat_id} via acc={chosen_acc} "
                    f"(trigger: {matched_kw!r}, latency={latency_ms}ms)"
                )
            else:
                kind = result.get("kind", "error")
                error = result.get("error", "unknown")

                # Don't release claims after network attempt (safety — keep ceiling)
                await db.create_engagement_log({
                    "campaign_id": campaign_id,
                    "group_id": chat_id,
                    "account_id": chosen_acc,
                    "trigger_user_id": sender_id,
                    "trigger_msg_id": msg_id,
                    "trigger_text": text[:500],
                    "reply_text": reply_text[:2000],
                    "kind": "reply",
                    "status": "failed",
                    "skip_reason": f"{kind}: {error}"[:200],
                    "latency_ms": latency_ms,
                })

                # AC5: Auto-pause hook
                if kind in ("muted", "kicked"):
                    await _on_group_send_failure(campaign_id, chosen_acc, kind)

                logger.warning(
                    f"[Engagement] ✗ Campaign {campaign_id} send failed "
                    f"in group {chat_id} via acc={chosen_acc}: {kind} - {error}"
                )

        except Exception as e:
            logger.error(f"[Engagement] Handler error for campaign {campaign_id}: {e}", exc_info=True)

    async def handler(event):
        """Top-level event handler — dispatches to _process_engagement as a background task."""
        try:
            asyncio.create_task(_process_engagement(event))
        except Exception as e:
            logger.error(f"[Engagement] Could not create task: {e}")

    return handler


# ── Registration ──────────────────────────────────────────────────────────────

def register_engagement_campaign(campaign: dict):
    """Register Telethon event handlers for an engagement campaign."""
    campaign_id = campaign["id"]
    observer_account_ids = campaign.get("observer_account_ids", [])
    reply_account_ids = campaign.get("reply_account_ids", [])
    group_ids = campaign.get("group_ids", [])

    # Use observer accounts if set, otherwise reply accounts observe too
    register_ids = list(dict.fromkeys(observer_account_ids or reply_account_ids))

    if not register_ids or not group_ids:
        logger.warning(f"[Engagement] Campaign {campaign_id}: no accounts or groups — skipping")
        return

    # Consent gate check
    if not campaign.get("operator_consent_confirmed"):
        logger.warning(f"[Engagement] Campaign {campaign_id}: consent not confirmed — skipping")
        return

    # Unregister old handlers first
    unregister_engagement_campaign(campaign_id)

    handler_fn = _make_engagement_handler(campaign)
    removers = []

    for acc_id in register_ids:
        client = tg.get_client(acc_id)
        if not client:
            logger.warning(f"[Engagement] Campaign {campaign_id}: acc={acc_id} not found, skipping")
            continue
        client.add_event_handler(handler_fn, events.NewMessage(incoming=True))
        removers.append((client, handler_fn))
        logger.info(
            f"[Engagement] Campaign {campaign_id}: registered on acc={acc_id} "
            f"— groups: {group_ids}, keywords: {len(campaign.get('trigger_keywords', []))}"
        )

    _engagement_handlers[campaign_id] = removers


def unregister_engagement_campaign(campaign_id: int):
    """Remove all event handlers for an engagement campaign."""
    if campaign_id not in _engagement_handlers:
        return
    for client, fn in _engagement_handlers[campaign_id]:
        try:
            client.remove_event_handler(fn)
        except Exception:
            pass
    del _engagement_handlers[campaign_id]
    logger.info(f"[Engagement] Campaign {campaign_id}: unregistered handlers")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def start_all_engagement_campaigns():
    """Load all active engagement campaigns from DB and register handlers.
    Call on startup after accounts are connected.
    """
    campaigns = await db.get_active_engagement_campaigns()
    registered = 0
    for c in campaigns:
        if c.get("status") == "active" and c.get("operator_consent_confirmed"):
            register_engagement_campaign(c)
            registered += 1
    logger.info(f"[Engagement] {registered} active campaign(s) registered")

    # Cleanup old claims (§4.1.2)
    try:
        cleaned = await db.cleanup_engagement_claims(max_age_days=2)
        if cleaned > 0:
            logger.info(f"[Engagement] Cleaned {cleaned} old claim/counter records")
    except Exception as e:
        logger.warning(f"[Engagement] Claim cleanup failed: {e}")


async def reload_engagement_campaign(campaign_id: int):
    """Re-register a single campaign (call after create/update/toggle)."""
    c = await db.get_engagement_campaign(campaign_id)
    if not c:
        unregister_engagement_campaign(campaign_id)
        return
    if c.get("status") == "active" and c.get("operator_consent_confirmed"):
        register_engagement_campaign(c)
    else:
        unregister_engagement_campaign(campaign_id)


def stop_all_engagement_campaigns():
    """Unregister all engagement campaign handlers."""
    for campaign_id in list(_engagement_handlers.keys()):
        unregister_engagement_campaign(campaign_id)
    logger.info("[Engagement] All campaigns stopped")
