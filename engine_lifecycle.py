"""Shared startup/shutdown lifecycle for Telegram engines, scheduler jobs, and Discord.

Used by both main.py (combined mode) and telegram_worker.py so they run identical
engine init and teardown sequences. No IPC logic — that belongs to Phase 2.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import database as db

logger = logging.getLogger("tg-scheduler.lifecycle")

# ── Singleton file lock ──────────────────────────────────────────────────────
# Prevents combined+worker or two workers from owning the same DATA_DIR.
# Uses msvcrt.locking on Windows, fcntl.flock on POSIX — both stdlib.

_lock_fd = None


def acquire_data_lock() -> None:
    """Acquire an exclusive OS file lock on DATA_DIR/engine.lock.

    Raises RuntimeError if another process already holds it.
    Must be called BEFORE any engine or Telethon session initialization.
    """
    global _lock_fd
    if _lock_fd is not None:
        raise RuntimeError("Engine ownership already acquired in this process")
    data_dir = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
    os.makedirs(data_dir, exist_ok=True)
    lock_path = os.path.join(data_dir, "engine.lock")
    _lock_fd = open(lock_path, "a+")
    try:
        _lock_fd.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(_lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.truncate(0)
        _lock_fd.write(f"{os.getpid()}\n")
        _lock_fd.flush()
    except (OSError, IOError) as exc:
        _lock_fd.close()
        _lock_fd = None
        raise RuntimeError(
            f"Another engine process already owns {lock_path}. "
            "Cannot run combined+worker or two workers on the same DATA_DIR."
        ) from exc


def release_data_lock() -> None:
    """Release the OS file lock. Call after all cleanup is finished."""
    global _lock_fd
    if _lock_fd is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt
            try:
                _lock_fd.seek(0)
                msvcrt.locking(_lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
    finally:
        try:
            _lock_fd.close()
        except OSError:
            pass
        _lock_fd = None


# ── Heartbeat ────────────────────────────────────────────────────────────────
_HEARTBEAT_KEY = "telegram_worker_status"
_STALE_SECONDS = 60  # heartbeat older than this → stale


def _status_payload(state: str, *, error: str | None = None, version: str | None = None) -> str:
    value = {
        "state": state,
        "pid": os.getpid(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        value["error"] = error[:500]
    if version:
        value["version"] = version
    return json.dumps(value)


async def write_status(state: str, *, error: str | None = None) -> None:
    await db.set_setting(_HEARTBEAT_KEY, _status_payload(state, error=error))


async def read_worker_health() -> dict:
    """Read + classify worker health for the /api/health endpoint."""
    raw = await db.get_setting(_HEARTBEAT_KEY, "")
    if not raw:
        return {"state": "unknown"}
    try:
        status = json.loads(raw)
    except (TypeError, ValueError):
        return {"state": "invalid", "reason": "malformed_json"}
    if not isinstance(status, dict):
        return {"state": "invalid", "reason": "non_object_json"}
    updated = status.get("updated_at")
    if updated is None:
        status["timestamp_status"] = "missing"
        return status
    try:
        ts = datetime.fromisoformat(updated)
        if ts.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        status["timestamp_status"] = "valid"
        status["age_seconds"] = round(age, 1)
        if age > _STALE_SECONDS:
            status["stale"] = True
    except (ValueError, TypeError):
        status["timestamp_status"] = "invalid"
    return status


# ── Engine startup (shared between combined lifespan and worker) ────────────

async def start_engines() -> asyncio.Task:
    """Start scheduler, command bot, campaign restore, daily summary, backup,
    SLA check, message queue, and spawn the background account/watcher connector.

    Returns the background connect task so the caller can track/cancel it.
    """
    import scheduler as sch
    import message_queue as mq

    sch.start_scheduler()
    sch.start_auto_resume_job()

    # Command bot
    try:
        import command_bot
        await command_bot.start_command_bot()
    except Exception as e:
        logger.warning("Command bot startup error: %s", e)

    await sch.load_all_jobs()

    # Reload scheduled DM campaigns
    scheduled_campaigns = await db.get_scheduled_campaigns()
    for sc in scheduled_campaigns:
        if sc["scheduled_at"] and sc["target_timezone"]:
            sch.add_campaign_schedule_job(sc["id"], sc["scheduled_at"], sc["target_timezone"])
    if scheduled_campaigns:
        logger.info("Reloaded %d scheduled DM campaigns", len(scheduled_campaigns))

    # Auto-resume running DM campaigns
    try:
        all_campaigns = await db.get_all_dm_campaigns()
        running_cnt = 0
        for rc in all_campaigns:
            if rc.get("status") == "running":
                from routes.members import _run_campaign, _active_campaigns
                curr_task = _active_campaigns.get(rc["id"])
                is_running = curr_task is True or (isinstance(curr_task, asyncio.Task) and not curr_task.done())
                if not is_running:
                    logger.info("Auto-resuming running DM campaign #%s (%s)...", rc["id"], rc["name"])
                    _active_campaigns[rc["id"]] = asyncio.create_task(_run_campaign(rc["id"]))
                    running_cnt += 1
        if running_cnt:
            logger.info("Auto-resumed %d active running DM campaigns", running_cnt)
    except Exception as e:
        logger.warning("Error auto-resuming DM campaigns: %s", e)

    # Daily summary
    from daily_summary import send_daily_summary
    from apscheduler.triggers.cron import CronTrigger
    summary_time = await db.get_setting("daily_summary_time", "21:00")
    hour, minute = map(int, summary_time.split(":"))
    sch.get_scheduler().add_job(
        send_daily_summary,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=sch.TZ),
        id="daily_summary",
        name="Daily Summary",
        replace_existing=True,
    )
    logger.info("Daily summary scheduled at %s", summary_time)

    # DB backup
    import alerts
    alerts.run_backup()
    sch.get_scheduler().add_job(
        alerts.run_backup,
        trigger=CronTrigger(hour=3, minute=0, timezone=sch.TZ),
        id="db_backup",
        name="DB Backup",
        replace_existing=True,
    )
    logger.info("DB backup scheduled daily at 03:00")

    # Lead SLA check
    from apscheduler.triggers.interval import IntervalTrigger
    sch.get_scheduler().add_job(
        alerts.check_lead_sla,
        trigger=IntervalTrigger(minutes=30),
        id="lead_sla_check",
        name="Lead SLA Check",
        replace_existing=True,
    )
    logger.info("Lead SLA check scheduled every 30 min")

    # Message queue worker
    mq.start_worker()

    # Background connect task
    connect_task = asyncio.create_task(_connect_accounts_and_watchers())
    return connect_task


async def _connect_accounts_and_watchers() -> None:
    """Connect Telegram accounts + start all watchers + Discord bots."""
    import telegram_client as tg
    import keyword_watcher as kw
    import reaction_watcher as rw
    import dm_reply_tracker as drt
    import kol_channel_watcher as kcw

    accounts = await db.get_all_accounts()

    async def connect_single(acc):
        try:
            proxy_url = acc.get("proxy_url")
            await tg.create_client(
                acc["id"], int(acc["api_id"]), acc["api_hash"],
                acc["session_name"], proxy_url=proxy_url,
            )
            authorized = await asyncio.wait_for(tg.start_client(acc["id"]), timeout=30)
            await db.update_account_login_status(acc["id"], bool(authorized))
            return bool(authorized)
        except asyncio.TimeoutError:
            logger.warning("Account %s (%s): connect timed out after 30s", acc["id"], acc["name"])
            await db.update_account_login_status(acc["id"], False)
        except Exception as e:
            logger.warning("Account %s (%s): connect failed: %s", acc["id"], acc["name"], e)
            await db.update_account_login_status(acc["id"], False)
        return False

    results = await asyncio.gather(*(connect_single(acc) for acc in accounts), return_exceptions=True)
    logged_count = sum(1 for r in results if r is True)
    logger.info("Loaded %d accounts, %d successfully logged in", len(accounts), logged_count)

    await kw.start_all_watchers()
    await rw.start_all()
    await drt.start_reply_tracker()
    await kcw.start_kol_channel_watcher()

    # Discord bots (optional dependency)
    try:
        from platforms.discord_adapter import DiscordAdapter
        import discord_watcher as dw
        import discord_reaction_watcher as drw
        import discord_reply_tracker as drt_discord
        from routes import discord as discord_routes

        adapter = DiscordAdapter()
        discord_routes._adapter = adapter
        dw.set_adapter(adapter)
        drw.set_adapter(adapter)
        drt_discord.set_adapter(adapter)

        discord_bots = await db.get_all_discord_bots()
        for bot in discord_bots:
            try:
                success = await adapter.connect_bot(bot["id"], bot["bot_token"])
                if success:
                    info = await adapter.get_account_info(bot["id"])
                    await db.update_discord_bot_status(
                        bot["id"], True,
                        user_id=str(info.get("user_id", "")),
                        username=info.get("username", ""),
                        guild_count=info.get("guild_count", 0),
                    )
                    logger.info("Discord bot %s (%s): connected", bot["id"], bot["name"])
                else:
                    logger.warning("Discord bot %s (%s): connect failed", bot["id"], bot["name"])
            except Exception as e:
                logger.warning("Discord bot %s (%s): %s", bot["id"], bot["name"], e)
        logger.info("Discord: %d bots loaded", len(discord_bots))

        await dw.start_all_watchers()
        await drw.start_all()
        await drt_discord.start_reply_tracker()
        logger.info("Discord engines started (watcher + reaction + reply)")
    except ImportError:
        logger.info("Discord adapter not available (discord.py not installed)")
    except Exception as e:
        logger.warning("Discord startup error: %s", e)

    logger.info("All background engines started successfully.")


# ── Engine shutdown ─────────────────────────────────────────────────────────

async def stop_engines(connect_task: asyncio.Task | None) -> None:
    """Inverse of start_engines. Await task cancellations; preserve error state."""
    import message_queue as mq
    import scheduler as sch
    import reaction_watcher as rw
    import dm_reply_tracker as drt
    import telegram_client as tg

    # Cancel and AWAIT the connect task
    if connect_task and not connect_task.done():
        connect_task.cancel()
        try:
            await connect_task
        except (asyncio.CancelledError, Exception):
            pass

    mq.stop_worker()
    sch.stop_scheduler()
    await rw.stop_all()
    await drt.stop_reply_tracker()
    await tg.disconnect_all()

    # Discord shutdown
    try:
        import discord_watcher as dw
        import discord_reaction_watcher as drw
        import discord_reply_tracker as drt_discord
        await dw.stop_all_watchers()
        await drw.stop_all()
        await drt_discord.stop_reply_tracker()
    except Exception:
        pass
    try:
        from routes import discord as discord_routes
        if discord_routes._adapter:
            await discord_routes._adapter.disconnect_all()
    except Exception:
        pass

    # Command bot shutdown
    try:
        import command_bot
        await command_bot.stop_command_bot()
    except Exception:
        pass
