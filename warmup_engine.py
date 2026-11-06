"""
Warmup Engine v2 — Diverse behavior-weighted warmup with graduation stages.

Stage 1 (Day 1-2): PASSIVE — read history, react emoji, view profiles
Stage 2 (Day 3-4): WARMING — add posting + channel joining
Stage 3 (Day 5-7): ACTIVE — full behavior mix + forwarding
Stage 4 (Day 8+):  READY — heavy posting, check graduation criteria
"""
import asyncio
import difflib
import json
import logging
import random
import re
import time
from collections import deque
from datetime import datetime, timezone

import database as db
import telegram_client as tg
from ai_remix import remix_message

logger = logging.getLogger("tg-scheduler.warmup")

_active_jobs: dict[int, bool] = {}  # job_id -> running flag
_tasks: dict[int, asyncio.Task] = {}  # job_id -> asyncio task (keeps a strong ref)
_job_state: dict[int, dict] = {}  # job_id -> {"note", "at", "next_at"} shown on the job card

# Passive mix used when a post is due but the group has no scripts yet.
PASSIVE_WEIGHTS = {"read_history": 50, "react_emoji": 30, "view_profile": 20}

BEHAVIOR_LABELS = {
    "read_history": "Đọc tin", "react_emoji": "Thả cảm xúc", "view_profile": "Xem profile",
    "post_message": "Đăng tin", "forward_message": "Lưu tin (forward)",
    "join_channel": "Đếm nhóm đã vào", "join_group": "Vào nhóm",
}


def _note(job_id: int, text: str, next_at: float | None = None) -> None:
    st = _job_state.setdefault(job_id, {})
    st["note"] = text
    st["at"] = time.time()
    st["next_at"] = next_at


def job_state(job_id: int) -> dict:
    """What the job is doing right now (in-memory, for the dashboard)."""
    return dict(_job_state.get(job_id) or {})


def in_window(now_hm: str, start: str, end: str) -> bool:
    """HH:MM window check; supports windows that cross midnight (22:00–02:00)."""
    start, end = (start or "00:00"), (end or "23:59")
    if start <= end:
        return start <= now_hm <= end
    return now_hm >= start or now_hm <= end


def start_job(job_id: int, initial_delay: float = 0) -> bool:
    """Run a job as a tracked background task. False if it is already running."""
    if is_job_running(job_id):
        return False
    _active_jobs[job_id] = True
    _note(job_id, "Đang khởi động…")
    _tasks[job_id] = asyncio.create_task(run_warmup_job(job_id, initial_delay=initial_delay))
    return True


async def resume_running_jobs(initial_delay: float = 90) -> int:
    """After a restart, continue jobs whose DB status is still 'running'.

    Jobs the user stopped have status 'stopped' and are left alone."""
    n = 0
    for j in await db.get_warmup_jobs():
        if j.get("status") == "running" and start_job(j["id"], initial_delay=initial_delay):
            n += 1
    return n


def _unsafe_reason(acc: dict | None) -> str | None:
    """Why an account must not do warmup actions right now (None = OK).

    Flagged / PeerFlood accounts that keep acting in groups get scored as
    spam harder, so warmup rests them instead of making things worse.
    """
    if not acc:
        return "not found"
    if acc.get("session_error"):
        return "session error"
    if acc.get("is_paused"):
        return "paused"
    if acc.get("is_flagged"):
        return "flagged"
    try:
        if float(acc.get("peerflood_until") or 0) > time.time():
            return "PeerFlood"
    except (TypeError, ValueError):
        pass
    return None

# ── Behavior Weights by Stage ────────────────────────────────────────

