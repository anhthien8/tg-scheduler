import os
import sys
import tempfile
import types
import asyncio
import time
import fastapi

# Setup path so it runs in project root
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# 1. Setup temp environment
temp_dir = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = temp_dir.name
os.environ["DASHBOARD_SECRET_KEY"] = "test_secret_key"
os.environ["PORT"] = "8899"
os.environ["DEBUG_ENDPOINTS"] = "1"

# 2. Mock telegram_client module to avoid connections/authentication issues
tg_mock = types.ModuleType("telegram_client")
tg_mock.SESSION_DIR = os.path.join(temp_dir.name, "sessions")
os.makedirs(tg_mock.SESSION_DIR, exist_ok=True)
tg_mock._clients = {}
tg_mock._auth_cache = {}
tg_mock._me_cache = {}

class DummyClient:
    def __init__(self, *args, **kwargs):
        pass
    def is_connected(self):
        return True
    async def connect(self):
        pass
    async def disconnect(self):
        pass
    async def get_me(self):
        class User:
            id = 12345
            first_name = "Mocked User"
            last_name = "Telegram"
            username = "mocked_user"
            phone = "+8412345678"
        return User()
    async def get_entity(self, chat_id_or_username):
        class Entity:
            id = int(chat_id_or_username) if str(chat_id_or_username).strip("-").isdigit() else 12345
            title = "Mocked Chat"
            broadcast = False
            megagroup = True
            username = "mocked_username"
        return Entity()
    async def get_messages(self, entity, limit=20):
        class Message:
            def __init__(self, i):
                self.id = i
                self.text = f"Mock message {i}"
                self.sender_id = 999
                self.date = time.time()
            async def get_sender(self):
                class Sender:
                    username = "mock_sender"
                    first_name = "Mock"
                    last_name = "Sender"
                return Sender()
        return [Message(i) for i in range(limit)]
    async def send_message(self, chat_id, text):
        class SentMsg:
            id = 1000
        return SentMsg()
    async def delete_messages(self, chat_id, message_ids):
        pass
    def list_event_handlers(self):
        return []

tg_mock.DummyClient = DummyClient

async def is_authorized(account_id: int):
    return True
tg_mock.is_authorized = is_authorized

async def get_me(account_id: int):
    return {
        "user_id": 12345,
        "first_name": "Mocked User",
        "last_name": "Telegram",
        "username": "mocked_user",
        "phone": "+8412345678"
    }
tg_mock.get_me = get_me

async def create_client(account_id, api_id, api_hash, session_name, proxy_url=None):
    return DummyClient()
tg_mock.create_client = create_client

async def start_client(account_id):
    return True
tg_mock.start_client = start_client

def get_client(account_id):
    return DummyClient()
tg_mock.get_client = get_client

async def check_accounts_in_groups(account_ids, group_ids):
    return {"not_in_groups": []}
tg_mock.check_accounts_in_groups = check_accounts_in_groups

async def disconnect_all():
    pass
tg_mock.disconnect_all = disconnect_all

sys.modules["telegram_client"] = tg_mock

# Mock Discord and watch platforms
discord_adapter_mock = types.ModuleType("platforms.discord_adapter")
class DiscordAdapter:
    platform = "discord"
    async def is_connected(self, bot_id):
        return True
    async def connect_bot(self, bot_id, token):
        return True
    async def disconnect_bot(self, bot_id):
        return True
    async def get_account_info(self, bot_id):
        return {"user_id": "123", "username": "test_bot", "guild_count": 5}
    async def disconnect_all(self):
        return True
discord_adapter_mock.DiscordAdapter = DiscordAdapter
sys.modules["platforms.discord_adapter"] = discord_adapter_mock

dw_mock = types.ModuleType("discord_watcher")
dw_mock.set_adapter = lambda adapter: None
dw_mock.start_all_watchers = lambda: None
dw_mock.stop_all_watchers = lambda: None
dw_mock.reload_watcher = lambda watcher_id: None
dw_mock.remove_watcher = lambda watcher_id: None
sys.modules["discord_watcher"] = dw_mock

