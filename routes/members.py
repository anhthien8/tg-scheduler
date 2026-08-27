"""
Member Scraping & Bulk DM Campaign routes.
"""
import asyncio
import json
import logging
import uuid
import time
import random
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

import database as db
import telegram_client as tg
import alerts
import ai_remix as ai_rmx
import template_rotation as tmpl_rot
import image_randomizer as img_rand
from personalization import apply_personalization
from message_merger import merge_messages
from telethon import errors as tg_errors

logger = logging.getLogger("tg-scheduler.members")
router = APIRouter(prefix="/api/members", tags=["members"])

# ── Active campaign tracking ──
_active_campaigns: dict[int, bool] = {}  # campaign_id -> is_running


# ── Request Models ──────────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    account_id: int
    group_id: int
    group_title: Optional[str] = None
    filter_active_days: Optional[int] = None  # Filter by last seen within N days
    exclude_bots: bool = True
    scrape_method: Optional[str] = "members"
    max_messages: Optional[int] = 3000


class BatchScrapeRequest(BaseModel):
    account_id: int
    channels: list[str]  # List of t.me links or usernames
    filter_active_days: Optional[int] = None
    exclude_bots: bool = True
    scrape_method: Optional[str] = "members"
    max_messages: Optional[int] = 3000


class CampaignCreate(BaseModel):
    name: str
    scrape_job_id: str
    sender_account_ids: list[int]
    messages: list[dict]  # [{msg_type, content, media_path}]
    delay_min: int = 180
    delay_max: int = 420
    daily_limit_premium: int = 60
    daily_limit_normal: int = 10
    use_ai_remix: bool = False
    exclude_previous_dms: bool = True
    scheduled_at: str | None = None       # ISO datetime: "2026-07-24 09:00"
    target_timezone: str | None = None    # IANA: "America/New_York"
    ai_agent_id: int | None = None
    auto_translate_native: int = 1


class CampaignUpdateMessages(BaseModel):
    messages: list[dict]
    delay_min: Optional[int] = None
    delay_max: Optional[int] = None
    daily_limit_premium: Optional[int] = None
    daily_limit_normal: Optional[int] = None
    use_ai_remix: Optional[bool] = None
    exclude_previous_dms: Optional[bool] = None
    ai_agent_id: Optional[int] = None
    auto_translate_native: Optional[int] = None


class CampaignCloneRequest(BaseModel):
    name: Optional[str] = None
    scrape_job_id: Optional[str] = None
    exclude_source_results: bool = True


class SimilarChannelsRequest(BaseModel):
    account_id: int
    channel_link: str


class JoinChannelRequest(BaseModel):
    account_id: int
    channel_link: str


class ImportContactsRequest(BaseModel):
    scrape_job_id: str
    group_title: str
    contacts: list[dict]


class DeepCrawlRequest(BaseModel):
    account_ids: list[int]           # Premium accounts to rotate
    channel_link: str                # Source channel link/username
    max_depth: int = 2               # 1-4 layers deep


class TranslateDescriptionsRequest(BaseModel):
    texts: list[str]
    target_lang: str = "en"


# ── Deep Crawl State (module-level for progress polling) ──
_deep_crawl_state: dict = {
    "status": "idle",       # idle | running | completed | stopped | error
    "current_depth": 0,
    "max_depth": 0,
    "channels_found": 0,
    "channels_processed": 0,
    "contacts_found": 0,
    "queue_remaining": 0,
    "current_channel": "",
    "current_account": "",
    "errors": [],
    "results": [],
}
_deep_crawl_stop_flag: dict = {"stopped": False}
_deep_crawl_task = None  # asyncio.Task reference — survives browser refresh
_deep_crawl_queue: list[dict] = []  # Queue of pending crawl requests


def _parse_channel_identifier(raw: str) -> str:
    """Extract channel username from various formats."""
    raw = raw.strip()
    if not raw:
        return ""
    # Handle https://t.me/username, http://t.me/username, t.me/username
    for prefix in ["https://t.me/", "http://t.me/", "t.me/", "https://telegram.me/", "http://telegram.me/"]:
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    # Remove trailing slashes and +joinchat prefix
    raw = raw.strip("/")
    # Handle @username
    if raw.startswith("@"):
        raw = raw[1:]
    return raw


def _get_last_seen(sender, message=None):
    """Extract last_seen from a user status."""
    from telethon.tl.types import (
        UserStatusOnline, UserStatusOffline, UserStatusRecently,
        UserStatusLastWeek, UserStatusLastMonth,
    )
    status = getattr(sender, "status", None)
    if isinstance(status, UserStatusOnline):
        return datetime.utcnow().isoformat()
    elif isinstance(status, UserStatusOffline):
        return status.was_online.isoformat() if status.was_online else None
    elif isinstance(status, UserStatusRecently):
        return "recently"
    elif isinstance(status, UserStatusLastWeek):
        return "last_week"
    elif isinstance(status, UserStatusLastMonth):
        return "last_month"
    # Fallback to message date
    if message and message.date:
        return message.date.isoformat()
    return None


def _get_last_seen_from_user(user):
    """Extract last_seen from user (no message context)."""
    return _get_last_seen(user)


def _passes_active_filter(last_seen, filter_active_days):
    """Check if a user passes the active days filter."""
    if not filter_active_days:
        return True
    if last_seen is None:
        return False
    if last_seen == "recently":
        return True
    if last_seen == "last_week":
        return filter_active_days >= 7
    if last_seen == "last_month":
        return filter_active_days >= 30
    try:
        seen_dt = datetime.fromisoformat(last_seen)
        cutoff = datetime.utcnow() - timedelta(days=filter_active_days)
        return seen_dt >= cutoff
    except Exception:
        return False


def _build_member_dict(user, last_seen):
    """Build a member dict from a Telethon User object."""
    return {
        "user_id": user.id,
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "phone": getattr(user, "phone", None),
        "is_bot": getattr(user, "bot", False),
        "is_premium": getattr(user, "premium", False),
        "status": "active",
        "last_seen": last_seen,
    }


# ── Member Scraping ─────────────────────────────────────────────────────────

@router.post("/scrape")
async def scrape_members(req: ScrapeRequest, background_tasks: BackgroundTasks):
    """Start scraping members from a Telegram group."""
    acc = await db.get_account(req.account_id)
    if not acc:
        raise HTTPException(status_code=400, detail="Tài khoản không tồn tại")
    client = tg.get_client(req.account_id)
    if not client:
        raise HTTPException(status_code=400, detail="Tài khoản không tồn tại hoặc chưa đăng nhập")
    if not client.is_connected():
        raise HTTPException(status_code=400, detail="Tài khoản chưa kết nối Telegram")

    scrape_job_id = f"scrape_{req.group_id}_{uuid.uuid4().hex[:8]}"

    background_tasks.add_task(
        _do_scrape, scrape_job_id, req.account_id, req.group_id,
        req.group_title, req.filter_active_days, req.exclude_bots,
        req.scrape_method, req.max_messages
    )

    return {
        "status": "started",
        "scrape_job_id": scrape_job_id,
        "message": "Đang cào thành viên... Kiểm tra lại sau vài giây."
    }