BEHAVIOR_WEIGHTS = {
    1: {  # Day 1-2: Passive only
        "read_history": 50,
        "react_emoji": 30,
        "view_profile": 20,
    },
    2: {  # Day 3-4: Start engaging
        "read_history": 30,
        "react_emoji": 25,
        "view_profile": 10,
        "post_message": 25,
        "join_channel": 10,
    },
    3: {  # Day 5-7: Active participation
        "read_history": 15,
        "react_emoji": 20,
        "post_message": 35,
        "join_channel": 10,
        "forward_message": 10,
        "view_profile": 10,
    },
    4: {  # Day 8+: Ready for graduation
        "post_message": 40,
        "react_emoji": 25,
        "read_history": 15,
        "forward_message": 10,
        "view_profile": 10,
    },
}

# Graduation criteria
GRAD_MIN_DAYS = 7
GRAD_MIN_MESSAGES = 10
GRAD_MIN_REACTIONS = 15
GRAD_MIN_CHANNELS = 3


# ── Stage Determination ──────────────────────────────────────────────

def determine_stage(started_at_str: str | None) -> int:
    """Determine warmup stage based on days since start."""
    if not started_at_str:
        return 1
    try:
        started = datetime.fromisoformat(str(started_at_str))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (now - started).total_seconds() / 86400
    except (ValueError, TypeError):
        return 1

    if days < 2:
        return 1
    if days < 4:
        return 2
    if days < 7:
        return 3
    return 4


def pick_behavior(stage: int) -> str:
    """Pick a weighted-random behavior for the given stage."""
    weights = BEHAVIOR_WEIGHTS.get(stage, BEHAVIOR_WEIGHTS[4])
    behaviors = list(weights.keys())
    probs = list(weights.values())
    return random.choices(behaviors, weights=probs, k=1)[0]


def should_graduate(progress: dict | None) -> bool:
    """Check if an account meets all graduation criteria."""
    if not progress:
        return False
    if progress.get("graduated_at"):
        return True  # already graduated

    started = progress.get("started_at")
    if not started:
        return False

    try:
        started_dt = datetime.fromisoformat(str(started))
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - started_dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return False

    return (
        days >= GRAD_MIN_DAYS
        and (progress.get("messages_sent", 0) or 0) >= GRAD_MIN_MESSAGES
        and (progress.get("reactions_sent", 0) or 0) >= GRAD_MIN_REACTIONS
        and (progress.get("channels_joined", 0) or 0) >= GRAD_MIN_CHANNELS
    )


# ── Behavior Implementations ────────────────────────────────────────

async def _warmup_read_history(acc_id: int, chat_id: int) -> bool:
    """Scroll through recent messages — mimics natural reading."""
    try:
        client = tg.get_client(acc_id)
        if not client:
            return False
        messages = await client.get_messages(chat_id, limit=random.randint(10, 30))
        if messages:
            await client.send_read_acknowledge(chat_id, messages[0])
        await asyncio.sleep(random.uniform(2, 8))  # "reading time"
        return True
    except Exception as e:
        logger.debug("[Warmup] read_history failed for acc %d: %s", acc_id, e)
        return False


async def _warmup_react_emoji(acc_id: int, chat_id: int) -> bool:
    """React to a recent message with random emoji."""
    try:
        client = tg.get_client(acc_id)
        if not client:
            return False
        messages = await client.get_messages(chat_id, limit=10)
        if not messages:
            return False
        msg = random.choice(messages)
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionEmoji
        # Groups often allow only some emojis — fall back to 👍 before giving up,
        # and report a real failure instead of pretending the reaction landed.
        for emoji in dict.fromkeys([random.choice(["👍", "🔥", "❤️", "😂", "🤔", "💯", "👀"]), "👍"]):
            try:
                await client(SendReactionRequest(
                    peer=chat_id,
                    msg_id=msg.id,
                    reaction=[ReactionEmoji(emoticon=emoji)],
                ))
                return True
            except Exception as e:
                last = e
        logger.debug("[Warmup] reaction refused for acc %d: %s", acc_id, last)
        return False
    except Exception as e:
        logger.debug("[Warmup] react_emoji failed for acc %d: %s", acc_id, e)
        return False