drw_mock = types.ModuleType("discord_reaction_watcher")
drw_mock.set_adapter = lambda adapter: None
drw_mock.start_all = lambda: None
drw_mock.stop_all = lambda: None
sys.modules["discord_reaction_watcher"] = drw_mock

drt_discord_mock = types.ModuleType("discord_reply_tracker")
drt_discord_mock.set_adapter = lambda adapter: None
drt_discord_mock.start_reply_tracker = lambda: None
drt_discord_mock.stop_reply_tracker = lambda: None
sys.modules["discord_reply_tracker"] = drt_discord_mock

# 3. Import FastAPI app, database, and test client
import database as db
from main import app
from fastapi.testclient import TestClient

db.DB_DIR = temp_dir.name
db.DB_PATH = os.path.join(temp_dir.name, "scheduler.db")

client = TestClient(app)
client.headers.update({"X-API-Key": "test_secret_key"})

print("=== Mocks and environment initialized ===")

# --- 1. Verify TTL caching behavior ---
def verify_ttl_caching():
    print("\n[Testing] Analytics TTL caching behavior...")
    
    # Store original methods to restore them later
    orig_get_overview = db.get_analytics_overview
    orig_get_daily_stats = db.get_analytics_daily_stats
    
    overview_calls = 0
    async def mock_get_analytics_overview():
        nonlocal overview_calls
        overview_calls += 1
        return {"total_contacts": 100, "call_count": overview_calls}
    
    daily_stats_calls = []
    async def mock_get_analytics_daily_stats(days):
        daily_stats_calls.append(days)
        return [{"date": "2026-07-13", "days": days, "call_index": len(daily_stats_calls)}]
        
    db.get_analytics_overview = mock_get_analytics_overview
    db.get_analytics_daily_stats = mock_get_analytics_daily_stats
    
    from routes.analytics import analytics_cache
    analytics_cache.cache.clear()
    
    try:
        # A. Multiple calls to /api/analytics/overview return cached values
        print("  - Verifying cached value on second call...")
        r1 = client.get("/api/analytics/overview")
        assert r1.status_code == 200, f"Expected 200, got {r1.status_code}"
        assert r1.json() == {"total_contacts": 100, "call_count": 1}
        assert overview_calls == 1
        
        r2 = client.get("/api/analytics/overview")
        assert r2.status_code == 200
        assert r2.json() == {"total_contacts": 100, "call_count": 1} # Cached!
        assert overview_calls == 1
        
        # B. Verify TTL expiration
        print("  - Verifying TTL expiration...")
        # Manually alter timestamp in cache to simulate expiration
        overview_cache_key = "overview"
        assert overview_cache_key in analytics_cache.cache
        # Set cache time to 0 (long in the past)
        analytics_cache.cache[overview_cache_key] = (0.0, analytics_cache.cache[overview_cache_key][1])
        
        r3 = client.get("/api/analytics/overview")
        assert r3.status_code == 200
        assert r3.json() == {"total_contacts": 100, "call_count": 2} # Fresh query executed!
        assert overview_calls == 2
        
        # C. Verify caching key safety for different 'days' on /api/analytics/daily-stats
        print("  - Verifying cache key safety for daily-stats...")
        analytics_cache.cache.clear()
        
        # Call with days=30
        r_d30_1 = client.get("/api/analytics/daily-stats?days=30")
        assert r_d30_1.status_code == 200
        assert r_d30_1.json() == [{"date": "2026-07-13", "days": 30, "call_index": 1}]
        assert daily_stats_calls == [30]
        
        # Call days=30 again (should be cached)
        r_d30_2 = client.get("/api/analytics/daily-stats?days=30")
        assert r_d30_2.json() == [{"date": "2026-07-13", "days": 30, "call_index": 1}]
        assert daily_stats_calls == [30]
        
        # Call with days=7 (should NOT return cached 30, but make new query)
        r_d7_1 = client.get("/api/analytics/daily-stats?days=7")
        assert r_d7_1.status_code == 200
        assert r_d7_1.json() == [{"date": "2026-07-13", "days": 7, "call_index": 2}]
        assert daily_stats_calls == [30, 7]
        
        # Call days=7 again (should be cached)
        r_d7_2 = client.get("/api/analytics/daily-stats?days=7")
        assert r_d7_2.json() == [{"date": "2026-07-13", "days": 7, "call_index": 2}]
        assert daily_stats_calls == [30, 7]
        
        # Call days=30 again (should still return cached 30!)
        r_d30_3 = client.get("/api/analytics/daily-stats?days=30")
        assert r_d30_3.json() == [{"date": "2026-07-13", "days": 30, "call_index": 1}]
        assert daily_stats_calls == [30, 7]
        
        print("[PASS] TTL Caching behavior verified successfully!")
        
    finally:
        # Restore original database methods
        db.get_analytics_overview = orig_get_overview
        db.get_analytics_daily_stats = orig_get_daily_stats


