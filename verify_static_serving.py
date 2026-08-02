import os
import sys
import tempfile
import types
import asyncio
import time
from fastapi import Response
from fastapi.testclient import TestClient

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
    async def get_entity(self, chat_id_or_username):
        class Entity:
            id = 12345
            title = "Mocked Chat"
            broadcast = False
            megagroup = True
            username = "mocked_username"
        return Entity()
    async def get_messages(self, entity, limit=20): return []
    async def send_message(self, chat_id, text):
        class SentMsg: id = 1000
        return SentMsg()
    async def delete_messages(self, chat_id, message_ids): pass
    def list_event_handlers(self): return []

tg_mock.DummyClient = DummyClient
tg_mock.is_authorized = lambda account_id: True
tg_mock.get_me = lambda account_id: {"user_id": 12345, "first_name": "Mocked User"}
tg_mock.create_client = lambda *args, **kwargs: DummyClient()
tg_mock.start_client = lambda account_id: True
tg_mock.disconnect_all = lambda: None
sys.modules["telegram_client"] = tg_mock

# Mock Discord
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

# 3. Import app and database
import database as db
from main import app

db.DB_DIR = temp_dir.name
db.DB_PATH = os.path.join(temp_dir.name, "scheduler.db")

# Add custom test endpoints for byte boundary testing
@app.get("/test/bytes/{num_bytes}")
def test_bytes(num_bytes: int):
    return Response("x" * num_bytes, media_type="text/plain")