async def _do_scrape(scrape_job_id: str, account_id: int, group_id: int,
                     group_title: str, filter_active_days: int, exclude_bots: bool,
                     scrape_method: str = "members", max_messages: int = 3000):
    """Background task: scrape all members from a group."""
    try:
        client = tg.get_client(account_id)
        if not client:
            logger.error(f"[Scrape {scrape_job_id}] Client not found for account {account_id}")
            return

        from telethon.tl.functions.channels import GetParticipantsRequest
        from telethon.tl.types import (
            ChannelParticipantsSearch,
            UserStatusOnline, UserStatusOffline, UserStatusRecently,
            UserStatusLastWeek, UserStatusLastMonth,
        )

        input_chat = await client.get_input_entity(group_id)
        if not group_title:
            try:
                entity = await client.get_entity(group_id)
                group_title = getattr(entity, "title", str(group_id))
            except Exception:
                group_title = str(group_id)

        all_members = []
        offset = 0
        batch_size = 200
        seen_ids = set()

        # Get administrators list to exclude them
        admin_ids = set()
        try:
            from telethon.tl.types import ChannelParticipantsAdmins
            admins_result = await client(GetParticipantsRequest(
                channel=input_chat,
                filter=ChannelParticipantsAdmins(),
                offset=0,
                limit=200,
                hash=0,
            ))
            for admin in admins_result.users:
                admin_ids.add(admin.id)
            logger.info(f"[Scrape {scrape_job_id}] Found {len(admin_ids)} administrators to exclude")
        except Exception as e:
            logger.warning(f"[Scrape {scrape_job_id}] Failed to fetch administrators: {e}")

        if scrape_method == "history":
            logger.info(f"[Scrape {scrape_job_id}] Scraping via chat history. Limit: {max_messages} messages.")
            async for message in client.iter_messages(input_chat, limit=max_messages):
                sender = message.sender
                if not sender:
                    try:
                        sender = await message.get_sender()
                    except Exception:
                        continue
                if not sender:
                    continue

                from telethon.tl.types import User
                if not isinstance(sender, User):
                    continue

                if sender.id in seen_ids:
                    continue
                seen_ids.add(sender.id)

                # Filter administrators
                if sender.id in admin_ids:
                    continue

                # Filter bots
                if exclude_bots and tg.is_bot_account(sender, getattr(sender, "username", None)):
                    continue

                # Determine last seen
                last_seen = None
                status = getattr(sender, "status", None)
                if isinstance(status, UserStatusOnline):
                    last_seen = datetime.utcnow().isoformat()
                elif isinstance(status, UserStatusOffline):
                    last_seen = status.was_online.isoformat() if status.was_online else None
                elif isinstance(status, UserStatusRecently):
                    last_seen = "recently"
                elif isinstance(status, UserStatusLastWeek):
                    last_seen = "last_week"
                elif isinstance(status, UserStatusLastMonth):
                    last_seen = "last_month"

                # Fallback to message date if status unknown
                if not last_seen and message.date:
                    last_seen = message.date.isoformat()

                # Apply active filter
                if filter_active_days:
                    if last_seen is None:
                        continue
                    if last_seen == "recently":
                        pass
                    elif last_seen == "last_week":
                        if filter_active_days < 7:
                            continue
                    elif last_seen == "last_month":
                        if filter_active_days < 30:
                            continue
                    else:
                        try:
                            seen_dt = datetime.fromisoformat(last_seen)
                            cutoff = datetime.utcnow() - timedelta(days=filter_active_days)
                            if seen_dt < cutoff:
                                if message.date and message.date < cutoff:
                                    continue
                        except Exception:
                            continue

                all_members.append({
                    "user_id": sender.id,
                    "username": getattr(sender, "username", None),
                    "first_name": getattr(sender, "first_name", None),
                    "last_name": getattr(sender, "last_name", None),
                    "phone": getattr(sender, "phone", None),
                    "is_bot": getattr(sender, "bot", False),
                    "is_premium": getattr(sender, "premium", False),
                    "status": "active",
                    "last_seen": last_seen,
                })
        else:
            # Use empty search to get all participants
            while True:
                try:
                    result = await client(GetParticipantsRequest(
                        channel=input_chat,
                        filter=ChannelParticipantsSearch(""),
                        offset=offset,
                        limit=batch_size,
                        hash=0,
                    ))
                except Exception as e:
                    logger.warning(f"[Scrape {scrape_job_id}] Error at offset {offset}: {e}")
                    # Try alphabetical search as fallback
                    break

                if not result.users:
                    break

                for user in result.users:
                    if user.id in seen_ids:
                        continue
                    seen_ids.add(user.id)

                    # Filter administrators
                    if user.id in admin_ids:
                        continue

                    # Filter bots
                    if exclude_bots and tg.is_bot_account(user, getattr(user, "username", None)):
                        continue

                    # Determine last seen
                    last_seen = None
                    status = getattr(user, "status", None)
                    if isinstance(status, UserStatusOnline):
                        last_seen = datetime.utcnow().isoformat()
                    elif isinstance(status, UserStatusOffline):
                        last_seen = status.was_online.isoformat() if status.was_online else None
                    elif isinstance(status, UserStatusRecently):
                        last_seen = "recently"
                    elif isinstance(status, UserStatusLastWeek):
                        last_seen = "last_week"
                    elif isinstance(status, UserStatusLastMonth):
                        last_seen = "last_month"

                    # Apply active filter
                    if filter_active_days:
                        if last_seen is None:
                            continue  # Unknown status, skip
                        if last_seen == "recently":
                            pass  # Always include recently active
                        elif last_seen == "last_week":
                            if filter_active_days < 7:
                                continue
                        elif last_seen == "last_month":
                            if filter_active_days < 30:
                                continue
                        elif last_seen not in ("recently", "last_week", "last_month"):
                            try:
                                seen_dt = datetime.fromisoformat(last_seen)
                                cutoff = datetime.utcnow() - timedelta(days=filter_active_days)
                                if seen_dt < cutoff:
                                    continue
                            except Exception:
                                continue

                    all_members.append({
                        "user_id": user.id,
                        "username": getattr(user, "username", None),
                        "first_name": getattr(user, "first_name", None),
                        "last_name": getattr(user, "last_name", None),
                        "phone": getattr(user, "phone", None),
                        "is_bot": getattr(user, "bot", False),
                        "is_premium": getattr(user, "premium", False),
                        "status": "active",
                        "last_seen": last_seen,
                    })

                offset += len(result.participants)
                if len(result.participants) < batch_size:
                    break

                # Rate limit: small delay between batches
                await asyncio.sleep(1.5)

            # If empty search didn't get all, try alphabetical search
            if len(all_members) < 100:
                for letter in "abcdefghijklmnopqrstuvwxyz":
                    try:
                        result = await client(GetParticipantsRequest(
                            channel=input_chat,
                            filter=ChannelParticipantsSearch(letter),
                            offset=0,
                            limit=200,
                            hash=0,
                        ))
                        for user in result.users:
                            if user.id in seen_ids:
                                continue
                            seen_ids.add(user.id)

                            # Filter administrators
                            if user.id in admin_ids:
                                continue

                            if exclude_bots and tg.is_bot_account(user, getattr(user, "username", None)):
                                continue

                            last_seen = None
                            status = getattr(user, "status", None)
                            if isinstance(status, UserStatusOnline):
                                last_seen = datetime.utcnow().isoformat()
                            elif isinstance(status, UserStatusOffline):
                                last_seen = status.was_online.isoformat() if status.was_online else None
                            elif isinstance(status, UserStatusRecently):
                                last_seen = "recently"
                            elif isinstance(status, UserStatusLastWeek):
                                last_seen = "last_week"
                            elif isinstance(status, UserStatusLastMonth):
                                last_seen = "last_month"

                            if filter_active_days:
                                if last_seen is None:
                                    continue
                                if last_seen == "recently":
                                    pass
                                elif last_seen == "last_week" and filter_active_days < 7:
                                    continue
                                elif last_seen == "last_month" and filter_active_days < 30:
                                    continue
                                elif last_seen not in ("recently", "last_week", "last_month"):
                                    try:
                                        seen_dt = datetime.fromisoformat(last_seen)
                                        cutoff = datetime.utcnow() - timedelta(days=filter_active_days)
                                        if seen_dt < cutoff:
                                            continue
                                    except Exception:
                                        continue

                            all_members.append({
                                "user_id": user.id,
                                "username": getattr(user, "username", None),
                                "first_name": getattr(user, "first_name", None),
                                "last_name": getattr(user, "last_name", None),
                                "phone": getattr(user, "phone", None),
                                "is_bot": getattr(user, "bot", False),
                                "is_premium": getattr(user, "premium", False),
                                "status": "active",
                                "last_seen": last_seen,
                                "lang_code": getattr(user, "lang_code", None),
                            })
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.debug(f"[Scrape {scrape_job_id}] Letter '{letter}': {e}")
                        continue

        # Save to DB
        await db.save_scraped_members(scrape_job_id, account_id, group_id, group_title, all_members)
        logger.info(f"[Scrape {scrape_job_id}] Done! Scraped {len(all_members)} members from {group_title}")

    except Exception as e:
        logger.error(f"[Scrape {scrape_job_id}] Fatal error: {e}", exc_info=True)


@router.get("/scrape-jobs")
async def get_scrape_jobs():
    """List all scrape jobs."""
    jobs = await db.get_scrape_jobs()
    return {"jobs": jobs}


@router.get("/scrape-jobs/{scrape_job_id}")
async def get_scrape_job_members(scrape_job_id: str,
                                  limit: int = Query(500, ge=1, le=2000),
                                  offset: int = Query(0, ge=0)):
    """Get members for a specific scrape job."""
    members = await db.get_scraped_members(scrape_job_id, limit, offset)
    return {"members": members, "count": len(members)}


@router.delete("/scrape-jobs/{scrape_job_id}")
async def delete_scrape_job(scrape_job_id: str):
    """Delete a scrape job and its members."""
    try:
        await db.delete_scrape_job(scrape_job_id)
        return {"status": "deleted"}
    except Exception as e:
        logger.error(f"Error deleting scrape job {scrape_job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi xóa scrape job: {str(e)}")


# ── Batch Scrape ─────────────────────────────────────────────────────────────

@router.post("/batch-scrape")
async def batch_scrape_members(req: BatchScrapeRequest, background_tasks: BackgroundTasks):
    """Start batch scraping members from multiple channels."""
    client = tg.get_client(req.account_id)
    if not client:
        raise HTTPException(status_code=400, detail="Tài khoản không tồn tại hoặc chưa đăng nhập")
    if not client.is_connected():
        raise HTTPException(status_code=400, detail="Tài khoản chưa kết nối Telegram")

    # Parse channel identifiers
    channels = []
    for raw in req.channels:
        parsed = _parse_channel_identifier(raw)
        if parsed:
            channels.append(parsed)

    if not channels:
        raise HTTPException(status_code=400, detail="Không tìm thấy channel hợp lệ")

    batch_job_id = f"batch_{uuid.uuid4().hex[:8]}"

    # Create batch channel records
    for ch in channels:
        await db.create_batch_channel(batch_job_id, ch)

    background_tasks.add_task(
        _do_batch_scrape, batch_job_id, req.account_id, channels,
        req.filter_active_days, req.exclude_bots, req.scrape_method, req.max_messages
    )

    return {
        "status": "started",
        "batch_job_id": batch_job_id,
        "channel_count": len(channels),
        "message": f"Đang cào {len(channels)} channel... Kiểm tra tiến trình bên dưới."
    }


@router.get("/batch-scrape/{batch_job_id}/progress")
async def get_batch_progress(batch_job_id: str):
    """Get progress of a batch scrape job."""
    channels = await db.get_batch_channels(batch_job_id)
    total_members = await db.count_scraped_members(batch_job_id)

    done = sum(1 for c in channels if c["status"] == "done")
    errors = sum(1 for c in channels if c["status"] == "error")
    running = sum(1 for c in channels if c["status"] == "running")
    pending = sum(1 for c in channels if c["status"] == "pending")

    overall_status = "running"
    if pending == 0 and running == 0:
        overall_status = "done"

    return {
        "batch_job_id": batch_job_id,
        "status": overall_status,
        "total_channels": len(channels),
        "done": done,
        "errors": errors,
        "running": running,
        "pending": pending,
        "total_members": total_members,
        "channels": channels,
    }


@router.post("/batch-scrape/resolve")
async def resolve_channels(data: BatchScrapeRequest):
    """Resolve multiple channel identifiers to get their info."""
    client = tg.get_client(data.account_id)
    if not client:
        raise HTTPException(status_code=400, detail="Tài khoản không tồn tại")
    if not client.is_connected():
        raise HTTPException(status_code=400, detail="Tài khoản chưa kết nối")

    results = []
    for raw in data.channels:
        username = _parse_channel_identifier(raw)
        if not username:
            results.append({"input": raw, "success": False, "error": "Link không hợp lệ"})
            continue
        try:
            entity = await client.get_entity(username)
            title = getattr(entity, "title", username)
            participants = getattr(entity, "participants_count", None)
            results.append({
                "input": raw,
                "success": True,
                "username": username,
                "title": title,
                "channel_id": entity.id,
                "participants_count": participants,
            })
        except Exception as e:
            results.append({"input": raw, "success": False, "error": str(e), "username": username})
        await asyncio.sleep(0.5)  # Rate limit

    return {"results": results}


