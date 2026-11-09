from datetime import datetime, timedelta, timezone

import pytest
import pytz
from fastapi import HTTPException

import telegram_client as tg
from routes.members import _parse_schedule, _unscheduled_status


def test_parse_spambot_until():
    ts = tg.parse_spambot_until("Your account is limited until 8 Nov 2026, 08:44 UTC.")
    assert ts == int(datetime(2026, 11, 8, 8, 44, tzinfo=timezone.utc).timestamp())
    assert tg.parse_spambot_until("limited until 12 November 2026 23:05 UTC") is not None
    assert tg.parse_spambot_until("Unfortunately, some users reported you") is None
    assert tg.parse_spambot_until("") is None


def test_parse_schedule_future_and_past():
    tz = pytz.timezone("Asia/Ho_Chi_Minh")
    future = (datetime.now(tz) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    when, tz_name = _parse_schedule(future, None)
    assert tz_name == "Asia/Ho_Chi_Minh" and when == future.replace("T", " ")
    past = (datetime.now(tz) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    with pytest.raises(HTTPException):
        _parse_schedule(past, "Asia/Ho_Chi_Minh")
    with pytest.raises(HTTPException):
        _parse_schedule(future, "Mars/Base")
    with pytest.raises(HTTPException):
        _parse_schedule("tomorrow", "UTC")


def test_unscheduled_status_keeps_progress():
    assert _unscheduled_status({"sent_count": 0}) == "draft"
    assert _unscheduled_status({"sent_count": 3}) == "paused"