async def _warmup_view_profile(acc_id: int, chat_id: int) -> bool:
    """View random user profiles from recent messages."""
    try:
        client = tg.get_client(acc_id)
        if not client:
            return False
        messages = await client.get_messages(chat_id, limit=20)
        if not messages:
            return False
        senders = [m for m in messages if m.sender_id and m.sender_id > 0]
        if not senders:
            return False
        msg = random.choice(senders)
        await client.get_entity(msg.sender_id)
        await asyncio.sleep(random.uniform(1, 3))
        return True
    except Exception as e:
        logger.debug("[Warmup] view_profile failed for acc %d: %s", acc_id, e)
        return False


async def _warmup_forward_message(acc_id: int, chat_id: int, to_chat_id: int | None = None) -> bool:
    """Forward an interesting message to Saved Messages (safe)."""
    try:
        client = tg.get_client(acc_id)
        if not client:
            return False
        messages = await client.get_messages(chat_id, limit=5)
        if not messages:
            return False
        msg = random.choice(messages)
        # Forward to Saved Messages (me) — safest option
        await client.forward_messages("me", msg, chat_id)
        return True
    except Exception as e:
        logger.debug("[Warmup] forward_message failed for acc %d: %s", acc_id, e)
        return False


# ── Main Engine ──────────────────────────────────────────────────────

async def _sleep_while_active(job_id: int, seconds: float) -> None:
    """Sleep in 1s chunks so Stop takes effect quickly."""
    for _ in range(int(seconds)):
        if not _active_jobs.get(job_id, False):
            return
        await asyncio.sleep(1)


def _peer(group: dict):
    """Public @username resolves for every account; a bare -100… id only works
    for accounts that have already seen the group (Telethon entity cache)."""
    uname = (group.get("chat_username") or "").strip().lstrip("@")
    return uname or int(group["chat_id"])


async def _ensure_member(acc_id: int, peer) -> tuple[bool, str | None]:
    """Join the public group once if the account is not in it yet.

    Returns (ok, action): action "joined" when a join happened this call."""
    client = tg.get_client(acc_id)
    if not client:
        return False, "chưa kết nối"
    from telethon import errors as tl_errors
    from telethon.tl.functions.channels import GetParticipantRequest, JoinChannelRequest
    try:
        ent = await client.get_input_entity(peer)
    except Exception as e:
        return False, f"không tìm thấy nhóm: {str(e)[:150]}"
    try:
        await client(GetParticipantRequest(ent, "me"))
        return True, None
    except tl_errors.UserNotParticipantError:
        pass
    except Exception:
        return True, None  # can't check (e.g. basic group) — let the action try
    try:
        await client(JoinChannelRequest(ent))
        return True, "joined"
    except tl_errors.FloodWaitError as e:
        try:
            import alerts
            await alerts.flood_wait_guard(acc_id, e.seconds, "warmup-join")
        except Exception:
            pass
        return False, f"FloodWait {e.seconds}s khi vào nhóm"
    except Exception as e:
        return False, f"không vào được nhóm: {str(e)[:150]}"


# ── AI-written group messages (v3.21) ─────────────────────────────────────
# Instead of fixed scripts the AI reads what real people are saying in the group
# and writes one short, on-topic line. Our own accounts and bots never feed the
# context (hard rule: AI never talks to our own accounts), and anything with
# links / mentions / hashtags / near-duplicates is thrown away.

_recent_ai: dict[int, deque] = {}  # job_id -> texts the AI posted lately (no repeats)
_self_ids: set[int] = set()
_self_ids_at = 0.0
_AI_SKIP = re.compile(r"<\s*/?\s*skip\s*>", re.I)
_AI_BAD = re.compile(r"https?://|www\.|t\.me/|@\w|#\w|\bref(?:erral)?\s*(?:code|link)\b", re.I)
_AI_SIMILAR = 0.72  # SequenceMatcher ratio above which a text counts as a repeat
_QUOTES = "\"'`\u201c\u201d\u2018\u2019"