# --- 2. Verify CSV streaming behavior ---
async def verify_csv_streaming():
    print("\n[Testing] CSV export endpoints streaming behavior...")
    
    orig_get_scraped_members = db.get_scraped_members
    
    scraped_members_calls = []
    async def mock_get_scraped_members_lazy(scrape_job_id, limit, offset):
        scraped_members_calls.append((limit, offset))
        if offset >= 2500:
            return []
        count = min(limit, 2500 - offset)
        return [
            {
                "username": f"user_{i}",
                "first_name": "First",
                "last_name": "Last",
                "user_id": i,
                "phone": "12345",
                "is_premium": 0,
                "status": "active",
                "scraped_at": "2026-07-13"
            }
            for i in range(offset, offset + count)
        ]
        
    db.get_scraped_members = mock_get_scraped_members_lazy
    
    try:
        from routes.analytics import export_members_csv
        
        # A. Call route function directly to assert StreamingResponse
        print("  - Verifying direct route returns StreamingResponse...")
        response = await export_members_csv("job_1")
        assert isinstance(response, fastapi.responses.StreamingResponse)
        assert response.media_type == "text/csv"
        assert "attachment; filename=members_job_1.csv" in response.headers["Content-Disposition"]
        
        # B. Verify lazy query execution (only the first chunk 1000 is loaded initially)
        print("  - Verifying lazy query execution (chunk limit)...")
        assert len(scraped_members_calls) == 1
        assert scraped_members_calls[0] == (1000, 0)
        
        # C. Consume the body_iterator and verify it yields rows iteratively
        print("  - Consuming stream and verifying lazy database queries...")
        iterator = response.body_iterator
        async for chunk in iterator:
            # Consuming chunks
            pass
            
        # Full consumption should execute all 4 paginated queries (offset=0, 1000, 2000, 3000)
        assert len(scraped_members_calls) == 4
        assert scraped_members_calls == [(1000, 0), (1000, 1000), (1000, 2000), (1000, 3000)]
        
        # D. Verify using HTTP Test Client
        print("  - Verifying HTTP streaming via TestClient...")
        with client.get("/api/export/members/job_1", stream=True) as r:
            assert r.status_code == 200
            assert r.headers["content-type"] == "text/csv; charset=utf-8"
            
            # Read lines and verify count
            lines = list(r.iter_lines())
            # Header line (1) + members (2500) + trailing newline/empty line (1) = 2502
            assert len(lines) == 2502, f"Expected 2502 lines, got {len(lines)}"
            
        print("[PASS] CSV export streaming verified successfully!")
        
    finally:
        db.get_scraped_members = orig_get_scraped_members


