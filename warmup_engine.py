"""
Warmup Engine — Posts AI-varied messages in Telegram groups on a schedule.
Uses multiple accounts to simulate organic group activity.
"""
import asyncio
import json
import random
import logging
from datetime import datetime

import database as db
import telegram_client as tg
from ai_remix import remix_message

logger = logging.getLogger("tg-scheduler.warmup")

_active_jobs: dict[int, bool] = {}  # job_id -> running flag


async def run_warmup_job(job_id: int):
    """Main loop for a warmup job. Posts messages at intervals within schedule window."""
    _active_jobs[job_id] = True

    try:
        job = await db.get_warmup_job(job_id)
        if not job:
            logger.error(f"[Warmup] Job {job_id} not found")
            return

        group = await db.get_warmup_group(job["group_id"])
        scripts = await db.get_warmup_scripts(job["group_id"])
        account_ids = json.loads(job.get("account_ids", "[]"))

        if not scripts:
            logger.warning(f"[Warmup] Job {job_id}: No scripts configured")
            await db.update_warmup_job_status(job_id, "stopped")
            return

        if not account_ids:
            logger.warning(f"[Warmup] Job {job_id}: No accounts assigned")
            await db.update_warmup_job_status(job_id, "stopped")
            return

        await db.update_warmup_job_status(job_id, "running")

        # Get AI settings
        ai_provider = await db.get_setting("ai_provider", "")
        ai_api_key = await db.get_setting("ai_api_key", "")
        # Parse API keys (comma-separated)
        ai_api_keys = [k.strip() for k in ai_api_key.split(",") if k.strip()] if ai_api_key else []

        chat_id = group["chat_id"]
        account_index = 0
        script_index = 0

        while _active_jobs.get(job_id, False):
            # Check schedule window
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            if current_time < job["schedule_start"] or current_time > job["schedule_end"]:
                await asyncio.sleep(60)  # Check every minute outside window
                continue

            # Check daily limit
            job = await db.get_warmup_job(job_id)  # Refresh
            if not job or job["status"] != "running":
                break
            if job["posts_today"] >= job["daily_post_limit"]:
                logger.info(f"[Warmup] Job {job_id}: Daily limit reached ({job['posts_today']})")
                await asyncio.sleep(300)  # Check every 5 min
                continue

            # Select account (round-robin)
            acc_id = account_ids[account_index % len(account_ids)]
            account_index += 1

            # Select script (round-robin)
            script = scripts[script_index % len(scripts)]
            script_index += 1

            # Get message content
            content = script["content"]

            # AI remix if enabled
            if script.get("use_ai_remix") and ai_provider and ai_api_keys:
                try:
                    content = await remix_message(content, ai_provider, ai_api_keys)
                except Exception as e:
                    logger.warning(f"[Warmup] AI remix failed: {e}, using original")

            # Send message
            try:
                await tg.send_text_message(acc_id, int(chat_id), content)

                await db.add_warmup_log(job_id, group["id"], acc_id, script["id"],
                                         content, "success")
                await db.update_warmup_job_status(
                    job_id, "running",
                    posts_today=job["posts_today"] + 1,
                    last_post_at=datetime.now().isoformat()
                )

                logger.info(f"[Warmup] Job {job_id}: Posted to {group['chat_title']} via account {acc_id}")

            except Exception as e:
                error_msg = str(e)[:200]
                logger.error(f"[Warmup] Job {job_id}: Send error: {error_msg}")
                await db.add_warmup_log(job_id, group["id"], acc_id, script["id"],
                                         content, "failed", error_msg)

            # Random delay
            delay = random.randint(job["interval_min"] * 60, job["interval_max"] * 60)
            logger.info(f"[Warmup] Job {job_id}: Next post in {delay}s")

            # Sleep in chunks so we can check stop flag
            for _ in range(delay):
                if not _active_jobs.get(job_id, False):
                    break
                await asyncio.sleep(1)

        await db.update_warmup_job_status(job_id, "stopped")
        logger.info(f"[Warmup] Job {job_id} stopped")

    except Exception as e:
        logger.error(f"[Warmup] Job {job_id} fatal error: {e}", exc_info=True)
        await db.update_warmup_job_status(job_id, "error")
    finally:
        _active_jobs.pop(job_id, None)


def stop_warmup_job(job_id: int):
    """Signal a running warmup job to stop."""
    _active_jobs[job_id] = False


def is_job_running(job_id: int) -> bool:
    """Check if a warmup job is currently running."""
    return _active_jobs.get(job_id, False)