async def _do_batch_scrape(batch_job_id: str, account_id: int, channels: list[str],
                            filter_active_days: int, exclude_bots: bool,
                            scrape_method: str = "members", max_messages: int = 3000):
    """Background task: scrape members from multiple channels with dedup."""
    global_seen_ids = set()  # Cross-channel dedup
    total_saved = 0

    for channel_username in channels:
        logger.info(f"[Batch {batch_job_id}] Starting channel: {channel_username}")
        await db.update_batch_channel(batch_job_id, channel_username,
                                       status="running",
                                       started_at=datetime.utcnow().isoformat())
        try:
            client = tg.get_client(account_id)
            if not client or not client.is_connected():
                raise Exception("Client not connected")

            # Resolve channel
            try:
                entity = await client.get_entity(channel_username)
            except Exception as e:
                raise Exception(f"Không thể resolve: {e}")

            group_id = entity.id
            group_title = getattr(entity, "title", channel_username)
            input_chat = await client.get_input_entity(group_id)

            from telethon.tl.functions.channels import GetParticipantsRequest
            from telethon.tl.types import (
                ChannelParticipantsSearch, ChannelParticipantsAdmins,
                UserStatusOnline, UserStatusOffline, UserStatusRecently,
                UserStatusLastWeek, UserStatusLastMonth,
            )

            # Get admin IDs to exclude
            admin_ids = set()
            try:
                admins_result = await client(GetParticipantsRequest(
                    channel=input_chat, filter=ChannelParticipantsAdmins(),
                    offset=0, limit=200, hash=0,
                ))
                admin_ids = {a.id for a in admins_result.users}
            except Exception:
                pass

            channel_members = []

            if scrape_method == "history":
                async for message in client.iter_messages(input_chat, limit=max_messages):
                    sender = message.sender
                    if not sender:
                        try:
                            sender = await message.get_sender()
                        except Exception:
                            continue
                    if not sender:
                        continue

                    from telethon.tl.types import User
                    if not isinstance(sender, User):
                        continue
                    if sender.id in global_seen_ids:
                        continue
                    if sender.id in admin_ids:
                        continue
                    if exclude_bots and tg.is_bot_account(sender, getattr(sender, "username", None)):
                        continue

                    last_seen = _get_last_seen(sender, message)
                    if not _passes_active_filter(last_seen, filter_active_days):
                        continue

                    global_seen_ids.add(sender.id)
                    channel_members.append(_build_member_dict(sender, last_seen))
            else:
                # Members method
                offset = 0
                batch_size = 200
                local_seen = set()

                while True:
                    try:
                        result = await client(GetParticipantsRequest(
                            channel=input_chat,
                            filter=ChannelParticipantsSearch(""),
                            offset=offset, limit=batch_size, hash=0,
                        ))
                    except Exception:
                        break

                    if not result.users:
                        break

                    for user in result.users:
                        if user.id in global_seen_ids or user.id in local_seen:
                            continue
                        local_seen.add(user.id)
                        if user.id in admin_ids:
                            continue
                        if exclude_bots and tg.is_bot_account(user, getattr(user, "username", None)):
                            continue

                        last_seen = _get_last_seen_from_user(user)
                        if not _passes_active_filter(last_seen, filter_active_days):
                            continue

                        global_seen_ids.add(user.id)
                        channel_members.append(_build_member_dict(user, last_seen))

                    offset += len(result.participants)
                    if len(result.participants) < batch_size:
                        break
                    await asyncio.sleep(1.5)

                # Alphabetical fallback if too few
                if len(channel_members) < 100:
                    for letter in "abcdefghijklmnopqrstuvwxyz":
                        try:
                            result = await client(GetParticipantsRequest(
                                channel=input_chat,
                                filter=ChannelParticipantsSearch(letter),
                                offset=0, limit=200, hash=0,
                            ))
                            for user in result.users:
                                if user.id in global_seen_ids:
                                    continue
                                if user.id in admin_ids:
                                    continue
                                if exclude_bots and tg.is_bot_account(user, getattr(user, "username", None)):
                                    continue
                                last_seen = _get_last_seen_from_user(user)
                                if not _passes_active_filter(last_seen, filter_active_days):
                                    continue
                                global_seen_ids.add(user.id)
                                channel_members.append(_build_member_dict(user, last_seen))
                            await asyncio.sleep(0.5)
                        except Exception:
                            continue

            # Save members for this channel under the BATCH job_id
            if channel_members:
                await db.save_scraped_members(
                    batch_job_id, account_id, group_id, group_title, channel_members
                )
                total_saved += len(channel_members)

            await db.update_batch_channel(batch_job_id, channel_username,
                                           status="done",
                                           channel_title=group_title,
                                           channel_id=group_id,
                                           member_count=len(channel_members),
                                           finished_at=datetime.utcnow().isoformat())
            logger.info(f"[Batch {batch_job_id}] Done {channel_username}: {len(channel_members)} members (deduped)")

            # Delay between channels to avoid rate limits
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"[Batch {batch_job_id}] Error on {channel_username}: {e}")
            await db.update_batch_channel(batch_job_id, channel_username,
                                           status="error",
                                           error_message=str(e),
                                           finished_at=datetime.utcnow().isoformat())
            await asyncio.sleep(2)

    logger.info(f"[Batch {batch_job_id}] All channels done! Total unique members: {total_saved}")


# ── Similar Channels Scraper ──────────────────────────────────────────────────

@router.post("/similar-channels")
async def get_similar_channels(req: SimilarChannelsRequest):
    """Get recommendations of similar channels and extract admin contacts."""
    try:
        leads = await tg.get_similar_channels_and_contacts(req.account_id, req.channel_link)
        return {"success": True, "leads": leads}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/join-channel")
async def join_channel(req: JoinChannelRequest):
    """Join a public or private channel using a specific account."""
    res = await tg.join_channel(req.account_id, req.channel_link)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to join"))
    return res