# --- 3. Verify existence checks do not load unnecessary data ---
def verify_existence_checks():
    print("\n[Testing] Watcher/Schedule existence checks data loading...")
    
    # Store original methods
    orig_schedule_exists = db.schedule_exists
    orig_get_schedule = db.get_schedule
    orig_update_schedule = db.update_schedule
    orig_delete_schedule = db.delete_schedule
    orig_reset_send_count = db.reset_send_count
    orig_get_blocked_targets = db.get_blocked_targets
    
    # Spies for schedule database methods
    schedule_exists_calls = 0
    get_schedule_calls = 0
    
    async def mock_schedule_exists(schedule_id):
        nonlocal schedule_exists_calls
        schedule_exists_calls += 1
        return True
        
    async def mock_get_schedule(schedule_id):
        nonlocal get_schedule_calls
        get_schedule_calls += 1
        return {
            "id": schedule_id,
            "account_id": 1,
            "name": "Spy Schedule",
            "schedule_type": "daily",
            "time_of_day": "12:00",
            "messages": [],
            "targets": []
        }
        
    async def mock_delete_schedule(schedule_id):
        return True
        
    async def mock_reset_send_count(schedule_id):
        return True
        
    async def mock_get_blocked_targets_db(schedule_id):
        return []
        
    db.schedule_exists = mock_schedule_exists
    db.get_schedule = mock_get_schedule
    db.delete_schedule = mock_delete_schedule
    db.reset_send_count = mock_reset_send_count
    db.get_blocked_targets = mock_get_blocked_targets_db
    
    # Mock message queue and scheduler functions to avoid side-effects
    import message_queue as mq
    import scheduler as sch
    
    orig_enqueue_schedule = mq.enqueue_schedule
    orig_remove_schedule_job = sch.remove_schedule_job
    
    async def mock_enqueue_schedule(schedule_id):
        pass
    def mock_remove_schedule_job(schedule_id):
        pass
        
    mq.enqueue_schedule = mock_enqueue_schedule
    sch.remove_schedule_job = mock_remove_schedule_job
    
    # Spies for watcher database methods
    orig_get_watcher_platform = db.get_watcher_platform
    orig_get_watcher = db.get_watcher
    orig_delete_watcher = db.delete_watcher
    orig_toggle_watcher = db.toggle_watcher
    orig_update_watcher = db.update_watcher
    
    get_watcher_platform_calls = 0
    get_watcher_calls = 0
    
    async def mock_get_watcher_platform(watcher_id):
        nonlocal get_watcher_platform_calls
        get_watcher_platform_calls += 1
        return "telegram"
        
    async def mock_get_watcher(watcher_id):
        nonlocal get_watcher_calls
        get_watcher_calls += 1
        return {"id": watcher_id, "name": "Spy Watcher", "platform": "telegram"}
        
    async def mock_delete_watcher(watcher_id):
        return True
        
    async def mock_toggle_watcher(watcher_id):
        return {"id": watcher_id, "is_active": 1}
        
    async def mock_update_watcher(watcher_id, data):
        return True
        
    db.get_watcher_platform = mock_get_watcher_platform
    db.get_watcher = mock_get_watcher
    db.delete_watcher = mock_delete_watcher
    db.toggle_watcher = mock_toggle_watcher
    db.update_watcher = mock_update_watcher
    
    # Mock keyword watcher functions to avoid side effects
    import keyword_watcher as kw
    orig_kw_reload_watcher = kw.reload_watcher
    orig_kw_remove_watcher = kw.remove_watcher
    
    async def mock_kw_reload_watcher(watcher_id):
        pass
    def mock_kw_remove_watcher(watcher_id):
        pass
        
    kw.reload_watcher = mock_kw_reload_watcher
    kw.remove_watcher = mock_kw_remove_watcher
    
    try:
        # A. Verify DELETE /api/schedules/{id} uses existence check
        print("  - Verifying DELETE /api/schedules/{id} existence check optimization...")
        schedule_exists_calls = 0
        get_schedule_calls = 0
        r_del_sch = client.delete("/api/schedules/123")
        assert r_del_sch.status_code == 200
        assert schedule_exists_calls == 1
        assert get_schedule_calls == 0
        
        # B. Verify POST /api/schedules/{id}/send-now uses existence check
        print("  - Verifying POST /api/schedules/{id}/send-now existence check optimization...")
        schedule_exists_calls = 0
        get_schedule_calls = 0
        r_send_sch = client.post("/api/schedules/123/send-now")
        assert r_send_sch.status_code == 200
        assert schedule_exists_calls == 1
        assert get_schedule_calls == 0
        
        # C. Verify POST /api/schedules/{id}/reset-count uses existence check
        print("  - Verifying POST /api/schedules/{id}/reset-count existence check optimization...")
        schedule_exists_calls = 0
        get_schedule_calls = 0
        r_reset_sch = client.post("/api/schedules/123/reset-count")
        assert r_reset_sch.status_code == 200
        assert schedule_exists_calls == 1
        assert get_schedule_calls == 0
        
        # D. Verify GET /api/schedules/{id}/blocked-targets uses existence check
        print("  - Verifying GET /api/schedules/{id}/blocked-targets existence check optimization...")
        schedule_exists_calls = 0
        get_schedule_calls = 0
        r_blocked_sch = client.get("/api/schedules/123/blocked-targets")
        assert r_blocked_sch.status_code == 200
        assert schedule_exists_calls == 1
        assert get_schedule_calls == 0
        
        # E. Verify PUT /api/watchers/{id} uses platform check instead of full watcher load
        print("  - Verifying PUT /api/watchers/{id} platform existence check...")
        get_watcher_platform_calls = 0
        get_watcher_calls = 0
        payload = {
            "name": "Updated Watcher",
            "sender_account_ids": [1],
            "keywords": ["test"],
            "group_ids": [123],
            "cooldown_hours": 24,
            "dm_once": False,
            "excluded_usernames": [],
            "is_active": 1,
            "messages": [{"msg_order": 0, "msg_type": "text", "content": "Hello!"}],
            "reply_in_group": False,
            "group_reply_text": "Check my DM",
            "group_reply_account_id": None
        }
        r_put_wat = client.put("/api/watchers/456", json=payload)
        assert r_put_wat.status_code == 200
        assert get_watcher_platform_calls == 1
        assert get_watcher_calls == 0
        
        # F. Verify POST /api/watchers/{id}/toggle uses platform check
        print("  - Verifying POST /api/watchers/{id}/toggle platform existence check...")
        get_watcher_platform_calls = 0
        get_watcher_calls = 0
        r_toggle_wat = client.post("/api/watchers/456/toggle")
        assert r_toggle_wat.status_code == 200
        assert get_watcher_platform_calls == 1
        assert get_watcher_calls == 0
        
        # G. Verify DELETE /api/watchers/{id} uses platform check
        print("  - Verifying DELETE /api/watchers/{id} platform existence check...")
        get_watcher_platform_calls = 0
        get_watcher_calls = 0
        r_del_wat = client.delete("/api/watchers/456")
        assert r_del_wat.status_code == 200
        assert get_watcher_platform_calls == 1
        assert get_watcher_calls == 0
        
        print("[PASS] Existence check optimizations verified successfully!")
        
    finally:
        # Restore schedule database methods
        db.schedule_exists = orig_schedule_exists
        db.get_schedule = orig_get_schedule
        db.delete_schedule = orig_delete_schedule
        db.reset_send_count = orig_reset_send_count
        db.get_blocked_targets = orig_get_blocked_targets
        
        # Restore message queue and scheduler
        mq.enqueue_schedule = orig_enqueue_schedule
        sch.remove_schedule_job = orig_remove_schedule_job
        
        # Restore watcher database methods
        db.get_watcher_platform = orig_get_watcher_platform
        db.get_watcher = orig_get_watcher
        db.delete_watcher = orig_delete_watcher
        db.toggle_watcher = orig_toggle_watcher
        db.update_watcher = orig_update_watcher
        
        # Restore keyword watcher
        kw.reload_watcher = orig_kw_reload_watcher
        kw.remove_watcher = orig_kw_remove_watcher


def run_all_tests():
    print("=" * 60)
    print("RUNNING CHALLENGER BACKEND OPTIMIZATION VERIFICATION SUITE")
    print("=" * 60)
    
    try:
        verify_ttl_caching()
        asyncio.run(verify_csv_streaming())
        verify_existence_checks()
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED SUCCESSFULLY! BACKEND OPTIMIZATIONS CONFIRMED.")
        print("=" * 60)
        sys.exit(0)
    except AssertionError as e:
        import traceback
        print("\n" + "!" * 60)
        print("VERIFICATION FAILED:")
        traceback.print_exc()
        print("!" * 60)
        sys.exit(1)
    except Exception as e:
        import traceback
        print("\n" + "!" * 60)
        print("UNEXPECTED ERROR:")
        traceback.print_exc()
        print("!" * 60)
        sys.exit(1)

if __name__ == "__main__":
    run_all_tests()