AI_WRITE_PROMPT = (
    'You are a regular, low-key member of the Telegram group "{title}". '
    "Write ONE short message (3 to 20 words) to post in the group right now, the way a real member types.\n"
    "Rules:\n"
    "- Use the main language of the recent messages. If there are none, write in English.\n"
    "- Fit what people are talking about, or the group's topic if it is quiet. Casual tone, "
    "lowercase is fine, at most one emoji.\n"
    "- Do not tag, quote or name anyone. No links, no @, no hashtags.\n"
    "- No promotion, referral codes, giveaways, other exchanges or projects, price predictions or financial advice.\n"
    "- Do not answer support questions and do not speak for the team or the admins.\n"
    "- Do not copy or closely paraphrase any message listed by the user.\n"
    "- If nothing natural fits, output exactly <SKIP>.\n"
    "Output only the message text, no quotes."
)


async def _own_user_ids() -> set[int]:
    """Telegram user ids of our own accounts (DB every 10 min + live me-cache)."""
    global _self_ids_at
    now = time.time()
    if now - _self_ids_at >= 600 and hasattr(db, "get_all_internal_telegram_user_ids"):
        _self_ids_at = now  # also on failure: no retry storm
        try:
            _self_ids.update(int(x) for x in await db.get_all_internal_telegram_user_ids() if x)
        except Exception as e:
            logger.warning("[Warmup] own ids load failed: %s", e)
    for me in (getattr(tg, "_me_cache", None) or {}).values():
        if me and me.get("user_id"):
            _self_ids.add(int(me["user_id"]))
    return _self_ids


async def _group_context(acc_id: int, peer, limit: int = 30, keep: int = 12) -> list[str]:
    """Recent texts from real people in the group, oldest first.

    Our accounts and bots are left out. If we cannot tell which senders are ours
    the context is empty (fail closed) and the AI writes from the group topic only.
    """
    get_client = getattr(tg, "get_client", None)
    client = get_client(acc_id) if get_client else None
    if not client:
        return []
    own = await _own_user_ids()
    if not own:
        return []
    out = []
    for m in await client.get_messages(peer, limit=limit) or []:
        text = (getattr(m, "message", None) or "").strip()
        if not text or getattr(m, "action", None) or getattr(m, "via_bot_id", None):
            continue
        sid = getattr(m, "sender_id", None)
        if sid is None or int(sid) in own:
            continue
        if getattr(getattr(m, "sender", None), "bot", False):
            continue
        out.append(" ".join(text.split())[:200])
    out.reverse()
    return out[-keep:]


def _clean_ai_text(raw: str | None) -> str:
    t = " ".join((raw or "").split()).strip().strip(_QUOTES).strip()
    try:
        from ai_remix import humanize_text
        t = humanize_text(t) or t
    except Exception:
        pass
    return t


def _check_ai_text(text: str, avoid: list[str]) -> str | None:
    """None when the text is fine to post, else the reason it was rejected."""
    if not text:
        return "AI không trả lời"
    if _AI_SKIP.search(text):
        return "AI thấy chưa có gì hợp để nói"
    if _AI_BAD.search(text):
        return "tin có link/@/hashtag nên bỏ"
    if len(text) > 220 or len(text.split()) > 32:
        return "tin quá dài nên bỏ"
    low = text.lower()
    for prev in avoid:
        if prev and difflib.SequenceMatcher(None, low, prev.lower()).ratio() > _AI_SIMILAR:
            return "tin gần giống tin cũ nên bỏ"
    return None