@router.post("/import-contacts")
async def import_contacts(req: ImportContactsRequest):
    """Import selected contacts into the scraped_members table under a specific job.
    Automatically deduplicates against all previously DM'd users across campaigns and watchers."""
    try:
        import zlib
        from database import get_db

        # ── Step 1: Collect already-DM'd usernames (cross-campaign dedup) ──
        already_dmd: set[str] = set()
        async with get_db() as conn:
            # From DM campaigns (success only)
            cursor = await conn.execute(
                "SELECT DISTINCT LOWER(target_username) FROM dm_campaign_logs "
                "WHERE status = 'success' AND target_username IS NOT NULL AND target_username != ''"
            )
            for row in await cursor.fetchall():
                already_dmd.add(row[0])

            # From Watcher auto-DMs (success only)
            cursor = await conn.execute(
                "SELECT DISTINCT LOWER(target_username) FROM watcher_dm_logs "
                "WHERE status = 'success' AND target_username IS NOT NULL AND target_username != ''"
            )
            for row in await cursor.fetchall():
                already_dmd.add(row[0])

        # ── Step 2: Build members list, filtering out bots and already-DM'd ──
        members_list = []
        skipped_count = 0
        skipped_bots = 0
        for c in req.contacts:
            username = c.get("username", "").strip()
            if username.startswith("@"):
                username = username[1:]
            if not username:
                continue

            # Bot account check
            if tg.is_bot_account(None, username):
                skipped_bots += 1
                continue

            # Cross-campaign dedup check
            if username.lower() in already_dmd:
                skipped_count += 1
                continue

            # Generate deterministic negative ID based on username hash to satisfy UNIQUE(scrape_job_id, user_id)
            h = zlib.crc32(username.encode("utf-8")) & 0x7fffffff
            if h == 0:
                h = 1
            dummy_user_id = -int(h)

            members_list.append({
                "user_id": dummy_user_id,
                "username": username,
                "first_name": c.get("first_name") or username,
                "last_name": c.get("last_name") or "",
                "phone": "",
                "is_bot": False,
                "is_premium": False,
                "status": "active",
                "last_seen": "Recently"
            })

        # ── Step 3: Save to DB ──
        if members_list:
            await db.save_scraped_members(
                scrape_job_id=req.scrape_job_id,
                account_id=0,
                group_id=0,
                group_title=req.group_title,
                members=members_list
            )

        details = []
        if skipped_count:
            details.append(f"bỏ qua {skipped_count} đã DM trước đó")
        if skipped_bots:
            details.append(f"lọc {skipped_bots} tài khoản bot")
        detail_msg = f" ({', '.join(details)})" if details else ""

        return {
            "success": True,
            "count": len(members_list),
            "skipped_dmd": skipped_count,
            "skipped_bots": skipped_bots,
            "message": f"Imported {len(members_list)} contacts{detail_msg}"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Deep Crawl (BFS Multi-Layer) ─────────────────────────────────────────────

async def _save_deep_crawl_snapshot():
    """Persist deep crawl state and queue to database settings so it survives restarts."""
    try:
        state_to_save = dict(_deep_crawl_state)
        await db.set_setting("deep_crawl_state", json.dumps(state_to_save, ensure_ascii=False))
        await db.set_setting("deep_crawl_queue", json.dumps(_deep_crawl_queue, ensure_ascii=False))
    except Exception as e:
        logger.debug(f"[DeepCrawl] Error persisting snapshot: {e}")


async def _load_deep_crawl_snapshot():
    """Load persisted deep crawl state and queue on startup."""
    global _deep_crawl_state, _deep_crawl_queue
    try:
        raw_state = await db.get_setting("deep_crawl_state", "")
        if raw_state:
            s = json.loads(raw_state)
            if s.get("status") == "running":
                s["status"] = "stopped"
            if "results" in s and isinstance(s["results"], list):
                for lead in s["results"]:
                    if "contacts" in lead and isinstance(lead["contacts"], list):
                        lead["contacts"] = [c for c in lead["contacts"] if not tg.is_bot_account(None, c)]
                    if "trading_score" not in lead or lead.get("trading_score") is None:
                        score_info = tg.score_community_trading(
                            title=lead.get("title", ""),
                            description=lead.get("description", ""),
                            username=lead.get("username", ""),
                            contacts=lead.get("contacts", []),
                            participants_count=lead.get("participants_count", 0)
                        )
                        lead.update(score_info)
            _deep_crawl_state.update(s)
        
        raw_queue = await db.get_setting("deep_crawl_queue", "")
        if raw_queue:
            _deep_crawl_queue = json.loads(raw_queue)
    except Exception as e:
        logger.debug(f"[DeepCrawl] Error loading snapshot: {e}")


@router.post("/deep-crawl")
async def start_deep_crawl(req: DeepCrawlRequest):
    """Start a deep BFS crawl of similar channels (1-4 layers).
    If a crawl is already running, the request is added to the queue."""
    global _deep_crawl_state, _deep_crawl_stop_flag, _deep_crawl_task, _deep_crawl_queue
    import asyncio

    if req.max_depth < 1 or req.max_depth > 4:
        raise HTTPException(status_code=400, detail="Độ sâu phải từ 1 đến 4.")

    # If a crawl is running, queue this request
    if _deep_crawl_state.get("status") == "running":
        queue_item = {
            "account_ids": req.account_ids,
            "channel_link": req.channel_link,
            "max_depth": req.max_depth,
            "added_at": datetime.now().isoformat(),
        }
        _deep_crawl_queue.append(queue_item)
        await _save_deep_crawl_snapshot()
        pos = len(_deep_crawl_queue)
        logger.info(f"[DeepCrawl] Queued: {req.channel_link} (depth={req.max_depth}), position #{pos}")
        return {
            "success": True,
            "queued": True,
            "position": pos,
            "message": f"Đã thêm vào hàng đợi (vị trí #{pos}). Sẽ tự chạy khi crawl hiện tại xong."
        }

    # Start immediately
    _deep_crawl_state = {
        "status": "running",
        "current_depth": 0,
        "max_depth": req.max_depth,
        "channels_found": 0,
        "channels_processed": 0,
        "contacts_found": 0,
        "queue_remaining": 0,
        "current_channel": "Đang khởi tạo...",
        "current_account": "",
        "errors": [],
        "results": [],
        "source_url": req.channel_link,
    }
    _deep_crawl_stop_flag = {"stopped": False}
    await _save_deep_crawl_snapshot()

    _deep_crawl_task = asyncio.create_task(
        _do_deep_crawl(req.account_ids, req.channel_link, req.max_depth)
    )
    return {"success": True, "queued": False, "message": f"Deep crawl started: {req.max_depth} layers"}


async def _do_deep_crawl(account_ids: list[int], channel_link: str, max_depth: int):
    """Background task that runs the BFS deep crawl. Auto-starts next queued item when done."""
    global _deep_crawl_state, _deep_crawl_queue, _deep_crawl_stop_flag, _deep_crawl_task
    import asyncio

    async def _progress_cb(state: dict):
        """Callback to update module-level state for polling."""
        _deep_crawl_state.update(state)

    _floodwait_retry_after = None  # seconds to wait before retry
    try:
        results = await tg.deep_crawl_similar_channels(
            account_ids=account_ids,
            channel_link=channel_link,
            max_depth=max_depth,
            progress_callback=_progress_cb,
            stop_flag=_deep_crawl_stop_flag,
        )
        _deep_crawl_state["results"] = results
        if _deep_crawl_state["status"] != "stopped":
            _deep_crawl_state["status"] = "completed"
        await _save_deep_crawl_snapshot()
        logger.info(f"[DeepCrawl] Background task complete. {len(results)} leads.")
    except Exception as e:
        err_str = str(e)
        # Detect FloodWait-only failure — extract longest wait time
        import re as _re
        fw_seconds = _re.findall(r'FloodWait\s+(\d+)s', err_str)
        if fw_seconds and "Không thể resolve" in err_str:
            max_wait = max(int(s) for s in fw_seconds)
            _floodwait_retry_after = max_wait + 30  # extra buffer
            _deep_crawl_state["status"] = "flood_wait"
            _deep_crawl_state["errors"] = [
                f"⏳ FloodWait {max_wait}s — tự động thử lại sau {_floodwait_retry_after}s "
                f"({int(_floodwait_retry_after/60)} phút {_floodwait_retry_after%60}s)"
            ]
            logger.warning(f"[DeepCrawl] All accounts FloodWait. Scheduling auto-retry in {_floodwait_retry_after}s")
        else:
            _deep_crawl_state["status"] = "error"
            _deep_crawl_state["errors"].append(f"Fatal: {err_str}")
            logger.error(f"[DeepCrawl] Fatal error: {e}", exc_info=True)
        await _save_deep_crawl_snapshot()

    # ── FloodWait auto-retry: re-queue current crawl with a delay ──
    if _floodwait_retry_after and not _deep_crawl_stop_flag.get("stopped"):
        import time as _time
        retry_item = {
            "account_ids": account_ids,
            "channel_link": channel_link,
            "max_depth": max_depth,
            "retry_after": _time.time() + _floodwait_retry_after,
        }
        _deep_crawl_queue.insert(0, retry_item)  # put at front of queue
        await _save_deep_crawl_snapshot()
        logger.info(f"[DeepCrawl] FloodWait retry scheduled: will restart '{channel_link}' in {_floodwait_retry_after}s")

    # ── Auto-start next queued crawl ──
    await asyncio.sleep(3)  # Brief pause between crawls
    if _deep_crawl_queue:
        import time as _time2
        next_item = _deep_crawl_queue[0]
        retry_after = next_item.get("retry_after", 0)
        if retry_after > _time2.time():
            # Not ready yet — sleep until FloodWait expires
            wait_secs = retry_after - _time2.time()
            logger.info(f"[DeepCrawl] Queue: next item has FloodWait, sleeping {wait_secs:.0f}s before starting...")
            _deep_crawl_state["errors"] = [
                f"⏳ Chờ hết FloodWait — bắt đầu lại sau {int(wait_secs//60)} phút {int(wait_secs%60)} giây..."
            ]
            await asyncio.sleep(wait_secs)
        next_item = _deep_crawl_queue.pop(0)
        await _save_deep_crawl_snapshot()
        logger.info(f"[DeepCrawl] Queue: auto-starting next → {next_item['channel_link']} (depth={next_item['max_depth']}), {len(_deep_crawl_queue)} remaining")
        _deep_crawl_state = {
            "status": "running",
            "current_depth": 0,
            "max_depth": next_item["max_depth"],
            "channels_found": 0,
            "channels_processed": 0,
            "contacts_found": 0,
            "queue_remaining": 0,
            "current_channel": "Đang khởi tạo...",
            "current_account": "",
            "errors": [],
            "results": [],
            "source_url": next_item["channel_link"],
        }
        _deep_crawl_stop_flag = {"stopped": False}
        await _save_deep_crawl_snapshot()
        _deep_crawl_task = asyncio.create_task(
            _do_deep_crawl(next_item["account_ids"], next_item["channel_link"], next_item["max_depth"])
        )


@router.get("/deep-crawl/status")
async def get_deep_crawl_status():
    """Poll the current deep crawl progress + queue info."""
    global _deep_crawl_state, _deep_crawl_queue
    if _deep_crawl_state.get("status") == "idle" and not _deep_crawl_state.get("results") and not _deep_crawl_queue:
        await _load_deep_crawl_snapshot()

    state_copy = {k: v for k, v in _deep_crawl_state.items() if k != "results"}
    state_copy["results_count"] = len(_deep_crawl_state.get("results", []))
    state_copy["queue"] = [
        {"channel_link": q["channel_link"], "max_depth": q["max_depth"], "added_at": q.get("added_at", "")}
        for q in _deep_crawl_queue
    ]
    state_copy["queue_count"] = len(_deep_crawl_queue)
    return state_copy


@router.get("/deep-crawl/results")
async def get_deep_crawl_results():
    """Get the full results of the last deep crawl with up-to-date trading scores."""
    global _deep_crawl_state
    if not _deep_crawl_state.get("results"):
        await _load_deep_crawl_snapshot()
    leads = _deep_crawl_state.get("results", [])
    for lead in leads:
        if "contacts" in lead and isinstance(lead["contacts"], list):
            lead["contacts"] = [c for c in lead["contacts"] if not tg.is_bot_account(None, c)]
        if "trading_score" not in lead or lead.get("trading_score") is None:
            score_info = tg.score_community_trading(
                title=lead.get("title", ""),
                description=lead.get("description", ""),
                username=lead.get("username", ""),
                contacts=lead.get("contacts", []),
                participants_count=lead.get("participants_count", 0)
            )
            lead.update(score_info)
    return {
        "status": _deep_crawl_state.get("status"),
        "leads": leads,
        "total": len(leads),
    }


_translation_cache: dict[str, str] = {}
_translate_executor = None
_translate_semaphore = None


def _get_translate_executor():
    global _translate_executor
    if _translate_executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _translate_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="translate")
    return _translate_executor


def _get_translate_semaphore():
    global _translate_semaphore
    if _translate_semaphore is None:
        import asyncio
        _translate_semaphore = asyncio.Semaphore(10)
    return _translate_semaphore


async def _translate_single_text(text: str, target_lang: str = "en") -> str:
    """Translate a single text string to target_lang using Google Translate with in-memory caching."""
    if not text or not text.strip():
        return text
    clean = text.strip()
    cache_key = f"{target_lang}:{clean}"
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]

    import urllib.request
    import urllib.parse
    import json
    import asyncio

    def _do_http():
        try:
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target_lang}&dt=t&q=" + urllib.parse.quote(clean[:1200])
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=5) as response:
                res = json.loads(response.read().decode("utf-8"))
                if res and isinstance(res, list) and res[0]:
                    translated = "".join([part[0] for part in res[0] if part and part[0]])
                    return translated
        except Exception as e:
            logger.debug(f"[Translation] Error: {e}")
        return clean

    sem = _get_translate_semaphore()
    async with sem:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(_get_translate_executor(), _do_http)
    _translation_cache[cache_key] = res
    # Cap cache at 5000 entries (FIFO eviction)
    if len(_translation_cache) > 5000:
        oldest = next(iter(_translation_cache))
        del _translation_cache[oldest]
    return res


