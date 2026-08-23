import os
import sys
import tempfile
import types
import pytest
from datetime import datetime
import random

# 1. Set environment variables before any other imports
temp_dir = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = temp_dir.name
os.environ["DASHBOARD_SECRET_KEY"] = "test_secret_key"
os.environ["PORT"] = "8899"
os.environ["DEBUG_ENDPOINTS"] = "1"  # enable debug routes for testing

# 2. Mock telegram_client module
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
    def add_event_handler(self, handler, *args, **kwargs):
        pass
    def remove_event_handler(self, handler, *args, **kwargs):
        pass
    async def __call__(self, *args, **kwargs):
        class DummyResult:
            users = []
            participants = []
            count = 0
        return DummyResult()
    async def iter_participants(self, *args, **kwargs):
        class User:
            id = 999
            first_name = "Scraped"
            last_name = "User"
            username = "scraped_user"
            phone = "+84999999"
            bot = False
            premium = False
            status = None
        yield User()
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
    async def get_input_entity(self, chat_id_or_username):
        return chat_id_or_username
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
                self.date = datetime.now()
                self.views = 42
            async def get_sender(self):
                class Sender:
                    username = "mock_sender"
                    first_name = "Mock"
                    last_name = "Sender"
                return Sender()
        return [Message(i) for i in range(limit)]
    async def send_message(self, chat_id, text):
        class SentMsg:
            id = random.randint(1000, 9999)
        return SentMsg()
    async def delete_messages(self, chat_id, message_ids):
        pass
    def list_event_handlers(self):
        return []
    async def sign_in(self, *args, **kwargs):
        class User:
            id = 12345
            first_name = "Mocked User"
            last_name = "Telegram"
            username = "mocked_user"
        return User()
    async def send_code_request(self, phone):
        class Hash:
            phone_code_hash = "mock_hash"
        return Hash()

tg_mock.DummyClient = DummyClient

async def is_authorized(account_id: int, *args, **kwargs):
    return True
tg_mock.is_authorized = is_authorized

async def get_me(account_id: int, *args, **kwargs):
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

async def send_code(account_id, phone):
    return "mock_hash"
tg_mock.send_code = send_code

async def sign_in(account_id, phone, code, phone_code_hash, password=None):
    return {
        "success": True,
        "user_id": 12345,
        "first_name": "Mocked User",
        "last_name": "Telegram",
        "username": "mocked_user"
    }
tg_mock.sign_in = sign_in

async def logout(account_id):
    pass
tg_mock.logout = logout

def get_client(account_id):
    return DummyClient()
tg_mock.get_client = get_client

async def check_accounts_in_groups(account_ids, group_ids):
    return {"not_in_groups": []}
tg_mock.check_accounts_in_groups = check_accounts_in_groups

async def auto_join_accounts_to_groups(account_ids, group_ids):
    return {"success": True}
tg_mock.auto_join_accounts_to_groups = auto_join_accounts_to_groups

async def check_spam_status(account_id):
    return {"status": "ok", "message": "", "is_spambanned": False, "reason": None}
tg_mock.check_spam_status = check_spam_status

async def get_similar_channels_and_contacts(account_id, channel_link):
    return [{"channel_id": 111, "title": "Similar Channel", "username": "similar_ch", "contacts": ["@admin_user"]}]
tg_mock.get_similar_channels_and_contacts = get_similar_channels_and_contacts

async def join_channel(account_id, channel_link):
    return {"success": True, "channel_id": 111}
tg_mock.join_channel = join_channel

async def deep_crawl_similar_channels(account_ids, channel_link, max_depth, progress_callback=None, stop_flag=None):
    if progress_callback:
        await progress_callback({
            "status": "running",
            "current_depth": 1,
            "max_depth": max_depth,
            "channels_found": 1,
            "channels_processed": 1,
            "contacts_found": 1,
            "queue_remaining": 0,
            "current_channel": "Mocked",
            "current_account": "Mocked",
            "errors": [],
        })
    return [{"channel_id": 111, "title": "Deep Channel", "username": "deep_ch", "contacts": ["@deep_admin"]}]
