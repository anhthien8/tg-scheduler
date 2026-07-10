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
import ai_remix as ai_rmx

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


class CampaignCreate(BaseModel):
    name: str
    scrape_job_id: str
    sender_account_ids: list[int]
    messages: list[dict]  # [{msg_type, content, media_path}]
    delay_min: int = 30
    delay_max: int = 90
    daily_limit: int = 30
    use_ai_remix: bool = False


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


# ── Member Scraping ─────────────────────────────────────────────────────────

@router.post("/scrape")
async def scrape_members(req: ScrapeRequest, background_tasks: BackgroundTasks):
    """Start scraping members from a Telegram group."""
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
                if exclude_bots and getattr(sender, "bot", False):
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
                    if exclude_bots and getattr(user, "bot", False):
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

                            if exclude_bots and getattr(user, "bot", False):
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
    await db.delete_scrape_job(scrape_job_id)
    return {"status": "deleted"}


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
    """Import selected contacts into the scraped_members table under a specific job."""
    try:
        import zlib
        members_list = []
        for c in req.contacts:
            username = c.get("username", "").strip()
            if username.startswith("@"):
                username = username[1:]
            if not username:
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
        if members_list:
            await db.save_scraped_members(
                scrape_job_id=req.scrape_job_id,
                account_id=0,
                group_id=0,
                group_title=req.group_title,
                members=members_list
            )
        return {"success": True, "count": len(members_list)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Deep Crawl (BFS Multi-Layer) ─────────────────────────────────────────────

@router.post("/deep-crawl")
async def start_deep_crawl(req: DeepCrawlRequest, bg: BackgroundTasks):
    """Start a deep BFS crawl of similar channels (1-4 layers)."""
    global _deep_crawl_state, _deep_crawl_stop_flag

    if _deep_crawl_state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Deep crawl đang chạy. Vui lòng dừng trước khi bắt đầu mới.")

    if req.max_depth < 1 or req.max_depth > 4:
        raise HTTPException(status_code=400, detail="Độ sâu phải từ 1 đến 4.")

    # Reset state
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
    }
    _deep_crawl_stop_flag = {"stopped": False}

    bg.add_task(_do_deep_crawl, req.account_ids, req.channel_link, req.max_depth)
    return {"success": True, "message": f"Deep crawl started: {req.max_depth} layers"}


async def _do_deep_crawl(account_ids: list[int], channel_link: str, max_depth: int):
    """Background task that runs the BFS deep crawl."""
    global _deep_crawl_state

    async def _progress_cb(state: dict):
        """Callback to update module-level state for polling."""
        _deep_crawl_state.update(state)

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
        logger.info(f"[DeepCrawl] Background task complete. {len(results)} leads.")
    except Exception as e:
        _deep_crawl_state["status"] = "error"
        _deep_crawl_state["errors"].append(f"Fatal: {str(e)}")
        logger.error(f"[DeepCrawl] Fatal error: {e}", exc_info=True)


@router.get("/deep-crawl/status")
async def get_deep_crawl_status():
    """Poll the current deep crawl progress."""
    # Return state without the full results array to keep response small during polling
    state_copy = {k: v for k, v in _deep_crawl_state.items() if k != "results"}
    state_copy["results_count"] = len(_deep_crawl_state.get("results", []))
    return state_copy


@router.get("/deep-crawl/results")
async def get_deep_crawl_results():
    """Get the full results of the last deep crawl."""
    return {
        "status": _deep_crawl_state.get("status"),
        "leads": _deep_crawl_state.get("results", []),
        "total": len(_deep_crawl_state.get("results", [])),
    }


@router.post("/deep-crawl/stop")
async def stop_deep_crawl():
    """Stop the running deep crawl gracefully."""
    global _deep_crawl_stop_flag
    if _deep_crawl_state.get("status") != "running":
        return {"success": False, "message": "Không có deep crawl nào đang chạy."}
    _deep_crawl_stop_flag["stopped"] = True
    return {"success": True, "message": "Đang dừng deep crawl..."}


# ── DM Campaigns ────────────────────────────────────────────────────────────