async def _ai_group_message(acc_id: int, peer, job_id: int, group: dict) -> tuple[str | None, str]:
    """(text, why): text is None when AI is not set up, declined, or the output was unsafe."""
    import ai_remix
    import message_queue
    cfg = await message_queue._remix_config({"id": f"warmup-{job_id}"})
    if not cfg:
        return None, "chưa cấu hình AI (Cài đặt → AI)"
    try:
        ctx = await _group_context(acc_id, peer)
    except Exception as e:
        logger.info("[Warmup] Job %d: could not read group context: %s", job_id, e)
        ctx = []
    recent = _recent_ai.setdefault(job_id, deque(maxlen=15))
    title = group.get("chat_title") or group.get("name") or "this group"
    lines = ["Recent messages in the group, oldest first:"]
    lines += [f"{i}. {t}" for i, t in enumerate(ctx, 1)] or ["(none, the group is quiet)"]
    if recent:
        lines += ["", "Messages you already posted recently (never repeat):"] + [f"- {t}" for t in recent]
    raw = await ai_remix._generate_chat_response_raw(
        [{"role": "user", "content": "\n".join(lines)}],
        AI_WRITE_PROMPT.format(title=title), cfg["provider"], cfg["keys"], **cfg["kwargs"])
    text = _clean_ai_text(raw)
    why = _check_ai_text(text, ctx + list(recent))
    if why:
        return None, why
    recent.append(text)
    return text, ""