@router.post("/translate-descriptions")
async def translate_descriptions(req: TranslateDescriptionsRequest):
    """Batch translate list of channel descriptions into English for BD analysis.
    Deduplicates texts, uses cache, and runs up to 10 concurrent translations."""
    import asyncio
    # Deduplicate and skip already-cached
    unique = []
    seen = set()
    for t in req.texts:
        clean = (t or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            unique.append(clean)

    tasks = [_translate_single_text(t, req.target_lang) for t in unique]
    translated_list = await asyncio.gather(*tasks, return_exceptions=True)
    translations = {}
    for orig, trans in zip(unique, translated_list):
        if isinstance(trans, Exception):
            translations[orig] = orig  # fallback to original on error
        else:
            translations[orig] = trans
    return {"translations": translations}


@router.post("/deep-crawl/clear")
async def clear_deep_crawl_results():
    """Clear previous deep crawl results from memory and database snapshot."""
    global _deep_crawl_state
    _deep_crawl_state["results"] = []
    await _save_deep_crawl_snapshot()
    return {"success": True, "message": "Đã xóa sạch kết quả deep crawl cũ."}


@router.post("/deep-crawl/stop")
async def stop_deep_crawl():
    """Stop the running deep crawl gracefully."""
    global _deep_crawl_stop_flag
    if _deep_crawl_state.get("status") != "running":
        return {"success": False, "message": "Không có deep crawl nào đang chạy."}
    _deep_crawl_stop_flag["stopped"] = True
    await _save_deep_crawl_snapshot()
    return {"success": True, "message": "Đang dừng deep crawl..."}


@router.get("/deep-crawl/queue")
async def get_deep_crawl_queue():
    """Get the pending deep crawl queue."""
    global _deep_crawl_queue
    if not _deep_crawl_queue:
        await _load_deep_crawl_snapshot()
    return {
        "queue": [
            {"channel_link": q["channel_link"], "max_depth": q["max_depth"],
             "added_at": q.get("added_at", ""), "index": i}
            for i, q in enumerate(_deep_crawl_queue)
        ],
        "count": len(_deep_crawl_queue)
    }


@router.delete("/deep-crawl/queue/{index}")
async def remove_from_deep_crawl_queue(index: int):
    """Remove an item from the queue by index."""
    global _deep_crawl_queue
    if index < 0 or index >= len(_deep_crawl_queue):
        raise HTTPException(status_code=404, detail="Không tìm thấy item trong queue")
    removed = _deep_crawl_queue.pop(index)
    await _save_deep_crawl_snapshot()
    logger.info(f"[DeepCrawl] Queue: removed #{index} → {removed['channel_link']}")
    return {"success": True, "removed": removed["channel_link"], "remaining": len(_deep_crawl_queue)}


@router.delete("/deep-crawl/queue")
async def clear_deep_crawl_queue():
    """Clear the entire queue."""
    global _deep_crawl_queue
    count = len(_deep_crawl_queue)
    _deep_crawl_queue = []
    await _save_deep_crawl_snapshot()
    logger.info(f"[DeepCrawl] Queue: cleared {count} items")
    return {"success": True, "cleared": count}


# ── DM Campaigns ────────────────────────────────────────────────────────────

@router.post("/campaigns")
async def create_campaign(req: CampaignCreate):
    """Create a new DM campaign."""
    # Count total targets (efficient COUNT query, no row loading)
    total = await db.count_scraped_members(req.scrape_job_id)
    if total == 0:
        raise HTTPException(status_code=400, detail="Scrape job không tồn tại hoặc trống")

    # Determine status: scheduled if scheduled_at provided, else draft
    status = "draft"
    if req.scheduled_at and req.target_timezone:
        status = "scheduled"

    campaign_id = await db.create_dm_campaign({
        "name": req.name,
        "scrape_job_id": req.scrape_job_id,
        "sender_account_ids": req.sender_account_ids,
        "messages": [m if isinstance(m, dict) else m.dict() for m in req.messages],
        "delay_min": req.delay_min,
        "delay_max": req.delay_max,
        "daily_limit_premium": req.daily_limit_premium or 60,
        "daily_limit_normal": min(req.daily_limit_normal or 10, 10),
        "use_ai_remix": req.use_ai_remix,
        "exclude_previous_dms": req.exclude_previous_dms,
        "total_targets": total,
        "status": status,
        "scheduled_at": req.scheduled_at,
        "target_timezone": req.target_timezone,
    })

    # Register APScheduler job if scheduled
    if status == "scheduled":
        import scheduler as sch
        sch.add_campaign_schedule_job(campaign_id, req.scheduled_at, req.target_timezone)

    return {"status": "created", "campaign_id": campaign_id, "total_targets": total}


@router.post("/campaigns/{campaign_id}/clone")
async def clone_campaign(campaign_id: int, req: Optional[CampaignCloneRequest] = None):
    """Clone an existing campaign, optionally excluding already-contacted members."""
    source_cmp = await db.get_dm_campaign(campaign_id)
    if not source_cmp:
        raise HTTPException(status_code=404, detail="Campaign nguồn không tồn tại")

    target_job_id = (req.scrape_job_id if req and req.scrape_job_id else source_cmp["scrape_job_id"])
    target_name = (req.name if req and req.name else f"{source_cmp['name']} - Clone")
    exclude_source = req.exclude_source_results if req else True

    total = await db.count_scraped_members(target_job_id)
    if total == 0:
        raise HTTPException(status_code=400, detail="Scrape job target không tồn tại hoặc trống")

    # Count excluded members from source campaign
    excluded_count = 0
    exclude_ids_json = "[]"
    if exclude_source:
        from database import get_db
        async with get_db() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(DISTINCT target_user_id) FROM dm_campaign_logs "
                "WHERE campaign_id = ? AND status IN ('success', 'failed')",
                (campaign_id,)
            )
            row = await cursor.fetchone()
            excluded_count = row[0] if row else 0
        exclude_ids_json = json.dumps([campaign_id])

    adjusted_total = max(0, total - excluded_count)

    new_campaign_id = await db.create_dm_campaign({
        "name": target_name,
        "scrape_job_id": target_job_id,
        "sender_account_ids": source_cmp.get("sender_account_ids", []),
        "messages": source_cmp.get("messages", []),
        "delay_min": source_cmp.get("delay_min", 180),
        "delay_max": source_cmp.get("delay_max", 420),
        "daily_limit_premium": source_cmp.get("daily_limit_premium", 60),
        "daily_limit_normal": source_cmp.get("daily_limit_normal", 10),
        "use_ai_remix": bool(source_cmp.get("use_ai_remix", False)),
        "exclude_previous_dms": bool(source_cmp.get("exclude_previous_dms", 1)),
        "total_targets": adjusted_total,
    })

    # Save exclude_campaign_ids to new campaign
    if exclude_source and exclude_ids_json != "[]":
        from database import get_db
        async with get_db() as conn:
            await conn.execute(
                "UPDATE dm_campaigns SET exclude_campaign_ids = ? WHERE id = ?",
                (exclude_ids_json, new_campaign_id)
            )
            await conn.commit()

    return {
        "status": "cloned",
        "campaign_id": new_campaign_id,
        "name": target_name,
        "total_targets": adjusted_total,
        "excluded_count": excluded_count,
        "source_campaign_id": campaign_id
    }


@router.get("/campaigns")
async def list_campaigns(updated_since: Optional[str] = None):
    """List all DM campaigns."""
    if updated_since:
        campaigns = await db.get_campaigns_updated_since(updated_since)
    else:
        campaigns = await db.get_all_dm_campaigns()
    return {"campaigns": campaigns}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int):
    """Get campaign details."""
    campaign = await db.get_dm_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign không tồn tại")
    return {"campaign": campaign}


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: int, background_tasks: BackgroundTasks):
    """Start running a DM campaign."""
    campaign = await db.get_dm_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign không tồn tại")

    if campaign["status"] == "running":
        task = _active_campaigns.get(campaign_id)
        if task is True or (isinstance(task, asyncio.Task) and not task.done()):
            raise HTTPException(status_code=400, detail="Campaign đang chạy")
        logger.info(f"[Campaign {campaign_id}] Campaign marked running in DB but no active background task found. Re-starting task...")

    # Mark as running
    await db.update_dm_campaign_status(campaign_id, "running")
    task = asyncio.create_task(_run_campaign(campaign_id))
    _active_campaigns[campaign_id] = task

    return {"status": "started", "message": "Campaign đã bắt đầu chạy"}


@router.post("/campaigns/{campaign_id}/stop")
async def stop_campaign(campaign_id: int):
    """Stop a running campaign."""
    _active_campaigns[campaign_id] = False
    await db.update_dm_campaign_status(campaign_id, "paused")
    return {"status": "stopped"}


@router.post("/campaigns/{campaign_id}/cancel-schedule")
async def cancel_campaign_schedule(campaign_id: int):
    """Cancel a scheduled campaign, reverting it to draft."""
    campaign = await db.get_dm_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign không tồn tại")
    if campaign["status"] != "scheduled":
        raise HTTPException(status_code=400, detail="Campaign không ở trạng thái đã hẹn giờ")

    import scheduler as sch
    sch.remove_campaign_schedule_job(campaign_id)
    await db.update_dm_campaign_status(campaign_id, "draft")
    return {"status": "ok", "message": "Đã hủy lịch hẹn giờ"}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int):
    """Delete a campaign."""
    _active_campaigns.pop(campaign_id, None)
    await db.delete_dm_campaign(campaign_id)
    return {"status": "deleted"}

@router.put("/campaigns/{campaign_id}/messages")
async def update_campaign_messages(campaign_id: int, req: CampaignUpdateMessages):
    """Update campaign messages/settings (only when paused or draft)."""
    campaign = await db.get_dm_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign không tồn tại")
    if campaign["status"] not in ("draft", "paused", "error"):
        raise HTTPException(status_code=400,
                            detail="Chỉ có thể sửa campaign khi đang tạm dừng hoặc chưa chạy")
    if not req.messages:
        raise HTTPException(status_code=400, detail="Cần ít nhất 1 tin nhắn")

    msgs = [m if isinstance(m, dict) else m.dict() for m in req.messages]
    await db.update_dm_campaign_messages(
        campaign_id, msgs,
        delay_min=req.delay_min,
        delay_max=req.delay_max,
        daily_limit_premium=req.daily_limit_premium,
        daily_limit_normal=min(req.daily_limit_normal, 10) if req.daily_limit_normal is not None else None,
        use_ai_remix=req.use_ai_remix,
        exclude_previous_dms=req.exclude_previous_dms,
        ai_agent_id=req.ai_agent_id,
    )
    return {"status": "updated", "message": "Đã cập nhật tin nhắn campaign"}



@router.get("/campaigns/{campaign_id}/logs")
async def get_campaign_logs(campaign_id: int, limit: int = Query(200, ge=1, le=1000)):
    """Get logs for a campaign."""
    logs = await db.get_dm_campaign_logs(campaign_id, limit)
    return {"logs": logs}


# ── SpamBot Health Check Endpoints ──────────────────────────────────────────

@router.post("/accounts/{account_id}/spam-check")
async def check_account_spam(account_id: int):
    """Check spam status of a single account via @SpamBot."""
    result = await tg.check_spam_status(account_id)
    return {"account_id": account_id, **result}


