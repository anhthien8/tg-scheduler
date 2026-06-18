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
    skipped_blocked = 0
    for target in targets:
        chat_id_t = target["chat_id"]
        # Skip targets that have been blocked due to repeated failures
        if await db.is_target_blocked(schedule_id, account_id, chat_id_t):
            logger.info(
                f"⏭️  Schedule {schedule_id}: skipping BLOCKED target "
                f"{target.get('chat_title', chat_id_t)} for account {account_id}"
            )
            skipped_blocked += 1
            continue
        for msg in sorted(messages, key=lambda m: m.get("msg_order", 0)):
            await q.put({
                "schedule_id": schedule_id,
                "account_id": account_id,
                "message": msg,
                "target": target,
                "retry_count": 0
            })
    if skipped_blocked:
        logger.warning(f"⚠️  Schedule {schedule_id}: {skipped_blocked} blocked target(s) skipped")

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


def translate_error(error_msg: str) -> str:
    """Translate Telegram error messages to Vietnamese (for schedule send logs)."""
    e = (error_msg or "").lower()

    # PeerFlood
    if "peerflood" in e or "too many dms" in e:
        return "Tài khoản gửi tin nhắn quá nhiều, bị Telegram hạn chế tạm thời (PeerFlood)."

    # Cannot resolve entity
    if "cannot resolve entity" in e or "cannot resolve" in e:
        return "Không tìm thấy nhóm/kênh. Tài khoản chưa tham gia hoặc nhóm đã bị xóa."

    # Plain text / send plain forbidden
    if "plain results" in e or "chat_send_plain_forbidden" in e:
        return "Admin nhóm đã tắt quyền gửi tin nhắn văn bản thường trong nhóm này."

    # Chat write forbidden
    if "chat_write_forbidden" in e:
        return "Tài khoản bị cấm gửi tin nhắn trong nhóm/kênh này."

    # Privacy premium required
    if "privacy_premium_required" in e or "privacy premium required" in e:
        return "Người nhận yêu cầu tài khoản Telegram Premium mới nhắn tin được."

    # User banned
    if "user_banned_in_channel" in e or "banned" in e:
        return "Tài khoản đã bị cấm (ban) khỏi nhóm/kênh này."

    # FloodWait
    if "floodwait" in e or "flood_wait" in e:
        import re as _re
        m = _re.search(r'(\d+)', error_msg)
        secs = m.group(1) if m else "?"
        return f"Telegram yêu cầu chờ {secs} giây trước khi gửi tiếp (FloodWait)."

    # Chat admin required
    if "chat_admin_required" in e:
        return "Cần quyền Admin để thực hiện thao tác này trong nhóm/kênh."

    # Invalid peer
    if "invalid peer" in e or "invalid_peer" in e:
        return "Thông tin nhóm/kênh không hợp lệ. Kiểm tra lại ID hoặc tài khoản đã join chưa."

    # User privacy restricted
    if "userprivacyrestricted" in e or "privacy restrictions" in e:
        return "User bật chế độ riêng tư, không nhận tin nhắn từ người lạ."

    # Not found / channel invalid
    if "channel invalid" in e or "not found" in e:
        return "Không tìm thấy nhóm/kênh. Có thể đã bị xóa hoặc tài khoản chưa join."

    # Slowmode wait
    if "slowmodewait" in e or "slowmode" in e:
        import re as _re
        m = _re.search(r'(\d+)', error_msg)
        secs = m.group(1) if m else "?"
        return f"Nhóm đang bật chế độ chậm (Slowmode), chờ {secs}s mới gửi được."

    # Message too long
    if "message_too_long" in e or "too long" in e:
        return "Tin nhắn quá dài, vượt quá giới hạn Telegram (4096 ký tự)."

    # Media caption too long
    if "media_caption_too_long" in e:
        return "Caption ảnh/video quá dài."

    # Bots cannot start
    if "bots cannot start" in e:
        return "Bot không thể tự nhắn tin trước, cần user nhắn bot trước."

    # Generic fallback
    return error_msg


async def queue_worker():
    """Single worker consuming the queue."""
    q = get_queue()
    logger.info("Message queue worker started")

    while True:
        try:
            item = await q.get()
        except asyncio.CancelledError:
            logger.info("Queue worker cancelled")
            break

        try:
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
                                          chat_id, chat_title, "failed", "Gửi tin nhắn thất bại (Không rõ lý do)")

            except tg_errors.FloodWaitError as e:
                wait_time = e.seconds + 1
                logger.warning(f"⏳ FloodWait: pausing {wait_time}s")
                await asyncio.sleep(wait_time)
                if retry_count < MAX_RETRIES:
                    item["retry_count"] = retry_count + 1
                    await q.put(item)
                else:
                    friendly_error = translate_error(f"FloodWait after {MAX_RETRIES} retries")
                    await db.add_send_log(schedule_id, account_id, msg.get("id"),
                                          chat_id, chat_title, "failed", friendly_error)

            except Exception as e:
                error_msg = str(e)
                logger.error(f"✗ [{account_id}] Error -> {chat_title}: {error_msg}")
                if retry_count < MAX_RETRIES:
                    item["retry_count"] = retry_count + 1
                    await q.put(item)
                    await asyncio.sleep(2)
                else:
                    friendly_error = translate_error(error_msg)
                    await db.add_send_log(schedule_id, account_id, msg.get("id"),
                                          chat_id, chat_title, "failed", friendly_error)
                    # Track consecutive failures — block target if > 3 total fails
                    block_result = await db.record_target_failure(
                        schedule_id, account_id, chat_id, chat_title
                    )
                    if block_result["just_blocked"]:
                        logger.warning(
                            f"🚫 BLOCKED [{account_id}] -> {chat_title} (chat_id={chat_id}): "
                            f"failed {block_result['fail_count']} times. Stopping sends to this target."
                        )
                    # Feature #2: check if account should be flagged
                    await db.check_and_flag_account(account_id)

            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            logger.info("Queue worker cancelled")
            q.task_done()
            break
        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            await asyncio.sleep(1)
        finally:
            # BUG-04 fix: task_done() always called, even on exception
            q.task_done()


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