async def run_warmup_job(job_id: int, initial_delay: float = 0):
    """Main loop for a warmup job — behavior-weighted per stage."""
    _active_jobs[job_id] = True

    try:
        if initial_delay:
            # Resumed after a restart: give accounts time to connect first.
            _note(job_id, "Chạy lại sau khi khởi động lại server — chờ account kết nối",
                  next_at=time.time() + initial_delay)
            await _sleep_while_active(job_id, initial_delay)

        job = await db.get_warmup_job(job_id)
        if not job:
            logger.error("[Warmup] Job %d not found", job_id)
            return

        group = await db.get_warmup_group(job["group_id"])
        scripts = await db.get_warmup_scripts(job["group_id"])
        account_ids = json.loads(job.get("account_ids", "[]"))

        if not account_ids:
            logger.warning("[Warmup] Job %d: No accounts assigned", job_id)
            _note(job_id, "Job chưa có account nào")
            await db.update_warmup_job_status(job_id, "stopped")
            return

        if not scripts:
            # Passive warmup (read / react / view profile) needs no scripts;
            # post turns fall back to a passive action until scripts are added.
            logger.info("[Warmup] Job %d: no scripts — passive actions only", job_id)

        await db.update_warmup_job_status(job_id, "running")

        # AI settings — same provider/key/model lookup as scheduled group posts
        ai_cfg = None
        if any(s.get("use_ai_remix") for s in scripts):
            import message_queue
            ai_cfg = await message_queue._remix_config({"id": f"warmup-{job_id}"})

        chat_id = _peer(group)
        account_index = 0
        script_index = 0
        consecutive_errors: dict[int, int] = {}  # acc_id -> error count
        members_ok: set[int] = set()  # accounts confirmed inside the group this run
        skips = 0  # consecutive accounts skipped (unsafe / offline / broken)
        has_v2_progress = hasattr(db, "get_warmup_progress")
        get_client = getattr(tg, "get_client", None)

        async def _skip(acc_id: int, why: str) -> None:
            nonlocal skips
            skips += 1
            logger.info("[Warmup] Job %d: skip account %d (%s)", job_id, acc_id, why)
            if skips >= len(account_ids):
                skips = 0
                _note(job_id, f"Không account nào dùng được lúc này (vd. #{acc_id}: {why}) — thử lại sau 15 phút",
                      next_at=time.time() + 900)
                await _sleep_while_active(job_id, 900)
            else:
                await asyncio.sleep(5)

        while _active_jobs.get(job_id, False):
            # Check schedule window
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            if not in_window(current_time, job["schedule_start"], job["schedule_end"]):
                _note(job_id, f"Ngoài khung giờ {job['schedule_start']}–{job['schedule_end']} — "
                              f"tự chạy lại lúc {job['schedule_start']}")
                await asyncio.sleep(60)
                continue

            # Refresh job status
            job = await db.get_warmup_job(job_id)
            if not job or job["status"] != "running":
                break
            # Accounts edited while running (1-click / PATCH) apply from the next turn.
            try:
                fresh = [int(a) for a in json.loads(job.get("account_ids") or "[]")]
            except (TypeError, ValueError):
                fresh = account_ids
            if fresh and fresh != account_ids:
                account_ids = fresh

            # New day → reset the post counter (it used to grow forever).
            last = str(job.get("last_post_at") or "")
            if job.get("posts_today") and last and last[:10] != now.strftime("%Y-%m-%d"):
                await db.update_warmup_job_status(job_id, "running", posts_today=0)
                job["posts_today"] = 0

            if job["posts_today"] >= job["daily_post_limit"]:
                logger.info("[Warmup] Job %d: Daily limit reached (%d)", job_id, job["posts_today"])
                _note(job_id, f"Đã đủ {job['daily_post_limit']} tin hôm nay — chờ sang ngày")
                await asyncio.sleep(300)
                continue

            # Select account (round-robin)
            acc_id = account_ids[account_index % len(account_ids)]
            account_index += 1

            # One DB read per turn, shared by the active-hours and safety checks.
            _acc_data = await db.get_account(acc_id) if hasattr(db, "get_account") else None

            # C3: Skip accounts outside active hours
            if _acc_data is not None and hasattr(db, "check_account_active_hours"):
                if not db.check_account_active_hours(_acc_data):
                    await _skip(acc_id, "ngoài giờ hoạt động của account")
                    continue

            # Rest flagged / PeerFlood / paused accounts; nap if all of them are.
            if hasattr(db, "get_account"):
                _why = _unsafe_reason(_acc_data)
                if _why:
                    await _skip(acc_id, _why)
                    continue

            if get_client and not get_client(acc_id):
                await _skip(acc_id, "chưa kết nối")
                continue

            # Skip accounts with too many consecutive errors
            if consecutive_errors.get(acc_id, 0) >= 5:
                await _skip(acc_id, "lỗi 5 lần liên tiếp")
                continue

            # First turn for this account: make sure it is inside the group.
            if get_client and acc_id not in members_ok:
                ok, what = await _ensure_member(acc_id, chat_id)
                if not ok:
                    consecutive_errors[acc_id] = consecutive_errors.get(acc_id, 0) + 1
                    _note(job_id, f"Acc #{acc_id}: {what}")
                    if hasattr(db, "add_warmup_action_log"):
                        try:
                            await db.add_warmup_action_log(
                                acc_id, "join_group", target_chat_id=int(group["chat_id"]),
                                status="flood" if "flood" in (what or "").lower() else "error",
                                error_detail=what)
                        except Exception:
                            pass
                    await asyncio.sleep(5)
                    continue
                members_ok.add(acc_id)
                if what == "joined":
                    skips = 0
                    # Joining is this account's action for the turn.
                    if hasattr(db, "add_warmup_action_log"):
                        try:
                            await db.add_warmup_action_log(
                                acc_id, "join_group", target_chat_id=int(group["chat_id"]), status="success")
                        except Exception:
                            pass
                    delay = random.randint(job["interval_min"] * 60, job["interval_max"] * 60)
                    _note(job_id, f"Acc #{acc_id} vừa vào nhóm", next_at=time.time() + delay)
                    await _sleep_while_active(job_id, delay)
                    continue

            if has_v2_progress:
                # Get or create warmup progress
                progress = await db.get_warmup_progress(acc_id)
                if not progress:
                    await db.create_or_update_warmup_progress(
                        acc_id, warmup_job_id=job_id, stage=1
                    )
                    progress = await db.get_warmup_progress(acc_id)

                # Check if already graduated
                if progress and progress.get("graduated_at"):
                    await _skip(acc_id, "đã tốt nghiệp warmup")
                    continue

                # Determine stage from progress
                stage = determine_stage(progress.get("started_at") if progress else None)

                # Update stage in DB if changed
                if progress and progress.get("stage") != stage:
                    await db.create_or_update_warmup_progress(acc_id, stage=stage)

                # Pick weighted random behavior
                behavior = pick_behavior(stage)
            else:
                progress = None
                stage = 4
                behavior = "post_message"

            # AI writes the message from the live group context (falls back to
            # scripts, then to a passive action, when it declines or is not set up).
            ai_text, ai_why = None, ""
            if behavior == "post_message" and job.get("ai_write") and get_client:
                try:
                    ai_text, ai_why = await _ai_group_message(acc_id, chat_id, job_id, group)
                except Exception as e:
                    ai_text, ai_why = None, f"AI lỗi: {str(e)[:80]}"
                if not ai_text:
                    logger.info("[Warmup] Job %d: AI message skipped (%s)", job_id, ai_why)
            if behavior == "post_message" and not ai_text and not scripts:
                behavior = random.choices(list(PASSIVE_WEIGHTS), weights=list(PASSIVE_WEIGHTS.values()))[0]
            skips = 0

            # Execute behavior
            success = False
            error_detail = None
            try:
                # Try to acquire account_busy lock
                try:
                    import account_busy
                    async with account_busy.acquire(acc_id, "warmup", ttl_secs=60):
                        success, error_detail = await _execute_behavior(
                            acc_id, behavior, chat_id, scripts, script_index,
                            ai_cfg, job, job_id, group, ai_text=ai_text
                        )
                except Exception as busy_err:
                    if "AccountBusy" in str(type(busy_err).__name__):
                        logger.debug("[Warmup] Account %d busy, skip", acc_id)
                        await asyncio.sleep(30)
                        continue
                    raise
            except Exception as e:
                error_detail = str(e)[:200]
                logger.error("[Warmup] Job %d: behavior '%s' error: %s", job_id, behavior, error_detail)

            # Log action
            status_str = "success" if success else ("flood" if "flood" in (error_detail or "").lower() else "error")
            if hasattr(db, "add_warmup_action_log"):
                try:
                    await db.add_warmup_action_log(
                        acc_id, behavior, target_chat_id=int(group["chat_id"]),
                        status=status_str, error_detail=error_detail
                    )
                except Exception:
                    pass

            # Update progress counters
            if success:
                consecutive_errors[acc_id] = 0
                counter_map = {
                    "post_message": "messages_sent",
                    "react_emoji": "reactions_sent",
                    "read_history": "history_reads",
                    "view_profile": "profiles_viewed",
                    "forward_message": "forwards_sent",
                }
                counter = counter_map.get(behavior)
                if counter and hasattr(db, "increment_warmup_counter"):
                    try:
                        await db.increment_warmup_counter(acc_id, counter)
                    except Exception:
                        pass

                # Refresh progress and check graduation
                if has_v2_progress:
                    progress = await db.get_warmup_progress(acc_id)
                    if progress and should_graduate(progress):
                        await db.create_or_update_warmup_progress(
                            acc_id, graduated_at=datetime.now(timezone.utc).isoformat()
                        )
                        logger.info("[Warmup] 🎓 Account %d graduated from warmup!", acc_id)
            else:
                consecutive_errors[acc_id] = consecutive_errors.get(acc_id, 0) + 1
                if has_v2_progress and "flood" in (error_detail or "").lower():
                    await db.create_or_update_warmup_progress(
                        acc_id, last_flood_at=datetime.now(timezone.utc).isoformat()
                    )

            if behavior == "post_message" and not ai_text:
                script_index += 1

            # Random delay
            delay = random.randint(job["interval_min"] * 60, job["interval_max"] * 60)
            logger.info("[Warmup] Job %d: Next action in %ds (stage %d)", job_id, delay, stage)
            label = BEHAVIOR_LABELS.get(behavior, behavior)
            if ai_text:
                label = "✨ AI đăng tin"
            elif ai_why:
                label += f" (AI: {ai_why})"
            _note(job_id, f"{label} · acc #{acc_id} · " + ("OK" if success else f"lỗi: {(error_detail or '')[:80]}"),
                  next_at=time.time() + delay)

            # Sleep in chunks so we can check stop flag
            await _sleep_while_active(job_id, delay)

        await db.update_warmup_job_status(job_id, "stopped")
        logger.info("[Warmup] Job %d stopped", job_id)
        _note(job_id, "Đã dừng")

    except asyncio.CancelledError:
        raise  # server shutdown: keep DB status 'running' so the job resumes on restart
    except Exception as e:
        logger.error("[Warmup] Job %d fatal error: %s", job_id, e, exc_info=True)
        _note(job_id, f"Lỗi: {str(e)[:150]}")
        await db.update_warmup_job_status(job_id, "error")
    finally:
        _active_jobs.pop(job_id, None)
        _tasks.pop(job_id, None)


