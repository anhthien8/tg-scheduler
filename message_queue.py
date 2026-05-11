"""
Message Queue - rate limiting, retry, multi-account aware.
"""
import asyncio
import random
import json
import logging
from telethon import errors as tg_errors

import database as db
import telegram_client as tg

logger = logging.getLogger("tg-scheduler")

_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None

MAX_RETRIES = 3
MIN_DELAY = 1.5
MAX_DELAY = 3.5


def get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def enqueue_schedule(schedule_id: int):
    """Load schedule and enqueue all message+target combos."""
    schedule = await db.get_schedule(schedule_id)
    if not schedule:
        logger.warning(f"Schedule {schedule_id} not found")
        return
    if not schedule["is_active"]:
        logger.info(f"Schedule {schedule_id} is inactive, skipping")
        return

    # Check max_sends limit before enqueueing
    max_sends = schedule.get("max_sends")
    current_sends = schedule.get("current_sends", 0)
    if max_sends and current_sends >= max_sends:
        logger.info(f"Schedule {schedule_id}: reached max sends ({current_sends}/{max_sends}), auto-deactivating")
        await db.toggle_schedule(schedule_id)
        import scheduler as sch
        sch.remove_schedule_job(schedule_id)
        return

    account_id = schedule.get("account_id", 1)
    messages = schedule.get("messages", [])
    targets = schedule.get("targets", [])

    if not messages or not targets:
        logger.warning(f"Schedule {schedule_id}: no messages or targets")
        return

    q = get_queue()
    for target in targets:
        for msg in sorted(messages, key=lambda m: m.get("msg_order", 0)):
            await q.put({
                "schedule_id": schedule_id,
                "account_id": account_id,
                "message": msg,
                "target": target,
                "retry_count": 0
            })

    # Increment send count
    result = await db.increment_send_count(schedule_id)
    if result.get("reached_limit"):
        logger.info(f"Schedule {schedule_id}: reached limit ({result['current_sends']}/{result['max_sends']}), will deactivate after this batch")
        import scheduler as sch
        sch.remove_schedule_job(schedule_id)

    logger.info(f"Enqueued {len(messages)*len(targets)} items for schedule '{schedule['name']}' (account {account_id})")


async def _send_single_message(account_id: int, msg: dict, chat_id: int) -> bool:
    msg_type = msg["msg_type"]
    content = msg.get("content", "")
    media_path = msg.get("media_path")

    if msg_type == "text":
        return await tg.send_text_message(account_id, chat_id, content)
    elif msg_type == "photo":
        return await tg.send_photo_message(account_id, chat_id, media_path, caption=content or "")
    elif msg_type == "video":
        return await tg.send_video_message(account_id, chat_id, media_path, caption=content or "")
    elif msg_type == "document":
        return await tg.send_document_message(account_id, chat_id, media_path, caption=content or "")
    elif msg_type == "poll":
        options = json.loads(msg.get("poll_options", "[]"))
        question = msg.get("poll_question", "")
        multiple = bool(msg.get("poll_multiple", False))
        return await tg.send_poll_message(account_id, chat_id, question, options, multiple)
    else:
        logger.error(f"Unknown message type: {msg_type}")
        return False


async def queue_worker():
    """Single worker consuming the queue."""
    q = get_queue()
    logger.info("Message queue worker started")

    while True:
        try:
            item = await q.get()
            schedule_id = item["schedule_id"]
            account_id = item["account_id"]
            msg = item["message"]
            target = item["target"]
            retry_count = item.get("retry_count", 0)
            chat_id = target["chat_id"]
            chat_title = target.get("chat_title", str(chat_id))

            try:
                success = await _send_single_message(account_id, msg, chat_id)
                if success:
                    await db.add_send_log(schedule_id, account_id, msg.get("id"),
                                          chat_id, chat_title, "success")
                    logger.info(f"✓ [{account_id}] Sent {msg['msg_type']} to {chat_title}")
                else:
                    await db.add_send_log(schedule_id, account_id, msg.get("id"),
                                          chat_id, chat_title, "failed", "Send returned False")

            except tg_errors.FloodWaitError as e:
                wait_time = e.seconds + 1
                logger.warning(f"⏳ FloodWait: pausing {wait_time}s")
                await asyncio.sleep(wait_time)
                if retry_count < MAX_RETRIES:
                    item["retry_count"] = retry_count + 1
                    await q.put(item)
                else:
                    await db.add_send_log(schedule_id, account_id, msg.get("id"),
                                          chat_id, chat_title, "failed",
                                          f"FloodWait after {MAX_RETRIES} retries")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"✗ [{account_id}] Error -> {chat_title}: {error_msg}")
                if retry_count < MAX_RETRIES:
                    item["retry_count"] = retry_count + 1
                    await q.put(item)
                    await asyncio.sleep(2)
                else:
                    await db.add_send_log(schedule_id, account_id, msg.get("id"),
                                          chat_id, chat_title, "failed", error_msg)

            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            await asyncio.sleep(delay)
            q.task_done()

        except asyncio.CancelledError:
            logger.info("Queue worker cancelled")
            break
        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            await asyncio.sleep(1)


def start_worker():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(queue_worker())
    return _worker_task


def stop_worker():
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        _worker_task = None