@router.post("/campaigns")
async def create_campaign(req: CampaignCreate):
    """Create a new DM campaign."""
    # Validate scrape job exists
    members = await db.get_scraped_members(req.scrape_job_id, limit=1)
    if not members:
        raise HTTPException(status_code=400, detail="Scrape job không tồn tại hoặc trống")

    # Count total targets
    all_members = await db.get_scraped_members(req.scrape_job_id, limit=10000)
    total = len(all_members)

    campaign_id = await db.create_dm_campaign({
        "name": req.name,
        "scrape_job_id": req.scrape_job_id,
        "sender_account_ids": req.sender_account_ids,
        "messages": [m if isinstance(m, dict) else m.dict() for m in req.messages],
        "delay_min": req.delay_min,
        "delay_max": req.delay_max,
        "daily_limit": req.daily_limit,
        "use_ai_remix": req.use_ai_remix,
        "total_targets": total,
    })

    return {"status": "created", "campaign_id": campaign_id, "total_targets": total}


@router.get("/campaigns")
async def list_campaigns():
    """List all DM campaigns."""
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
        raise HTTPException(status_code=400, detail="Campaign đang chạy")

    # Mark as running
    await db.update_dm_campaign_status(campaign_id, "running")
    _active_campaigns[campaign_id] = True

    background_tasks.add_task(_run_campaign, campaign_id)

    return {"status": "started", "message": "Campaign đã bắt đầu chạy"}


@router.post("/campaigns/{campaign_id}/stop")
async def stop_campaign(campaign_id: int):
    """Stop a running campaign."""
    _active_campaigns[campaign_id] = False
    await db.update_dm_campaign_status(campaign_id, "paused")
    return {"status": "stopped"}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int):
    """Delete a campaign."""
    _active_campaigns.pop(campaign_id, None)
    await db.delete_dm_campaign(campaign_id)
    return {"status": "deleted"}


@router.get("/campaigns/{campaign_id}/logs")
async def get_campaign_logs(campaign_id: int, limit: int = Query(200, ge=1, le=1000)):
    """Get logs for a campaign."""
    logs = await db.get_dm_campaign_logs(campaign_id, limit)
    return {"logs": logs}