async def run_verifications():
    print("Initializing Database...")
    await db.init_db()
    
    # Seed data: insert 2500 scraped members to test streaming
    print("Seeding database with 2500 scraped members...")
    members = []
    for i in range(2500):
        members.append({
            "user_id": 100000 + i,
            "username": f"user_{i}",
            "first_name": f"First_{i}",
            "last_name": f"Last_{i}",
            "phone": f"+123456789{i:03d}",
            "is_bot": False,
            "is_premium": i % 10 == 0,
            "status": "active",
            "last_seen": "online"
        })
    
    await db.save_scraped_members(
        scrape_job_id="job_test_1",
        account_id=1,
        group_id=999,
        group_title="Test Group",
        members=members
    )
    print("Database seeded.")

    # Initialize TestClient
    client = TestClient(app)
    # Update auth header
    client.headers.update({"X-API-Key": "test_secret_key"})
    
    results = {}
    
    print("\n--- Testing GZip Compression Boundary Conditions ---")
    # GZip compression boundary testing (minimum_size=1000)
    for size in [999, 1000, 1001]:
        # Without GZip accept header
        res_no_gzip = client.get(f"/test/bytes/{size}")
        no_gzip_encoding = res_no_gzip.headers.get("content-encoding")
        no_gzip_size = len(res_no_gzip.content)
        
        # With GZip accept header
        res_gzip = client.get(f"/test/bytes/{size}", headers={"Accept-Encoding": "gzip"})
        gzip_encoding = res_gzip.headers.get("content-encoding")
        gzip_size = len(res_gzip.content)
        
        print(f"Size {size} bytes:")
        print(f"  No Accept-Encoding: Content-Encoding={no_gzip_encoding}, Size={no_gzip_size}")
        print(f"  Accept-Encoding: gzip: Content-Encoding={gzip_encoding}, Size={gzip_size}")
        
        results[f"gzip_{size}_no_gzip_header"] = no_gzip_encoding
        results[f"gzip_{size}_gzip_header"] = gzip_encoding
        results[f"gzip_{size}_no_gzip_len"] = no_gzip_size
        results[f"gzip_{size}_gzip_len"] = gzip_size

    print("\n--- Testing Cache-Control Headers ---")
    # Test CC header for static files mounted under /static
    res_static_html = client.get("/static/index.html")
    cc_static_html = res_static_html.headers.get("cache-control")
    print(f"/static/index.html Cache-Control: {cc_static_html}")
    results["cc_static_html"] = cc_static_html

    # Test CC header for root index.html served via FileResponse at /
    res_root = client.get("/")
    cc_root = res_root.headers.get("cache-control")
    print(f"/ (root) Cache-Control: {cc_root}")
    results["cc_root"] = cc_root

    # Test CC header for non-existent static file
    res_static_404 = client.get("/static/non_existent_file.html")
    cc_static_404 = res_static_404.headers.get("cache-control")
    print(f"/static/non_existent_file.html (404) Cache-Control: {cc_static_404}")
    results["cc_static_404"] = cc_static_404

    print("\n--- Testing Streaming CSV Exports ---")
    # Test CSV export streaming under compression
    # /api/export/contacts returns StreamingResponse
    start_time = time.perf_counter()
    res_csv_no_gzip = client.get("/api/export/contacts")
    duration_no_gzip = time.perf_counter() - start_time
    csv_no_gzip_encoding = res_csv_no_gzip.headers.get("content-encoding")
    csv_no_gzip_len = len(res_csv_no_gzip.content)
    
    start_time = time.perf_counter()
    res_csv_gzip = client.get("/api/export/contacts", headers={"Accept-Encoding": "gzip"})
    duration_gzip = time.perf_counter() - start_time
    csv_gzip_encoding = res_csv_gzip.headers.get("content-encoding")
    csv_gzip_len = len(res_csv_gzip.content)
    
    print(f"Streaming CSV export (2500 contacts):")
    print(f"  No Accept-Encoding: Content-Encoding={csv_no_gzip_encoding}, Size={csv_no_gzip_len} bytes, Time={duration_no_gzip:.4f}s")
    print(f"  Accept-Encoding: gzip: Content-Encoding={csv_gzip_encoding}, Size={csv_gzip_len} bytes, Time={duration_gzip:.4f}s")
    
    results["csv_no_gzip_encoding"] = csv_no_gzip_encoding
    results["csv_no_gzip_len"] = csv_no_gzip_len
    results["csv_gzip_encoding"] = csv_gzip_encoding
    results["csv_gzip_len"] = csv_gzip_len
    results["csv_duration_no_gzip"] = duration_no_gzip
    results["csv_duration_gzip"] = duration_gzip

    # Let's verify CSV correctness (can it be parsed, is it valid CSV?)
    import csv as csv_parser
    import io as string_io
    # Decompressed content is automatically returned by res_csv_gzip.text or content in TestClient?
    # Actually, TestClient's response.content/text is decompressed if it was gzipped.
    content_text = res_csv_gzip.text
    csv_file = string_io.StringIO(content_text)
    reader = csv_parser.reader(csv_file)
    rows = list(reader)
    print(f"Parsed CSV rows: {len(rows)} (expected 2501 including header)")
    results["csv_row_count"] = len(rows)
    results["csv_header"] = rows[0] if len(rows) > 0 else []

    print("\n--- Testing Large index.html serving ---")
    # Verify index.html size and compression performance
    res_index_no_gzip = client.get("/", headers={})
    index_no_gzip_encoding = res_index_no_gzip.headers.get("content-encoding")
    index_no_gzip_len = len(res_index_no_gzip.content)
    
    res_index_gzip = client.get("/", headers={"Accept-Encoding": "gzip"})
    index_gzip_encoding = res_index_gzip.headers.get("content-encoding")
    index_gzip_len = len(res_index_gzip.content)
    
    print(f"index.html serving:")
    print(f"  No Accept-Encoding: Content-Encoding={index_no_gzip_encoding}, Size={index_no_gzip_len} bytes")
    print(f"  Accept-Encoding: gzip: Content-Encoding={index_gzip_encoding}, Size={index_gzip_len} bytes")
    
    results["index_no_gzip_encoding"] = index_no_gzip_encoding
    results["index_no_gzip_len"] = index_no_gzip_len
    results["index_gzip_encoding"] = index_gzip_encoding
    results["index_gzip_len"] = index_gzip_len

    # Save results to a file for review
    import json as json_saver
    with open("verify_static_serving_results.json", "w") as f:
        json_saver.dump(results, f, indent=4)
        
    print("\nAll verifications complete. Results saved to verify_static_serving_results.json.")
    await db.close_db()

if __name__ == "__main__":
    asyncio.run(run_verifications())