async def _execute_behavior(
    acc_id: int, behavior: str, chat_id, scripts, script_index: int,
    ai_cfg: dict | None, job: dict, job_id: int, group: dict, ai_text: str | None = None
) -> tuple[bool, str | None]:
    """Execute a single warmup behavior. Returns (success, error_detail)."""
    try:
        if behavior == "read_history":
            ok = await _warmup_read_history(acc_id, chat_id)
            return ok, None if ok else "read failed"

        elif behavior == "react_emoji":
            ok = await _warmup_react_emoji(acc_id, chat_id)
            return ok, None if ok else "react failed"

        elif behavior == "view_profile":
            ok = await _warmup_view_profile(acc_id, chat_id)
            return ok, None if ok else "view failed"

        elif behavior == "forward_message":
            ok = await _warmup_forward_message(acc_id, chat_id)
            return ok, None if ok else "forward failed"

        elif behavior == "post_message":
            if ai_text:
                script, content = {"id": None}, ai_text
            elif not scripts:
                return False, "no scripts configured"
            else:
                script = scripts[script_index % len(scripts)]
                content = script["content"]

            # AI remix if enabled (scripts only: AI-written text is already unique)
            if not ai_text and script.get("use_ai_remix") and ai_cfg:
                try:
                    content = await remix_message(
                        content, ai_cfg["provider"], ai_cfg["keys"],
                        custom_instruction=ai_cfg["instruction"], **ai_cfg["kwargs"]) or content
                except Exception as e:
                    logger.warning("[Warmup] AI remix failed: %s, using original", e)

            try:
                await tg.send_text_message(acc_id, chat_id, content)
                await db.add_warmup_log(
                    job_id, group["id"], acc_id, script["id"], content, "success"
                )
                await db.update_warmup_job_status(
                    job_id, "running",
                    posts_today=job["posts_today"] + 1,
                    last_post_at=datetime.now().isoformat(),
                )
                logger.info("[Warmup] Job %d: Posted to %s via account %d", job_id, group["chat_title"], acc_id)
                return True, None
            except Exception as e:
                error_msg = str(e)[:200]
                logger.error("[Warmup] Job %d: Send error: %s", job_id, error_msg)
                await db.add_warmup_log(
                    job_id, group["id"], acc_id, script["id"], content, "failed", error_msg
                )
                return False, error_msg

        elif behavior == "join_channel":
            # No auto-joining: record how many groups/channels the account is
            # really a member of, so the graduation check uses true numbers.
            try:
                client = tg.get_client(acc_id)
                if not client:
                    return False, "no client"
                count = 0
                async for d in client.iter_dialogs(limit=300):
                    if d.is_group or d.is_channel:
                        count += 1
                await db.create_or_update_warmup_progress(acc_id, channels_joined=count)
                return True, None
            except Exception as e:
                return False, str(e)[:200]

        else:
            return False, f"unknown behavior: {behavior}"

    except Exception as e:
        error_str = str(e)[:200]
        logger.error("[Warmup] behavior '%s' error for acc %d: %s", behavior, acc_id, error_str)
        return False, error_str


def stop_warmup_job(job_id: int):
    """Signal a running warmup job to stop."""
    _active_jobs[job_id] = False


def is_job_running(job_id: int) -> bool:
    """Check if a warmup job is currently running."""
    return _active_jobs.get(job_id, False)