async def _run_campaign(campaign_id: int):
    """Background task: run a DM campaign, sending to each target member."""
    try:
        campaign = await db.get_dm_campaign(campaign_id)
        if not campaign:
            return

        members = await db.get_scraped_members(campaign["scrape_job_id"], limit=10000)
        sender_ids = campaign["sender_account_ids"]
        messages = campaign["messages"]
        delay_min = campaign["delay_min"]
        delay_max = campaign["delay_max"]
        daily_limit = campaign["daily_limit"]
        use_ai = campaign["use_ai_remix"]

        # Load AI settings if enabled
        ai_provider = None
        ai_keys = []
        if use_ai:
            ai_provider = await db.get_setting("ai_provider", None)
            if ai_provider:
                try:
                    raw = await db.get_setting(f"ai_keys_{ai_provider}", "[]")
                    ai_keys = json.loads(raw) if raw else []
                except Exception:
                    ai_keys = []

        # Get already-sent user IDs for this campaign
        existing_logs = await db.get_dm_campaign_logs(campaign_id, limit=50000)
        sent_user_ids = {log["target_user_id"] for log in existing_logs if log["status"] == "success"}

        sent = campaign.get("sent_count", 0)
        failed = campaign.get("failed_count", 0)
        skipped = campaign.get("skipped_count", 0)
        daily_sent = 0  # Track daily sends per session
        account_idx = 0  # Round-robin account index
        flooded_accounts = set()

        for member in members:
            # Check if campaign was stopped
            if not _active_campaigns.get(campaign_id, False):
                logger.info(f"[Campaign {campaign_id}] Stopped by user")
                break

            user_id = member["user_id"]
            username = member.get("username")

            # Skip already sent
            if user_id in sent_user_ids:
                continue

            # Check daily limit
            if daily_sent >= daily_limit:
                logger.info(f"[Campaign {campaign_id}] Daily limit reached ({daily_limit}), stopping")
                await db.update_dm_campaign_status(campaign_id, "paused",
                                                    sent=sent, failed=failed, skipped=skipped)
                return

            # Check blacklist
            bl = await db.get_dm_blacklist()
            blacklisted_ids = {b["user_id"] for b in bl}
            if user_id in blacklisted_ids:
                skipped += 1
                await db.add_dm_campaign_log(campaign_id, None, user_id, username, "skipped", "Trong blacklist")
                continue

            # Pick sender account (round-robin, excluding flooded ones)
            available_senders = [sid for sid in sender_ids if sid not in flooded_accounts]
            if not available_senders:
                logger.warning(f"[Campaign {campaign_id}] Tất cả các tài khoản gửi đều bị giới hạn/flood. Tạm dừng chiến dịch.")
                await db.update_dm_campaign_status(campaign_id, "paused",
                                                    sent=sent, failed=failed, skipped=skipped)
                break

            acc_id = available_senders[account_idx % len(available_senders)]
            account_idx += 1

            client = tg.get_client(acc_id)
            if not client or not client.is_connected():
                skipped += 1
                await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "skipped", "Account offline")
                continue

            # Daily DM limit check per account
            limit_reached, dm_count, dm_limit = await db.is_account_dm_limit_reached(acc_id)
            if limit_reached:
                logger.warning(f"[Campaign {campaign_id}] Account {acc_id} daily limit ({dm_count}/{dm_limit})")
                skipped += 1
                await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "skipped",
                                            f"Account {acc_id} hết limit DM hàng ngày")
                continue

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
                        continue

                # Send messages
                for msg in sorted(messages, key=lambda m: m.get("msg_order", 0)):
                    content = msg.get("content", "")
                    msg_type = msg.get("msg_type", "text")

                    # AI remix if enabled
                    if use_ai and ai_provider and ai_keys and content:
                        try:
                            content = await ai_rmx.remix_message(
                                original_text=content,
                                provider=ai_provider,
                                api_keys=ai_keys,
                                sender_name=username if username else None
                            )
                        except Exception as ae:
                            logger.warning(f"[Campaign {campaign_id}] AI remix failed: {ae}")

                    if msg_type == "text":
                        await client.send_message(peer, content)
                    elif msg_type in ("photo", "video", "document"):
                        media_path = msg.get("media_path")
                        if media_path:
                            await client.send_file(peer, media_path, caption=content)
                        elif content:
                            await client.send_message(peer, content)

                    # Small delay between messages in sequence
                    if len(messages) > 1:
                        await asyncio.sleep(random.uniform(2, 5))

                sent += 1
                daily_sent += 1
                await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "success")
                logger.info(f"[Campaign {campaign_id}] Sent to {username or user_id} via account {acc_id} [{sent}/{len(members)}]")

            except Exception as e:
                err_str = str(e)
                logger.warning(f"[Campaign {campaign_id}] Error sending to {user_id}: {err_str}")

                # Auto-blacklist on privacy errors
                if "UserPrivacyRestricted" in err_str or "UserDeactivated" in err_str:
                    try:
                        await db.add_to_dm_blacklist(user_id, username, f"Campaign auto: {err_str[:50]}")
                    except Exception:
                        pass
                    skipped += 1
                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "skipped", err_str[:100])
                elif "PeerFlood" in err_str or "FloodWait" in err_str:
                    # Account-level error — mark as flooded to exclude from rotation
                    flooded_accounts.add(acc_id)
                    failed += 1
                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed", f"Flood: {err_str[:80]}")
                    logger.warning(f"[Campaign {campaign_id}] Account {acc_id} rate-limited/flooded. Excluded from this run.")
                else:
                    failed += 1
                    await db.add_dm_campaign_log(campaign_id, acc_id, user_id, username, "failed", err_str[:100])

            # Update progress periodically
            if (sent + failed + skipped) % 5 == 0:
                await db.update_dm_campaign_status(campaign_id, "running",
                                                    sent=sent, failed=failed, skipped=skipped)

            # Random delay between DMs (anti-ban)
            delay = random.uniform(delay_min, delay_max)
            logger.info(f"[Campaign {campaign_id}] Waiting {delay:.0f}s before next DM")
            await asyncio.sleep(delay)

        # Campaign completed
        final_status = "completed" if _active_campaigns.get(campaign_id) else "paused"
        _active_campaigns.pop(campaign_id, None)
        await db.update_dm_campaign_status(campaign_id, final_status,
                                            sent=sent, failed=failed, skipped=skipped)
        logger.info(f"[Campaign {campaign_id}] Finished: {sent} sent, {failed} failed, {skipped} skipped")

    except Exception as e:
        logger.error(f"[Campaign {campaign_id}] Fatal error: {e}", exc_info=True)
        _active_campaigns.pop(campaign_id, None)
        await db.update_dm_campaign_status(campaign_id, "error")
