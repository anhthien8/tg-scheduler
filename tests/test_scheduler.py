"""Unit tests for scheduler.py — trigger construction per schedule_type.

Offline: apscheduler runs with an in-memory jobstore (never started), and
database/message_queue are monkeypatched so no real job fires.
"""
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

import scheduler as sch


@pytest.fixture
def fake_db(monkeypatch):
    fake = types.ModuleType("database")
    fake.get_active_schedules = AsyncMock(return_value=[])
    monkeypatch.setattr(sch, "db", fake)
    return fake


@pytest.fixture
def scheduler_instance():
    """Fresh, non-started scheduler per test (no shared global state).

    APScheduler only populates Job.next_run_time once the scheduler is running,
    and starting it needs a live event loop. add_schedule_job reads it only for
    a log line, so we patch that single attribute access: get_job returns the
    real Job (for registration assertions) wrapped so .next_run_time is None.
    """
    sch._scheduler = None
    sched = sch.get_scheduler()

    class _JobView:
        next_run_time = None

        def __init__(self, job):
            self._job = job

        def __getattr__(self, name):
            return getattr(self._job, name)

    original_get_job = sched.get_job

    def get_job_patched(job_id):
        job = original_get_job(job_id)
        return _JobView(job) if job is not None else None

    with patch.object(sched, "get_job", side_effect=get_job_patched):
        yield sched
    sch._scheduler = None


def _add(schedule, scheduler_instance):
    """Add a job via add_schedule_job; return the registered Job (or None)."""
    sch.add_schedule_job(schedule)
    return scheduler_instance.get_job(f"schedule_{schedule['id']}")


def test_daily_schedule_builds_cron_job(scheduler_instance):
    job = _add({"id": 1, "name": "d", "schedule_type": "daily",
                "time_of_day": "09:30"}, scheduler_instance)
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "9"
    assert fields["minute"] == "30"


def test_hourly_schedule_uses_minute_only(scheduler_instance):
    job = _add({"id": 2, "name": "h", "schedule_type": "hourly",
                "time_of_day": "00:15"}, scheduler_instance)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["minute"] == "15"


def test_weekly_schedule_maps_days_1_7_to_names(scheduler_instance):
    job = _add({"id": 3, "name": "w", "schedule_type": "weekly",
                "time_of_day": "08:00", "days_of_week": "1,7"}, scheduler_instance)
    dow = str({f.name: str(f) for f in job.trigger.fields}["day_of_week"])
    assert "mon" in dow
    assert "sun" in dow


def test_monthly_schedule_sets_day_of_month(scheduler_instance):
    job = _add({"id": 4, "name": "m", "schedule_type": "monthly",
                "time_of_day": "12:00", "day_of_month": 15}, scheduler_instance)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day"] == "15"


def test_invalid_time_of_day_is_rejected(scheduler_instance):
    sch.add_schedule_job({"id": 5, "name": "bad", "schedule_type": "daily",
                          "time_of_day": "not-a-time"})
    assert scheduler_instance.get_job("schedule_5") is None


def test_once_without_date_is_rejected(scheduler_instance):
    sch.add_schedule_job({"id": 6, "name": "once", "schedule_type": "once",
                          "time_of_day": "10:00"})
    assert scheduler_instance.get_job("schedule_6") is None


def test_unknown_schedule_type_is_rejected(scheduler_instance):
    sch.add_schedule_job({"id": 7, "name": "x", "schedule_type": "yearly",
                          "time_of_day": "10:00"})
    assert scheduler_instance.get_job("schedule_7") is None


def test_remove_schedule_job(scheduler_instance):
    _add({"id": 8, "name": "d", "schedule_type": "daily", "time_of_day": "09:00"},
         scheduler_instance)
    assert scheduler_instance.get_job("schedule_8") is not None
    sch.remove_schedule_job(8)
    assert scheduler_instance.get_job("schedule_8") is None


async def test_trigger_schedule_enqueues(fake_db):
    with patch.object(sch, "mq") as mock_mq:
        mock_mq.enqueue_schedule = AsyncMock()
        await sch.trigger_schedule(42)
        mock_mq.enqueue_schedule.assert_awaited_once_with(42)


async def test_load_all_jobs_skips_schedules_over_max_sends(fake_db, scheduler_instance):
    fake_db.get_active_schedules = AsyncMock(return_value=[
        {"id": 100, "name": "done", "schedule_type": "daily",
         "time_of_day": "09:00", "max_sends": 5, "current_sends": 5},
        {"id": 101, "name": "active", "schedule_type": "daily",
         "time_of_day": "09:00", "max_sends": 5, "current_sends": 1},
    ])
    await sch.load_all_jobs()
    assert scheduler_instance.get_job("schedule_100") is None
    assert scheduler_instance.get_job("schedule_101") is not None


def test_stop_scheduler_is_safe_when_never_started():
    sch._scheduler = None
    sch.stop_scheduler()  # must not raise


def test_get_next_run_on_pending_job_returns_none_not_raise(scheduler_instance):
    """Regression: APScheduler 3.11.3 doesn't set Job.next_run_time on a
    pending (not-yet-started) scheduler. get_next_run must not AttributeError.
    """
    _add({"id": 9, "name": "p", "schedule_type": "daily", "time_of_day": "09:00"},
         scheduler_instance)
    assert sch.get_next_run(9) is None
