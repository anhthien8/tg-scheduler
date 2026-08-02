import os
import sys
import tempfile
import asyncio
import time
import types

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

class DummyClient:
    def __init__(self, *args, **kwargs): pass
    def is_connected(self): return True
    async def connect(self): pass
    async def disconnect(self): pass
    async def get_me(self):
        class User:
            id = 12345
            first_name = "Mocked User"
            last_name = "Telegram"
            username = "mocked_user"
            phone = "+8412345678"
        return User()

tg_mock.DummyClient = DummyClient
tg_mock.is_authorized = lambda account_id: True
tg_mock.get_me = lambda account_id: {"user_id": 12345, "first_name": "Mocked User"}

# Track concurrency metrics
active_connections = 0
max_concurrent_connections = 0
connection_lock = asyncio.Lock()

async def mock_create_client(account_id, api_id, api_hash, session_name, proxy_url=None):
    return DummyClient()
tg_mock.create_client = mock_create_client

async def mock_start_client(account_id):
    global active_connections, max_concurrent_connections
    async with connection_lock:
        active_connections += 1
        if active_connections > max_concurrent_connections:
            max_concurrent_connections = active_connections
    
    # Introduce a delay of 0.5 seconds to test concurrency
    await asyncio.sleep(0.5)
    
    async with connection_lock:
        active_connections -= 1
    return True

tg_mock.start_client = mock_start_client
tg_mock.disconnect_all = lambda: None
sys.modules["telegram_client"] = tg_mock

# Mock Discord and watch platforms
discord_adapter_mock = types.ModuleType("platforms.discord_adapter")
class DiscordAdapter:
    platform = "discord"
    async def is_connected(self, bot_id): return True
    async def connect_bot(self, bot_id, token): return True
    async def disconnect_bot(self, bot_id): return True
    async def get_account_info(self, bot_id): return {"user_id": "123", "username": "test_bot"}
    async def disconnect_all(self): return True
discord_adapter_mock.DiscordAdapter = DiscordAdapter
sys.modules["platforms.discord_adapter"] = discord_adapter_mock

dw_mock = types.ModuleType("discord_watcher")
dw_mock.set_adapter = lambda adapter: None
dw_mock.start_all_watchers = lambda: None
dw_mock.stop_all_watchers = lambda: None
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

# Mock reaction_watcher, dm_reply_tracker, and keyword_watcher
rw_mock = types.ModuleType("reaction_watcher")
rw_mock.start_all = lambda: None
rw_mock.stop_all = lambda: None
sys.modules["reaction_watcher"] = rw_mock

drt_mock = types.ModuleType("dm_reply_tracker")
drt_mock.start_reply_tracker = lambda: None
drt_mock.stop_reply_tracker = lambda: None
sys.modules["dm_reply_tracker"] = drt_mock

kw_mock = types.ModuleType("keyword_watcher")
kw_mock.start_all_watchers = lambda: None
sys.modules["keyword_watcher"] = kw_mock

# 3. Import app and database
import database as db
import main
from main import app

db.DB_DIR = temp_dir.name
db.DB_PATH = os.path.join(temp_dir.name, "scheduler.db")

async def run_lifespan_concurrency_test():
    print("Initializing Database...")
    await db.init_db()
    
    # Seed 16 mock accounts
    print("Seeding database with 16 mock accounts...")
    for i in range(16):
        await db.create_account({
            "name": f"Mock Account {i}",
            "phone": f"+8490000000{i:02d}",
            "api_id": "2040",
            "api_hash": "b18441a1ff607e10a989891a5462e627",
            "session_name": f"mock_session_{i}",
            "proxy_url": None
        })
    print("Database seeded with 16 accounts.")
    
    start_time = time.perf_counter()
    
    # We trigger the lifespan manually via TestClient or by calling context manager
    print("Starting FastAPI app lifespan (triggering startup)...")
    async with main.lifespan(app):
        # The lifespan will start main._startup_task
        print("Waiting for background startup task to complete...")
        await main._startup_task
        
    duration = time.perf_counter() - start_time
    print(f"Startup task completed in {duration:.4f} seconds.")
    print(f"Max concurrent connections observed: {max_concurrent_connections}")
    
    # Assertions
    assert max_concurrent_connections > 1, f"Expected concurrent execution, but max concurrent was {max_concurrent_connections}"
    assert max_concurrent_connections >= 16, f"Expected all 16 connections to run concurrently, got {max_concurrent_connections}"
    assert duration < 2.0, f"Expected concurrent startup to finish in < 2.0 seconds, took {duration:.4f} seconds"
    
    # Verify that all 16 accounts are marked as logged in in the DB
    accounts = await db.get_all_accounts()
    logged_in_count = sum(1 for a in accounts if a.get("is_logged_in") == 1)
    print(f"Accounts logged in: {logged_in_count} / 16")
    assert logged_in_count == 16, f"Expected 16 logged in accounts, got {logged_in_count}"
    
    print("[PASS] Concurrency and regression safety check passed successfully!")

if __name__ == "__main__":
    try:
        asyncio.run(run_lifespan_concurrency_test())
    finally:
        temp_dir.cleanup()
