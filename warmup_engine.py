"""
Warmup Engine v2 — Diverse behavior-weighted warmup with graduation stages.

Stage 1 (Day 1-2): PASSIVE — read history, react emoji, view profiles
Stage 2 (Day 3-4): WARMING — add posting + channel joining
Stage 3 (Day 5-7): ACTIVE — full behavior mix + forwarding
Stage 4 (Day 8+):  READY — heavy posting, check graduation criteria
"""
import asyncio
import json
import logging
import random
from datetime import datetime, timezone

import database as db
import telegram_client as tg
from ai_remix import remix_message

logger = logging.getLogger("tg-scheduler.warmup")

_active_jobs: dict[int, bool] = {}  # job_id -> running flag

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
        messages = await client.get_messages(int(chat_id), limit=random.randint(10, 30))
        if messages:
            await client.send_read_acknowledge(int(chat_id), messages[0])
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
        messages = await client.get_messages(int(chat_id), limit=10)
        if not messages:
            return False
        msg = random.choice(messages)
        emoji = random.choice(["👍", "🔥", "❤️", "😂", "🤔", "💯", "👀"])
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            await client(SendReactionRequest(
                peer=int(chat_id),
                msg_id=msg.id,
                reaction=[ReactionEmoji(emoticon=emoji)],
            ))
        except Exception:
            # Fallback — some chats/messages don't allow reactions
            pass
        return True
    except Exception as e:
        logger.debug("[Warmup] react_emoji failed for acc %d: %s", acc_id, e)
        return False


async def _warmup_view_profile(acc_id: int, chat_id: int) -> bool:
    """View random user profiles from recent messages."""
    try:
        client = tg.get_client(acc_id)
        if not client:
            return False
        messages = await client.get_messages(int(chat_id), limit=20)
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
        messages = await client.get_messages(int(chat_id), limit=5)
        if not messages:
            return False
        msg = random.choice(messages)
        # Forward to Saved Messages (me) — safest option
        await client.forward_messages("me", msg, int(chat_id))
        return True
    except Exception as e:
        logger.debug("[Warmup] forward_message failed for acc %d: %s", acc_id, e)
        return False


# ── Main Engine ──────────────────────────────────────────────────────

async def run_warmup_job(job_id: int):
    """Main loop for a warmup job — behavior-weighted per stage."""
    _active_jobs[job_id] = True

    try:
        job = await db.get_warmup_job(job_id)
        if not job:
            logger.error("[Warmup] Job %d not found", job_id)
            return

        group = await db.get_warmup_group(job["group_id"])
        scripts = await db.get_warmup_scripts(job["group_id"])
        account_ids = json.loads(job.get("account_ids", "[]"))

        if not scripts:
            logger.warning("[Warmup] Job %d: No scripts configured", job_id)
            await db.update_warmup_job_status(job_id, "stopped")
            return

        if not account_ids:
            logger.warning("[Warmup] Job %d: No accounts assigned", job_id)
            await db.update_warmup_job_status(job_id, "stopped")
            return

        await db.update_warmup_job_status(job_id, "running")

        # Get AI settings
        ai_provider = await db.get_setting("ai_provider", "")
        ai_api_key = await db.get_setting("ai_api_key", "")
        ai_api_keys = [k.strip() for k in ai_api_key.split(",") if k.strip()] if ai_api_key else []

        chat_id = group["chat_id"]
        account_index = 0
        script_index = 0
        consecutive_errors: dict[int, int] = {}  # acc_id -> error count
        has_v2_progress = hasattr(db, "get_warmup_progress")

        while _active_jobs.get(job_id, False):
            # Check schedule window
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            if current_time < job["schedule_start"] or current_time > job["schedule_end"]:
                await asyncio.sleep(60)
                continue

            # Refresh job status
            job = await db.get_warmup_job(job_id)
            if not job or job["status"] != "running":
                break
            if job["posts_today"] >= job["daily_post_limit"]:
                logger.info("[Warmup] Job %d: Daily limit reached (%d)", job_id, job["posts_today"])
                await asyncio.sleep(300)
                continue

            # Select account (round-robin)
            acc_id = account_ids[account_index % len(account_ids)]
            account_index += 1

            # C3: Skip accounts outside active hours
            if hasattr(db, "get_account") and hasattr(db, "check_account_active_hours"):
                _acc_data = await db.get_account(acc_id)
                if not db.check_account_active_hours(_acc_data):
                    logger.debug("[Warmup] Job %d: Account %d outside active hours", job_id, acc_id)
                    await asyncio.sleep(300)
                    continue

            # Skip accounts with too many consecutive errors
            if consecutive_errors.get(acc_id, 0) >= 5:
                logger.warning("[Warmup] Job %d: Account %d paused (5+ consecutive errors)", job_id, acc_id)
                await asyncio.sleep(300)
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
                    logger.info("[Warmup] Job %d: Account %d already graduated, skipping", job_id, acc_id)
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
                            ai_provider, ai_api_keys, job, job_id, group
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
                        acc_id, behavior, target_chat_id=int(chat_id),
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
                    "join_channel": "channels_joined",
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

            if behavior == "post_message":
                script_index += 1

            # Random delay
            delay = random.randint(job["interval_min"] * 60, job["interval_max"] * 60)
            logger.info("[Warmup] Job %d: Next action in %ds (stage %d)", job_id, delay, stage)

            # Sleep in chunks so we can check stop flag
            for _ in range(delay):
                if not _active_jobs.get(job_id, False):
                    break
                await asyncio.sleep(1)

        await db.update_warmup_job_status(job_id, "stopped")
        logger.info("[Warmup] Job %d stopped", job_id)

    except Exception as e:
        logger.error("[Warmup] Job %d fatal error: %s", job_id, e, exc_info=True)
        await db.update_warmup_job_status(job_id, "error")
    finally:
        _active_jobs.pop(job_id, None)


async def _execute_behavior(
    acc_id: int, behavior: str, chat_id, scripts, script_index: int,
    ai_provider: str, ai_api_keys: list, job: dict, job_id: int, group: dict
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
            if not scripts:
                return False, "no scripts configured"
            script = scripts[script_index % len(scripts)]
            content = script["content"]

            # AI remix if enabled
            if script.get("use_ai_remix") and ai_provider and ai_api_keys:
                try:
                    content = await remix_message(content, ai_provider, ai_api_keys)
                except Exception as e:
                    logger.warning("[Warmup] AI remix failed: %s, using original", e)

            try:
                await tg.send_text_message(acc_id, int(chat_id), content)
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
            # Placeholder — join from curated channel list (if configured)
            # For now, just view the group entity as a safe substitute
            try:
                client = tg.get_client(acc_id)
                if client:
                    await client.get_entity(int(chat_id))
                    return True, None
            except Exception as e:
                return False, str(e)[:200]
            return False, "no client"

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