tg_mock.deep_crawl_similar_channels = deep_crawl_similar_channels

def is_bot_account(sender, username: str = None) -> bool:
    if not sender and not username:
        return False
    if sender:
        if getattr(sender, "bot", False) or getattr(sender, "is_bot", False):
            return True
        sender_id = getattr(sender, "id", 0) or 0
        if sender_id in (777000, 178220800, 4244000, 4244001, 1088515515) or (0 < sender_id < 1000):
            return True
        return False
    uname = (username or "").strip().lower()
    if uname and uname.endswith("bot"):
        return True
    return False
tg_mock.is_bot_account = is_bot_account

import telegram_client as real_tg
tg_mock.score_community_trading = real_tg.score_community_trading

async def disconnect_all():
    pass
tg_mock.disconnect_all = disconnect_all

async def _get_entity_safe(client, chat_id):
    class Entity:
        id = chat_id
        title = "Mocked Chat"
        broadcast = False
        megagroup = True
        username = "mocked_username"
    return Entity()
tg_mock._get_entity_safe = _get_entity_safe

async def send_text_message(account_id, chat_id, content):
    return True
tg_mock.send_text_message = send_text_message

async def send_photo_message(account_id, chat_id, media_path, caption):
    return True
tg_mock.send_photo_message = send_photo_message

async def send_video_message(account_id, chat_id, media_path, caption):
    return True
tg_mock.send_video_message = send_video_message

async def send_document_message(account_id, chat_id, media_path, caption):
    return True
tg_mock.send_document_message = send_document_message

async def send_poll_message(account_id, chat_id, question, options, is_multiple):
    return True
tg_mock.send_poll_message = send_poll_message

async def get_dialogs(account_id: int, *args, **kwargs):
    return [{"chat_id": 12345, "chat_title": "Mock Group", "chat_type": "group", "username": "mock_grp", "participants_count": 10}]
tg_mock.get_dialogs = get_dialogs

sys.modules["telegram_client"] = tg_mock

# 3. Mock Discord and watch platforms
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

# 4. Import FastAPI app and database
import database as db
from main import app
from fastapi.testclient import TestClient

# Fix database path variable resolution
db.DB_DIR = temp_dir.name
db.DB_PATH = os.path.join(temp_dir.name, "scheduler.db")

@pytest.fixture(autouse=True, scope="session")
def setup_teardown_session():
    # Let FastAPI lifespan initialize things (which calls db.init_db())
    with TestClient(app) as c:
        yield
    # Cleanup temp directory safely on Windows
    try:
        import asyncio
        asyncio.run(db.close_db())
    except Exception:
        pass
    try:
        temp_dir.cleanup()
    except Exception:
        pass

@pytest.fixture(autouse=True)
def clean_database():
    # Empty tables or re-create schema to ensure a completely clean slate between tests
    import aiosqlite
    import asyncio
    async def _do_clean():
        await db.close_db()
        if os.path.exists(db.DB_PATH):
            async with aiosqlite.connect(db.DB_PATH) as conn:
                # We can drop all tables and call init_db
                cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = await cursor.fetchall()
                for t in tables:
                    if t[0] != "sqlite_sequence":
                        await conn.execute(f"DROP TABLE IF EXISTS {t[0]}")
                await conn.commit()
        await db.init_db()
    asyncio.run(_do_clean())
    yield
    asyncio.run(db.close_db())

@pytest.fixture
def client():
    # Return a TestClient with headers pre-configured for the secret key
    c = TestClient(app)
    c.headers.update({"X-API-Key": "test_secret_key"})
    return c

@pytest.fixture
def unauth_client():
    # Return a TestClient without X-API-Key
    return TestClient(app)
