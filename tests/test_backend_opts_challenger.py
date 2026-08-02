import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from main import app
import database as db

pytestmark = pytest.mark.asyncio

# 1. Analytics endpoints caching verification
async def test_analytics_ttl_caching(client):
    # We want to mock db.get_analytics_overview to return dynamic mock data
    with patch("database.get_analytics_overview", new_callable=AsyncMock) as mock_overview:
        mock_overview.side_effect = [
            {"total_dm_sent": 100, "total_contacts": 50},
            {"total_dm_sent": 200, "total_contacts": 75},
        ]
        
        # Reset the cache first
        from routes.analytics import analytics_cache
        analytics_cache.cache.clear()
        
        # First call: should query database
        resp1 = client.get("/api/analytics/overview")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["total_dm_sent"] == 100
        assert mock_overview.call_count == 1
        
        # Second call (immediate): should return cached value (without querying DB)
        resp2 = client.get("/api/analytics/overview")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["total_dm_sent"] == 100  # Still cached value
        assert mock_overview.call_count == 1  # No extra database call
        
        # Simulate time travel (TTL is 30 seconds)
        current_time = time.time()
        with patch("time.time", return_value=current_time + 31):
            resp3 = client.get("/api/analytics/overview")
            assert resp3.status_code == 200
            data3 = resp3.json()
            assert data3["total_dm_sent"] == 200  # New value fetched
            assert mock_overview.call_count == 2  # Database queried again

async def test_analytics_daily_stats_key_safety(client):
    # Verify caching key safety for different 'days' on /api/analytics/daily-stats
    with patch("database.get_analytics_daily_stats", new_callable=AsyncMock) as mock_daily:
        mock_daily.side_effect = lambda days: [{"date": "2026-07-13", "sent": days}]
        
        from routes.analytics import analytics_cache
        analytics_cache.cache.clear()
        
        # Call with days=30
        resp1 = client.get("/api/analytics/daily-stats?days=30")
        assert resp1.status_code == 200
        assert resp1.json()[0]["sent"] == 30
        
        # Call with days=10
        resp2 = client.get("/api/analytics/daily-stats?days=10")
        assert resp2.status_code == 200
        assert resp2.json()[0]["sent"] == 10
        
        # Verify call counts and different cache keys
        assert mock_daily.call_count == 2
        
        # Verify subsequent calls are cached independently
        resp3 = client.get("/api/analytics/daily-stats?days=30")
        assert resp3.status_code == 200
        assert resp3.json()[0]["sent"] == 30
        assert mock_daily.call_count == 2  # No extra call for days=30
        
        resp4 = client.get("/api/analytics/daily-stats?days=10")
        assert resp4.status_code == 200
        assert resp4.json()[0]["sent"] == 10
        assert mock_daily.call_count == 2  # No extra call for days=10

# 2. CSV export streaming behavior verification
async def test_csv_export_streaming(client):
    # Mock scraped members database call
    scrape_job_id = "test_job_123"
    mock_members_chunk1 = [
        {"username": "user1", "first_name": "F1", "last_name": "L1", "user_id": 1, "phone": "123", "is_premium": 0, "status": "active", "scraped_at": "2026-07-13"},
        {"username": "user2", "first_name": "F2", "last_name": "L2", "user_id": 2, "phone": "456", "is_premium": 1, "status": "active", "scraped_at": "2026-07-13"}
    ]
    mock_members_chunk2 = []  # End of data
    
    with patch("database.get_scraped_members", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_members_chunk1, mock_members_chunk2]
        
        # Use TestClient stream context manager to test streaming
        with client.stream("GET", f"/api/export/members/{scrape_job_id}") as response:
            assert response.status_code == 200
            assert "text/csv" in response.headers["content-type"]
            assert "attachment" in response.headers["content-disposition"]
            
            # Read chunked content line by line or chunk by chunk
            content_iter = response.iter_lines()
            header = next(content_iter)
            assert "username" in header
            assert "first_name" in header
            
            row1 = next(content_iter)
            assert "user1" in row1
            
            row2 = next(content_iter)
            assert "user2" in row2
            
            # End of stream
            with pytest.raises(StopIteration):
                next(content_iter)

# 3. Watcher/schedule existence checks verification
async def test_schedule_existence_checks(client):
    # Mock database methods
    with patch("database.schedule_exists", new_callable=AsyncMock) as mock_exists, \
         patch("database.get_schedule", new_callable=AsyncMock) as mock_get_schedule, \
         patch("database.delete_schedule", new_callable=AsyncMock) as mock_delete, \
         patch("scheduler.remove_schedule_job", return_value=None):
         
        mock_exists.return_value = True
        mock_delete.return_value = True
        
        # Call delete endpoint
        resp = client.delete("/api/schedules/123")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        
        # Assert database.schedule_exists was called with 123
        mock_exists.assert_called_once_with(123)
        # Assert database.get_schedule was NOT called (unnecessary loading of data avoided!)
        mock_get_schedule.assert_not_called()

async def test_watcher_existence_checks(client):
    # Mock database methods
    with patch("database.get_watcher_platform", new_callable=AsyncMock) as mock_get_platform, \
         patch("database.get_watcher", new_callable=AsyncMock) as mock_get_watcher, \
         patch("database.delete_watcher", new_callable=AsyncMock) as mock_delete_watcher, \
         patch("keyword_watcher.remove_watcher", return_value=None):
         
        mock_get_platform.return_value = "telegram"
        mock_delete_watcher.return_value = True
        
        # Call delete endpoint
        resp = client.delete("/api/watchers/123")
        assert resp.status_code == 200
        assert resp.json() == {"message": "Watcher deleted"}
        
        # Assert database.get_watcher_platform was called to check existence/platform
        mock_get_platform.assert_called_once_with(123)
        # Assert database.get_watcher was NOT called (avoid loading full messages & config)
        mock_get_watcher.assert_not_called()
