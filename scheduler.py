"""
Scheduler - APScheduler with hourly, daily, weekly, monthly, once.
"""
import logging
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from datetime import datetime

import database as db
import message_queue as mq

logger = logging.getLogger("tg-scheduler")
TZ = pytz.timezone("Asia/Ho_Chi_Minh")
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=TZ)
    return _scheduler


def start_scheduler():
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


async def trigger_schedule(schedule_id: int):
    """Called by APScheduler when a job fires."""
    logger.info(f"⏰ Trigger: schedule {schedule_id}")
    await mq.enqueue_schedule(schedule_id)


def add_schedule_job(schedule: dict):
    """Add or replace a scheduler job for a given schedule."""
    scheduler = get_scheduler()
    job_id = f"schedule_{schedule['id']}"
    stype = schedule["schedule_type"]
    time_str = schedule["time_of_day"]  # HH:MM

    try:
        hour, minute = time_str.split(":")
        hour, minute = int(hour), int(minute)
    except (ValueError, IndexError):
        logger.error(f"Invalid time_of_day: {time_str}")
        return

    trigger = None
    if stype == "hourly":
        trigger = CronTrigger(minute=minute, timezone=TZ)
    elif stype == "daily":
        trigger = CronTrigger(hour=hour, minute=minute, timezone=TZ)
    elif stype == "weekly":
        days_str = schedule.get("days_of_week", "1,2,3,4,5")
        # Convert 1-7 (Mon-Sun) to 0-6 (Mon-Sun) for APScheduler
        day_map = {"1": "mon", "2": "tue", "3": "wed", "4": "thu",
                   "5": "fri", "6": "sat", "7": "sun"}
        days = ",".join([day_map.get(d.strip(), d.strip()) for d in days_str.split(",")])
        trigger = CronTrigger(day_of_week=days, hour=hour, minute=minute, timezone=TZ)
    elif stype == "monthly":
        day_of_month = schedule.get("day_of_month", 1)
        trigger = CronTrigger(day=day_of_month, hour=hour, minute=minute, timezone=TZ)
    elif stype == "once":
        once_date = schedule.get("once_date")
        if not once_date:
            logger.error(f"Schedule {schedule['id']}: once_date is required")
            return
        run_dt = datetime.strptime(f"{once_date} {time_str}", "%Y-%m-%d %H:%M")
        run_dt = TZ.localize(run_dt)
        trigger = DateTrigger(run_date=run_dt, timezone=TZ)
    else:
        logger.error(f"Unknown schedule type: {stype}")
        return

    scheduler.add_job(
        trigger_schedule,
        trigger=trigger,
        args=[schedule["id"]],
        id=job_id,
        name=schedule["name"],
        replace_existing=True,
        misfire_grace_time=60
    )

    next_run = scheduler.get_job(job_id)
    next_str = str(next_run.next_run_time) if next_run else "N/A"
    logger.info(f"Job added: {job_id} ({stype} at {time_str}) → next: {next_str}")


def remove_schedule_job(schedule_id: int):
    scheduler = get_scheduler()
    job_id = f"schedule_{schedule_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info(f"Job removed: {job_id}")


def get_next_run(schedule_id: int) -> str | None:
    scheduler = get_scheduler()
    job = scheduler.get_job(f"schedule_{schedule_id}")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


async def load_all_jobs():
    """Load all active schedules and create jobs."""
    schedules = await db.get_active_schedules()
    for sch in schedules:
        # Check max_sends
        max_sends = sch.get("max_sends")
        current = sch.get("current_sends", 0)
        if max_sends and current >= max_sends:
            continue
        add_schedule_job(sch)
    logger.info(f"Loaded {len(schedules)} active schedule jobs")
