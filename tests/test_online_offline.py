"""Tests for C3: Online/Offline Cycle per Account."""
import pytest
from unittest.mock import patch
from datetime import datetime

from database import is_account_in_active_hours, check_account_active_hours


class TestIsAccountInActiveHours:
    """Test the core active hours check function."""

    def _mock_now(self, hour, minute=0, tz_name="Asia/Ho_Chi_Minh"):
        """Helper to mock datetime.now for a specific time."""
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
        return datetime(2026, 10, 19, hour, minute, 0, tzinfo=tz)

    def test_active_during_hours(self):
        """10:00 within 08:00-22:00 → True."""
        with patch("database.zoneinfo") as mock_zi:
            import zoneinfo
            mock_zi.ZoneInfo = zoneinfo.ZoneInfo
            with patch("database._dt") as mock_dt:
                mock_dt.now.return_value = self._mock_now(10, 0)
                assert is_account_in_active_hours("08:00", "22:00", "Asia/Ho_Chi_Minh") is True

    def test_inactive_night(self):
        """03:00 within 08:00-22:00 → False."""
        with patch("database.zoneinfo") as mock_zi:
            import zoneinfo
            mock_zi.ZoneInfo = zoneinfo.ZoneInfo
            with patch("database._dt") as mock_dt:
                mock_dt.now.return_value = self._mock_now(3, 0)
                assert is_account_in_active_hours("08:00", "22:00", "Asia/Ho_Chi_Minh") is False

    def test_overnight_window_active(self):
        """23:00 within 22:00-06:00 → True."""
        with patch("database.zoneinfo") as mock_zi:
            import zoneinfo
            mock_zi.ZoneInfo = zoneinfo.ZoneInfo
            with patch("database._dt") as mock_dt:
                mock_dt.now.return_value = self._mock_now(23, 0)
                assert is_account_in_active_hours("22:00", "06:00", "Asia/Ho_Chi_Minh") is True

    def test_overnight_window_inactive(self):
        """10:00 within 22:00-06:00 → False."""
        with patch("database.zoneinfo") as mock_zi:
            import zoneinfo
            mock_zi.ZoneInfo = zoneinfo.ZoneInfo
            with patch("database._dt") as mock_dt:
                mock_dt.now.return_value = self._mock_now(10, 0)
                assert is_account_in_active_hours("22:00", "06:00", "Asia/Ho_Chi_Minh") is False

    def test_boundary_start(self):
        """08:00 within 08:00-22:00 → True (inclusive start)."""
        with patch("database.zoneinfo") as mock_zi:
            import zoneinfo
            mock_zi.ZoneInfo = zoneinfo.ZoneInfo
            with patch("database._dt") as mock_dt:
                mock_dt.now.return_value = self._mock_now(8, 0)
                assert is_account_in_active_hours("08:00", "22:00", "Asia/Ho_Chi_Minh") is True

    def test_boundary_end(self):
        """22:00 within 08:00-22:00 → False (exclusive end)."""
        with patch("database.zoneinfo") as mock_zi:
            import zoneinfo
            mock_zi.ZoneInfo = zoneinfo.ZoneInfo
            with patch("database._dt") as mock_dt:
                mock_dt.now.return_value = self._mock_now(22, 0)
                assert is_account_in_active_hours("08:00", "22:00", "Asia/Ho_Chi_Minh") is False

    def test_bad_timezone_returns_true(self):
        """Invalid timezone → returns True (safe default)."""
        assert is_account_in_active_hours("08:00", "22:00", "Invalid/TZ") is True


class TestCheckAccountActiveHours:
    """Test the convenience wrapper."""

    def test_none_account_returns_true(self):
        assert check_account_active_hours(None) is True

    def test_empty_dict_returns_true(self):
        assert check_account_active_hours({}) is True

    def test_delegates_to_core(self):
        with patch("database.is_account_in_active_hours", return_value=False) as mock:
            result = check_account_active_hours({
                "active_hours_start": "09:00",
                "active_hours_end": "17:00",
                "active_timezone": "UTC",
            })
            assert result is False
            mock.assert_called_once_with(start="09:00", end="17:00", tz_name="UTC")

    def test_defaults_used_for_missing_keys(self):
        with patch("database.is_account_in_active_hours", return_value=True) as mock:
            result = check_account_active_hours({"id": 1})
            assert result is True
            mock.assert_called_once_with(
                start="08:00", end="22:00", tz_name="Asia/Ho_Chi_Minh"
            )