@router.post("/accounts/spam-check-all")
async def check_all_accounts_spam():
    """Check spam status of all connected accounts via @SpamBot."""
    accounts = await db.get_all_accounts()
    results = []
    for acc in accounts:
        aid = acc["id"]
        client = tg.get_client(aid)
        if client and client.is_connected():
            result = await tg.check_spam_status(aid)
            results.append({"account_id": aid, "phone": acc.get("phone", ""), **result})
            # Small delay between checks to avoid rate-limiting SpamBot itself
            await asyncio.sleep(2)
        else:
            results.append({
                "account_id": aid,
                "phone": acc.get("phone", ""),
                "status": "unknown",
                "message": "Tài khoản chưa kết nối",
                "checked_at": datetime.now().isoformat(),
            })
    return {"results": results}


async def _run_campaign(campaign_id: int):
    """Background task: run a DM campaign, sending to each target member."""
    _active_campaigns[campaign_id] = True
    try:
        campaign = await db.get_dm_campaign(campaign_id)
        if not campaign:
            return

        members = await db.get_scraped_members(campaign["scrape_job_id"], limit=10000)
        sender_ids = campaign["sender_account_ids"]
        messages = campaign["messages"]
        delay_min = campaign["delay_min"]
        delay_max = campaign["delay_max"]
        limit_premium = campaign.get("daily_limit_premium", 60) or 60
        limit_normal = min(int(campaign.get("daily_limit_normal", 10) or 10), 10)
        use_ai = campaign["use_ai_remix"]

        # ── Smart Template Rotation: detect multi-variant messages ──
        # If messages is a list-of-lists, each sub-list is a variant.
        # If messages is a flat list of message dicts, treat as single variant.
        template_id = campaign.get("template_id")  # may be None
        is_multi_variant = (
            messages
            and isinstance(messages[0], list)
        )
        if not is_multi_variant:
            # Wrap single variant for uniform handling
            messages_variants = [messages]
        else:
            messages_variants = messages

        # Load AI settings - global provider/keys + agent-specific remix instruction
        ai_provider = None
        ai_keys = []
        ai_remix_kwargs = {}
        ai_custom_prompt = None
        if use_ai:
            # 1. Global settings for provider and keys
            ai_provider = await db.get_setting("ai_provider", None)
            ai_custom_prompt = await db.get_setting("ai_custom_prompt", None)

            async def _load_provider_keys(prov):
                if not prov:
                    return []
                try:
                    raw = await db.get_setting(f"ai_keys_{prov}", "[]")
                    return json.loads(raw) if raw else []
                except Exception:
                    return []

            if ai_provider:
                ai_keys = await _load_provider_keys(ai_provider)

            if not ai_keys:
                all_providers = ["chatgpt_oauth", "gemini", "groq", "openai", "deepseek", "openai_compatible"]
                for alt_prov in all_providers:
                    alt_keys = await _load_provider_keys(alt_prov)
                    if alt_keys:
                        logger.warning(
                            f"[Campaign {campaign_id}] ⚠️ Provider '{ai_provider}' không có API key. "
                            f"Tự động chuyển sang '{alt_prov}' ({len(alt_keys)} keys available)."
                        )
                        ai_provider = alt_prov
                        ai_keys = alt_keys
                        break

            if ai_provider == "openai_compatible":
                b_url = await db.get_setting("ai_oai_compat_base_url", "")
                mod = await db.get_setting("ai_oai_compat_model", "")
                if b_url and b_url.strip():
                    ai_remix_kwargs["base_url"] = b_url.strip()
                if mod and mod.strip():
                    ai_remix_kwargs["model"] = mod.strip()
            elif ai_provider == "chatgpt_oauth":
                b_url = await db.get_setting("ai_chatgpt_oauth_base_url", "")
                mod = await db.get_setting("ai_chatgpt_oauth_model", "")
                if b_url and b_url.strip():
                    ai_remix_kwargs["base_url"] = b_url.strip()
                if mod and mod.strip():
                    ai_remix_kwargs["model"] = mod.strip()

            # 2. Check for AI Agent override for remix prompt
            agent_id = campaign.get("ai_agent_id")
            if agent_id:
                agent = await db.get_ai_agent(agent_id)
                if agent:
                    agent_remix = agent.get("remix_instruction", "")
                    if agent_remix and agent_remix.strip():
                        ai_custom_prompt = agent_remix.strip()
                    logger.info(f"[Campaign {campaign_id}] 🤖 Using AI Agent '{agent['name']}' prompt instruction")
                else:
                    logger.warning(f"[Campaign {campaign_id}] ⚠️ AI Agent #{agent_id} not found")

            if not ai_keys:
                logger.warning(f"[Campaign {campaign_id}] ⚠️ AI Remix BẬT nhưng không tìm thấy API Key nào!")

        # Get already-sent user IDs for this campaign
        existing_logs = await db.get_dm_campaign_logs(campaign_id, limit=50000)
        sent_user_ids = {log["target_user_id"] for log in existing_logs if log["status"] == "success"}

        # ── Cross-campaign / watcher dedup pre-loading ──
        exclude_previous_dms = bool(campaign.get("exclude_previous_dms", 1))
        all_previous_user_ids: set[int] = set()
        all_previous_usernames: set[str] = set()

        if exclude_previous_dms:
            from database import get_db
            async with get_db() as conn:
                # From other DM campaigns (success only)
                cursor = await conn.execute(
                    "SELECT DISTINCT target_user_id, LOWER(target_username) FROM dm_campaign_logs "
                    "WHERE status = 'success' AND campaign_id != ?", (campaign_id,)
                )
                for row in await cursor.fetchall():
                    if row[0]:
                        all_previous_user_ids.add(row[0])
                    if row[1]:
                        all_previous_usernames.add(row[1])

                # From Watcher auto-DMs (success only)
                cursor = await conn.execute(
                    "SELECT DISTINCT target_user_id, LOWER(target_username) FROM watcher_dm_logs "
                    "WHERE status = 'success'"
                )
                for row in await cursor.fetchall():
                    if row[0]:
                        all_previous_user_ids.add(row[0])
                    if row[1]:
                        all_previous_usernames.add(row[1])

        # ── Exclude members from source campaigns (clone exclusion) ──
        exclude_campaign_ids = campaign.get("exclude_campaign_ids", []) or []
        if isinstance(exclude_campaign_ids, str):
            try:
                exclude_campaign_ids = json.loads(exclude_campaign_ids)
            except Exception:
                exclude_campaign_ids = []
        if exclude_campaign_ids:
            from database import get_db
            async with get_db() as conn:
                placeholders = ','.join('?' * len(exclude_campaign_ids))
                cursor = await conn.execute(
                    f"SELECT DISTINCT target_user_id FROM dm_campaign_logs "
                    f"WHERE campaign_id IN ({placeholders}) AND status IN ('success', 'failed')",
                    exclude_campaign_ids
                )
                for row in await cursor.fetchall():
                    if row[0]:
                        sent_user_ids.add(row[0])
            logger.info(
                f"[Campaign {campaign_id}] 🔗 Clone exclusion: loaded {len(exclude_campaign_ids)} source campaign(s), "
                f"total excluded user_ids now = {len(sent_user_ids)}"
            )

        sent = campaign.get("sent_count", 0)
        failed = campaign.get("failed_count", 0)
        skipped = campaign.get("skipped_count", 0)
        daily_sent = 0  # Track daily sends per session
        account_idx = 0  # Round-robin account index
        consecutive_errors = 0  # Track consecutive errors for backoff
        consecutive_ai_errors = 0  # Track consecutive AI remix failures
        flooded_accounts: dict[int, float] = {}

        # Pre-load blacklist ONCE (avoid N+1 query pattern)
        bl = await db.get_dm_blacklist()
        blacklisted_ids = {b["user_id"] for b in bl if b.get("user_id")}
        blacklisted_usernames = await db.get_blacklisted_usernames_set()

        # ── Pre-campaign SpamBot health check ──
        # Automatically exclude accounts that are currently spam-limited
        for sid in list(sender_ids):
            try:
                spam_result = await tg.check_spam_status(sid)
                if spam_result["status"] == "limited":
                    logger.warning(f"[Campaign {campaign_id}] ⚠️ Account {sid} is spam-limited, excluding from senders")
                    flooded_accounts[sid] = float('inf')
                    await db.add_dm_campaign_log(campaign_id, sid, 0, None, "skipped",
                                                f"SpamBot: tài khoản đang bị giới hạn — {spam_result['message'][:80]}")
                await asyncio.sleep(2)  # Delay between SpamBot checks
            except Exception as e:
                logger.warning(f"[Campaign {campaign_id}] SpamBot check failed for {sid}: {e}")

        # Check if any senders are still available after spam check
        current_time = time.time()
        available_after_check = [s for s in sender_ids if s not in flooded_accounts or current_time >= flooded_accounts[s]]
        if not available_after_check:
            logger.error(f"[Campaign {campaign_id}] 🛑 All sender accounts are spam-limited! Cannot start campaign.")
            await db.update_dm_campaign_status(campaign_id, "paused",
                                                sent=sent, failed=failed, skipped=skipped)
            _active_campaigns.pop(campaign_id, None)
            return

        # ── Pre-flight AI Remix / AI Agent verification ──
        # Ensure AI provider & keys are actively working before spending premium DM limit
        if use_ai:
            if not ai_keys:
                logger.error(f"[Campaign {campaign_id}] 🛑 AI Remix BẬT nhưng không có API Key hợp lệ nào! Tạm dừng chiến dịch.")
                await db.update_dm_campaign_status(campaign_id, "paused",
                                                    sent=sent, failed=failed, skipped=skipped)
                await db.add_dm_campaign_log(campaign_id, None, 0, None, "skipped",
                                            "🛑 AI Remix BẬT nhưng chưa cấu hình API Key. Đã tạm dừng chiến dịch để bảo vệ limit DM!")
                _active_campaigns.pop(campaign_id, None)
                return

            logger.info(f"[Campaign {campaign_id}] 🧪 Đang kiểm tra kết nối AI Remix ({ai_provider})...")
            try:
                test_sample = "Hello, are you available for a brief discussion?"
                test_res = await ai_rmx.remix_message(
                    original_text=test_sample,
                    provider=ai_provider,
                    api_keys=ai_keys,
                    sender_name="tester",
                    custom_instruction=ai_custom_prompt,
                    **ai_remix_kwargs
                )
                if not test_res or test_res.strip() == test_sample.strip():
                    logger.error(f"[Campaign {campaign_id}] 🛑 AI Remix ({ai_provider}) kiểm tra thất bại: API không phản hồi hoặc trả về nội dung rỗng. Tạm dừng để tránh lãng phí DM limit!")
                    await db.update_dm_campaign_status(campaign_id, "paused",
                                                        sent=sent, failed=failed, skipped=skipped)
                    await db.add_dm_campaign_log(campaign_id, None, 0, None, "skipped",
                                                f"🛑 AI Remix ({ai_provider}) kiểm tra thất bại (API Key lỗi hoặc hết quota). Tạm dừng chiến dịch để bảo vệ limit DM!")
                    _active_campaigns.pop(campaign_id, None)
                    return
                logger.info(f"[Campaign {campaign_id}] ✅ AI Remix ({ai_provider}) kiểm tra thành công! Bắt đầu chạy chiến dịch.")
            except Exception as test_e:
                logger.error(f"[Campaign {campaign_id}] 🛑 Lỗi khi kiểm tra AI Remix ({ai_provider}): {test_e}")
                await db.update_dm_campaign_status(campaign_id, "paused",
                                                    sent=sent, failed=failed, skipped=skipped)
                await db.add_dm_campaign_log(campaign_id, None, 0, None, "skipped",
                                            f"🛑 Lỗi kết nối AI Remix ({ai_provider}): {str(test_e)[:100]}. Tạm dừng chiến dịch!")
                _active_campaigns.pop(campaign_id, None)
                return

        # ── Sync total_targets in DB if changed ──
        _total_members = len(members)
        if campaign.get("total_targets") != _total_members:
            async with db.get_db() as conn:
                await conn.execute("UPDATE dm_campaigns SET total_targets = ? WHERE id = ?", (_total_members, campaign_id))
                await conn.commit()

        # ── Diagnostic logging: show filtering stats ──
        _already_sent = len(sent_user_ids)
        _cross_excluded = len(all_previous_user_ids) + len(all_previous_usernames)
        _blacklisted = len(blacklisted_ids) + len(blacklisted_usernames)
        logger.info(
            f"[Campaign {campaign_id}] 📊 Campaign start stats: "
            f"{_total_members} total members | "
            f"{_already_sent} already sent (this campaign) | "
            f"{'exclude_previous ON' if exclude_previous_dms else 'exclude_previous OFF'} "
            f"({len(all_previous_user_ids)} user_ids + {len(all_previous_usernames)} usernames from other campaigns) | "
            f"{_blacklisted} blacklisted ({len(blacklisted_ids)} ids + {len(blacklisted_usernames)} usernames) | "
            f"{len(available_after_check)} sender accounts available"
        )

        # Pre-compute daily limit across all sender accounts
        all_accs = await db.get_all_accounts()
        acc_prem_map = {a["id"]: bool(a.get("is_premium", 0)) for a in all_accs}
        total_daily_limit = sum(
            limit_premium if acc_prem_map.get(sid, False) else limit_normal
            for sid in sender_ids
        )
        acc_daily_count_map = {
            sid: await db.get_account_daily_dm_count(sid)
            for sid in sender_ids
        }


        for member in members:
            # Check if campaign was stopped
            if not _active_campaigns.get(campaign_id, False):
                logger.info(f"[Campaign {campaign_id}] Stopped by user")
                break


            user_id = member["user_id"]
            username = member.get("username")

            # ── Bot filter: Layer 1 (pre-send username / is_bot check) ──
            if member.get("is_bot") or tg.is_bot_account(None, username):
                skipped += 1
                await db.add_dm_campaign_log(campaign_id, None, user_id, username, "skipped",
                                            "Tài khoản là Telegram Bot (lọc tự động)")
                continue

            # Skip already sent in current campaign
            if user_id in sent_user_ids:
                continue

            # Skip previously sent in other campaigns/watchers if exclude_previous_dms is True
            if exclude_previous_dms:
                uname_lower = username.lower() if username else None
                if user_id in all_previous_user_ids or (uname_lower and uname_lower in all_previous_usernames):
                    skipped += 1
                    await db.add_dm_campaign_log(campaign_id, None, user_id, username, "skipped",
                                                "Đã từng nhận DM ở chiến dịch/watcher khác")
                    continue

            # Check daily limit across all sender accounts
            if daily_sent >= total_daily_limit:
                logger.info(f"[Campaign {campaign_id}] Total daily limit reached ({daily_sent}/{total_daily_limit}), stopping")
                await db.update_dm_campaign_status(campaign_id, "paused",
                                                    sent=sent, failed=failed, skipped=skipped)
                _active_campaigns.pop(campaign_id, None)
                return

            # Check blacklist (pre-loaded: user_id AND username)
            member_uname_lower = (username or "").lower()
            if user_id in blacklisted_ids or (member_uname_lower and member_uname_lower in blacklisted_usernames):
                skipped += 1
                await db.add_dm_campaign_log(campaign_id, None, user_id, username, "skipped", "Trong blacklist")
                continue

            # Pick sender account (round-robin, excluding flooded/offline ones)
            # Inner loop: try different accounts for this member until one works
            acc_id = None
            client = None
            all_accounts_exhausted = False
            while True:
                current_time = time.time()
                available_senders = [sid for sid in sender_ids if sid not in flooded_accounts or current_time >= flooded_accounts[sid]]
                if not available_senders:
                    logger.warning(f"[Campaign {campaign_id}] Tất cả các tài khoản gửi đều bị giới hạn/flood/offline. Tạm dừng chiến dịch.")
                    await db.update_dm_campaign_status(campaign_id, "paused",
                                                        sent=sent, failed=failed, skipped=skipped)
                    _active_campaigns.pop(campaign_id, None)
                    all_accounts_exhausted = True
                    break

                acc_id = available_senders[account_idx % len(available_senders)]
                account_idx += 1

                c = tg.get_client(acc_id)
                if not c or not await tg.ensure_connected(c, acc_id):
                    # Account offline and cannot reconnect → remove from rotation
                    flooded_accounts[acc_id] = float('inf')
                    logger.warning(f"[Campaign {campaign_id}] Account {acc_id} offline/unreachable, loại khỏi danh sách gửi")
                    await db.add_dm_campaign_log(campaign_id, acc_id, 0, None, "failed", f"Account {acc_id} offline")
                    continue  # Try next account in inner loop


                # Daily DM limit check per account
                is_premium = acc_prem_map.get(acc_id, False)
                dm_limit = limit_premium if is_premium else limit_normal
                dm_count = acc_daily_count_map.get(acc_id, 0)
                limit_reached = dm_count >= dm_limit
                if limit_reached:
                    logger.warning(f"[Campaign {campaign_id}] Account {acc_id} daily limit ({dm_count}/{dm_limit})")
                    flooded_accounts[acc_id] = float('inf')
                    await db.add_dm_campaign_log(campaign_id, acc_id, 0, None, "skipped",
                                                f"Account {acc_id} hết limit DM hàng ngày")
                    continue  # Try next account in inner loop

                # Found a working account
                client = c
                break

            if all_accounts_exhausted:
                # Already paused & popped — exit and return to prevent overwriting status
                return

            try:
                # Resolve peer using get_entity (safe & uses session cache)
                try:
                    if username:
                        peer = await client.get_entity(username)
                    else:
                        peer = await client.get_entity(user_id)
                except Exception as pe:
                    try:
                        from telethon.tl.types import PeerUser
                        peer = await client.get_entity(PeerUser(user_id))
                    except Exception as pe2:
                        skipped += 1
                        await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username,
                                                    "skipped", f"Không resolve được: {str(pe2)[:80]}")
                        await asyncio.sleep(base_delay)  # Always respect delay to avoid FloodWait
                        continue

                # ── Bot filter: Layer 2 (Telegram API peer verification) ──
                if tg.is_bot_account(peer, username):
                    skipped += 1
                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "skipped",
                                                "Tài khoản là Telegram Bot (xác nhận qua API)")
                    continue

                # ── Smart Template Rotation: select variant ──
                selected_msgs, variant_idx = await tmpl_rot.select_variant(
                    messages_variants,
                    template_id=template_id,
                    campaign_id=campaign_id,
                )

                # ── Consolidate multiple message bubbles into 1 cohesive message ──
                merged_msgs = merge_messages(selected_msgs)

                # Send messages (1 consolidated message / media with caption)
                for msg in merged_msgs:
                    content = msg.get("content", "")
                    msg_type = msg.get("msg_type", "text")

                    # ── Personalization: replace {name}, {first_name}, etc. ──
                    if content:
                        content = apply_personalization(content, {
                            "first_name": member.get("first_name"),
                            "last_name": member.get("last_name"),
                            "username": username,
                        })

                    # AI remix if enabled
                    if use_ai and content:
                        if ai_provider and ai_keys:
                            try:
                                original_len = len(content)
                                auto_native = bool(campaign.get("auto_translate_native", 1))
                                mem_info = {
                                    "first_name": member.get("first_name"),
                                    "last_name": member.get("last_name"),
                                    "username": username,
                                    "lang_code": member.get("lang_code")
                                }
                                remixed_content = await ai_rmx.remix_message(
                                    original_text=content,
                                    provider=ai_provider,
                                    api_keys=ai_keys,
                                    sender_name=username if username else member.get("first_name"),
                                    custom_instruction=ai_custom_prompt,
                                    auto_translate_native=auto_native,
                                    member_info=mem_info,
                                    **ai_remix_kwargs
                                )
                                if remixed_content and remixed_content != content:
                                    content = remixed_content
                                    consecutive_ai_errors = 0
                                    logger.info(f"[Campaign {campaign_id}] ✨ AI Remix thành công ({ai_provider}) cho @{username or user_id}")
                                else:
                                    consecutive_ai_errors += 1
                                    logger.warning(f"[Campaign {campaign_id}] ⚠️ AI Remix không thay đổi nội dung (lần {consecutive_ai_errors})")
                                    if consecutive_ai_errors >= 3:
                                        logger.error(f"[Campaign {campaign_id}] 🛑 AI Remix thất bại {consecutive_ai_errors} lần liên tiếp. Tạm dừng để bảo vệ quota DM!")
                                        await db.update_dm_campaign_status(campaign_id, "paused",
                                                                            sent=sent, failed=failed, skipped=skipped)
                                        await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed",
                                                                    "AI Remix không phản hồi liên tiếp 3 lần, tạm dừng chiến dịch để bảo vệ limit DM")
                                        _active_campaigns.pop(campaign_id, None)
                                        return
                            except Exception as ae:
                                consecutive_ai_errors += 1
                                logger.warning(f"[Campaign {campaign_id}] ⚠️ AI Remix thất bại ({ae})")
                                if consecutive_ai_errors >= 3:
                                    logger.error(f"[Campaign {campaign_id}] 🛑 AI Remix gặp lỗi liên tiếp {consecutive_ai_errors} lần ({ae}). Tạm dừng!")
                                    await db.update_dm_campaign_status(campaign_id, "paused",
                                                                        sent=sent, failed=failed, skipped=skipped)
                                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed",
                                                                f"AI Remix lỗi liên tiếp: {str(ae)[:80]}")
                                    _active_campaigns.pop(campaign_id, None)
                                    return
                        else:
                            logger.warning(f"[Campaign {campaign_id}] ⚠️ AI Remix BẬT nhưng thiếu API Key (vui lòng kiểm tra Cài Đặt AI Remix)")

                    # ── Simulate realistic typing action (human-like behavior) ──
                    try:
                        typing_action = "typing" if msg_type == "text" else "photo"
                        typing_duration = min(max(len(content or "") * 0.012, 1.5), 3.5)
                        if hasattr(client, "action") and callable(client.action):
                            async with client.action(peer, typing_action):
                                await asyncio.sleep(typing_duration)
                    except Exception:
                        pass

                    if msg_type == "text":
                        await client.send_message(peer, content)
                    elif msg_type in ("photo", "video", "document"):
                        media_path = msg.get("media_path")
                        if media_path:
                            # Randomize image to change hash (anti-spam)
                            rand_path = None
                            actual_path = media_path
                            if msg_type == "photo":
                                rand_path = img_rand.randomize_image(media_path)
                                actual_path = rand_path
                            try:
                                await client.send_file(peer, actual_path, caption=content)
                            finally:
                                if rand_path:
                                    img_rand.cleanup_temp_image(rand_path, media_path)
                        elif content:
                            await client.send_message(peer, content)

                sent += 1
                daily_sent += 1
                acc_daily_count_map[acc_id] = acc_daily_count_map.get(acc_id, 0) + 1
                consecutive_errors = 0  # Reset on success
                await db.add_dm_campaign_log(
                    campaign_id, acc_id, user_id, username, "success",
                    template_variant_id=template_id,
                    template_variant_index=variant_idx,
                )
                # Record send for template performance tracking
                if template_id:
                    try:
                        await tmpl_rot.record_send(
                            template_id, variant_idx, campaign_id=campaign_id
                        )
                    except Exception:
                        pass  # Non-critical — don't break send loop
                logger.info(f"[Campaign {campaign_id}] Sent to {username or user_id} via account {acc_id} [{sent}/{len(members)}] (variant={variant_idx})")

            except tg_errors.FloodWaitError as e:
                # ── CRITICAL: Respect Telegram's FloodWait timer exactly ──
                wait_time = e.seconds + random.randint(5, 15)  # Add buffer
                logger.warning(f"[Campaign {campaign_id}] ⏳ FloodWait on account {acc_id}: waiting {wait_time}s (Telegram requested {e.seconds}s)")
                await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed",
                                            f"FloodWait: chờ {wait_time}s")
                flooded_accounts[acc_id] = time.time() + wait_time
                failed += 1
                consecutive_errors += 1
                continue

            except tg_errors.UserPrivacyRestrictedError:
                # User has privacy settings blocking DMs
                skipped += 1
                try:
                    await db.add_to_dm_blacklist(user_id, username, "Privacy restricted")
                except Exception:
                    pass
                await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "skipped",
                                            "User chặn tin nhắn (Privacy Restricted)")

            except tg_errors.InputUserDeactivatedError:
                # User account is deactivated
                skipped += 1
                try:
                    await db.add_to_dm_blacklist(user_id, username, "Account deactivated")
                except Exception:
                    pass
                await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "skipped",
                                            "Tài khoản đã bị xóa/vô hiệu hóa")

            except tg_errors.PeerFloodError:
                # Account is globally rate-limited — VERY DANGEROUS
                logger.error(f"[Campaign {campaign_id}] 🚨 PeerFlood on account {acc_id}! Tạm loại khỏi danh sách gửi.")
                await alerts.flood_guard(acc_id)  # auto-pause + cảnh báo Telegram
                flooded_accounts[acc_id] = float('inf')  # loại khỏi rotation campaign này
                failed += 1
                consecutive_errors += 1
                await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed",
                                            "PeerFlood — tài khoản bị giới hạn, đã tự động tắt")
                continue

            except Exception as e:
                err_str = str(e)
                err_lower = err_str.lower()
                logger.warning(f"[Campaign {campaign_id}] Error sending to {user_id}: {err_str}")

                # ── Auto-join detection ────────────────────────────────────
                # "You join the discussion group before commenting" = account not in group
                # This is NOT a user-level error — don't blacklist!
                if ("join" in err_lower and ("discussion" in err_lower or "group" in err_lower)) or \
                   "chatwriteforbidden" in err_lower.replace("_", "").replace(" ", ""):
                    logger.warning(
                        f"[Campaign {campaign_id}] Account {acc_id} not in source group — auto-joining..."
                    )
                    # Get group_id from scrape job
                    try:
                        source_group_id = None
                        scrape_members = await db.get_scraped_members(campaign["scrape_job_id"], limit=1)
                        if scrape_members:
                            source_group_id = scrape_members[0].get("group_id")
                        
                        if source_group_id:
                            join_result = await tg.join_channel(acc_id, str(source_group_id))
                            if join_result.get("success"):
                                logger.info(
                                    f"[Campaign {campaign_id}] ✓ Account {acc_id} auto-joined group "
                                    f"{source_group_id} ({join_result.get('title', '?')}). "
                                    f"Retrying DM to {user_id}..."
                                )
                                await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed",
                                                            "Đã auto-join nhóm, sẽ retry ở vòng tiếp")
                                # Don't increment failures — this is recoverable
                                await asyncio.sleep(base_delay)
                                continue
                            else:
                                logger.warning(
                                    f"[Campaign {campaign_id}] Auto-join failed: {join_result.get('error')}"
                                )
                        else:
                            logger.warning(f"[Campaign {campaign_id}] Could not determine source group_id")
                    except Exception as je:
                        logger.warning(f"[Campaign {campaign_id}] Auto-join exception: {je}")
                    
                    # If auto-join failed, skip but don't blacklist
                    skipped += 1
                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "skipped",
                                                "Cần join nhóm trước — không thể auto-join")
                    await asyncio.sleep(base_delay)
                    continue

                # ── Skip + blacklist non-sendable errors (don't retry) ──
                skip_patterns = [
                    "PRIVACY_PREMIUM_REQUIRED",
                    "UserPrivacyRestricted",
                    "UserDeactivated",
                    "UserIsBot",
                    "UserBannedInChannel",
                    "UserBlocked",
                    "InputUserDeactivated",
                    "can't write in this chat",
                    "You can't send messages",
                    "PEER_ID_INVALID",
                    "USER_ID_INVALID",
                    "CHAT_SEND_PLAIN_FORBIDDEN",
                    "CHAT_GUEST_SEND_FORBIDDEN",
                ]
                if any(p.lower() in err_lower for p in skip_patterns):
                    skipped += 1
                    consecutive_errors = 0  # Target-side issue, not our account's fault
                    try:
                        await db.add_to_dm_blacklist(user_id, username, f"Auto: {err_str[:50]}")
                    except Exception:
                        pass
                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "skipped", err_str[:100])
                elif "FloodWait" in err_str or "Too many requests" in err_str or "PeerFlood" in err_str:
                    # Catch string-based flood errors too
                    failed += 1
                    consecutive_errors += 1
                    # Try to extract wait time from error string
                    import re
                    wait_match = re.search(r'(\d+)', err_str)
                    wait_secs = int(wait_match.group(1)) if wait_match else 60
                    wait_secs = min(max(wait_secs, 30), 600)  # Clamp 30s-600s
                    logger.warning(f"[Campaign {campaign_id}] Flood detected on {acc_id}, waiting {wait_secs}s")
                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed",
                                                f"Flood: chờ {wait_secs}s")
                    flooded_accounts[acc_id] = time.time() + wait_secs
                    continue
                else:
                    failed += 1
                    consecutive_errors += 1
                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed", err_str[:100])

            # Update progress in DB real-time on every iteration
            await db.update_dm_campaign_status(campaign_id, "running",
                                                sent=sent, failed=failed, skipped=skipped)

            # ── Smart delay with exponential backoff (anti-ban) ──
            base_delay = random.uniform(delay_min, delay_max)
            # Increase delay when encountering consecutive errors
            if consecutive_errors > 0:
                backoff_multiplier = min(2 ** consecutive_errors, 16)  # Cap at 16x
                base_delay = base_delay * backoff_multiplier
                base_delay = min(base_delay, 300)  # Max 5 minutes
                logger.info(f"[Campaign {campaign_id}] ⚠️ Backoff x{backoff_multiplier}: waiting {base_delay:.0f}s (consecutive errors: {consecutive_errors})")
            else:
                logger.info(f"[Campaign {campaign_id}] Waiting {base_delay:.0f}s before next DM")

            # Auto-pause if too many consecutive errors (protect accounts)
            if consecutive_errors >= 10:
                logger.error(f"[Campaign {campaign_id}] 🛑 {consecutive_errors} consecutive errors! Auto-pausing campaign to protect accounts.")
                await db.update_dm_campaign_status(campaign_id, "paused",
                                                    sent=sent, failed=failed, skipped=skipped)
                _active_campaigns.pop(campaign_id, None)
                logger.info(f"[Campaign {campaign_id}] Finished: paused (consecutive errors) — {sent} sent, {failed} failed, {skipped} skipped")
                return

            await asyncio.sleep(base_delay)

        # Campaign completed
        if not _active_campaigns.get(campaign_id):
            final_status = "paused"  # Stopped by user
        elif sent == 0:
            final_status = "paused"  # Nothing was sent — likely all accounts offline/limited
            logger.warning(f"[Campaign {campaign_id}] ⚠️ 0 tin nhắn gửi thành công. Đánh dấu tạm dừng thay vì hoàn thành.")
        else:
            final_status = "completed"
        _active_campaigns.pop(campaign_id, None)
        await db.update_dm_campaign_status(campaign_id, final_status,
                                            sent=sent, failed=failed, skipped=skipped)
        logger.info(f"[Campaign {campaign_id}] Finished: {final_status} — {sent} sent, {failed} failed, {skipped} skipped (out of {len(members)} total members)")

    except Exception as e:
        logger.error(f"[Campaign {campaign_id}] Fatal error: {e}", exc_info=True)
        _active_campaigns.pop(campaign_id, None)
        await db.update_dm_campaign_status(campaign_id, "error")
