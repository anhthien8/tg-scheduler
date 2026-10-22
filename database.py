"""
Database layer - SQLite with aiosqlite
Multi-account + hourly schedule + max sends support
"""
import asyncio
import contextvars
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite

logger = logging.getLogger("tg-scheduler.database")

DB_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.path.join(DB_DIR, "scheduler.db")

# Timezone: DB lưu UTC (datetime('now')). Mọi query "hôm nay"/ngày phải tính theo giờ VN.
VN_TZ = "+7 hours"


async def _add_column(db, table: str, col_name: str, col_def: str) -> None:
    """ALTER TABLE ADD COLUMN that swallows ONLY 'duplicate column name'.

    The old bare `except Exception: pass` hid every real failure (locked DB,
    syntax error, read-only file) — the whole kol_step funnel schema silently
    never landed in production. Any error other than the expected
    duplicate-column is logged loudly and re-raised.
    """
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
    except Exception as exc:
        if "duplicate column" in str(exc).lower():
            return
        logger.error("[Schema] ALTER %s ADD %s failed: %s", table, col_name, exc)
        raise


# Schema contract: (table, [required columns]). verify_schema() asserts these
# against the live DB after init_db so drift can never be silent again.
REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "ai_followup_chats": (
        "status", "stage", "kol_step", "kol_step_updated_at",
        "step_reminders_sent", "last_step_reminder_at",
        "intent_score", "lead_tier", "last_user_message_at", "human_takeover_at",
    ),
    "kol_profiles": (
        "profile_json", "channel_link", "platform", "followers", "weex_uid",
        "volume_claimed", "volume_verified", "offer_version", "selected_campaign",
        "vip_code", "affiliate_link", "missing_items",
    ),
    "kol_step_events": ("account_id", "user_id", "from_step", "to_step", "actor"),
    "kol_offer_ladder_history": ("version", "ladder_json"),
    "lead_score_history": ("total_score", "tier", "signals_json"),
    "dm_campaign_logs": ("campaign_id", "target_user_id", "status"),
}

# Column definitions for every required column, so repair_schema() can re-add
# a column that was dropped after startup. Kept in sync with the ALTER blocks
# in init_db().
SCHEMA_COLUMN_DEFS: dict[tuple[str, str], str] = {
    ("ai_followup_chats", "status"): "TEXT DEFAULT 'active'",
    ("ai_followup_chats", "stage"): "TEXT DEFAULT 'new'",
    ("ai_followup_chats", "kol_step"): "TEXT",
    ("ai_followup_chats", "kol_step_updated_at"): "TEXT",
    ("ai_followup_chats", "step_reminders_sent"): "INTEGER DEFAULT 0",
    ("ai_followup_chats", "last_step_reminder_at"): "TEXT",
    ("ai_followup_chats", "intent_score"): "INTEGER DEFAULT 0",
    ("ai_followup_chats", "lead_tier"): "TEXT DEFAULT 'Tier C'",
    ("ai_followup_chats", "last_user_message_at"): "TEXT",
    ("ai_followup_chats", "human_takeover_at"): "TEXT",
    ("kol_profiles", "profile_json"): "TEXT DEFAULT '{}'",
    ("kol_profiles", "channel_link"): "TEXT",
    ("kol_profiles", "platform"): "TEXT",
    ("kol_profiles", "followers"): "TEXT",
    ("kol_profiles", "weex_uid"): "TEXT",
    ("kol_profiles", "volume_claimed"): "TEXT",
    ("kol_profiles", "volume_verified"): "TEXT",
    ("kol_profiles", "offer_version"): "INTEGER",
    ("kol_profiles", "selected_campaign"): "TEXT",
    ("kol_profiles", "vip_code"): "TEXT",
    ("kol_profiles", "affiliate_link"): "TEXT",
    ("kol_profiles", "missing_items"): "TEXT",
    ("kol_step_events", "account_id"): "INTEGER NOT NULL DEFAULT 0",
    ("kol_step_events", "user_id"): "INTEGER NOT NULL DEFAULT 0",
    ("kol_step_events", "from_step"): "TEXT",
    ("kol_step_events", "to_step"): "TEXT NOT NULL DEFAULT 'prospect'",
    ("kol_step_events", "actor"): "TEXT DEFAULT 'system'",
    ("kol_offer_ladder_history", "version"): "INTEGER NOT NULL DEFAULT 1",
    ("kol_offer_ladder_history", "ladder_json"): "TEXT NOT NULL DEFAULT '{}'",
    ("lead_score_history", "total_score"): "INTEGER DEFAULT 0",
    ("lead_score_history", "tier"): "TEXT DEFAULT 'Cold'",
    ("lead_score_history", "signals_json"): "TEXT DEFAULT '{}'",
    ("dm_campaign_logs", "campaign_id"): "INTEGER NOT NULL DEFAULT 0",
    ("dm_campaign_logs", "target_user_id"): "INTEGER NOT NULL DEFAULT 0",
    ("dm_campaign_logs", "status"): "TEXT NOT NULL DEFAULT 'failed'",
}


async def verify_schema() -> list[str]:
    """Return a list of human-readable schema problems; empty list == healthy.

    Called at the end of init_db. Problems are logged as ERROR so a missing
    migration shows up in the startup log instead of degrading silently.
    """
    problems: list[str] = []
    async with get_db() as db:
        for table, columns in REQUIRED_SCHEMA.items():
            cursor = await db.execute(f"PRAGMA table_info({table})")
            have = {row[1] for row in await cursor.fetchall()}
            if not have:
                problems.append(f"missing table: {table}")
                continue
            for col in columns:
                if col not in have:
                    problems.append(f"missing column: {table}.{col}")
    for p in problems:
        logger.error("[Schema] SELF-CHECK FAILED — %s", p)
    if not problems:
        logger.info("[Schema] self-check OK (%d tables verified)", len(REQUIRED_SCHEMA))
    return problems


def _sqlite_utc_age_hours(value) -> float:
    """Age in hours of a SQLite UTC timestamp ('YYYY-MM-DD HH:MM:SS').

    Returns a huge number for unparsable input so an unknown age NEVER
    releases a pending reservation early.
    """
    from datetime import timezone
    if not value:
        return 10 ** 6
    s = str(value).strip()
    ts = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            ts = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if ts is None:
        try:
            ts = datetime.fromisoformat(s)
        except ValueError:
            return 10 ** 6
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 3600)


async def repair_schema() -> list[str]:
    """Re-add any required column that verify_schema() reported as missing.

    The self-check alone only ALARMS; without a repair step a database that
    lost a column stays broken forever (the ALTERs in init_db run before the
    check, so a column dropped after startup was never restored). The column
    definitions live next to the ones init_db uses, so a repair is just one
    more ALTER TABLE ADD COLUMN.
    """
    problems = await verify_schema()
    if not problems:
        return []
    fixed: list[str] = []
    async with get_db() as db:
        for problem in problems:
            if not problem.startswith("missing column: "):
                continue
            target = problem[len("missing column: "):]
            table, _, col = target.partition(".")
            col_def = SCHEMA_COLUMN_DEFS.get((table, col))
            if not col_def:
                continue
            try:
                await _add_column(db, table, col, col_def)
                fixed.append(target)
                logger.warning("[Schema] REPAIRED missing column %s", target)
            except Exception as exc:
                logger.error("[Schema] could not repair %s: %s", target, exc)
    if fixed:
        await db.commit()
    return fixed

class ConnectionPool:
    def __init__(self, db_path: str, max_connections: int = 10, timeout: float = 10.0):
        self.db_path = db_path
        self.max_connections = max_connections
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(max_connections)
        self._queue = asyncio.Queue()
        self._connections = []
        self._lock = asyncio.Lock()

    async def _create_connection(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # aiosqlite.connect() spawns a worker thread that finishes opening the
        # file even if this await is cancelled. Without a guard the resulting
        # Connection is orphaned: its thread stays alive and Windows keeps the
        # .db file locked. Shield the connect, and on cancellation wait for it
        # to finish so the connection can be closed properly.
        connect_task = asyncio.ensure_future(aiosqlite.connect(self.db_path))
        try:
            conn = await asyncio.shield(connect_task)
        except asyncio.CancelledError:
            conn = None
            try:
                conn = await asyncio.shield(connect_task)
            except Exception:
                pass
            if conn is not None:
                try:
                    await asyncio.shield(conn.close())
                except Exception:
                    pass
            raise
        initialized = False
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
            # Performance tuning: larger cache, temp in RAM, memory-mapped I/O
            await conn.execute("PRAGMA cache_size=-8000")      # 8MB page cache (default ~2MB)
            await conn.execute("PRAGMA temp_store=MEMORY")     # temp tables in RAM
            await conn.execute("PRAGMA mmap_size=67108864")    # 64MB memory-mapped I/O
            initialized = True
            return conn
        finally:
            if not initialized:
                await conn.close()

    async def acquire(self) -> aiosqlite.Connection:
        # Wait for a slot in the pool to be available
        await self._semaphore.acquire()
        
        acquired = False
        conn = None
        try:
            async with self._lock:
                # Clean up any dead connections in _connections
                self._connections[:] = [c for c in self._connections if getattr(c, "_connection", None) is not None]
                
                # Check if there is an idle connection in the queue
                while not self._queue.empty():
                    conn = self._queue.get_nowait()
                    if getattr(conn, "_connection", None) is not None:
                        acquired = True
                        return conn
                    else:
                        if conn in self._connections:
                            self._connections.remove(conn)
                
                # If no idle connection, create a new one since we have a slot
                conn = await self._create_connection()
                self._connections.append(conn)
                acquired = True
                return conn
        finally:
            if not acquired:
                if conn is not None:
                    if conn in self._connections:
                        self._connections.remove(conn)
                    try:
                        await asyncio.shield(conn.close())
                    except Exception:
                        pass
                self._semaphore.release()

    async def release(self, conn: aiosqlite.Connection):
        try:
            if getattr(conn, "_connection", None) is not None:
                self._queue.put_nowait(conn)
            else:
                try:
                    async with self._lock:
                        if conn in self._connections:
                            self._connections.remove(conn)
                except asyncio.CancelledError:
                    if conn in self._connections:
                        self._connections.remove(conn)
                    raise
        finally:
            self._semaphore.release()

    async def close_all(self):
        async with self._lock:
            for conn in self._connections:
                try:
                    await conn.close()
                except Exception as e:
                    logger.debug("Error closing connection: %s", e)
            self._connections.clear()
            while not self._queue.empty():
                self._queue.get_nowait()

_pool = None
_current_db_conn: "contextvars.ContextVar[aiosqlite.Connection | None]" = contextvars.ContextVar("_current_db_conn", default=None)
# The task that owns the connection in _current_db_conn. asyncio.create_task
# COPIES the context, so a child task would otherwise inherit and reuse the
# parent's connection (which the parent releases while the child still holds it).
_current_db_owner: "contextvars.ContextVar[object | None]" = contextvars.ContextVar("_current_db_owner", default=None)

@asynccontextmanager
async def get_db():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DB_PATH)

    # Reuse the connection only if THIS task acquired it (prevents nested
    # deadlock) — never a connection inherited from a parent task via a copied
    # context, which would mean two tasks sharing one aiosqlite connection.
    current_task = asyncio.current_task()
    existing = _current_db_conn.get(None)
    if (existing is not None
            and getattr(existing, "_connection", None) is not None
            and _current_db_owner.get(None) is current_task):
        yield existing
        return

    conn = await _pool.acquire()
    token = _current_db_conn.set(conn)
    owner_token = _current_db_owner.set(current_task)
    try:
        yield conn
        # Ensure no connection is ever returned to the pool with an open write
        # transaction (e.g. a DELETE/UPDATE with rowcount=0 that skipped commit),
        # which would hold the SQLite WAL write lock and cause "database is locked".
        if getattr(conn, "_connection", None) is not None and conn.in_transaction:
            await conn.commit()
    except BaseException:
        try:
            if getattr(conn, "_connection", None) is not None:
                await conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _current_db_owner.reset(owner_token)
        _current_db_conn.reset(token)
        await _pool.release(conn)

async def close_db():
    global _pool
    if _pool is not None:
        await _pool.close_all()
        _pool = None


async def init_db():
    """Initialize database and create tables."""
    os.makedirs(DB_DIR, exist_ok=True)
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DB_PATH)
    async with get_db() as db:

        # Accounts table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                api_id TEXT NOT NULL,
                api_hash TEXT NOT NULL,
                session_name TEXT NOT NULL UNIQUE,
                is_logged_in INTEGER DEFAULT 0,
                is_premium INTEGER DEFAULT 0,
                peerflood_until REAL DEFAULT 0,
                is_flagged INTEGER DEFAULT 0,
                flag_reason TEXT,
                proxy_url TEXT DEFAULT NULL,
                is_paused INTEGER DEFAULT 0,
                paused_at TEXT,
                pause_reason TEXT,
                ai_agent_id INTEGER DEFAULT NULL,
                telegram_user_id INTEGER DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Schedules with account_id, hourly support, max_sends
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL DEFAULT 1,
                name TEXT NOT NULL,
                schedule_type TEXT NOT NULL CHECK(schedule_type IN ('hourly','daily','weekly','monthly','once')),
                time_of_day TEXT NOT NULL,
                days_of_week TEXT,
                day_of_month INTEGER,
                once_date TEXT,
                max_sends INTEGER,
                current_sends INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                msg_order INTEGER DEFAULT 0,
                msg_type TEXT NOT NULL CHECK(msg_type IN ('text','photo','video','document','poll')),
                content TEXT,
                media_path TEXT,
                poll_question TEXT,
                poll_options TEXT,
                poll_multiple INTEGER DEFAULT 0,
                FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                chat_type TEXT,
                FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS send_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                account_id INTEGER,
                message_id INTEGER,
                chat_id INTEGER,
                chat_title TEXT,
                status TEXT NOT NULL CHECK(status IN ('success','failed','skipped')),
                error_message TEXT,
                sent_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Keyword Watchers ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS keyword_watchers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sender_account_ids TEXT NOT NULL DEFAULT '[]',
                keywords TEXT NOT NULL DEFAULT '[]',
                group_ids TEXT NOT NULL DEFAULT '[]',
                cooldown_hours INTEGER DEFAULT 24,
                dm_once INTEGER DEFAULT 0,
                excluded_usernames TEXT NOT NULL DEFAULT '[]',
                reply_in_group INTEGER DEFAULT 0,
                group_reply_text TEXT DEFAULT 'Check my DM 😊',
                group_reply_account_id INTEGER DEFAULT NULL,
                platform TEXT DEFAULT 'telegram',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migrations
        for col_sql in [
            "ALTER TABLE keyword_watchers ADD COLUMN dm_once INTEGER DEFAULT 0",
            "ALTER TABLE keyword_watchers ADD COLUMN excluded_usernames TEXT NOT NULL DEFAULT '[]'",
            # BUG-01: rename account_ids → sender_account_ids (SQLite workaround via copy)
            "ALTER TABLE keyword_watchers ADD COLUMN sender_account_ids TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE keyword_watchers ADD COLUMN watch_type TEXT DEFAULT 'keyword'",
            "ALTER TABLE keyword_watchers ADD COLUMN join_dm_delay_min INTEGER DEFAULT 3",
            "ALTER TABLE keyword_watchers ADD COLUMN join_dm_delay_max INTEGER DEFAULT 15",
            # Observer accounts: the nick(s) that sit IN the group and listen for
            # ChatAction join events. Kept separate from sender_account_ids so a
            # watcher can observe from one nick (e.g. a secondary account that is a
            # member) while DMing from another nick that need not be in the group.
            "ALTER TABLE keyword_watchers ADD COLUMN observer_account_ids TEXT NOT NULL DEFAULT '[]'",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                pass  # Column already exists

        # BUG-01 migration: copy data from old account_ids column if it existed
        try:
            cols_info = await (await db.execute(
                "PRAGMA table_info(keyword_watchers)"
            )).fetchall()
            col_names = [c[1] for c in cols_info]
            if "account_ids" in col_names:
                await db.execute(
                    "UPDATE keyword_watchers SET sender_account_ids = account_ids "
                    "WHERE sender_account_ids = '[]' AND account_ids != '[]'"
                )
                await db.commit()
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS watcher_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watcher_id INTEGER NOT NULL,
                msg_order INTEGER DEFAULT 0,
                msg_type TEXT NOT NULL CHECK(msg_type IN ('text','photo','video','document')),
                content TEXT,
                media_path TEXT,
                variant_index INTEGER DEFAULT 0,
                FOREIGN KEY (watcher_id) REFERENCES keyword_watchers(id) ON DELETE CASCADE
            )
        """)
        # Migration for DBs created before variant_index existed.
        try:
            await db.execute("ALTER TABLE watcher_messages ADD COLUMN variant_index INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass  # Column already exists

        await db.execute("""
            CREATE TABLE IF NOT EXISTS watcher_dm_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watcher_id INTEGER NOT NULL,
                account_id INTEGER,
                target_user_id INTEGER NOT NULL,
                target_username TEXT,
                group_id INTEGER,
                group_title TEXT,
                matched_keyword TEXT,
                platform TEXT DEFAULT 'telegram',
                template_variant_index INTEGER DEFAULT 0,
                status TEXT NOT NULL CHECK(status IN ('success','failed','skipped')),
                error_message TEXT,
                sent_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Settings table (key-value store for app config)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Track blocked (account, chat) pairs per schedule due to repeated failures
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_target_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                fail_count INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                blocked_at TEXT,
                UNIQUE(schedule_id, account_id, chat_id)
            )
        """)

        # Feature #6: dm_blacklist table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dm_blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                reason TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # PeerFlood persistence column
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN peerflood_until REAL DEFAULT 0")
            await db.commit()
        except Exception:
            pass  # Column already exists

        # Feature: reaction_targets — channels to auto-react
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reaction_targets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_link  TEXT NOT NULL,
                channel_id    INTEGER,
                channel_title TEXT,
                account_ids   TEXT DEFAULT '[]',
                reactions     TEXT DEFAULT '["👍"]',
                delay_min     INTEGER DEFAULT 5,
                delay_max     INTEGER DEFAULT 30,
                view_enabled  INTEGER DEFAULT 0,
                view_ratio    REAL DEFAULT 1.0,
                platform      TEXT DEFAULT 'telegram',
                is_active     INTEGER DEFAULT 1,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)

        # Feature: reaction_logs — history of sent reactions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reaction_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id  INTEGER,
                account_id INTEGER,
                channel_id INTEGER,
                msg_id     INTEGER,
                reaction   TEXT,
                platform   TEXT DEFAULT 'telegram',
                status     TEXT DEFAULT 'success',
                error_msg  TEXT,
                sent_at    TEXT DEFAULT (datetime('now'))
            )
        """)

        # Feature #2: Add is_flagged columns to accounts (safe migration)
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN is_flagged INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN flag_reason TEXT")
        except Exception:
            pass
        # is_premium column (migration for existing DBs)
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN is_premium INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        # per-account proxy support
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN proxy_url TEXT DEFAULT NULL")
            await db.commit()
        except Exception:
            pass

        # Auto-pause account columns
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN is_paused INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN paused_at TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN pause_reason TEXT")
        except Exception:
            pass

        # Account-level AI Agent assignment column
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN ai_agent_id INTEGER DEFAULT NULL")
        except Exception:
            pass

        # Durable Telegram identity for internal-account loop prevention.
        try:
            await db.execute("ALTER TABLE accounts ADD COLUMN telegram_user_id INTEGER DEFAULT NULL")
        except Exception:
            pass
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_telegram_user_id "
            "ON accounts(telegram_user_id) WHERE telegram_user_id IS NOT NULL"
        )

        # C3: Online/Offline Cycle — per-account active hours
        for _ah_col, _ah_def in [
            ("active_hours_start", "TEXT DEFAULT '08:00'"),
            ("active_hours_end",   "TEXT DEFAULT '22:00'"),
            ("active_timezone",    "TEXT DEFAULT 'Asia/Ho_Chi_Minh'"),
        ]:
            try:
                await db.execute(f"ALTER TABLE accounts ADD COLUMN {_ah_col} {_ah_def}")
            except Exception:
                pass

        # Member lang_code & Campaign auto_translate_native columns
        try:
            await db.execute("ALTER TABLE scraped_members ADD COLUMN lang_code TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE dm_campaigns ADD COLUMN auto_translate_native INTEGER DEFAULT 1")
        except Exception:
            pass

        # View boost columns for reaction_targets
        try:
            await db.execute("ALTER TABLE reaction_targets ADD COLUMN view_enabled INTEGER DEFAULT 0")
            await db.execute("ALTER TABLE reaction_targets ADD COLUMN view_ratio REAL DEFAULT 1.0")
            await db.commit()
        except Exception:
            pass

        # Random react count columns for reaction_targets (0 = all accounts react)
        try:
            await db.execute("ALTER TABLE reaction_targets ADD COLUMN react_count_min INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE reaction_targets ADD COLUMN react_count_max INTEGER DEFAULT 0")
        except Exception:
            pass

        await db.commit()

        # Auto-migrate reply_in_group columns
        for _col, _coldef in [
            ("reply_in_group",        "INTEGER DEFAULT 0"),
            ("group_reply_text",      "TEXT DEFAULT 'Check my DM 😊'"),
            ("group_reply_account_id","INTEGER DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE keyword_watchers ADD COLUMN {_col} {_coldef}")
                logger.info(f"Migration: keyword_watchers.{_col} added")
            except Exception:
                pass  # column already exists

        # ── DM Reply Tracker ────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dm_replies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                watcher_id      INTEGER,
                account_id      INTEGER NOT NULL,
                sender_user_id  INTEGER NOT NULL,
                sender_username TEXT,
                sender_name     TEXT,
                message_text    TEXT,
                platform        TEXT DEFAULT 'telegram',
                is_read         INTEGER DEFAULT 0,
                received_at     TEXT DEFAULT (datetime('now'))
            )
        """)
        # Index for fast unread-count lookups
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dm_replies_unread "
            "ON dm_replies(is_read, received_at DESC)"
        )
        # Compound index for outreach nudge candidate detection (§7 T7.7)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dm_replies_sender "
            "ON dm_replies(sender_user_id, received_at)"
        )
        await db.commit()

        # ── Discord Bots ──────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS discord_bots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                bot_token     TEXT NOT NULL,
                bot_user_id   TEXT,
                bot_username  TEXT,
                guild_count   INTEGER DEFAULT 0,
                is_connected  INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Multi-platform migrations ─────────────────────────────────────
        _platform_migrations = [
            ("keyword_watchers", "platform", "TEXT DEFAULT 'telegram'"),
            ("watcher_dm_logs",  "platform", "TEXT DEFAULT 'telegram'"),
            ("reaction_targets", "platform", "TEXT DEFAULT 'telegram'"),
            ("reaction_logs",    "platform", "TEXT DEFAULT 'telegram'"),
            ("dm_replies",       "platform", "TEXT DEFAULT 'telegram'"),
        ]
        for _tbl, _col, _coldef in _platform_migrations:
            try:
                await db.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_coldef}")
            except Exception:
                pass  # column already exists
        await db.commit()

        # ── DM Campaign daily limit split & exclude_previous_dms migration ───────────────────────
        for _col, _coldef in [
            ("daily_limit_premium", "INTEGER DEFAULT 60"),
            ("daily_limit_normal", "INTEGER DEFAULT 10"),
            ("exclude_previous_dms", "INTEGER DEFAULT 1"),
        ]:
            try:
                await db.execute(f"ALTER TABLE dm_campaigns ADD COLUMN {_col} {_coldef}")
            except Exception:
                pass  # column already exists
        # Migrate old daily_limit value to daily_limit_premium if exists
        try:
            await db.execute("""
                UPDATE dm_campaigns SET daily_limit_premium = daily_limit
                WHERE daily_limit IS NOT NULL AND daily_limit_premium = 60
                AND daily_limit != 60
            """)
        except Exception:
            pass
        await db.commit()

        # ── DM Campaign scheduling migration ───────────────────────
        for _col, _coldef in [
            ("scheduled_at", "TEXT DEFAULT NULL"),
            ("target_timezone", "TEXT DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE dm_campaigns ADD COLUMN {_col} {_coldef}")
            except Exception:
                pass  # column already exists
        await db.commit()

        # ── DM Campaign clone exclusion migration ───────────────────────
        for _col, _coldef in [
            ("exclude_campaign_ids", "TEXT DEFAULT '[]'"),
        ]:
            try:
                await db.execute(f"ALTER TABLE dm_campaigns ADD COLUMN {_col} {_coldef}")
            except Exception:
                pass  # column already exists
        await db.commit()

        # ── DM Campaign auto-resume (SpamBot unlock) migration ───────────────────────
        for _col, _coldef in [
            ("auto_resume", "INTEGER DEFAULT 1"),
        ]:
            try:
                await db.execute(f"ALTER TABLE dm_campaigns ADD COLUMN {_col} {_coldef}")
            except Exception:
                pass  # column already exists
        await db.commit()

        # ── Lead scoring / ICP pre-send filter migration ───────────────────────
        # min_lead_score: 0 disables filtering (default). >0 skips members whose
        # lead-quality score is below the threshold. See lead_scoring.py.
        for _col, _coldef in [
            ("min_lead_score", "INTEGER DEFAULT 0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE dm_campaigns ADD COLUMN {_col} {_coldef}")
            except Exception:
                pass  # column already exists
        await db.commit()

        # ── Member Scraping ───────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scraped_members (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scrape_job_id   TEXT NOT NULL,
                account_id      INTEGER NOT NULL,
                group_id        INTEGER NOT NULL,
                group_title     TEXT,
                user_id         INTEGER NOT NULL,
                username        TEXT,
                first_name      TEXT,
                last_name       TEXT,
                phone           TEXT,
                is_bot          INTEGER DEFAULT 0,
                is_premium      INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'active',
                last_seen       TEXT,
                lang_code       TEXT DEFAULT NULL,
                scraped_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(scrape_job_id, user_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scraped_members_job "
            "ON scraped_members(scrape_job_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scraped_members_job_username "
            "ON scraped_members(scrape_job_id, username)"
        )

        # ── Batch Scrape Channels ──────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS batch_scrape_channels (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_job_id    TEXT NOT NULL,
                channel_username TEXT NOT NULL,
                channel_title   TEXT,
                channel_id      INTEGER,
                status          TEXT DEFAULT 'pending',
                member_count    INTEGER DEFAULT 0,
                error_message   TEXT,
                started_at      TEXT,
                finished_at     TEXT
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_batch_scrape_channels_job "
            "ON batch_scrape_channels(batch_job_id)"
        )

        # ── DM Campaigns ──────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dm_campaigns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                scrape_job_id   TEXT NOT NULL,
                sender_account_ids TEXT NOT NULL DEFAULT '[]',
                messages        TEXT NOT NULL DEFAULT '[]',
                delay_min       INTEGER DEFAULT 30,
                delay_max       INTEGER DEFAULT 90,
                daily_limit_premium INTEGER DEFAULT 60,
                daily_limit_normal  INTEGER DEFAULT 10,
                use_ai_remix    INTEGER DEFAULT 0,
                exclude_previous_dms INTEGER DEFAULT 1,
                auto_translate_native INTEGER DEFAULT 1,
                scheduled_at    TEXT DEFAULT NULL,
                target_timezone TEXT DEFAULT NULL,
                exclude_campaign_ids TEXT DEFAULT '[]',
                ai_agent_id     INTEGER DEFAULT NULL,
                auto_resume     INTEGER DEFAULT 1,
                min_lead_score  INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'draft',
                total_targets   INTEGER DEFAULT 0,
                sent_count      INTEGER DEFAULT 0,
                failed_count    INTEGER DEFAULT 0,
                skipped_count   INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── DM Campaign Reservations (reserve-before-send idempotency) ─────
        # A crash after Telegram accepts a message but before dm_campaign_logs
        # writes success must not cause startup auto-resume to send it twice.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dm_campaign_reservations (
                campaign_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                account_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (campaign_id, target_user_id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_dm_reservations_status
            ON dm_campaign_reservations(status, updated_at)
        """)

        # ── DM Campaign Logs ──────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dm_campaign_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id     INTEGER NOT NULL,
                account_id      INTEGER,
                target_user_id  INTEGER NOT NULL,
                target_username TEXT,
                template_variant_id INTEGER,
                template_variant_index INTEGER DEFAULT 0,
                status          TEXT NOT NULL CHECK(status IN ('success','failed','skipped')),
                error_message   TEXT,
                sent_at         TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (campaign_id) REFERENCES dm_campaigns(id) ON DELETE CASCADE
            )
        """)

        # ── Outreach Nudges (follow-up lần 2 cho KOL không reply) ────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS nudges (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id     INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                watcher_id     INTEGER,
                campaign_id    INTEGER,
                channel        TEXT NOT NULL,
                nudge_count    INTEGER DEFAULT 1,
                last_nudge_at  TEXT DEFAULT (datetime('now')),
                status         TEXT NOT NULL CHECK(status IN ('sent','skipped','failed')),
                error_message  TEXT,
                UNIQUE(account_id, target_user_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_nudges_lookup ON nudges(account_id, target_user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_nudges_last ON nudges(last_nudge_at)"
        )

        # ── DM Templates ──────────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dm_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                messages TEXT NOT NULL DEFAULT '[]',
                is_default INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Auto-Reply Rules ──────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_reply_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                trigger_type TEXT DEFAULT 'keyword',
                trigger_keywords TEXT DEFAULT '[]',
                reply_messages TEXT DEFAULT '[]',
                account_ids TEXT DEFAULT '[]',
                use_ai INTEGER DEFAULT 0,
                ai_system_prompt TEXT,
                max_replies_per_user INTEGER DEFAULT 3,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Auto-Reply Logs ───────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_reply_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                account_id INTEGER,
                user_id INTEGER,
                username TEXT,
                trigger_text TEXT,
                reply_text TEXT,
                status TEXT DEFAULT 'success',
                sent_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── AI Follow-Up Sales Agent Chats ────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_followup_chats (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                username        TEXT,
                name            TEXT,
                campaign_id     INTEGER,
                watcher_id      INTEGER,
                status          TEXT DEFAULT 'active',
                history_json    TEXT DEFAULT '[]',
                reply_count     INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_followup_acc_user
            ON ai_followup_chats(account_id, user_id)
        """)

        # Auto-pause / ignore any existing Telegram bot chats in ai_followup_chats
        try:
            await db.execute("""
                UPDATE ai_followup_chats 
                SET status = 'bot_ignored' 
                WHERE LOWER(username) LIKE '%bot' OR LOWER(name) LIKE '%bot%' OR user_id IN (777000, 178220800, 4244000, 4244001, 1088515515)
            """)
        except Exception:
            pass

        # Migrations for Crypto BD Lead Metrics & Drip stage in ai_followup_chats
        # NOTE: SQLite forbids ALTER TABLE ADD COLUMN with a non-constant default
        # (e.g. datetime('now')) — such a migration fails silently. So
        # last_user_message_at uses a NULL default and is backfilled below.
        for col_name, col_def in [
            ("intent_score", "INTEGER DEFAULT 0"),
            ("lead_tier", "TEXT DEFAULT 'Tier C'"),
            ("summary", "TEXT DEFAULT ''"),
            ("last_drip_stage", "INTEGER DEFAULT 0"),
            ("last_user_message_at", "TEXT"),
            ("human_takeover_at", "TEXT"),
            ("verification_status", "TEXT DEFAULT 'none'"),
            # KOL lifecycle stage: new -> nurture -> negotiating -> signed | paused.
            # Drives whether the AI may reply, so it is the single source of truth
            # instead of a per-topic on/off the operator has to remember.
            ("stage", "TEXT DEFAULT 'new'"),
            # KOL 5-step funnel (spec docs/spec-kol-starter-package.md §B):
            # prospect -> deal_agreed -> uid_received -> link_confirmed -> first_post -> active
            # (+ declined / dormant). NULL = legacy row not yet migrated (treated as prospect).
            ("kol_step", "TEXT"),
            ("kol_step_updated_at", "TEXT"),
            # Step-based reminders (max 2 per step, >= 48h apart).
            ("step_reminders_sent", "INTEGER DEFAULT 0"),
            ("last_step_reminder_at", "TEXT"),
        ]:
            await _add_column(db, "ai_followup_chats", col_name, col_def)

        # Backfill stage from the legacy status so no chat is left without one.
        # NOTE: only bot_ignored maps to 'paused'. 'needs_human'/'paused_admin'
        # mean "a human is reviewing this lead", NOT "operator turned AI off" —
        # mapping them to 'paused' froze 30+ live conversations behind the
        # v3.7.0 stage gate, so those now land in 'nurture' (AI keeps working).
        try:
            await db.execute("""
                UPDATE ai_followup_chats
                SET stage = CASE
                    WHEN status IN ('needs_human','paused_admin') THEN 'nurture'
                    WHEN status = 'bot_ignored' THEN 'paused'
                    WHEN status = 'active' AND COALESCE(reply_count, 0) > 0 THEN 'nurture'
                    ELSE 'new'
                END
                WHERE stage IS NULL OR stage = ''
            """)
            await db.commit()
        except Exception:
            pass

        # Backfill last_user_message_at for rows created before the column
        # existed, so the drip cadence has a sane anchor (fall back to
        # updated_at, else created_at).
        try:
            await db.execute("""
                UPDATE ai_followup_chats
                SET last_user_message_at = COALESCE(updated_at, created_at, datetime('now'))
                WHERE last_user_message_at IS NULL
            """)
            await db.commit()
        except Exception:
            pass

        # Auto-mark any chat as 'bot_ignored' if username contains 'bot'
        try:
            await db.execute("""
                UPDATE ai_followup_chats
                SET status = 'bot_ignored'
                WHERE status != 'bot_ignored'
                  AND (LOWER(username) LIKE '%bot%' OR LOWER(username) LIKE 'bot%')
            """)
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_agents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                description     TEXT DEFAULT '',
                avatar_emoji    TEXT DEFAULT '🤖',
                provider        TEXT DEFAULT 'gemini',
                model           TEXT DEFAULT '',
                base_url        TEXT DEFAULT '',
                api_keys_json   TEXT DEFAULT '[]',
                system_prompt   TEXT DEFAULT '',
                remix_instruction TEXT DEFAULT '',
                knowledge_base  TEXT DEFAULT '',
                handover_keywords TEXT DEFAULT '[]',
                max_replies     INTEGER DEFAULT 10,
                tone            TEXT DEFAULT 'friendly',
                is_active       INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS kol_profiles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                profile_json    TEXT DEFAULT '{}',
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(account_id, user_id)
            )
        """)

        # Structured KOL memory (spec §B2). profile_json keeps the free-form
        # LLM-extracted facts; these columns mirror the fields the funnel needs
        # so SQL (migration, distribution, missing-items) never has to parse JSON.
        for col_name, col_def in [
            ("channel_link", "TEXT"),
            ("platform", "TEXT"),
            ("followers", "TEXT"),
            ("weex_uid", "TEXT"),
            ("volume_claimed", "TEXT"),
            ("volume_verified", "TEXT"),
            ("offer_version", "INTEGER"),
            ("selected_campaign", "TEXT"),
            ("vip_code", "TEXT"),
            ("affiliate_link", "TEXT"),
            ("missing_items", "TEXT"),
            ("last_kol_reply_at", "TEXT"),
            ("last_our_reply_at", "TEXT"),
        ]:
            await _add_column(db, "kol_profiles", col_name, col_def)

        # Every step transition of a lead (audit + time-per-step analytics).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS kol_step_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                from_step       TEXT,
                to_step         TEXT NOT NULL,
                actor           TEXT DEFAULT 'system',
                reason          TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_kol_step_events_lead ON kol_step_events(account_id, user_id, id)")

        # Version history of the pre-approved commission ladder (spec §A1).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS kol_offer_ladder_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                version         INTEGER NOT NULL,
                ladder_json     TEXT NOT NULL,
                actor           TEXT DEFAULT 'admin',
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS kol_broadcast_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_chat_id  INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                sent_count      INTEGER DEFAULT 0,
                skipped_count   INTEGER DEFAULT 0,
                post_preview    TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(source_chat_id, source_message_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_learned_knowledge (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ai_agent_id     INTEGER NOT NULL,
                source_user_id  INTEGER DEFAULT 0,
                question_pattern TEXT NOT NULL,
                learned_answer  TEXT NOT NULL,
                status          TEXT DEFAULT 'approved',
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        # C4: FTS5 full-text search index for learned knowledge (semantic retrieval)
        await db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS agent_learned_knowledge_fts
            USING fts5(
                question_pattern,
                learned_answer,
                content='agent_learned_knowledge',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
        """)
        # Sync triggers: keep FTS in sync with the main table
        for _fts_trigger in [
            """CREATE TRIGGER IF NOT EXISTS learned_knowledge_fts_ai
               AFTER INSERT ON agent_learned_knowledge BEGIN
                   INSERT INTO agent_learned_knowledge_fts(rowid, question_pattern, learned_answer)
                   VALUES (new.id, new.question_pattern, new.learned_answer);
               END""",
            """CREATE TRIGGER IF NOT EXISTS learned_knowledge_fts_ad
               AFTER DELETE ON agent_learned_knowledge BEGIN
                   INSERT INTO agent_learned_knowledge_fts(agent_learned_knowledge_fts, rowid, question_pattern, learned_answer)
                   VALUES ('delete', old.id, old.question_pattern, old.learned_answer);
               END""",
            """CREATE TRIGGER IF NOT EXISTS learned_knowledge_fts_au
               AFTER UPDATE ON agent_learned_knowledge BEGIN
                   INSERT INTO agent_learned_knowledge_fts(agent_learned_knowledge_fts, rowid, question_pattern, learned_answer)
                   VALUES ('delete', old.id, old.question_pattern, old.learned_answer);
                   INSERT INTO agent_learned_knowledge_fts(rowid, question_pattern, learned_answer)
                   VALUES (new.id, new.question_pattern, new.learned_answer);
               END""",
        ]:
            try:
                await db.execute(_fts_trigger)
            except Exception:
                pass  # trigger already exists
        # One-time FTS rebuild to index pre-existing data
        try:
            await db.execute(
                "INSERT INTO agent_learned_knowledge_fts(agent_learned_knowledge_fts) VALUES('rebuild')"
            )
        except Exception:
            pass

        # ── C5: Referral / Viral Loop ─────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_codes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                referrer_user_id INTEGER NOT NULL,
                code            TEXT NOT NULL UNIQUE,
                max_uses        INTEGER DEFAULT 10,
                uses_count      INTEGER DEFAULT 0,
                is_active       INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT (datetime('now')),
                expires_at      TEXT
            )
        """)
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_code ON referral_codes(code)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_referral_referrer ON referral_codes(referrer_user_id)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_attributions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referral_code_id INTEGER NOT NULL,
                referrer_user_id INTEGER NOT NULL,
                referred_user_id INTEGER NOT NULL,
                referred_username TEXT,
                status          TEXT DEFAULT 'pending',
                attributed_at   TEXT DEFAULT (datetime('now')),
                converted_at    TEXT,
                FOREIGN KEY (referral_code_id) REFERENCES referral_codes(id)
            )
        """)


        # ── C2: Lead Score History ──────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lead_score_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                static_score INTEGER DEFAULT 0,
                dynamic_score INTEGER DEFAULT 0,
                total_score INTEGER DEFAULT 0,
                tier TEXT DEFAULT 'Cold',
                signals_json TEXT DEFAULT '{}',
                scored_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_score_history_user ON lead_score_history(user_id, scored_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_score_history_account ON lead_score_history(account_id, user_id)")

        # ── C6: Warmup Progress ────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warmup_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL UNIQUE,
                warmup_job_id INTEGER,
                stage INTEGER DEFAULT 1,
                started_at TEXT DEFAULT (datetime('now')),
                messages_sent INTEGER DEFAULT 0,
                reactions_sent INTEGER DEFAULT 0,
                channels_joined INTEGER DEFAULT 0,
                history_reads INTEGER DEFAULT 0,
                forwards_sent INTEGER DEFAULT 0,
                profiles_viewed INTEGER DEFAULT 0,
                last_flood_at TEXT,
                graduated_at TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_warmup_progress_stage ON warmup_progress(stage)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_warmup_progress_account ON warmup_progress(account_id)")

        # ── C6: Warmup Action Log ──────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warmup_action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                target_chat_id INTEGER,
                target_message_id INTEGER,
                status TEXT DEFAULT 'success',
                error_detail TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_warmup_log_account ON warmup_action_log(account_id, created_at DESC)")


        await db.execute("CREATE INDEX IF NOT EXISTS idx_attribution_referrer ON referral_attributions(referrer_user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_attribution_referred ON referral_attributions(referred_user_id)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_user_id INTEGER NOT NULL,
                attribution_id  INTEGER NOT NULL,
                reward_type     TEXT NOT NULL,
                reward_detail   TEXT,
                is_claimed      INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (attribution_id) REFERENCES referral_attributions(id)
            )
        """)

                # ── Invite Campaigns ─────────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS invite_campaigns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                scrape_job_id   TEXT NOT NULL,
                target_chat     TEXT NOT NULL,
                target_chat_title TEXT,
                invite_mode     TEXT NOT NULL DEFAULT 'direct',
                invite_link     TEXT,
                sender_account_ids TEXT NOT NULL DEFAULT '[]',
                daily_limit     INTEGER DEFAULT 50,
                delay_min       INTEGER DEFAULT 45,
                delay_max       INTEGER DEFAULT 120,
                dm_message      TEXT,
                use_ai_remix    INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'draft',
                total_targets   INTEGER DEFAULT 0,
                invited_count   INTEGER DEFAULT 0,
                failed_count    INTEGER DEFAULT 0,
                skipped_count   INTEGER DEFAULT 0,
                schedule_enabled INTEGER DEFAULT 0,
                schedule_time   TEXT,
                schedule_days   INTEGER DEFAULT 7,
                schedule_days_remaining INTEGER,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Invite Campaign Logs ─────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS invite_campaign_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id     INTEGER NOT NULL,
                account_id      INTEGER,
                target_user_id  INTEGER NOT NULL,
                target_username TEXT,
                status          TEXT NOT NULL CHECK(status IN ('invited','failed','skipped','already_member')),
                error_message   TEXT,
                invited_at      TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (campaign_id) REFERENCES invite_campaigns(id) ON DELETE CASCADE
            )
        """)

        # ── Telegram Command Bot Action Log ───────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS command_actions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_user_id   INTEGER NOT NULL,
                action          TEXT NOT NULL,
                account_id      INTEGER,
                target_username TEXT,
                message_text    TEXT,
                result          TEXT DEFAULT 'success',
                details         TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Telegram Forum Sub-Topics Mapping ────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS telegram_topics (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id            INTEGER NOT NULL,
                topic_id            INTEGER NOT NULL,
                account_id          INTEGER NOT NULL,
                user_id             INTEGER NOT NULL,
                username            TEXT,
                name                TEXT,
                campaign_name       TEXT,
                status              TEXT DEFAULT 'active',
                last_lead_message   TEXT,
                created_at          TEXT DEFAULT (datetime('now')),
                updated_at          TEXT DEFAULT (datetime('now')),
                UNIQUE(group_id, topic_id),
                UNIQUE(group_id, account_id, user_id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_topics_lookup ON telegram_topics(group_id, account_id, user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_topics_thread ON telegram_topics(group_id, topic_id)")

        # ── Performance Indexes ──────────────────────────────────────────────
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_campaign_logs_campaign ON dm_campaign_logs(campaign_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_watcher_dm_logs_lookup ON watcher_dm_logs(watcher_id, target_user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_watcher_dm_logs_acc ON watcher_dm_logs(account_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_campaign_logs_acc ON dm_campaign_logs(account_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_replies_sender ON dm_replies(sender_user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_campaign_logs_target ON dm_campaign_logs(target_user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_watcher_dm_logs_target ON watcher_dm_logs(target_user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_auto_reply_logs_lookup ON auto_reply_logs(rule_id, user_id)")

        # ── Invite Campaign Indexes ──
        await db.execute("CREATE INDEX IF NOT EXISTS idx_invite_campaign_logs_campaign ON invite_campaign_logs(campaign_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_invite_campaign_logs_target ON invite_campaign_logs(target_user_id)")

        # ── Performance Indexes on Foreign Keys ──
        await db.execute("CREATE INDEX IF NOT EXISTS idx_send_logs_schedule_id ON send_logs(schedule_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_send_logs_account_id ON send_logs(account_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reaction_logs_target_acc ON reaction_logs(target_id, account_id, msg_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reaction_logs_account_id ON reaction_logs(account_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reaction_logs_sent_at ON reaction_logs(sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_schedule_messages_schedule_id ON schedule_messages(schedule_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_schedule_targets_schedule_id ON schedule_targets(schedule_id)")

        # ── Performance Indexes for Daily Statistics range scans ──
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_campaign_logs_sent_at ON dm_campaign_logs(sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_watcher_dm_logs_sent_at ON watcher_dm_logs(sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_replies_received_at ON dm_replies(received_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_send_logs_status_sent ON send_logs(status, sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_campaign_logs_acc_stat_sent ON dm_campaign_logs(account_id, status, sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_watcher_dm_logs_acc_stat_sent ON watcher_dm_logs(account_id, status, sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_replies_acc_unread ON dm_replies(account_id, is_read, received_at)")

        # ── v3.2.4 speed: cover common hot queries ──
        # updated_at index serves ORDER BY updated_at DESC (status != '...' is an
        # inequality so a (status, updated_at) prefix can't be used)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_aifu_updated ON ai_followup_chats(updated_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reaction_logs_acc_sent ON reaction_logs(account_id, sent_at)")

        # ── v3.2.5 Quick-Win indexes ──
        await db.execute("CREATE INDEX IF NOT EXISTS idx_followup_stage_updated ON ai_followup_chats(stage, updated_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_followup_account_id ON ai_followup_chats(account_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_replies_sender_received ON dm_replies(sender_user_id, received_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_watcher_dm_logs_target_status ON watcher_dm_logs(target_user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_campaign_logs_target_status ON dm_campaign_logs(target_user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_replies_unread_cov ON dm_replies(is_read, sender_user_id, sender_username)")


        # ══════════════════════════════════════════════════════════════
        # ══ Warmup / Seeding Tables ══════════════════════════════════
        # ══════════════════════════════════════════════════════════════

        await db.execute("""
            CREATE TABLE IF NOT EXISTS warmup_groups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                chat_id         TEXT NOT NULL,
                chat_title      TEXT,
                chat_username   TEXT,
                status          TEXT DEFAULT 'active',
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS warmup_scripts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id        INTEGER NOT NULL,
                content         TEXT NOT NULL,
                msg_type        TEXT DEFAULT 'text',
                use_ai_remix    INTEGER DEFAULT 1,
                sort_order      INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (group_id) REFERENCES warmup_groups(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS warmup_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id        INTEGER NOT NULL,
                account_ids     TEXT NOT NULL DEFAULT '[]',
                interval_min    INTEGER DEFAULT 30,
                interval_max    INTEGER DEFAULT 120,
                daily_post_limit INTEGER DEFAULT 10,
                schedule_start  TEXT DEFAULT '09:00',
                schedule_end    TEXT DEFAULT '22:00',
                status          TEXT DEFAULT 'stopped',
                posts_today     INTEGER DEFAULT 0,
                last_post_at    TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (group_id) REFERENCES warmup_groups(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS warmup_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          INTEGER NOT NULL,
                group_id        INTEGER NOT NULL,
                account_id      INTEGER NOT NULL,
                script_id       INTEGER,
                message_sent    TEXT,
                status          TEXT DEFAULT 'success',
                error_message   TEXT,
                posted_at       TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (job_id) REFERENCES warmup_jobs(id) ON DELETE CASCADE
            )
        """)

        # ── Warmup Indexes ──
        await db.execute("CREATE INDEX IF NOT EXISTS idx_warmup_logs_job ON warmup_logs(job_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_warmup_logs_group ON warmup_logs(group_id)")

        # ══════════════════════════════════════════════════════════════
        # ══ Smart Template Rotation ══════════════════════════════════
        # ══════════════════════════════════════════════════════════════

        # Add template_variant tracking to campaign logs
        try:
            await db.execute("ALTER TABLE dm_campaigns ADD COLUMN ai_agent_id INTEGER DEFAULT NULL")
        except Exception:
            pass  # Column already exists
        try:
            await db.execute("ALTER TABLE dm_campaign_logs ADD COLUMN template_variant_id INTEGER")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE dm_campaign_logs ADD COLUMN template_variant_index INTEGER DEFAULT 0")
        except Exception:
            pass

        # Add template_variant tracking to watcher DM logs
        try:
            await db.execute("ALTER TABLE watcher_dm_logs ADD COLUMN template_variant_index INTEGER DEFAULT 0")
        except Exception:
            pass

        # Template performance tracking table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS template_performance (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id     INTEGER,
                variant_index   INTEGER DEFAULT 0,
                campaign_id     INTEGER,
                watcher_id      INTEGER,
                total_sent      INTEGER DEFAULT 0,
                total_replies   INTEGER DEFAULT 0,
                response_rate   REAL DEFAULT 0.0,
                last_updated    TEXT DEFAULT (datetime('now')),
                UNIQUE(template_id, variant_index, campaign_id, watcher_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_template_perf_template "
            "ON template_performance(template_id)"
        )

        # ── Blacklist username-only support ──
        # Allow username-only blacklist entries (user_id=NULL)
        # by adding a partial unique index on username
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_dm_blacklist_username "
            "ON dm_blacklist(username) WHERE username IS NOT NULL"
        )

        # ══════════════════════════════════════════════════════════════
        # ══ Community Engagement Engine ══════════════════════════════
        # ══════════════════════════════════════════════════════════════

        await db.execute("""
            CREATE TABLE IF NOT EXISTS engagement_campaigns (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT NOT NULL,

                -- Target groups (JSON list of negative chat_ids)
                group_ids           TEXT NOT NULL DEFAULT '[]',
                group_titles        TEXT DEFAULT '[]',

                -- Account configuration
                observer_account_ids TEXT NOT NULL DEFAULT '[]',
                reply_account_ids   TEXT DEFAULT '[]',

                -- Knowledge / AI
                ai_agent_id         INTEGER DEFAULT NULL,
                knowledge_extra     TEXT DEFAULT '',
                reply_mode          TEXT DEFAULT 'reactive',

                -- Trigger keywords (JSON list)
                trigger_keywords    TEXT DEFAULT '[]',

                -- Rate limits
                max_replies_per_hour_per_group INTEGER DEFAULT 4,
                max_replies_per_day_per_group  INTEGER DEFAULT 12,
                min_gap_seconds     INTEGER DEFAULT 180,
                participation_min   INTEGER DEFAULT 1,
                participation_max   INTEGER DEFAULT 1,

                -- Schedule window (Vietnam time HH:MM)
                schedule_start      TEXT DEFAULT '13:00',
                schedule_end        TEXT DEFAULT '23:00',
                active_days         TEXT DEFAULT '[1,2,3,4,5,6,7]',
                schedule_jitter_min INTEGER DEFAULT 0,

                -- Funnel
                signal_group_link   TEXT DEFAULT '',
                signal_group_title  TEXT DEFAULT '',

                -- Status
                status              TEXT DEFAULT 'draft',
                pause_reason        TEXT DEFAULT '',
                platform            TEXT DEFAULT 'telegram',

                -- Consent gate (fail-closed, default DENY)
                operator_consent_confirmed INTEGER DEFAULT 0,
                operator_consent_at        TEXT DEFAULT NULL,
                operator_consent_by        TEXT DEFAULT '',

                created_at          TEXT DEFAULT (datetime('now')),
                updated_at          TEXT DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS engagement_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id     INTEGER NOT NULL,
                group_id        INTEGER,
                group_title     TEXT,
                account_id      INTEGER,
                reply_account_ids TEXT DEFAULT '[]',
                trigger_user_id INTEGER,
                trigger_msg_id  INTEGER,
                trigger_text    TEXT,
                reply_text      TEXT,
                reply_msg_id    INTEGER,
                kind            TEXT DEFAULT 'reply',
                status          TEXT DEFAULT 'success',
                skip_reason     TEXT,
                latency_ms      INTEGER,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)

        # Rate-limit claims: detailed log for each claim attempt
        await db.execute("""
            CREATE TABLE IF NOT EXISTS engagement_claims (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                group_id    INTEGER,
                account_id  INTEGER NOT NULL,
                window_key  TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                kind        TEXT DEFAULT 'reply'
            )
        """)

        # Atomic counters for limit-type windows
        await db.execute("""
            CREATE TABLE IF NOT EXISTS engagement_window_counters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                window_kind TEXT NOT NULL,
                acct_key    INTEGER NOT NULL,
                group_key   INTEGER NOT NULL,
                window_key  TEXT NOT NULL,
                count       INTEGER NOT NULL DEFAULT 0,
                limit_      INTEGER NOT NULL,
                updated_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(window_kind, acct_key, group_key, window_key)
            )
        """)

        # DM attribution map for engagement leads
        await db.execute("""
            CREATE TABLE IF NOT EXISTS engagement_dm_map (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                account_id  INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        # Engagement indexes
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_engagement_claim_gap_unique
                ON engagement_claims(account_id, window_key)
                WHERE window_key LIKE 'gap:%' OR window_key LIKE 'gapacct:%'
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_engagement_claim_acct_time "
            "ON engagement_claims(account_id, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_engagement_claim_group_time "
            "ON engagement_claims(group_id, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_engagement_counter_acct "
            "ON engagement_window_counters(acct_key, window_key)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_engagement_counter_group "
            "ON engagement_window_counters(group_key, window_key)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_engagement_logs_campaign_time "
            "ON engagement_logs(campaign_id, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_engagement_logs_account_time "
            "ON engagement_logs(account_id, created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_engagement_dm_map_lookup "
            "ON engagement_dm_map(account_id, user_id, created_at)"
        )

        # ── v3.9.1 All-Pages Performance Indexes ──
        await db.execute("CREATE INDEX IF NOT EXISTS idx_send_logs_sent_at ON send_logs(sent_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_eng_logs_created_at ON engagement_logs(created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_watcher_dm_logs_watcher_sent ON watcher_dm_logs(watcher_id, sent_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_watcher_dm_logs_status_sent ON watcher_dm_logs(status, sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_campaign_logs_camp_sent ON dm_campaign_logs(campaign_id, sent_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dm_campaign_logs_status_sent ON dm_campaign_logs(status, sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_scraped_members_user_id ON scraped_members(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reaction_logs_status_sent ON reaction_logs(status, sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_wal_status_created ON warmup_action_log(status, created_at DESC)")

        await db.commit()

    # Seed default templates after init
    await seed_default_templates()

    # Schema self-check: assert every column the KOL funnel depends on actually
    # exists in the live DB. A failure here is a BLOCKER logged as ERROR —
    # previously the ALTERs failed silently and the whole 5-step funnel was inert.
    schema_problems = await verify_schema()
    if schema_problems:
        # The ALTERs in init_db run BEFORE the check, so a column dropped after
        # startup was never restored. Repair it, then re-check.
        repaired = await repair_schema()
        if repaired:
            logger.warning("[Schema] repaired %d missing column(s): %s", len(repaired), ", ".join(repaired))
            schema_problems = await verify_schema()

    # One-shot, idempotent backfill of kol_step for legacy leads (spec §B5).
    # Only run when the schema is healthy; migrating against a broken schema
    # would silently mark the FLAG and never retry.
    if schema_problems:
        logger.error("[KOLStep] migration SKIPPED — fix %d schema problem(s) first", len(schema_problems))
    else:
        try:
            result = await migrate_kol_steps_v1()
            if not result.get("skipped"):
                logger.info("[KOLStep] migration result: %s", result)
        except Exception as exc:
            logger.error(f"[KOLStep] migration failed: {exc}")

    # Prune reaction_logs >30 ngày (giữ DB gọn, query nhanh)
    try:
        async with get_db() as db:
            cur = await db.execute(
                "DELETE FROM reaction_logs WHERE sent_at < datetime('now', '-30 days')"
            )
            await db.commit()
            if cur.rowcount > 0:
                logging.getLogger(__name__).info("[DB] Pruned %d reaction_logs >30d", cur.rowcount)
    except Exception as e:
        logging.getLogger(__name__).warning("[DB] reaction_logs prune failed: %s", e)


# ── Account CRUD ──

async def create_account(data: dict) -> int:
    async with get_db() as db:
        proxy_url = data.get("proxy_url") or None
        cursor = await db.execute(
            """INSERT INTO accounts (name, phone, api_id, api_hash, session_name, proxy_url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data["name"], data["phone"], data["api_id"], data["api_hash"], data["session_name"], proxy_url)
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_accounts() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM accounts ORDER BY id")
        return [dict(row) for row in await cursor.fetchall()]

# Alias for backwards compatibility
get_accounts = get_all_accounts


async def get_account(account_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM accounts WHERE id=?", (account_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_account_name(account_id: int, name: str):
    """Update the display name of an account (after fetching real TG profile name)."""
    async with get_db() as conn:
        await conn.execute(
            "UPDATE accounts SET name = ? WHERE id = ?",
            (name, account_id)
        )
        await conn.commit()


async def update_account_telegram_user_id(account_id: int, telegram_user_id: int):
    """Persist the Telegram user_id for an account — durable identity used to detect
    internal accounts (nick chính / nick phụ) messaging each other, independent of
    the in-memory _me_cache (which is empty for paused/disconnected accounts)."""
    async with get_db() as conn:
        await conn.execute(
            "UPDATE accounts SET telegram_user_id = ? WHERE id = ?",
            (telegram_user_id, account_id)
        )
        await conn.commit()


async def get_all_internal_telegram_user_ids() -> set[int]:
    """All known Telegram user_ids belonging to any managed account (including
    paused/disconnected ones, via the durable telegram_user_id column)."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT telegram_user_id FROM accounts WHERE telegram_user_id IS NOT NULL"
        )
        rows = await cursor.fetchall()
        return {row[0] for row in rows}


async def update_account_login_status(account_id: int, is_logged_in: bool):
    async with get_db() as db:
        await db.execute("UPDATE accounts SET is_logged_in=? WHERE id=?",
                         (1 if is_logged_in else 0, account_id))
        await db.commit()


async def update_account_proxy(account_id: int, proxy_url: str | None):
    """Update the proxy URL for a specific account."""
    async with get_db() as db:
        await db.execute("UPDATE accounts SET proxy_url=? WHERE id=?",
                         (proxy_url, account_id))
        await db.commit()


async def update_accounts_proxy_bulk(assignments: dict):
    """Bulk-update proxy_url for many accounts in ONE commit.

    `assignments` maps account_id -> proxy_url (may be None to clear).
    """
    if not assignments:
        return
    async with get_db() as db:
        await db.executemany(
            "UPDATE accounts SET proxy_url=? WHERE id=?",
            [(proxy_url, account_id) for account_id, proxy_url in assignments.items()],
        )
        await db.commit()


async def delete_account(account_id: int):
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        await db.commit()


# ── Schedule CRUD ──

async def create_schedule(data: dict) -> int:
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys=ON")
        cursor = await db.execute(
            """INSERT INTO schedules (account_id, name, schedule_type, time_of_day, days_of_week,
               day_of_month, once_date, max_sends, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.get("account_id", 1), data["name"], data["schedule_type"], data["time_of_day"],
             data.get("days_of_week"), data.get("day_of_month"),
             data.get("once_date"), data.get("max_sends"),
             data.get("is_active", 1))
        )
        schedule_id = cursor.lastrowid

        for msg in data.get("messages", []):
            await db.execute(
                """INSERT INTO schedule_messages (schedule_id, msg_order, msg_type, content, media_path,
                   poll_question, poll_options, poll_multiple)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (schedule_id, msg.get("msg_order", 0), msg["msg_type"],
                 msg.get("content"), msg.get("media_path"),
                 msg.get("poll_question"), msg.get("poll_options"),
                 msg.get("poll_multiple", 0))
            )

        for target in data.get("targets", []):
            await db.execute(
                """INSERT INTO schedule_targets (schedule_id, chat_id, chat_title, chat_type)
                   VALUES (?, ?, ?, ?)""",
                (schedule_id, target["chat_id"], target.get("chat_title"), target.get("chat_type"))
            )

        await db.commit()
        return schedule_id


async def get_all_schedules(limit: int = None, active_only: bool = False) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        
        query = "SELECT s.*, a.name as account_name, a.phone as account_phone FROM schedules s LEFT JOIN accounts a ON s.account_id = a.id"
        params = []
        
        if active_only:
            query += " WHERE s.is_active = 1"
            
        query += " ORDER BY s.created_at DESC"
        
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            
        cursor = await db.execute(query, params)
        schedules = [dict(row) for row in await cursor.fetchall()]
        if not schedules:
            return []

        schedule_ids = [s["id"] for s in schedules]
        msgs_by_sch = {}
        tgts_by_sch = {}
        blocks_by_sch = {}

        # Fetch in chunks of 900
        for i in range(0, len(schedule_ids), 900):
            chunk = schedule_ids[i:i+900]
            placeholders = ",".join("?" for _ in chunk)

            # 1. Fetch messages
            cursor_msgs = await db.execute(
                f"SELECT * FROM schedule_messages WHERE schedule_id IN ({placeholders}) ORDER BY msg_order",
                chunk
            )
            for r in await cursor_msgs.fetchall():
                d = dict(r)
                msgs_by_sch.setdefault(d["schedule_id"], []).append(d)

            # 2. Fetch targets
            cursor_tgts = await db.execute(
                f"SELECT * FROM schedule_targets WHERE schedule_id IN ({placeholders})",
                chunk
            )
            for r in await cursor_tgts.fetchall():
                d = dict(r)
                tgts_by_sch.setdefault(d["schedule_id"], []).append(d)

            # 3. Fetch blocked counts
            cursor_blocks = await db.execute(
                f"SELECT schedule_id, COUNT(*) as cnt FROM schedule_target_blocks WHERE schedule_id IN ({placeholders}) AND is_blocked=1 GROUP BY schedule_id",
                chunk
            )
            for r in await cursor_blocks.fetchall():
                blocks_by_sch[r["schedule_id"]] = r["cnt"]

        for s in schedules:
            s_id = s["id"]
            s["messages"] = msgs_by_sch.get(s_id, [])
            s["targets"] = tgts_by_sch.get(s_id, [])
            s["blocked_count"] = blocks_by_sch.get(s_id, 0)

        return schedules


async def get_schedule(schedule_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT s.*, a.name as account_name, a.phone as account_phone
            FROM schedules s LEFT JOIN accounts a ON s.account_id = a.id
            WHERE s.id=?
        """, (schedule_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        sch = dict(row)

        cursor2 = await db.execute(
            "SELECT * FROM schedule_messages WHERE schedule_id=? ORDER BY msg_order", (schedule_id,))
        sch["messages"] = [dict(r) for r in await cursor2.fetchall()]

        cursor3 = await db.execute(
            "SELECT * FROM schedule_targets WHERE schedule_id=?", (schedule_id,))
        sch["targets"] = [dict(r) for r in await cursor3.fetchall()]

        return sch


async def schedule_exists(schedule_id: int) -> bool:
    """Check if a schedule exists with a lightweight query."""
    async with get_db() as db:
        cursor = await db.execute("SELECT 1 FROM schedules WHERE id=?", (schedule_id,))
        row = await cursor.fetchone()
        return row is not None


async def update_schedule(schedule_id: int, data: dict) -> bool:
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """UPDATE schedules SET account_id=?, name=?, schedule_type=?, time_of_day=?, days_of_week=?,
               day_of_month=?, once_date=?, max_sends=?, is_active=?, updated_at=datetime('now')
               WHERE id=?""",
            (data.get("account_id", 1), data["name"], data["schedule_type"], data["time_of_day"],
             data.get("days_of_week"), data.get("day_of_month"),
             data.get("once_date"), data.get("max_sends"),
             data.get("is_active", 1), schedule_id)
        )

        await db.execute("DELETE FROM schedule_messages WHERE schedule_id=?", (schedule_id,))
        for msg in data.get("messages", []):
            await db.execute(
                """INSERT INTO schedule_messages (schedule_id, msg_order, msg_type, content, media_path,
                   poll_question, poll_options, poll_multiple)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (schedule_id, msg.get("msg_order", 0), msg["msg_type"],
                 msg.get("content"), msg.get("media_path"),
                 msg.get("poll_question"), msg.get("poll_options"),
                 msg.get("poll_multiple", 0))
            )

        await db.execute("DELETE FROM schedule_targets WHERE schedule_id=?", (schedule_id,))
        for target in data.get("targets", []):
            await db.execute(
                """INSERT INTO schedule_targets (schedule_id, chat_id, chat_title, chat_type)
                   VALUES (?, ?, ?, ?)""",
                (schedule_id, target["chat_id"], target.get("chat_title"), target.get("chat_type"))
            )

        await db.commit()
        return True


async def delete_schedule(schedule_id: int) -> bool:
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        await db.commit()
        return True


async def toggle_schedule(schedule_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT is_active FROM schedules WHERE id=?", (schedule_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        new_state = 0 if row["is_active"] else 1
        await db.execute(
            "UPDATE schedules SET is_active=?, updated_at=datetime('now') WHERE id=?",
            (new_state, schedule_id))
        await db.commit()
        return {"id": schedule_id, "is_active": new_state}


async def get_active_schedules() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM schedules WHERE is_active=1")
        schedules = [dict(row) for row in await cursor.fetchall()]
        if not schedules:
            return []

        schedule_ids = [s["id"] for s in schedules]
        msgs_by_sch = {}
        tgts_by_sch = {}

        for i in range(0, len(schedule_ids), 900):
            chunk = schedule_ids[i:i+900]
            placeholders = ",".join("?" for _ in chunk)

            # 1. Fetch messages
            cursor_msgs = await db.execute(
                f"SELECT * FROM schedule_messages WHERE schedule_id IN ({placeholders}) ORDER BY msg_order",
                chunk
            )
            for r in await cursor_msgs.fetchall():
                d = dict(r)
                msgs_by_sch.setdefault(d["schedule_id"], []).append(d)

            # 2. Fetch targets
            cursor_tgts = await db.execute(
                f"SELECT * FROM schedule_targets WHERE schedule_id IN ({placeholders})",
                chunk
            )
            for r in await cursor_tgts.fetchall():
                d = dict(r)
                tgts_by_sch.setdefault(d["schedule_id"], []).append(d)

        for s in schedules:
            s_id = s["id"]
            s["messages"] = msgs_by_sch.get(s_id, [])
            s["targets"] = tgts_by_sch.get(s_id, [])

        return schedules


async def increment_send_count(schedule_id: int) -> dict:
    """Increment current_sends and auto-deactivate if max_sends reached. Returns updated state."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "UPDATE schedules SET current_sends = current_sends + 1 WHERE id=?", (schedule_id,))
        await db.commit()

        cursor = await db.execute(
            "SELECT current_sends, max_sends, is_active FROM schedules WHERE id=?", (schedule_id,))
        row = await cursor.fetchone()
        if not row:
            return {"reached_limit": False}

        current = row["current_sends"]
        maximum = row["max_sends"]

        if maximum and current >= maximum:
            await db.execute(
                "UPDATE schedules SET is_active=0, updated_at=datetime('now') WHERE id=?", (schedule_id,))
            await db.commit()
            return {"reached_limit": True, "current_sends": current, "max_sends": maximum}

        return {"reached_limit": False, "current_sends": current, "max_sends": maximum}


async def reset_send_count(schedule_id: int):
    async with get_db() as db:
        await db.execute("UPDATE schedules SET current_sends=0 WHERE id=?", (schedule_id,))
        await db.commit()


# ── Send Logs ──

async def add_send_log(schedule_id: int, account_id: int | None, message_id: int | None,
                       chat_id: int, chat_title: str, status: str, error_message: str | None = None):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO send_logs (schedule_id, account_id, message_id, chat_id, chat_title, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (schedule_id, account_id, message_id, chat_id, chat_title, status, error_message)
        )
        await db.commit()


async def get_send_logs(limit: int = 100, offset: int = 0,
                        schedule_id: int | None = None,
                        status: str | None = None,
                        account_id: int | None = None) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        where = []
        params = []
        if schedule_id:
            where.append("sl.schedule_id=?")
            params.append(schedule_id)
        if status:
            where.append("sl.status=?")
            params.append(status)
        if account_id:
            where.append("sl.account_id=?")
            params.append(account_id)

        where_str = " WHERE " + " AND ".join(where) if where else ""

        # BUG-03 fix: count query uses same sl.-prefixed where clauses
        count_cursor = await db.execute(
            f"""SELECT COUNT(*) as cnt FROM send_logs sl{where_str}""", params)
        total = (await count_cursor.fetchone())["cnt"]

        cursor = await db.execute(
            f"""SELECT sl.*, a.name AS account_name
               FROM send_logs sl
               LEFT JOIN accounts a ON a.id = sl.account_id
               {where_str}
               ORDER BY sl.sent_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset])
        logs = [dict(r) for r in await cursor.fetchall()]

        return {"total": total, "logs": logs, "limit": limit, "offset": offset}


async def get_log_stats() -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT 
                COALESCE(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END), 0) as success,
                COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0) as failed,
                COALESCE(SUM(CASE WHEN status='success'
                    AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours')
                    THEN 1 ELSE 0 END), 0) as today
            FROM send_logs
        """)
        log_row = await cursor.fetchone()
        success = log_row["success"] if log_row else 0
        failed = log_row["failed"] if log_row else 0
        today = log_row["today"] if log_row else 0

        cursor = await db.execute("""
            SELECT
                (SELECT COUNT(*) FROM schedules WHERE is_active=1) as active_schedules,
                (SELECT COUNT(*) FROM schedules) as total_schedules,
                (SELECT COUNT(*) FROM accounts) as total_accounts
        """)
        meta_row = await cursor.fetchone()
        active_schedules = meta_row["active_schedules"] if meta_row else 0
        total_schedules = meta_row["total_schedules"] if meta_row else 0
        total_accounts = meta_row["total_accounts"] if meta_row else 0

        return {
            "total_sent": success + failed,
            "success": success,
            "failed": failed,
            "today": today,
            "active_schedules": active_schedules,
            "total_schedules": total_schedules,
            "total_accounts": total_accounts
        }


# ── Keyword Watcher CRUD ──

async def create_watcher(data: dict) -> int:
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys=ON")
        import json as _json
        cursor = await db.execute(
            """INSERT INTO keyword_watchers
               (name, sender_account_ids, observer_account_ids, keywords, group_ids, cooldown_hours, dm_once, excluded_usernames, is_active, platform, watch_type, join_dm_delay_min, join_dm_delay_max)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["name"],
                _json.dumps(data.get("sender_account_ids", [])),
                _json.dumps(data.get("observer_account_ids", [])),
                _json.dumps(data.get("keywords", [])),
                _json.dumps(data.get("group_ids", [])),
                data.get("cooldown_hours", 24),
                1 if data.get("dm_once") else 0,
                _json.dumps([u.lstrip("@").lower() for u in data.get("excluded_usernames", [])]),
                data.get("is_active", 1),
                data.get("platform", "telegram"),
                data.get("watch_type", "keyword"),
                data.get("join_dm_delay_min", 3),
                data.get("join_dm_delay_max", 15),
            )
        )
        watcher_id = cursor.lastrowid
        for msg in data.get("messages", []):
            await db.execute(
                """INSERT INTO watcher_messages
                   (watcher_id, msg_order, msg_type, content, media_path, variant_index)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (watcher_id, msg.get("msg_order", 0), msg["msg_type"],
                 msg.get("content"), msg.get("media_path"), msg.get("variant_index", 0))
            )
        await db.commit()
        return watcher_id


async def _load_watcher_row(db, row: dict) -> dict:
    """Helper: attach messages to a watcher row."""
    import json as _json
    w = dict(row)
    for f in ("sender_account_ids", "observer_account_ids", "keywords", "group_ids", "excluded_usernames"):
        try:
            w[f] = _json.loads(w.get(f) or "[]")
        except Exception:
            w[f] = []
    if "observer_account_ids" not in w or w["observer_account_ids"] is None:
        w["observer_account_ids"] = []
    c = await db.execute(
        "SELECT * FROM watcher_messages WHERE watcher_id=? ORDER BY msg_order", (w["id"],))
    w["messages"] = [dict(r) for r in await c.fetchall()]
    w["message_variants"] = group_watcher_message_variants(w["messages"])
    return w


def _parse_watcher_json_fields(w: dict) -> dict:
    import json as _json
    for f in ("sender_account_ids", "observer_account_ids", "keywords", "group_ids", "excluded_usernames"):
        try:
            w[f] = _json.loads(w.get(f) or "[]")
        except Exception:
            w[f] = []
    if not w.get("observer_account_ids"):
        w["observer_account_ids"] = []
    return w


def group_watcher_message_variants(messages: list[dict]) -> list[list[dict]]:
    """Split a flat watcher-message list into template variants.

    Messages sharing a `variant_index` (default 0) form one variant; variants
    are returned in ascending index order, each preserving msg_order. Watchers
    with no variant_index set collapse to a single variant, so behaviour is
    unchanged for existing configs. This is the shape template_rotation
    .select_variant() expects.
    """
    if not messages:
        return []
    by_variant: dict[int, list[dict]] = {}
    for m in messages:
        idx = m.get("variant_index") or 0
        by_variant.setdefault(idx, []).append(m)
    variants = []
    for idx in sorted(by_variant):
        variants.append(sorted(by_variant[idx], key=lambda m: m.get("msg_order", 0)))
    return variants


async def _load_watcher_rows_bulk(db, rows: list) -> list:
    if not rows:
        return []
    watchers = [dict(r) for r in rows]
    for w in watchers:
        _parse_watcher_json_fields(w)
    
    watcher_ids = [w["id"] for w in watchers]
    msgs_by_watcher = {}

    for i in range(0, len(watcher_ids), 900):
        chunk = watcher_ids[i:i+900]
        placeholders = ",".join("?" for _ in chunk)
        cursor_msgs = await db.execute(
            f"SELECT * FROM watcher_messages WHERE watcher_id IN ({placeholders}) ORDER BY msg_order",
            chunk
        )
        for r in await cursor_msgs.fetchall():
            row_dict = dict(r)
            msgs_by_watcher.setdefault(row_dict["watcher_id"], []).append(row_dict)
        
    for w in watchers:
        w["messages"] = msgs_by_watcher.get(w["id"], [])
        w["message_variants"] = group_watcher_message_variants(w["messages"])
    return watchers


async def get_all_watchers() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM keyword_watchers ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return await _load_watcher_rows_bulk(db, rows)


async def get_active_watchers() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM keyword_watchers WHERE is_active=1")
        rows = await cursor.fetchall()
        return await _load_watcher_rows_bulk(db, rows)


async def get_watcher(watcher_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM keyword_watchers WHERE id=?", (watcher_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return await _load_watcher_row(db, row)


async def get_watcher_platform(watcher_id: int) -> str | None:
    """Get the platform of a watcher, or None if not found."""
    async with get_db() as db:
        cursor = await db.execute("SELECT platform FROM keyword_watchers WHERE id=?", (watcher_id,))
        row = await cursor.fetchone()
        return row[0] if row else None


async def update_watcher(watcher_id: int, data: dict) -> bool:
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys=ON")
        import json as _json
        await db.execute(
            """UPDATE keyword_watchers
               SET name=?, sender_account_ids=?, observer_account_ids=?, keywords=?, group_ids=?,
                   cooldown_hours=?, dm_once=?, excluded_usernames=?,
                   reply_in_group=?, group_reply_text=?, group_reply_account_id=?,
                   watch_type=?, join_dm_delay_min=?, join_dm_delay_max=?,
                   is_active=?, updated_at=datetime('now')
               WHERE id=?""",
            (
                data["name"],
                _json.dumps(data.get("sender_account_ids", data.get("account_ids", []))),
                _json.dumps(data.get("observer_account_ids", [])),
                _json.dumps(data.get("keywords", [])),
                _json.dumps(data.get("group_ids", [])),
                data.get("cooldown_hours", 24),
                1 if data.get("dm_once") else 0,
                _json.dumps([u.lstrip("@").lower() for u in data.get("excluded_usernames", [])]),
                1 if data.get("reply_in_group") else 0,
                data.get("group_reply_text", "Check my DM 😊") or "Check my DM 😊",
                data.get("group_reply_account_id"),
                data.get("watch_type", "keyword"),
                data.get("join_dm_delay_min", 3),
                data.get("join_dm_delay_max", 15),
                data.get("is_active", 1),
                watcher_id,
            )
        )
        await db.execute("DELETE FROM watcher_messages WHERE watcher_id=?", (watcher_id,))
        for msg in data.get("messages", []):
            await db.execute(
                """INSERT INTO watcher_messages
                   (watcher_id, msg_order, msg_type, content, media_path, variant_index)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (watcher_id, msg.get("msg_order", 0), msg["msg_type"],
                 msg.get("content"), msg.get("media_path"), msg.get("variant_index", 0))
            )
        await db.commit()
        return True


async def delete_watcher(watcher_id: int) -> bool:
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM keyword_watchers WHERE id=?", (watcher_id,))
        await db.commit()
        return True


async def toggle_watcher(watcher_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT is_active FROM keyword_watchers WHERE id=?", (watcher_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        new_state = 0 if row["is_active"] else 1
        await db.execute(
            "UPDATE keyword_watchers SET is_active=?, updated_at=datetime('now') WHERE id=?",
            (new_state, watcher_id))
        await db.commit()
        return {"id": watcher_id, "is_active": new_state}


async def count_user_dm_failures(watcher_id: int, user_id: int, hours: int = 24) -> int:
    """Count individual failed DM attempts for a user in the last N hours."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT COUNT(*) FROM watcher_dm_logs
               WHERE watcher_id=? AND target_user_id=? AND status='failed'
               AND sent_at > datetime('now', ? || ' hours')""",
            (watcher_id, user_id, f"-{hours}")
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def was_user_dmed_recently(watcher_id: int, user_id: int,
                                  cooldown_hours: int, dm_once: bool = False) -> bool:
    """
    Return True if this user should be skipped.
    - dm_once=True  → skip forever if ANY successful DM exists
    - dm_once=False → skip if:
        a) successfully DM'd within cooldown_hours, OR
        b) attempted (any status) within 2 hours — prevents repeated retry on fail
    """
    async with get_db() as db:
        if dm_once:
            # Permanent: skip if EVER successfully DM'd
            cursor = await db.execute(
                """SELECT COUNT(*) FROM watcher_dm_logs
                   WHERE watcher_id=? AND target_user_id=? AND status='success'""",
                (watcher_id, user_id)
            )
            row = await cursor.fetchone()
            if row[0] > 0:
                return True
            # Also skip if failed 3+ times in last 24h (prevent infinite retry)
            # Uses count_user_dm_failures which counts individual account failures
            fail_count = await count_user_dm_failures(watcher_id, user_id, hours=24)
            if fail_count >= 3:
                return True  # too many failed attempts today, give up
            return False

        # Check 1: successful DM within cooldown window
        cursor = await db.execute(
            """SELECT COUNT(*) FROM watcher_dm_logs
               WHERE watcher_id=? AND target_user_id=? AND status='success'
               AND sent_at >= datetime('now', ? || ' hours')""",
            (watcher_id, user_id, f"-{cooldown_hours}")
        )
        row = await cursor.fetchone()
        if row[0] > 0:
            return True  # Already successfully DM'd in cooldown window

        # Check 2: any attempt (even failed) within the FULL cooldown window
        # Prevents retrying the same user throughout the entire cooldown period
        # Example: cooldown_hours=24 → won't retry for 24h even if all DMs failed
        cursor2 = await db.execute(
            """SELECT COUNT(*) FROM watcher_dm_logs
               WHERE watcher_id=? AND target_user_id=?
               AND sent_at >= datetime('now', ? || ' hours')""",
            (watcher_id, user_id, f"-{cooldown_hours}")
        )
        row2 = await cursor2.fetchone()
        return row2[0] > 0  # Skip if attempted (any status) within cooldown window


async def add_watcher_dm_log(
    watcher_id: int, account_id: int | None,
    target_user_id: int, target_username: str | None,
    group_id: int | None, group_title: str | None,
    matched_keyword: str | None,
    status: str, error_message: str | None = None,
    platform: str = "telegram",
    template_variant_index: int | None = None,
):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO watcher_dm_logs
               (watcher_id, account_id, target_user_id, target_username,
                group_id, group_title, matched_keyword, status, error_message,
                platform, template_variant_index)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (watcher_id, account_id, target_user_id, target_username,
             group_id, group_title, matched_keyword, status, error_message,
             platform, template_variant_index)
        )
        await db.commit()


async def get_watcher_dm_logs(
    limit: int = 100, offset: int = 0,
    watcher_id: int | None = None,
    status: str | None = None
) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        where, params = [], []
        if watcher_id:
            where.append("watcher_id=?")
            params.append(watcher_id)
        if status:
            where.append("status=?")
            params.append(status)
        where_str = " WHERE " + " AND ".join(where) if where else ""
        count_cursor = await db.execute(
            f"SELECT COUNT(*) as cnt FROM watcher_dm_logs{where_str}", params)
        total = (await count_cursor.fetchone())[0]
        cursor = await db.execute(
            f"SELECT * FROM watcher_dm_logs{where_str} ORDER BY sent_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset])
        logs = [dict(r) for r in await cursor.fetchall()]
        return {"total": total, "logs": logs, "limit": limit, "offset": offset}


async def get_watcher_log_stats() -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        c = await db.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END), 0) AS success,
                COALESCE(SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END), 0) AS failed,
                COALESCE(SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END), 0) AS skipped,
                COALESCE(SUM(CASE WHEN status='success'
                    AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours')
                    THEN 1 ELSE 0 END), 0) AS today,
                (SELECT COUNT(*) FROM keyword_watchers WHERE is_active=1) AS active_watchers
            FROM watcher_dm_logs
        """)
        row = dict(await c.fetchone())
        return {
            "success": row["success"],
            "failed": row["failed"],
            "skipped": row["skipped"],
            "today": row["today"],
            "active_watchers": row["active_watchers"],
        }


async def get_setting(key: str, default=None):
    """Retrieve a setting value by key."""
    async with get_db() as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default


async def get_settings_bulk(keys: dict):
    """Fetch many settings in ONE pool acquire + ONE query.

    `keys` maps setting key -> default value. Returns {key: value} with
    defaults filled in for any key not present in the DB. Use this instead of
    N sequential get_setting() calls, which cost a pool acquire/release and a
    query round-trip each.
    """
    if not keys:
        return {}
    result = dict(keys)
    placeholders = ",".join("?" * len(keys))
    async with get_db() as db:
        cursor = await db.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            tuple(keys.keys()),
        )
        for row in await cursor.fetchall():
            result[row[0]] = row[1]
    return result


async def set_settings_bulk(values: dict):
    """Persist many settings in ONE connection + ONE commit."""
    if not values:
        return
    async with get_db() as db:
        await db.executemany(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            [(k, v) for k, v in values.items()],
        )
        await db.commit()


async def set_setting(key: str, value: str):
    """Insert or update a setting."""
    async with get_db() as db:
        await db.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value)
        )
        await db.commit()


async def log_command_action(admin_user_id: int, action: str,
                             account_id: int = None,
                             target_username: str = None,
                             message_text: str = None,
                             result: str = "success",
                             details: str = None):
    """Audit every action issued through the Telegram command bot."""
    async with get_db() as db:
        await db.execute(
            """INSERT INTO command_actions
               (admin_user_id, action, account_id, target_username,
                message_text, result, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (admin_user_id, action, account_id, target_username,
             message_text, result, details),
        )
        await db.commit()


async def get_command_actions(limit: int = 100) -> list[dict]:
    """Latest command bot audit entries."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM command_actions ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in await cursor.fetchall()]

save_setting = set_setting


# ── Target Block Tracking ──────────────────────────────────────────────────

async def record_target_failure(schedule_id: int, account_id: int, chat_id: int, chat_title: str = "") -> dict:
    """
    Increment fail count for (schedule, account, chat).
    Returns {"fail_count": N, "just_blocked": bool, "is_blocked": bool}
    """
    MAX_FAILURES = 3
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Upsert: insert or increment
        await db.execute("""
            INSERT INTO schedule_target_blocks (schedule_id, account_id, chat_id, chat_title, fail_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(schedule_id, account_id, chat_id) DO UPDATE SET
                fail_count = fail_count + 1,
                chat_title = COALESCE(NULLIF(excluded.chat_title, ''), schedule_target_blocks.chat_title)
        """, (schedule_id, account_id, chat_id, chat_title))
        await db.commit()

        row = await (await db.execute(
            "SELECT fail_count, is_blocked FROM schedule_target_blocks WHERE schedule_id=? AND account_id=? AND chat_id=?",
            (schedule_id, account_id, chat_id)
        )).fetchone()

        fail_count = row["fail_count"] if row else 1
        is_blocked = bool(row["is_blocked"]) if row else False
        just_blocked = False

        if fail_count >= MAX_FAILURES and not is_blocked:
            await db.execute("""
                UPDATE schedule_target_blocks SET is_blocked=1, blocked_at=datetime('now'), fail_count=0
                WHERE schedule_id=? AND account_id=? AND chat_id=?
            """, (schedule_id, account_id, chat_id))
            await db.commit()
            just_blocked = True
            is_blocked = True

        return {"fail_count": fail_count, "just_blocked": just_blocked, "is_blocked": is_blocked}


async def is_target_blocked(schedule_id: int, account_id: int, chat_id: int,
                            retry_after_hours: float = 2.0) -> bool:
    """
    Check if a (schedule, account, chat) is blocked.
    Block expires after retry_after_hours (default: 2 hours).
    If expired, auto-reset so it will be retried.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT is_blocked, blocked_at FROM schedule_target_blocks
               WHERE schedule_id=? AND account_id=? AND chat_id=? AND is_blocked=1""",
            (schedule_id, account_id, chat_id)
        )).fetchone()
        if not row:
            return False

        # Check if block has expired (2-hour cooldown)
        blocked_at_str = row["blocked_at"]
        if blocked_at_str:
            from datetime import datetime, timezone
            try:
                blocked_at = datetime.fromisoformat(blocked_at_str).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                elapsed_hours = (now - blocked_at).total_seconds() / 3600
                if elapsed_hours >= retry_after_hours:
                    # Auto-reset: unblock and let it try again
                    await db.execute(
                        """UPDATE schedule_target_blocks
                           SET is_blocked=0, blocked_at=NULL, fail_count=0
                           WHERE schedule_id=? AND account_id=? AND chat_id=?""",
                        (schedule_id, account_id, chat_id)
                    )
                    await db.commit()
                    return False  # Allow retry
            except Exception:
                pass  # If parse fails, treat as still blocked

        return True


async def get_blocked_targets(schedule_id: int) -> list:
    """Get all blocked targets for a schedule."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT b.*, a.name as account_name, a.phone as account_phone
            FROM schedule_target_blocks b
            LEFT JOIN accounts a ON b.account_id = a.id
            WHERE b.schedule_id=? AND b.is_blocked=1
            ORDER BY b.blocked_at DESC
        """, (schedule_id,))).fetchall()
        return [dict(r) for r in rows]


async def unblock_target(schedule_id: int, account_id: int, chat_id: int) -> bool:
    """Manually unblock a target."""
    async with get_db() as db:
        await db.execute("""
            UPDATE schedule_target_blocks SET is_blocked=0, fail_count=0, blocked_at=NULL
            WHERE schedule_id=? AND account_id=? AND chat_id=?
        """, (schedule_id, account_id, chat_id))
        await db.commit()
        return True


# ── Daily DM Limit Tracking ────────────────────────────────────────────────────

DM_DAILY_LIMIT_NORMAL = 10
DM_DAILY_LIMIT_PREMIUM = 60


async def get_campaign_dm_limits(campaign_id: int = None) -> tuple[int, int]:
    """Get DM daily limits. If campaign_id given, read from campaign; else use defaults."""
    if campaign_id:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT daily_limit_premium, daily_limit_normal FROM dm_campaigns WHERE id=?",
                (campaign_id,)
            )).fetchone()
            if row:
                return (row["daily_limit_premium"] or DM_DAILY_LIMIT_PREMIUM,
                        row["daily_limit_normal"] or DM_DAILY_LIMIT_NORMAL)
    return (DM_DAILY_LIMIT_PREMIUM, DM_DAILY_LIMIT_NORMAL)


async def get_account_daily_dm_count(account_id: int) -> int:
    """Count how many DMs this account sent today (UTC date) across watchers and campaigns."""
    async with get_db() as db:
        row = await (await db.execute(
            """SELECT (
                (SELECT COUNT(*) FROM watcher_dm_logs WHERE account_id=? AND status='success' AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours'))
                +
                (SELECT COUNT(*) FROM dm_campaign_logs WHERE account_id=? AND status='success' AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours'))
            ) as cnt""",
            (account_id, account_id)
        )).fetchone()
        return row[0] if row else 0


async def is_account_dm_limit_reached(account_id: int, limit_premium: int = None, limit_normal: int = None) -> tuple[bool, int, int]:
    """
    Check if account has reached daily DM limit.
    Free/Normal accounts are hard-capped to MAX 10 DMs/day to prevent bans.
    Premium accounts use limit_premium (default 60).
    Returns (limit_reached: bool, count: int, limit: int)
    """
    lp = limit_premium if limit_premium is not None else DM_DAILY_LIMIT_PREMIUM
    ln = min(limit_normal if limit_normal is not None else DM_DAILY_LIMIT_NORMAL, 10)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        acc = await (await db.execute(
            "SELECT is_premium FROM accounts WHERE id=?", (account_id,)
        )).fetchone()
        is_premium = bool(acc["is_premium"]) if acc else False
        limit = lp if is_premium else ln

        row = await (await db.execute(
            """SELECT (
                (SELECT COUNT(*) FROM watcher_dm_logs WHERE account_id=? AND status='success' AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours'))
                +
                (SELECT COUNT(*) FROM dm_campaign_logs WHERE account_id=? AND status='success' AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours'))
            ) as cnt""",
            (account_id, account_id)
        )).fetchone()
        count = row["cnt"] if row else 0
        return (count >= limit, count, limit)


async def get_account_daily_dm_count_live(account_id: int) -> int:
    """Daily DM count read fresh from DB, including in-flight reservations.

    Replaces the per-campaign in-memory counter (`acc_daily_count_map`) that let
    two concurrently running campaigns sharing a sender each count only their
    own sends and double-spend the account's daily cap. Pending reservations
    created today count as in-flight sends.
    """
    async with get_db() as db:
        row = await (await db.execute(
            """SELECT (
                (SELECT COUNT(*) FROM watcher_dm_logs WHERE account_id=? AND status='success' AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours'))
                +
                (SELECT COUNT(*) FROM dm_campaign_logs WHERE account_id=? AND status='success' AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours'))
                +
                (SELECT COUNT(*) FROM dm_campaign_reservations WHERE account_id=? AND status='pending' AND created_at >= datetime('now', '+7 hours', 'start of day', '-7 hours'))
            ) as cnt""",
            (account_id, account_id, account_id)
        )).fetchone()
        return row[0] if row else 0


async def set_account_premium(account_id: int, is_premium: bool) -> bool:
    """Toggle premium status for an account."""
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET is_premium=? WHERE id=?",
            (1 if is_premium else 0, account_id)
        )
        await db.commit()
        return True


# ============================================================
# DM BLACKLIST — Feature #6
# ============================================================

async def get_dm_blacklist() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM dm_blacklist ORDER BY created_at DESC"
        )).fetchall()
        return [dict(r) for r in rows]


async def add_to_dm_blacklist(user_id: int | None, username: str | None, reason: str = "") -> dict:
    """Insert or update a user in the DM blacklist.
    Supports: user_id only, username only, or both.
    Returns the saved row as a dict.
    """
    clean_username = username.lstrip("@").lower().strip() if username else None

    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        if user_id:
            # Has user_id → use ON CONFLICT(user_id) to upsert
            await db.execute(
                """INSERT INTO dm_blacklist (user_id, username, reason)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE
                   SET username=COALESCE(excluded.username, dm_blacklist.username),
                       reason=excluded.reason""",
                (user_id, clean_username, reason)
            )
            await db.commit()
            row = await (await db.execute(
                "SELECT * FROM dm_blacklist WHERE user_id=?", (user_id,)
            )).fetchone()
        else:
            # Username-only (no user_id) → check if username already exists
            existing = await (await db.execute(
                "SELECT * FROM dm_blacklist WHERE LOWER(username) = ?",
                (clean_username,)
            )).fetchone()
            if existing:
                # Update existing entry
                await db.execute(
                    "UPDATE dm_blacklist SET reason=? WHERE id=?",
                    (reason, existing["id"])
                )
                await db.commit()
                row = await (await db.execute(
                    "SELECT * FROM dm_blacklist WHERE id=?", (existing["id"],)
                )).fetchone()
            else:
                # Insert new username-only entry (user_id=NULL)
                await db.execute(
                    "INSERT INTO dm_blacklist (user_id, username, reason) VALUES (NULL, ?, ?)",
                    (clean_username, reason)
                )
                await db.commit()
                row = await (await db.execute(
                    "SELECT * FROM dm_blacklist WHERE LOWER(username)=?",
                    (clean_username,)
                )).fetchone()

        return dict(row) if row else {}


async def remove_from_dm_blacklist(blacklist_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM dm_blacklist WHERE id=?", (blacklist_id,))
        await db.commit()


async def ignore_followup_chats_for_blacklisted(user_id: int = None, username: str = None) -> int:
    """Khi 1 user vào blacklist: chuyển mọi chat AI follow-up của user đó sang
    'bot_ignored' để lead biến mất khỏi mục AI đang xử lý (AI vốn đã bị chặn reply,
    đây là sync trạng thái hiển thị). Returns số chat được cập nhật."""
    clean = (username or "").lstrip("@").lower().strip()
    async with get_db() as db:
        cur = await db.execute(
            """UPDATE ai_followup_chats
               SET status='bot_ignored', updated_at=datetime('now')
               WHERE status NOT IN ('bot_ignored','onboarded')
                 AND ((? IS NOT NULL AND user_id=?)
                      OR (? != '' AND LOWER(LTRIM(COALESCE(username,''),'@'))=?))""",
            (user_id, user_id, clean, clean)
        )
        await db.commit()
        return cur.rowcount


async def sync_all_blacklisted_followup_chats() -> int:
    """One-shot sync: đánh dấu bot_ignored cho mọi chat khớp blacklist hiện có."""
    async with get_db() as db:
        cur = await db.execute(
            """UPDATE ai_followup_chats
               SET status='bot_ignored', updated_at=datetime('now')
               WHERE status NOT IN ('bot_ignored','onboarded')
                 AND EXISTS (
                     SELECT 1 FROM dm_blacklist b
                     WHERE (b.user_id IS NOT NULL AND b.user_id = ai_followup_chats.user_id)
                        OR (b.username IS NOT NULL AND b.username != ''
                            AND LOWER(TRIM(LTRIM(b.username,'@'))) = LOWER(TRIM(LTRIM(COALESCE(ai_followup_chats.username,''),'@'))))
                 )"""
        )
        await db.commit()
        return cur.rowcount


async def is_user_blacklisted(user_id: int = None, username: str = None) -> bool:
    """Check if a user is in the global DM blacklist by user_id and/or username."""
    async with get_db() as db:
        if user_id:
            row = await (await db.execute(
                "SELECT 1 FROM dm_blacklist WHERE user_id=?", (user_id,)
            )).fetchone()
            if row:
                return True
        if username:
            clean = username.lstrip("@").lower().strip()
            if clean:
                row = await (await db.execute(
                    "SELECT 1 FROM dm_blacklist WHERE LOWER(username)=?", (clean,)
                )).fetchone()
                if row:
                    return True
        return False


async def get_blacklisted_usernames_set() -> set:
    """Return set of lowercased blacklisted usernames for efficient batch filtering."""
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT LOWER(username) FROM dm_blacklist WHERE username IS NOT NULL AND username != ''"
        )).fetchall()
        return {r[0] for r in rows}


# ============================================================
# ACCOUNT FLAGGING — Feature #2
# ============================================================

async def check_and_flag_account(account_id: int) -> bool:
    """Flag account if it has >= 5 failures in last 24h.

    Returns True chỉ khi account VỪA bị flag (trước đó chưa flag) —
    dùng để gửi cảnh báo 1 lần, không spam mỗi lần fail.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Count recent failures
        row = await (await db.execute("""
            SELECT COUNT(*) as cnt FROM send_logs
            WHERE account_id=? AND status='failed'
            AND sent_at >= datetime('now', '-24 hours')
        """, (account_id,))).fetchone()
        fail_count = row["cnt"] if row else 0

        # Check if already has is_flagged column
        cols = [c["name"] for c in await (await db.execute("PRAGMA table_info(accounts)")).fetchall()]
        if "is_flagged" not in cols:
            return False  # migration not done yet

        if fail_count >= 5:
            prev = await (await db.execute(
                "SELECT is_flagged FROM accounts WHERE id=?", (account_id,)
            )).fetchone()
            already = bool(prev["is_flagged"]) if prev else False
            await db.execute(
                """UPDATE accounts SET is_flagged=1,
                   flag_reason=? WHERE id=?""",
                (f"{fail_count} lỗi trong 24h gần nhất", account_id)
            )
            await db.commit()
            return not already
        return False


async def unflag_account(account_id: int):
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET is_flagged=0, flag_reason=NULL WHERE id=?",
            (account_id,)
        )
        await db.commit()


async def pause_account(account_id: int, reason: str):
    """Auto-pause an account due to critical error."""
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET is_paused=1, paused_at=datetime('now'), pause_reason=? WHERE id=?",
            (reason, account_id)
        )
        await db.commit()


async def unpause_account(account_id: int):
    """Manually unpause an account."""
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET is_paused=0, paused_at=NULL, pause_reason=NULL WHERE id=?",
            (account_id,)
        )
        await db.commit()


async def is_account_paused(account_id: int) -> bool:
    """Check if an account is currently paused."""
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT is_paused FROM accounts WHERE id=?", (account_id,)
        )).fetchone()
        return bool(row and row[0])


async def set_account_ai_agent(account_id: int, ai_agent_id: int | None):
    """Set or remove account-level AI Agent for organic DMs."""
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET ai_agent_id=? WHERE id=?",
            (ai_agent_id, account_id)
        )
        await db.commit()


async def get_paused_accounts() -> list:
    """Return all currently paused accounts."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, phone, pause_reason, paused_at FROM accounts WHERE is_paused=1"
        )
        return [dict(r) for r in await cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Reaction Target helpers
# ─────────────────────────────────────────────────────────────────────────────

async def add_reaction_target(
    channel_link: str,
    channel_id: int | None,
    channel_title: str | None,
    account_ids: list,
    reactions: list,
    delay_min: int = 5,
    delay_max: int = 30,
    view_enabled: int = 0,
    view_ratio: float = 1.0,
    react_count_min: int = 0,
    react_count_max: int = 0,
) -> int:
    """Insert a new reaction target. Returns new row id."""
    import json
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO reaction_targets
               (channel_link, channel_id, channel_title, account_ids, reactions, delay_min, delay_max, view_enabled, view_ratio, react_count_min, react_count_max)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                channel_link,
                channel_id,
                channel_title,
                json.dumps(account_ids),
                json.dumps(reactions),
                delay_min,
                delay_max,
                view_enabled,
                view_ratio,
                react_count_min,
                react_count_max,
            ),
        )
        await db.commit()
        return cur.lastrowid


async def get_all_reaction_targets(active_only: bool = True) -> list[dict]:
    import json
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        sql = "SELECT * FROM reaction_targets"
        if active_only:
            sql += " WHERE is_active=1"
        sql += " ORDER BY id"
        rows = await (await db.execute(sql)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["account_ids"] = json.loads(d["account_ids"] or "[]")
            d["reactions"]   = json.loads(d["reactions"]   or '["👍"]')
            result.append(d)
        return result


async def get_reaction_target(target_id: int) -> dict | None:
    import json
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM reaction_targets WHERE id=?", (target_id,)
        )).fetchone()
        if not row:
            return None
        d = dict(row)
        d["account_ids"] = json.loads(d["account_ids"] or "[]")
        d["reactions"]   = json.loads(d["reactions"]   or '["👍"]')
        return d


ALLOWED_REACTION_COLS = {"account_ids", "reactions", "delay_min", "delay_max", "is_active", "channel_title", "channel_id", "view_enabled", "view_ratio", "react_count_min", "react_count_max"}


async def update_reaction_target(target_id: int, **kwargs) -> None:
    import json
    # CRIT-02: allowlist to prevent SQL injection via column names
    invalid = set(kwargs.keys()) - ALLOWED_REACTION_COLS
    if invalid:
        raise ValueError(f"Invalid columns: {invalid}")
    if "account_ids" in kwargs and isinstance(kwargs["account_ids"], list):
        kwargs["account_ids"] = json.dumps(kwargs["account_ids"])
    if "reactions" in kwargs and isinstance(kwargs["reactions"], list):
        kwargs["reactions"] = json.dumps(kwargs["reactions"])
    if not kwargs:
        return
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [target_id]
    async with get_db() as db:
        await db.execute(f"UPDATE reaction_targets SET {cols} WHERE id=?", vals)
        await db.commit()


async def delete_reaction_target(target_id: int) -> None:
    async with get_db() as db:
        await db.execute("DELETE FROM reaction_targets WHERE id=?", (target_id,))
        await db.commit()


async def add_reaction_log(
    target_id: int,
    account_id: int,
    channel_id: int,
    msg_id: int,
    reaction: str,
    status: str = "success",
    error_msg: str | None = None,
    platform: str = "telegram",
) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT INTO reaction_logs
               (target_id, account_id, channel_id, msg_id, reaction, status, error_msg, platform)
               VALUES (?,?,?,?,?,?,?,?)""",
            (target_id, account_id, channel_id, msg_id, reaction, status, error_msg, platform),
        )
        await db.commit()


async def get_reaction_logs(target_id: int | None = None, limit: int = 100) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if target_id is not None:
            rows = await (await db.execute(
                "SELECT * FROM reaction_logs WHERE target_id=? ORDER BY sent_at DESC LIMIT ?",
                (target_id, limit),
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM reaction_logs ORDER BY sent_at DESC LIMIT ?",
                (limit,),
            )).fetchall()
        return [dict(r) for r in rows]


async def was_msg_reacted(target_id: int, account_id: int, msg_id: int) -> bool:
    """Return True if this account already reacted to this message."""
    async with get_db() as db:
        row = await (await db.execute(
            """SELECT COUNT(*) FROM reaction_logs
               WHERE target_id=? AND account_id=? AND msg_id=? AND status='success'""",
            (target_id, account_id, msg_id),
        )).fetchone()
        return (row[0] or 0) > 0


async def set_account_peerflood_until(account_id: int, until_timestamp: float) -> None:
    """Persist PeerFlood cooldown end time for an account."""
    async with get_db() as db:
        await db.execute(
            "UPDATE accounts SET peerflood_until=? WHERE id=?",
            (until_timestamp, account_id)
        )
        await db.commit()


async def get_accounts_with_peerflood() -> list[tuple[int, float]]:
    """Return [(account_id, peerflood_until)] for accounts still in cooldown."""
    now = __import__('time').time()
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id, peerflood_until FROM accounts WHERE peerflood_until > ?", (now,)
        )).fetchall()
        return [(r[0], r[1]) for r in rows]


# ── DM Reply Tracker CRUD ──────────────────────────────────────────────────────

async def add_dm_reply(data: dict) -> int:
    """
    Insert a new DM reply into dm_replies.
    data keys: account_id, sender_user_id, sender_username, sender_name,
               message_text, watcher_id (optional), platform (optional)
    Returns the inserted row id.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO dm_replies
               (watcher_id, account_id, sender_user_id, sender_username,
                sender_name, message_text, is_read, platform)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                data.get("watcher_id"),
                data["account_id"],
                data["sender_user_id"],
                data.get("sender_username"),
                data.get("sender_name"),
                data.get("message_text"),
                data.get("platform", "telegram"),
            )
        )
        await db.commit()
        return cursor.lastrowid


async def record_dm_reply(
    watcher_id: int | None = None,
    account_id: int = 0,
    sender_user_id: int = 0,
    sender_username: str | None = None,
    sender_name: str | None = None,
    message_text: str = "",
    platform: str = "telegram",
) -> int:
    """Helper alias for add_dm_reply supporting keyword arguments."""
    return await add_dm_reply({
        "watcher_id": watcher_id,
        "account_id": account_id,
        "sender_user_id": sender_user_id,
        "sender_username": sender_username,
        "sender_name": sender_name,
        "message_text": message_text,
        "platform": platform,
    })


async def get_dm_replies(
    limit: int = 50,
    offset: int = 0,
    is_read: int | None = None,
    watcher_id: int | None = None,
    account_id: int | None = None,
) -> list[dict]:
    """
    Fetch DM replies with optional filters.
    is_read: None=all, 0=unread only, 1=read only
    watcher_id: filter to a specific watcher
    account_id: filter to a specific account
    """
    conditions = []
    params: list = []
    
    # Exclude bots (username ending in 'bot' or similar)
    conditions.append("(r.sender_username IS NULL OR r.sender_username NOT LIKE '%bot')")
    
    # Only show replies from users we proactively DM'd (success in watcher_dm_logs or dm_campaign_logs)
    conditions.append("""(
        EXISTS (SELECT 1 FROM watcher_dm_logs wdl WHERE wdl.target_user_id = r.sender_user_id AND wdl.status = 'success')
        OR
        EXISTS (SELECT 1 FROM dm_campaign_logs dcl WHERE dcl.target_user_id = r.sender_user_id AND dcl.status = 'success')
    )""")
    
    if is_read is not None:
        conditions.append("r.is_read = ?")
        params.append(is_read)
    if watcher_id is not None:
        conditions.append("r.watcher_id = ?")
        params.append(watcher_id)
    if account_id is not None:
        conditions.append("r.account_id = ?")
        params.append(account_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"""SELECT r.*,
                       kw.name AS watcher_name,
                       a.name  AS account_name
                FROM dm_replies r
                LEFT JOIN keyword_watchers kw ON kw.id = r.watcher_id
                LEFT JOIN accounts         a  ON a.id  = r.account_id
                {where}
                ORDER BY r.received_at DESC
                LIMIT ? OFFSET ?""",
            params
        )).fetchall()
        return [dict(r) for r in rows]


async def mark_reply_read(reply_id: int) -> bool:
    """Mark a single reply as read. Returns True if a row was updated."""
    async with get_db() as db:
        await db.execute(
            "UPDATE dm_replies SET is_read = 1 WHERE id = ?", (reply_id,)
        )
        await db.commit()
        return True


async def mark_all_replies_read() -> int:
    """Mark all unread replies as read. Returns number of rows updated."""
    async with get_db() as db:
        cursor = await db.execute(
            """UPDATE dm_replies SET is_read = 1 
               WHERE is_read = 0 
                 AND (sender_username IS NULL OR sender_username NOT LIKE '%bot')
                 AND (
                     EXISTS (SELECT 1 FROM watcher_dm_logs wdl WHERE wdl.target_user_id = dm_replies.sender_user_id AND wdl.status = 'success')
                     OR
                     EXISTS (SELECT 1 FROM dm_campaign_logs dcl WHERE dcl.target_user_id = dm_replies.sender_user_id AND dcl.status = 'success')
                 )"""
        )
        await db.commit()
        return cursor.rowcount


async def count_unread_replies() -> int:
    """Return the count of unread DM replies (for the inbox badge) excluding bots."""
    async with get_db() as db:
        row = await (await db.execute(
            """SELECT COUNT(*) FROM dm_replies r
               WHERE r.is_read = 0 
                 AND (r.sender_username IS NULL OR r.sender_username NOT LIKE '%bot')
                 AND (
                     EXISTS (SELECT 1 FROM watcher_dm_logs wdl WHERE wdl.target_user_id = r.sender_user_id AND wdl.status = 'success')
                     OR
                     EXISTS (SELECT 1 FROM dm_campaign_logs dcl WHERE dcl.target_user_id = r.sender_user_id AND dcl.status = 'success')
                 )"""
        )).fetchone()
        return row[0] if row else 0


async def find_watcher_id_for_user(user_id: int) -> int | None:
    """
    Return the watcher_id of the most recent successful DM sent to user_id,
    or None if the user was never DM'd by any watcher.
    Used by dm_reply_tracker to link a reply back to the originating watcher.
    """
    async with get_db() as db:
        row = await (await db.execute(
            """SELECT watcher_id FROM watcher_dm_logs
               WHERE target_user_id = ? AND status = 'success'
               ORDER BY sent_at DESC LIMIT 1""",
            (user_id,)
        )).fetchone()
        return row[0] if row else None


# ── Discord Bot CRUD ─────────────────────────────────────────────────────────

async def create_discord_bot(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO discord_bots (name, bot_token)
               VALUES (?, ?)""",
            (data["name"], data["bot_token"])
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_discord_bots() -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM discord_bots ORDER BY id"
        )).fetchall()
        return [dict(r) for r in rows]


async def get_discord_bot(bot_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM discord_bots WHERE id = ?", (bot_id,)
        )).fetchone()
        return dict(row) if row else None


async def update_discord_bot(bot_id: int, data: dict) -> bool:
    async with get_db() as db:
        await db.execute(
            """UPDATE discord_bots SET name = ?, bot_token = ?
               WHERE id = ?""",
            (data["name"], data["bot_token"], bot_id)
        )
        await db.commit()
        return True


async def update_discord_bot_status(bot_id: int, connected: bool,
                                     user_id: str = None, username: str = None,
                                     guild_count: int = 0):
    async with get_db() as db:
        await db.execute(
            """UPDATE discord_bots
               SET is_connected = ?, bot_user_id = ?, bot_username = ?, guild_count = ?
               WHERE id = ?""",
            (1 if connected else 0, user_id, username, guild_count, bot_id)
        )
        await db.commit()


async def delete_discord_bot(bot_id: int) -> bool:
    async with get_db() as db:
        await db.execute("DELETE FROM discord_bots WHERE id = ?", (bot_id,))
        await db.commit()
        return True


# ── Platform-filtered queries ────────────────────────────────────────────────

async def get_all_watchers_by_platform(platform: str = "telegram") -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM keyword_watchers WHERE platform = ? ORDER BY id", (platform,))
        rows = await cursor.fetchall()
        return await _load_watcher_rows_bulk(db, rows)


async def get_reaction_targets_by_platform(platform: str = "telegram") -> list[dict]:
    """Return reaction targets filtered by platform."""
    import json
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM reaction_targets WHERE platform = ? ORDER BY id",
            (platform,)
        )).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["account_ids"] = json.loads(d["account_ids"] or "[]")
            d["reactions"]   = json.loads(d["reactions"]   or '["👍"]')
            result.append(d)
        return result


async def get_dm_logs_by_platform(platform: str = "telegram",
                                   limit: int = 50, offset: int = 0,
                                   watcher_id: int = None) -> list[dict]:
    """Return DM logs filtered by platform."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        sql = "SELECT * FROM watcher_dm_logs WHERE platform = ?"
        params = [platform]
        if watcher_id:
            sql += " AND watcher_id = ?"
            params.append(watcher_id)
        sql += " ORDER BY sent_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = await (await db.execute(sql, params)).fetchall()
        return [dict(r) for r in rows]


# ── Member Scraping ──

async def save_scraped_members(scrape_job_id: str, account_id: int, group_id: int,
                                group_title: str, members: list[dict]):
    """Save scraped members to DB in bulk (INSERT OR IGNORE for dedup)."""
    if not members:
        return

    # Strict safeguard: never insert bots or usernames ending in 'bot'
    members = [
        m for m in members
        if not (m.get("is_bot") or (m.get("username") and m["username"].strip().lower().endswith("bot")))
    ]
    if not members:
        return

    data = [
        (
            scrape_job_id,
            account_id,
            group_id,
            group_title,
            m["user_id"],
            m.get("username"),
            m.get("first_name"),
            m.get("last_name"),
            m.get("phone"),
            1 if m.get("is_bot") else 0,
            1 if m.get("is_premium") else 0,
            m.get("status", "active"),
            m.get("last_seen"),
            m.get("lang_code")
        )
        for m in members
    ]

    async with get_db() as db:
        await db.executemany("""
            INSERT OR IGNORE INTO scraped_members
            (scrape_job_id, account_id, group_id, group_title, user_id,
             username, first_name, last_name, phone, is_bot, is_premium, status, last_seen, lang_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, data)
        await db.commit()


async def get_scrape_jobs() -> list:
    """Get all distinct scrape jobs with counts."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT scrape_job_id, account_id, group_id, group_title,
                   COUNT(*) as member_count,
                   MIN(scraped_at) as scraped_at
            FROM scraped_members
            GROUP BY scrape_job_id
            ORDER BY scraped_at DESC
        """)
        return [dict(row) for row in await cursor.fetchall()]


async def get_scraped_members(scrape_job_id: str, limit: int = 500, offset: int = 0, last_id: int = None) -> list:
    """Get members for a specific scrape job."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if last_id is not None:
            cursor = await db.execute("""
                SELECT * FROM scraped_members
                WHERE scrape_job_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
            """, (scrape_job_id, last_id, limit))
        else:
            cursor = await db.execute("""
                SELECT * FROM scraped_members
                WHERE scrape_job_id = ?
                ORDER BY id ASC
                LIMIT ? OFFSET ?
            """, (scrape_job_id, limit, offset))
        return [dict(row) for row in await cursor.fetchall()]


async def count_scraped_members(scrape_job_id: str) -> int:
    """Count members for a scrape job WITHOUT loading all rows."""
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT COUNT(*) as cnt FROM scraped_members WHERE scrape_job_id = ?",
            (scrape_job_id,)
        )).fetchone()
        return row[0] if row else 0


async def delete_scrape_job(scrape_job_id: str):
    """Delete all members for a scrape job."""
    async with get_db() as db:
        await db.execute("DELETE FROM scraped_members WHERE scrape_job_id = ?", (scrape_job_id,))
        await db.commit()


# ── Batch Scrape Channels ──

async def create_batch_channel(batch_job_id: str, channel_username: str):
    """Add a channel to a batch scrape job."""
    async with get_db() as db:
        await db.execute("""
            INSERT INTO batch_scrape_channels (batch_job_id, channel_username)
            VALUES (?, ?)
        """, (batch_job_id, channel_username))
        await db.commit()


async def create_batch_channels_bulk(batch_job_id: str, channel_usernames):
    """Add many channels to a batch scrape job in ONE insert + ONE commit."""
    rows = [(batch_job_id, ch) for ch in channel_usernames]
    if not rows:
        return
    async with get_db() as db:
        await db.executemany(
            "INSERT INTO batch_scrape_channels (batch_job_id, channel_username) VALUES (?, ?)",
            rows,
        )
        await db.commit()


async def update_batch_channel(batch_job_id: str, channel_username: str, **kwargs):
    """Update a batch channel record."""
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [batch_job_id, channel_username]
    async with get_db() as db:
        await db.execute(
            f"UPDATE batch_scrape_channels SET {sets} WHERE batch_job_id = ? AND channel_username = ?",
            vals
        )
        await db.commit()


async def get_batch_channels(batch_job_id: str) -> list:
    """Get all channels for a batch scrape job."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM batch_scrape_channels WHERE batch_job_id = ? ORDER BY id",
            (batch_job_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]


async def delete_batch_channels(batch_job_id: str):
    """Delete batch channel records for a job."""
    async with get_db() as db:
        await db.execute("DELETE FROM batch_scrape_channels WHERE batch_job_id = ?", (batch_job_id,))
        await db.commit()


# ── DM Campaigns ──

async def create_dm_campaign(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO dm_campaigns
            (name, scrape_job_id, sender_account_ids, messages,
             delay_min, delay_max, daily_limit_premium, daily_limit_normal,
             use_ai_remix, exclude_previous_dms, total_targets, status,
             scheduled_at, target_timezone, ai_agent_id, auto_resume, min_lead_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["name"], data["scrape_job_id"],
            json.dumps(data.get("sender_account_ids", [])),
            json.dumps(data.get("messages", [])),
            data.get("delay_min", 30), data.get("delay_max", 90),
            data.get("daily_limit_premium", 60),
            data.get("daily_limit_normal", 10),
            1 if data.get("use_ai_remix") else 0,
            1 if data.get("exclude_previous_dms", True) else 0,
            data.get("total_targets", 0),
            data.get("status", "draft"),
            data.get("scheduled_at"),
            data.get("target_timezone"),
            data.get("ai_agent_id"),
            1 if data.get("auto_resume", True) else 0,
            data.get("min_lead_score", 0) or 0,
        ))
        await db.commit()
        return cursor.lastrowid


async def get_all_dm_campaigns() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM dm_campaigns ORDER BY created_at DESC
        """)
        rows = [dict(row) for row in await cursor.fetchall()]
        for r in rows:
            r["sender_account_ids"] = json.loads(r.get("sender_account_ids", "[]"))
            r["messages"] = json.loads(r.get("messages", "[]"))
        return rows


async def get_campaigns_updated_since(updated_since: str) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM dm_campaigns 
            WHERE updated_at > ? 
            ORDER BY created_at DESC
        """, (updated_since,))
        rows = [dict(row) for row in await cursor.fetchall()]
        for r in rows:
            r["sender_account_ids"] = json.loads(r.get("sender_account_ids", "[]"))
            r["messages"] = json.loads(r.get("messages", "[]"))
        return rows


async def get_dm_campaign(campaign_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM dm_campaigns WHERE id = ?", (campaign_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        r = dict(row)
        r["sender_account_ids"] = json.loads(r.get("sender_account_ids", "[]"))
        r["messages"] = json.loads(r.get("messages", "[]"))
        return r


async def get_scheduled_campaigns() -> list:
    """Get all campaigns with status 'scheduled' for re-registering scheduler jobs."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, scheduled_at, target_timezone FROM dm_campaigns WHERE status = 'scheduled'"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def update_dm_campaign_status(campaign_id: int, status: str,
                                     sent: int = None, failed: int = None, skipped: int = None,
                                     ai_agent_id: int = None):
    async with get_db() as db:
        updates = ["status = ?", "updated_at = datetime('now')"]
        params = [status]
        if sent is not None:
            updates.append("sent_count = ?")
            params.append(sent)
        if failed is not None:
            updates.append("failed_count = ?")
            params.append(failed)
        if skipped is not None:
            updates.append("skipped_count = ?")
            params.append(skipped)
        if ai_agent_id is not None:
            updates.append('ai_agent_id = ?')
            params.append(ai_agent_id if ai_agent_id != 0 else None)
        params.append(campaign_id)
        await db.execute(
            f"UPDATE dm_campaigns SET {', '.join(updates)} WHERE id = ?",
            params
        )
        await db.commit()


async def get_auto_resume_dm_campaigns() -> list[dict]:
    """Campaigns waiting for a SpamBot-limited sender to recover."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM dm_campaigns WHERE status='paused_auto' AND COALESCE(auto_resume, 1)=1"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def set_dm_campaign_auto_resume(campaign_id: int, enabled: bool) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE dm_campaigns SET auto_resume=?, updated_at=datetime('now') WHERE id=?",
            (1 if enabled else 0, campaign_id),
        )
        await db.commit()


async def update_dm_campaign_messages(campaign_id: int, messages: list,
                                       delay_min: int = None, delay_max: int = None,
                                       daily_limit_premium: int = None,
                                       daily_limit_normal: int = None,
                                       use_ai_remix: bool = None,
                                       exclude_previous_dms: bool = None,
                                       ai_agent_id: int = None,
                                       auto_resume: bool = None,
                                       scrape_job_id: str = None,
                                       sender_account_ids: list[int] = None,
                                       total_targets: int = None,
                                       min_lead_score: int = None):
    """Update an inactive campaign's source, senders, messages, and settings."""
    async with get_db() as db:
        updates = ["messages = ?", "updated_at = datetime('now')"]
        params = [json.dumps(messages)]
        if scrape_job_id is not None:
            updates.append("scrape_job_id = ?")
            params.append(scrape_job_id)
        if sender_account_ids is not None:
            updates.append("sender_account_ids = ?")
            params.append(json.dumps(sender_account_ids))
        if total_targets is not None:
            updates.append("total_targets = ?")
            params.append(total_targets)
        if delay_min is not None:
            updates.append("delay_min = ?")
            params.append(delay_min)
        if delay_max is not None:
            updates.append("delay_max = ?")
            params.append(delay_max)
        if daily_limit_premium is not None:
            updates.append("daily_limit_premium = ?")
            params.append(daily_limit_premium)
        if daily_limit_normal is not None:
            updates.append("daily_limit_normal = ?")
            params.append(daily_limit_normal)
        if use_ai_remix is not None:
            updates.append("use_ai_remix = ?")
            params.append(1 if use_ai_remix else 0)
        if exclude_previous_dms is not None:
            updates.append("exclude_previous_dms = ?")
            params.append(1 if exclude_previous_dms else 0)
        if ai_agent_id is not None:
            updates.append("ai_agent_id = ?")
            params.append(ai_agent_id if ai_agent_id != 0 else None)
        if auto_resume is not None:
            updates.append("auto_resume = ?")
            params.append(1 if auto_resume else 0)
        if min_lead_score is not None:
            updates.append("min_lead_score = ?")
            params.append(max(0, int(min_lead_score)))
        params.append(campaign_id)
        await db.execute(
            f"UPDATE dm_campaigns SET {', '.join(updates)} WHERE id = ?",
            params
        )
        await db.commit()


async def delete_dm_campaign(campaign_id: int):
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM dm_campaigns WHERE id = ?", (campaign_id,))
        await db.commit()


async def reserve_dm_campaign_target(campaign_id: int, target_user_id: int, account_id: int | None = None) -> bool:
    """Atomically reserve one campaign target before the Telegram network call.

    Returns True when the caller owns the send. A 'sent' or 'pending' row means
    the target is already handled (or may have been — a crash between the
    network call and the log write must never re-send). Only a 'failed' row may
    be retried, and only after the 24h re-try window.
    """
    async with get_db() as db:
        cur = await db.execute("""
            INSERT OR IGNORE INTO dm_campaign_reservations
                (campaign_id, target_user_id, account_id, status)
            VALUES (?, ?, ?, 'pending')
        """, (campaign_id, target_user_id, account_id))
        await db.commit()
        if cur.rowcount == 1:
            return True
        # Row exists — decide whether this target may be retried.
        row = await (await db.execute(
            "SELECT status, created_at FROM dm_campaign_reservations "
            "WHERE campaign_id=? AND target_user_id=?",
            (campaign_id, target_user_id)
        )).fetchone()
        if not row:
            return True  # vanished concurrently; treat as ours
        status, created_at = row[0], row[1]
        if status == "sent":
            return False
        if status == "pending":
            # Still in flight, or a crash left it pending. Only release it
            # after a full day — a duplicate DM to a KOL costs far more than
            # a delayed follow-up.
            # created_at is SQLite UTC ('YYYY-MM-DD HH:MM:SS'); datetime.now()
            # is local time — comparing them directly skewed the age by the
            # UTC offset (and broke outright for the 'T'-separated format).
            age_h = _sqlite_utc_age_hours(created_at)
            if age_h < 24:
                return False
            await db.execute(
                "UPDATE dm_campaign_reservations SET status='pending', account_id=?, "
                "created_at=datetime('now'), updated_at=datetime('now') "
                "WHERE campaign_id=? AND target_user_id=?",
                (account_id, campaign_id, target_user_id))
            await db.commit()
            return True
        # status == 'failed' → retryable
        await db.execute(
            "UPDATE dm_campaign_reservations SET status='pending', account_id=?, "
            "error_message=NULL, created_at=datetime('now'), updated_at=datetime('now') "
            "WHERE campaign_id=? AND target_user_id=?",
            (account_id, campaign_id, target_user_id))
        await db.commit()
        return True


async def finish_dm_campaign_reservation(campaign_id: int, target_user_id: int,
                                         status: str, error_message: str | None = None) -> None:
    """Resolve a pre-send reservation.

    'sent'   → keep the row (permanent dedupe against auto-resume re-sends).
    'failed' → mark failed; a later resume may retry the target
               (dm_campaign_logs already keeps the failure audit trail).
    """
    if status not in {"sent", "failed"}:
        raise ValueError(f"invalid reservation status: {status}")
    async with get_db() as db:
        await db.execute("""
            UPDATE dm_campaign_reservations
            SET status=?, error_message=?, updated_at=datetime('now')
            WHERE campaign_id=? AND target_user_id=?
        """, (status, error_message, campaign_id, target_user_id))
        await db.commit()


async def add_dm_campaign_log(campaign_id: int, account_id: int,
                               target_user_id: int, target_username: str,
                               status: str, error_message: str = None,
                               template_variant_id: int = None,
                               template_variant_index: int = None):
    async with get_db() as db:
        await db.execute("""
            INSERT INTO dm_campaign_logs
            (campaign_id, account_id, target_user_id, target_username,
             status, error_message, template_variant_id, template_variant_index)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (campaign_id, account_id, target_user_id, target_username,
              status, error_message, template_variant_id, template_variant_index))
        await db.commit()


async def get_dm_campaign_logs(campaign_id: int, limit: int = 200, offset: int = 0, last_sent_at: str = None, last_id: int = None) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if last_sent_at is not None and last_id is not None:
            cursor = await db.execute("""
                SELECT l.*, a.name as account_name
                FROM dm_campaign_logs l
                LEFT JOIN accounts a ON l.account_id = a.id
                WHERE l.campaign_id = ? AND (l.sent_at < ? OR (l.sent_at = ? AND l.id < ?))
                ORDER BY l.sent_at DESC, l.id DESC
                LIMIT ?
            """, (campaign_id, last_sent_at, last_sent_at, last_id, limit))
        else:
            cursor = await db.execute("""
                SELECT l.*, a.name as account_name
                FROM dm_campaign_logs l
                LEFT JOIN accounts a ON l.account_id = a.id
                WHERE l.campaign_id = ?
                ORDER BY l.sent_at DESC, l.id DESC
                LIMIT ? OFFSET ?
            """, (campaign_id, limit, offset))
        return [dict(row) for row in await cursor.fetchall()]


# ============================================================
# CSV EXPORT HELPERS
# ============================================================

async def get_all_scraped_contacts(limit: int = 1000000, offset: int = 0, last_id: int = None) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if last_id is not None:
            cursor = await db.execute("""
                SELECT * FROM (
                    SELECT MAX(id) as id, username, first_name, last_name, user_id, phone,
                           is_premium, status, group_title, scraped_at
                    FROM scraped_members
                    GROUP BY user_id
                )
                WHERE id < ?
                ORDER BY id DESC
                LIMIT ?
            """, (last_id, limit))
        else:
            cursor = await db.execute("""
                SELECT MAX(id) as id, username, first_name, last_name, user_id, phone,
                       is_premium, status, group_title, scraped_at
                FROM scraped_members
                GROUP BY user_id
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
        return [dict(row) for row in await cursor.fetchall()]


# ============================================================
# ANALYTICS
# ============================================================

async def get_agent_evolution(days: int = 30) -> dict:
    """Measure how much the AI agents have actually evolved.

    Returns five evidence-backed signals computed from ai_followup_chats +
    agent_learned_knowledge — no new tracking tables needed:
      learning   : new learned rules per ISO week (rate of learning)
      repetition : learnings re-distilled for an already-seen question
                   (AI failed to apply the lesson -> it 'forgot')
      handover   : share of chats that needed a human, per ISO week
                   (a real improvement pushes this DOWN)
      funnel     : lead_tier distribution + stage distribution
      discipline : avg AI replies per chat (fewer = sharper qualification)
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        span = f"-{int(days)} days"

        cur = await db.execute("""
            SELECT strftime('%Y-W%W', created_at) AS wk, COUNT(*) AS n
            FROM agent_learned_knowledge
            WHERE created_at >= datetime('now', ?)
            GROUP BY wk ORDER BY wk
        """, (span,))
        learning = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT question_pattern) AS u FROM agent_learned_knowledge")
        r = dict(await cur.fetchone())
        total_rules = r["n"] or 0
        distinct_q = r["u"] or 0
        repetition = {
            "total_rules": total_rules,
            "distinct_questions": distinct_q,
            "redundant": max(0, total_rules - distinct_q),
            "redundant_pct": round(100.0 * max(0, total_rules - distinct_q) / total_rules, 1) if total_rules else 0.0,
        }

        cur = await db.execute("""
            SELECT strftime('%Y-W%W', updated_at) AS wk,
                   COUNT(*) AS n,
                   SUM(CASE WHEN status IN ('needs_human','paused_admin') THEN 1 ELSE 0 END) AS h
            FROM ai_followup_chats
            WHERE updated_at >= datetime('now', ?)
            GROUP BY wk ORDER BY wk
        """, (span,))
        handover = []
        for row in await cur.fetchall():
            d = dict(row)
            n, h = d["n"] or 0, d["h"] or 0
            d["handover_pct"] = round(100.0 * h / n, 1) if n else 0.0
            handover.append(d)

        cur = await db.execute("SELECT lead_tier, COUNT(*) AS n FROM ai_followup_chats GROUP BY lead_tier")
        funnel = {str(dict(x)["lead_tier"] or "?"): dict(x)["n"] for x in await cur.fetchall()}

        cur = await db.execute("SELECT stage, COUNT(*) AS n FROM ai_followup_chats GROUP BY stage")
        stages = {str(dict(x)["stage"] or "?"): dict(x)["n"] for x in await cur.fetchall()}

        cur = await db.execute("""
            SELECT COUNT(*) AS chats, AVG(reply_count) AS avg_replies,
                   SUM(CASE WHEN reply_count > 0 THEN 1 ELSE 0 END) AS engaged
            FROM ai_followup_chats
        """)
        d = dict(await cur.fetchone())
        chats = d["chats"] or 0
        discipline = {
            "chats": chats,
            "engaged": d["engaged"] or 0,
            "avg_replies": round(d["avg_replies"] or 0.0, 2),
        }

    return {
        "range_days": days,
        "learning_per_week": learning,
        "repetition": repetition,
        "handover_per_week": handover,
        "funnel": funnel,
        "stages": stages,
        "discipline": discipline,
    }


async def get_analytics_overview() -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            WITH camp_agg AS (
                SELECT
                    COALESCE(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END), 0) AS sent,
                    COALESCE(SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END), 0) AS failed,
                    COALESCE(SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END), 0) AS skipped
                FROM dm_campaign_logs
            ),
            watch_agg AS (
                SELECT
                    COALESCE(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END), 0) AS sent,
                    COALESCE(SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END), 0) AS failed,
                    COALESCE(SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END), 0) AS skipped
                FROM watcher_dm_logs
            )
            SELECT
                c.sent AS campaign_sent,
                w.sent AS watcher_sent,
                c.failed AS campaign_failed,
                w.failed AS watcher_failed,
                c.skipped AS campaign_skipped,
                w.skipped AS watcher_skipped,
                (SELECT COUNT(*) FROM dm_replies r 
                 WHERE (r.sender_username IS NULL OR r.sender_username NOT LIKE '%bot')
                   AND (
                       EXISTS (SELECT 1 FROM watcher_dm_logs wdl WHERE wdl.target_user_id = r.sender_user_id AND wdl.status = 'success')
                       OR
                       EXISTS (SELECT 1 FROM dm_campaign_logs dcl WHERE dcl.target_user_id = r.sender_user_id AND dcl.status = 'success')
                   )
                ) as total_replies,
                (SELECT COUNT(DISTINCT user_id) FROM scraped_members) as total_contacts,
                (SELECT COUNT(*) FROM dm_campaigns) as total_campaigns,
                (SELECT COUNT(*) FROM dm_campaigns WHERE status='running') as active_campaigns,
                (SELECT COUNT(*) FROM keyword_watchers) as total_watchers,
                (SELECT COUNT(*) FROM reaction_logs WHERE status='success') as total_reactions,
                (SELECT COUNT(*) FROM accounts) as accounts_count,
                (SELECT COUNT(*) FROM schedules WHERE is_active=1) as active_schedules_count
            FROM camp_agg c, watch_agg w
        """)
        row = await cursor.fetchone()
        
        campaign_sent = row["campaign_sent"] or 0
        watcher_sent = row["watcher_sent"] or 0
        total_dm_sent = campaign_sent + watcher_sent
        
        campaign_failed = row["campaign_failed"] or 0
        watcher_failed = row["watcher_failed"] or 0
        total_dm_failed = campaign_failed + watcher_failed
        
        campaign_skipped = row["campaign_skipped"] or 0
        watcher_skipped = row["watcher_skipped"] or 0
        total_dm_skipped = campaign_skipped + watcher_skipped
        
        total_replies = row["total_replies"] or 0
        response_rate = round((total_replies / total_dm_sent * 100), 2) if total_dm_sent > 0 else 0
        
        return {
            "total_dm_sent": total_dm_sent,
            "total_dm_failed": total_dm_failed,
            "total_dm_skipped": total_dm_skipped,
            "total_replies": total_replies,
            "response_rate": response_rate,
            "total_contacts": row["total_contacts"] or 0,
            "total_campaigns": row["total_campaigns"] or 0,
            "active_campaigns": row["active_campaigns"] or 0,
            "total_watchers": row["total_watchers"] or 0,
            "total_reactions": row["total_reactions"] or 0,
            "accounts_count": row["accounts_count"] or 0,
            "active_schedules_count": row["active_schedules_count"] or 0,
        }


async def get_analytics_daily_stats(days: int = 30) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            WITH RECURSIVE dates(d) AS (
                SELECT date('now', '+7 hours', ? || ' days')
                UNION ALL
                SELECT date(d, '+1 day') FROM dates WHERE d < date('now', '+7 hours')
            ),
            campaign_stats AS (
                SELECT date(sent_at, '+7 hours') as d,
                       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as sent,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
                FROM dm_campaign_logs
                WHERE sent_at >= datetime('now', ? || ' days')
                GROUP BY date(sent_at, '+7 hours')
            ),
            watcher_stats AS (
                SELECT date(sent_at, '+7 hours') as d,
                       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as sent,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
                FROM watcher_dm_logs
                WHERE sent_at >= datetime('now', ? || ' days')
                GROUP BY date(sent_at, '+7 hours')
            ),
            reply_stats AS (
                SELECT date(received_at, '+7 hours') as d, COUNT(*) as replies
                FROM dm_replies
                WHERE received_at >= datetime('now', ? || ' days')
                GROUP BY date(received_at, '+7 hours')
            )
            SELECT dates.d as date,
                   COALESCE(cs.sent, 0) + COALESCE(ws.sent, 0) as sent,
                   COALESCE(cs.failed, 0) + COALESCE(ws.failed, 0) as failed,
                   COALESCE(rs.replies, 0) as replies
            FROM dates
            LEFT JOIN campaign_stats cs ON cs.d = dates.d
            LEFT JOIN watcher_stats ws ON ws.d = dates.d
            LEFT JOIN reply_stats rs ON rs.d = dates.d
            ORDER BY dates.d ASC
        """, (f"-{days}", f"-{days}", f"-{days}", f"-{days}"))
        return [dict(row) for row in await cursor.fetchall()]


async def get_analytics_account_health() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT
                a.id as account_id,
                a.name as account_name,
                a.is_flagged,
                a.flag_reason,
                COALESCE(l.sent_today, 0) as dm_sent_today,
                COALESCE(l.sent_total, 0) as dm_sent_total,
                COALESCE(l.flood_total, 0) as flood_count,
                COALESCE(l.failed_total, 0) as total_failed
            FROM accounts a
            LEFT JOIN (
                SELECT
                    account_id,
                    SUM(sent_today) as sent_today,
                    SUM(sent_total) as sent_total,
                    SUM(flood_total) as flood_total,
                    SUM(failed_total) as failed_total
                FROM (
                    SELECT
                        account_id,
                        SUM(CASE WHEN status = 'success' AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours') THEN 1 ELSE 0 END) as sent_today,
                        SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as sent_total,
                        SUM(CASE WHEN status = 'failed' AND (error_message LIKE '%Flood%' OR error_message LIKE '%PeerFlood%') THEN 1 ELSE 0 END) as flood_total,
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_total
                    FROM dm_campaign_logs
                    GROUP BY account_id
                    
                    UNION ALL
                    
                    SELECT
                        account_id,
                        SUM(CASE WHEN status = 'success' AND sent_at >= datetime('now', '+7 hours', 'start of day', '-7 hours') THEN 1 ELSE 0 END) as sent_today,
                        SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as sent_total,
                        SUM(CASE WHEN status = 'failed' AND (error_message LIKE '%Flood%' OR error_message LIKE '%PeerFlood%') THEN 1 ELSE 0 END) as flood_total,
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_total
                    FROM watcher_dm_logs
                    GROUP BY account_id
                )
                GROUP BY account_id
            ) l ON a.id = l.account_id
            ORDER BY a.id
        """)
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            acc = dict(row)
            dm_sent_total = acc["dm_sent_total"]
            total_failed = acc["total_failed"]
            total_attempts = dm_sent_total + total_failed
            success_rate = round((dm_sent_total / total_attempts * 100), 1) if total_attempts > 0 else 100.0
 
            flood_count = acc["flood_count"]
            health = 100
            if acc["is_flagged"]:
                health -= 40
            health -= min(flood_count * 5, 30)
            health -= max(0, round((100 - success_rate) * 0.3))
            health = max(0, min(100, health))
 
            result.append({
                "account_id": acc["account_id"],
                "account_name": acc["account_name"],
                "dm_sent_today": acc["dm_sent_today"],
                "dm_sent_total": dm_sent_total,
                "flood_count": flood_count,
                "success_rate": success_rate,
                "is_flagged": acc["is_flagged"] or 0,
                "flag_reason": acc["flag_reason"],
                "health_score": health,
            })
        return result


async def get_analytics_campaign_performance() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        campaigns = await (await db.execute(
            "SELECT * FROM dm_campaigns ORDER BY created_at DESC")).fetchall()
        if not campaigns:
            return []
            
        campaign_ids = [camp["id"] for camp in campaigns]
        replies_by_camp = {}

        for i in range(0, len(campaign_ids), 900):
            chunk = campaign_ids[i:i+900]
            placeholders = ",".join("?" for _ in chunk)
            cursor_replies = await db.execute(f"""
                SELECT l.campaign_id, COUNT(*) as cnt
                FROM dm_replies r
                JOIN (
                    SELECT DISTINCT campaign_id, target_user_id FROM dm_campaign_logs
                ) l ON r.sender_user_id = l.target_user_id
                WHERE l.campaign_id IN ({placeholders})
                GROUP BY l.campaign_id
            """, chunk)
            for r in await cursor_replies.fetchall():
                replies_by_camp[r["campaign_id"]] = r["cnt"]

        result = []
        for camp in campaigns:
            camp = dict(camp)
            cid = camp["id"]
            sent = camp.get("sent_count", 0)
            failed = camp.get("failed_count", 0)
            skipped = camp.get("skipped_count", 0)
            total = sent + failed
            success_rate = round((sent / total * 100), 1) if total > 0 else 0
            reply_count = replies_by_camp.get(cid, 0)

            result.append({
                "id": cid,
                "name": camp.get("name", ""),
                "status": camp.get("status", ""),
                "sent": sent,
                "failed": failed,
                "skipped": skipped,
                "success_rate": success_rate,
                "reply_count": reply_count,
                "created_at": camp.get("created_at"),
            })
        return result


# ============================================================
# TEMPLATE LIBRARY
# ============================================================

async def get_all_templates() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM dm_templates ORDER BY is_default DESC, created_at DESC"
        )).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["messages"] = json.loads(d.get("messages") or "[]")
            result.append(d)
        return result


async def get_template(template_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM dm_templates WHERE id=?", (template_id,)
        )).fetchone()
        if not row:
            return None
        d = dict(row)
        d["messages"] = json.loads(d.get("messages") or "[]")
        return d


async def create_template(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO dm_templates (name, category, messages, is_default)
            VALUES (?, ?, ?, ?)
        """, (
            data["name"],
            data.get("category", "general"),
            json.dumps(data.get("messages", [])),
            data.get("is_default", 0),
        ))
        await db.commit()
        return cursor.lastrowid


async def update_template(template_id: int, data: dict) -> bool:
    async with get_db() as db:
        cur = await db.execute("""
            UPDATE dm_templates SET name=?, category=?, messages=?, is_default=?
            WHERE id=?
        """, (
            data["name"],
            data.get("category", "general"),
            json.dumps(data.get("messages", [])),
            data.get("is_default", 0),
            template_id,
        ))
        await db.commit()
        return cur.rowcount > 0


async def delete_template(template_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("DELETE FROM dm_templates WHERE id=?", (template_id,))
        await db.commit()
        return cur.rowcount > 0


async def seed_default_templates():
    async with get_db() as db:
        row = await (await db.execute("SELECT COUNT(*) FROM dm_templates")).fetchone()
        if row[0] > 0:
            return
        defaults = [
            ("Crypto Community Outreach", "crypto", json.dumps([
                {"msg_type": "text", "content": "Hey {{first_name}}! 👋 I saw you in the group and thought I'd reach out. I'm building a crypto community focused on alpha calls and market analysis. Would love to connect!"}
            ])),
            ("NFT/Web3 Networking", "crypto", json.dumps([
                {"msg_type": "text", "content": "Hi {{first_name}}! 🎨 Fellow Web3 enthusiast here. I noticed we're in the same NFT community. Always great to connect with like-minded people in the space!"}
            ])),
            ("Forex Signal Promotion", "finance", json.dumps([
                {"msg_type": "text", "content": "Hello {{first_name}}! 📈 I run a trading signal channel with verified results. We've been consistently profitable this quarter. Interested in checking out our track record?"}
            ])),
            ("Affiliate Marketing", "marketing", json.dumps([
                {"msg_type": "text", "content": "Hey {{first_name}}! I came across your profile and thought you might be interested in a revenue opportunity I've been working with. Mind if I share some details?"}
            ])),
            ("General Networking", "general", json.dumps([
                {"msg_type": "text", "content": "Hi {{first_name}}! 👋 We're in the same group and I'd love to connect. Always looking to network with interesting people. How's your day going?"}
            ])),
            ("Service Promotion", "business", json.dumps([
                {"msg_type": "text", "content": "Hello {{first_name}}! I help businesses grow their online presence with proven strategies. Would you be open to a quick chat about how we could help?"}
            ])),
        ]
        for name, category, messages in defaults:
            await db.execute(
                "INSERT INTO dm_templates (name, category, messages, is_default) VALUES (?, ?, ?, 1)",
                (name, category, messages)
            )
        await db.commit()


# ── Template Performance CRUD ────────────────────────────────────────────────

async def get_template_performance(template_id: int = None,
                                    campaign_id: int = None) -> list[dict]:
    """Return performance stats, optionally filtered by template or campaign."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        conditions = []
        params: list = []
        if template_id is not None:
            conditions.append("template_id = ?")
            params.append(template_id)
        if campaign_id is not None:
            conditions.append("campaign_id = ?")
            params.append(campaign_id)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        cursor = await db.execute(
            f"SELECT * FROM template_performance{where} ORDER BY response_rate DESC",
            params,
        )
        return [dict(row) for row in await cursor.fetchall()]


async def update_template_performance(
    template_id: int, variant_index: int,
    campaign_id: int = None, watcher_id: int = None,
    sent_delta: int = 0, reply_delta: int = 0,
):
    """Increment sent/reply counters and recalculate response_rate.

    NULL campaign_id/watcher_id are normalised to 0 because SQLite treats NULLs
    as distinct in UNIQUE indexes — with NULL the ON CONFLICT never fires and
    every send inserted a new row instead of upserting.
    """
    campaign_id = 0 if campaign_id is None else campaign_id
    watcher_id = 0 if watcher_id is None else watcher_id
    async with get_db() as db:
        # Upsert row
        await db.execute("""
            INSERT INTO template_performance
                (template_id, variant_index, campaign_id, watcher_id,
                 total_sent, total_replies, response_rate, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, 0.0, datetime('now'))
            ON CONFLICT(template_id, variant_index, campaign_id, watcher_id)
            DO UPDATE SET
                total_sent    = total_sent    + ?,
                total_replies = total_replies + ?,
                response_rate = CASE
                    WHEN (total_sent + ?) > 0
                    THEN CAST((total_replies + ?) AS REAL) / (total_sent + ?)
                    ELSE 0.0
                END,
                last_updated  = datetime('now')
        """, (
            template_id, variant_index, campaign_id, watcher_id,
            sent_delta, reply_delta,
            sent_delta, reply_delta,
            sent_delta, reply_delta, sent_delta,
        ))
        await db.commit()


async def get_best_template_variant(template_id: int) -> int:
    """
    Return the variant_index with the highest response_rate
    (min MIN_SENDS sends). Falls back to 0.
    """
    MIN_SENDS = 5
    async with get_db() as db:
        row = await (await db.execute("""
            SELECT variant_index, SUM(total_sent) as ts, SUM(total_replies) as tr,
                   CASE WHEN SUM(total_sent) > 0
                        THEN CAST(SUM(total_replies) AS REAL) / SUM(total_sent)
                        ELSE 0.0 END as rate
            FROM template_performance
            WHERE template_id = ?
            GROUP BY variant_index
            HAVING ts >= ?
            ORDER BY rate DESC
            LIMIT 1
        """, (template_id, MIN_SENDS))).fetchone()
        return row[0] if row else 0


async def find_campaign_log_for_user(sender_user_id: int) -> dict | None:
    """
    Find the most recent successful campaign log entry for a user.
    Returns dict with campaign_id, template_variant_id, template_variant_index
    or None.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("""
            SELECT campaign_id, template_variant_id, template_variant_index
            FROM dm_campaign_logs
            WHERE target_user_id = ? AND status = 'success'
            ORDER BY sent_at DESC LIMIT 1
        """, (sender_user_id,))).fetchone()
        return dict(row) if row else None


async def find_running_campaign_log_for_user(sender_user_id: int) -> dict | None:
    """
    Find the most recent successful campaign log entry for a user
    where the associated campaign status is currently 'running'.
    Falls back to any campaign (completed/paused) that has an ai_agent_id set,
    so users who reply to finished campaigns still get AI follow-up.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # 1. Prefer RUNNING campaign (highest priority)
        row = await (await db.execute("""
            SELECT l.campaign_id, l.template_variant_id, l.template_variant_index, c.ai_agent_id
            FROM dm_campaign_logs l
            JOIN dm_campaigns c ON l.campaign_id = c.id
            WHERE l.target_user_id = ? AND l.status = 'success' AND c.status = 'running'
            ORDER BY l.sent_at DESC LIMIT 1
        """, (sender_user_id,))).fetchone()
        if row:
            return dict(row)
        # 2. Fallback: any campaign (completed/paused) that has an AI agent configured
        row = await (await db.execute("""
            SELECT l.campaign_id, l.template_variant_id, l.template_variant_index, c.ai_agent_id
            FROM dm_campaign_logs l
            JOIN dm_campaigns c ON l.campaign_id = c.id
            WHERE l.target_user_id = ? AND l.status = 'success' AND c.ai_agent_id IS NOT NULL
            ORDER BY l.sent_at DESC LIMIT 1
        """, (sender_user_id,))).fetchone()
        return dict(row) if row else None



async def find_watcher_log_for_user(sender_user_id: int) -> dict | None:
    """
    Find the most recent successful watcher DM log entry for a user.
    Returns dict with watcher_id, template_variant_index or None.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("""
            SELECT watcher_id, template_variant_index
            FROM watcher_dm_logs
            WHERE target_user_id = ? AND status = 'success'
            ORDER BY sent_at DESC LIMIT 1
        """, (sender_user_id,))).fetchone()
        return dict(row) if row else None


# ============================================================
# AUTO-REPLY RULES
# ============================================================

async def get_all_auto_reply_rules() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM auto_reply_rules ORDER BY created_at DESC"
        )).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["trigger_keywords"] = json.loads(d.get("trigger_keywords") or "[]")
            d["reply_messages"] = json.loads(d.get("reply_messages") or "[]")
            d["account_ids"] = json.loads(d.get("account_ids") or "[]")
            result.append(d)
        return result


async def get_active_auto_reply_rules() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM auto_reply_rules WHERE is_active=1 ORDER BY id"
        )).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["trigger_keywords"] = json.loads(d.get("trigger_keywords") or "[]")
            d["reply_messages"] = json.loads(d.get("reply_messages") or "[]")
            d["account_ids"] = json.loads(d.get("account_ids") or "[]")
            result.append(d)
        return result


async def create_auto_reply_rule(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO auto_reply_rules
            (name, trigger_type, trigger_keywords, reply_messages, account_ids,
             use_ai, ai_system_prompt, max_replies_per_user, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["name"],
            data.get("trigger_type", "keyword"),
            json.dumps(data.get("trigger_keywords", [])),
            json.dumps(data.get("reply_messages", [])),
            json.dumps(data.get("account_ids", [])),
            1 if data.get("use_ai") else 0,
            data.get("ai_system_prompt"),
            data.get("max_replies_per_user", 3),
            data.get("is_active", 1),
        ))
        await db.commit()
        return cursor.lastrowid


async def update_auto_reply_rule(rule_id: int, data: dict) -> bool:
    async with get_db() as db:
        cur = await db.execute("""
            UPDATE auto_reply_rules
            SET name=?, trigger_type=?, trigger_keywords=?, reply_messages=?,
                account_ids=?, use_ai=?, ai_system_prompt=?, max_replies_per_user=?, is_active=?
            WHERE id=?
        """, (
            data["name"],
            data.get("trigger_type", "keyword"),
            json.dumps(data.get("trigger_keywords", [])),
            json.dumps(data.get("reply_messages", [])),
            json.dumps(data.get("account_ids", [])),
            1 if data.get("use_ai") else 0,
            data.get("ai_system_prompt"),
            data.get("max_replies_per_user", 3),
            data.get("is_active", 1),
            rule_id,
        ))
        await db.commit()
        return cur.rowcount > 0


async def delete_auto_reply_rule(rule_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("DELETE FROM auto_reply_rules WHERE id=?", (rule_id,))
        await db.commit()
        return cur.rowcount > 0


async def toggle_auto_reply_rule(rule_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT is_active FROM auto_reply_rules WHERE id=?", (rule_id,)
        )).fetchone()
        if not row:
            return None
        new_state = 0 if row["is_active"] else 1
        await db.execute(
            "UPDATE auto_reply_rules SET is_active=? WHERE id=?",
            (new_state, rule_id))
        await db.commit()
        return {"id": rule_id, "is_active": new_state}


async def add_auto_reply_log(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO auto_reply_logs
            (rule_id, account_id, user_id, username, trigger_text, reply_text, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            data["rule_id"], data.get("account_id"), data["user_id"],
            data.get("username"), data.get("trigger_text"),
            data.get("reply_text"), data.get("status", "success"),
        ))
        await db.commit()
        return cursor.lastrowid


async def get_auto_reply_logs(rule_id: int, limit: int = 100) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM auto_reply_logs WHERE rule_id=? ORDER BY sent_at DESC LIMIT ?",
            (rule_id, limit)
        )).fetchall()
        return [dict(r) for r in rows]


async def count_user_auto_replies(rule_id: int, user_id: int) -> int:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM auto_reply_logs WHERE rule_id=? AND user_id=? AND status='success'",
            (rule_id, user_id)
        )).fetchone()
        return row[0] if row else 0


# ── Invite Campaigns CRUD ──────────────────────────────────────────────────────

async def create_invite_campaign(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO invite_campaigns
            (name, scrape_job_id, target_chat, target_chat_title, invite_mode,
             invite_link, sender_account_ids, daily_limit, delay_min, delay_max,
             dm_message, use_ai_remix, total_targets,
             schedule_enabled, schedule_time, schedule_days, schedule_days_remaining)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["name"], data["scrape_job_id"], data["target_chat"],
            data.get("target_chat_title"), data.get("invite_mode", "direct"),
            data.get("invite_link"), json.dumps(data.get("sender_account_ids", [])),
            data.get("daily_limit", 50), data.get("delay_min", 45),
            data.get("delay_max", 120), data.get("dm_message"),
            1 if data.get("use_ai_remix") else 0,
            data.get("total_targets", 0),
            1 if data.get("schedule_enabled") else 0,
            data.get("schedule_time"), data.get("schedule_days", 7),
            data.get("schedule_days", 7) if data.get("schedule_enabled") else None,
        ))
        await db.commit()
        return cursor.lastrowid


async def get_all_invite_campaigns() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM invite_campaigns ORDER BY created_at DESC"
        )).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["sender_account_ids"] = json.loads(d.get("sender_account_ids") or "[]")
            result.append(d)
        return result


async def get_invite_campaign(campaign_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM invite_campaigns WHERE id=?", (campaign_id,)
        )).fetchone()
        if not row:
            return None
        d = dict(row)
        d["sender_account_ids"] = json.loads(d.get("sender_account_ids") or "[]")
        return d


async def update_invite_campaign_status(campaign_id: int, status: str,
                                         invited=None, failed=None, skipped=None):
    async with get_db() as db:
        parts = ["status=?", "updated_at=datetime('now')"]
        params = [status]
        if invited is not None:
            parts.append("invited_count=?")
            params.append(invited)
        if failed is not None:
            parts.append("failed_count=?")
            params.append(failed)
        if skipped is not None:
            parts.append("skipped_count=?")
            params.append(skipped)
        params.append(campaign_id)
        await db.execute(
            f"UPDATE invite_campaigns SET {','.join(parts)} WHERE id=?",
            params
        )
        await db.commit()


async def update_invite_campaign(campaign_id: int, data: dict):
    async with get_db() as db:
        fields = []
        params = []
        for key in ["name", "scrape_job_id", "target_chat", "target_chat_title",
                    "invite_mode", "invite_link", "daily_limit", "delay_min",
                    "delay_max", "dm_message", "use_ai_remix",
                    "schedule_enabled", "schedule_time", "schedule_days",
                    "schedule_days_remaining"]:
            if key in data:
                fields.append(f"{key}=?")
                if key == "use_ai_remix" or key == "schedule_enabled":
                    params.append(1 if data[key] else 0)
                else:
                    params.append(data[key])
        if "sender_account_ids" in data:
            fields.append("sender_account_ids=?")
            params.append(json.dumps(data["sender_account_ids"]))
        fields.append("updated_at=datetime('now')")
        params.append(campaign_id)
        await db.execute(
            f"UPDATE invite_campaigns SET {','.join(fields)} WHERE id=?",
            params
        )
        await db.commit()


async def delete_invite_campaign(campaign_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM invite_campaign_logs WHERE campaign_id=?", (campaign_id,))
        await db.execute("DELETE FROM invite_campaigns WHERE id=?", (campaign_id,))
        await db.commit()


async def add_invite_campaign_log(campaign_id, account_id, target_user_id,
                                   target_username, status, error_message=None):
    async with get_db() as db:
        await db.execute("""
            INSERT INTO invite_campaign_logs
            (campaign_id, account_id, target_user_id, target_username, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (campaign_id, account_id, target_user_id, target_username, status, error_message))
        await db.commit()


async def get_invite_campaign_logs(campaign_id: int, limit=200, offset=0) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM invite_campaign_logs WHERE campaign_id=? ORDER BY invited_at DESC LIMIT ? OFFSET ?",
            (campaign_id, limit, offset)
        )).fetchall()
        return [dict(r) for r in rows]


async def get_invite_campaign_sent_user_ids(campaign_id: int) -> set:
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT target_user_id FROM invite_campaign_logs WHERE campaign_id=? AND status IN ('invited','already_member')",
            (campaign_id,)
        )).fetchall()
        return {r[0] for r in rows}



# ══════════════════════════════════════════════════════════════════════════════
# ══ Warmup / Seeding CRUD ════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════

async def create_warmup_group(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO warmup_groups (name, chat_id, chat_title, chat_username)
               VALUES (?, ?, ?, ?)""",
            (data["name"], data["chat_id"],
             data.get("chat_title", ""), data.get("chat_username", ""))
        )
        await db.commit()
        return cursor.lastrowid


async def get_warmup_groups() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM warmup_groups ORDER BY created_at DESC"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_warmup_group(group_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM warmup_groups WHERE id=?", (group_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_warmup_group(group_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM warmup_logs WHERE group_id=?", (group_id,))
        await db.execute(
            "DELETE FROM warmup_logs WHERE job_id IN "
            "(SELECT id FROM warmup_jobs WHERE group_id=?)", (group_id,)
        )
        await db.execute("DELETE FROM warmup_jobs WHERE group_id=?", (group_id,))
        await db.execute("DELETE FROM warmup_scripts WHERE group_id=?", (group_id,))
        await db.execute("DELETE FROM warmup_groups WHERE id=?", (group_id,))
        await db.commit()


async def create_warmup_script(group_id: int, content: str,
                                msg_type: str = "text",
                                use_ai_remix: int = 1,
                                sort_order: int = 0) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO warmup_scripts (group_id, content, msg_type, use_ai_remix, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (group_id, content, msg_type, use_ai_remix, sort_order)
        )
        await db.commit()
        return cursor.lastrowid


async def get_warmup_scripts(group_id: int) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM warmup_scripts WHERE group_id=? ORDER BY sort_order, id",
            (group_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]


async def delete_warmup_script(script_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM warmup_scripts WHERE id=?", (script_id,))
        await db.commit()


async def create_warmup_job(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO warmup_jobs
               (group_id, account_ids, interval_min, interval_max,
                daily_post_limit, schedule_start, schedule_end)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (data["group_id"],
             json.dumps(data.get("account_ids", [])),
             data.get("interval_min", 30),
             data.get("interval_max", 120),
             data.get("daily_post_limit", 10),
             data.get("schedule_start", "09:00"),
             data.get("schedule_end", "22:00"))
        )
        await db.commit()
        return cursor.lastrowid


async def get_warmup_jobs(group_id: int = None) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if group_id is not None:
            cursor = await db.execute(
                "SELECT * FROM warmup_jobs WHERE group_id=? ORDER BY id DESC",
                (group_id,)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM warmup_jobs ORDER BY id DESC"
            )
        return [dict(row) for row in await cursor.fetchall()]


async def get_warmup_job(job_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM warmup_jobs WHERE id=?", (job_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_warmup_job_status(job_id: int, status: str,
                                    posts_today: int = None,
                                    last_post_at: str = None):
    async with get_db() as db:
        sets = ["status=?"]
        params = [status]
        if posts_today is not None:
            sets.append("posts_today=?")
            params.append(posts_today)
        if last_post_at is not None:
            sets.append("last_post_at=?")
            params.append(last_post_at)
        params.append(job_id)
        await db.execute(
            f"UPDATE warmup_jobs SET {', '.join(sets)} WHERE id=?", params
        )
        await db.commit()


async def delete_warmup_job(job_id: int):
    async with get_db() as db:
        await db.execute("DELETE FROM warmup_logs WHERE job_id=?", (job_id,))
        await db.execute("DELETE FROM warmup_jobs WHERE id=?", (job_id,))
        await db.commit()


async def add_warmup_log(job_id: int, group_id: int, account_id: int,
                          script_id: int = None, message_sent: str = "",
                          status: str = "success", error_message: str = None):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO warmup_logs
               (job_id, group_id, account_id, script_id, message_sent, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job_id, group_id, account_id, script_id, message_sent, status, error_message)
        )
        await db.commit()


async def get_warmup_logs(job_id: int = None, group_id: int = None,
                           limit: int = 100) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        where_parts = []
        params = []
        if job_id is not None:
            where_parts.append("job_id=?")
            params.append(job_id)
        if group_id is not None:
            where_parts.append("group_id=?")
            params.append(group_id)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        params.append(limit)
        cursor = await db.execute(
            f"SELECT * FROM warmup_logs {where} ORDER BY posted_at DESC LIMIT ?",
            params
        )
        return [dict(row) for row in await cursor.fetchall()]


async def reset_warmup_daily_counts():
    async with get_db() as db:
        await db.execute("UPDATE warmup_jobs SET posts_today = 0")
        await db.commit()


# NOTE: find_campaign_log_for_user / find_watcher_log_for_user are defined once
# above (near find_running_campaign_log_for_user). A duplicate pair used to live
# here and silently shadowed them (F811) — removed to keep one source of truth.


# ── AI Follow-Up Sales Agent DB Helpers ─────────────────────────────────────

async def get_followup_chat(account_id: int, user_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM ai_followup_chats WHERE account_id = ? AND user_id = ?",
            (account_id, user_id)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        res["history"] = json.loads(res.get("history_json", "[]"))
        return res


async def get_or_create_followup_chat(
    account_id: int,
    user_id: int,
    username: str | None = None,
    name: str | None = None,
    campaign_id: int | None = None,
    watcher_id: int | None = None
) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM ai_followup_chats WHERE account_id = ? AND user_id = ?",
            (account_id, user_id)
        )
        row = await cursor.fetchone()
        if row:
            res = dict(row)
            res["history"] = json.loads(res.get("history_json", "[]"))
            if campaign_id and not res.get("campaign_id"):
                await db.execute(
                    "UPDATE ai_followup_chats SET campaign_id = ? WHERE account_id = ? AND user_id = ?",
                    (campaign_id, account_id, user_id)
                )
                await db.commit()
                res["campaign_id"] = campaign_id
            return res

        # Create new (kol_step starts at 'prospect' so the legacy migration never touches it)
        await db.execute("""
            INSERT INTO ai_followup_chats
            (account_id, user_id, username, name, campaign_id, watcher_id, status, history_json, reply_count,
             kol_step, kol_step_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', '[]', 0, 'prospect', datetime('now'))
        """, (account_id, user_id, username, name, campaign_id, watcher_id))
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM ai_followup_chats WHERE account_id = ? AND user_id = ?",
            (account_id, user_id)
        )
        row = await cursor.fetchone()
        res = dict(row) if row else {}
        res["history"] = []
        return res


async def append_followup_chat_message(
    account_id: int,
    user_id: int,
    role: str,
    content: str,
    inc_reply_count: bool = False
) -> dict | None:
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")  # Serialize history read-modify-write across connections.
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM ai_followup_chats WHERE account_id = ? AND user_id = ?",
            (account_id, user_id)
        )
        row = await cursor.fetchone()
        if not row:
            # Must not leave the BEGIN IMMEDIATE transaction open — an uncommitted
            # write transaction would hold the SQLite write lock indefinitely.
            await db.rollback()
            return None

        chat = dict(row)
        history = json.loads(chat.get("history_json", "[]"))
        history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        # Keep last 60 messages to preserve deep context
        if len(history) > 60:
            history = history[-60:]

        reply_count = chat.get("reply_count", 0) + (1 if inc_reply_count else 0)

        # last_user_message_at anchors the drip cadence to the USER's real
        # silence (it must NOT be bumped by our own outgoing messages, or the
        # day 1/3/7 cadence resets on every follow-up we send).
        if role == "user":
            await db.execute("""
                UPDATE ai_followup_chats
                SET history_json = ?, reply_count = ?, updated_at = datetime('now'),
                    last_user_message_at = datetime('now')
                WHERE account_id = ? AND user_id = ?
            """, (json.dumps(history), reply_count, account_id, user_id))
        else:
            await db.execute("""
                UPDATE ai_followup_chats
                SET history_json = ?, reply_count = ?, updated_at = datetime('now')
                WHERE account_id = ? AND user_id = ?
            """, (json.dumps(history), reply_count, account_id, user_id))
        await db.commit()

        chat["history"] = history
        chat["history_json"] = json.dumps(history)
        chat["reply_count"] = reply_count
        return chat


async def update_followup_chat_status(account_id: int, user_id: int, status: str) -> bool:
    async with get_db() as db:
        await db.execute("""
            UPDATE ai_followup_chats
            SET status = ?, updated_at = datetime('now')
            WHERE account_id = ? AND user_id = ?
        """, (status, account_id, user_id))
        await db.commit()
        return True


async def set_human_takeover(account_id: int, user_id: int) -> bool:
    """Mark chat as human-controlled. AI will NOT reply until 60m of admin inactivity."""
    async with get_db() as db:
        await db.execute("""
            INSERT INTO ai_followup_chats (account_id, user_id, status, human_takeover_at, kol_step, kol_step_updated_at)
            VALUES (?, ?, 'needs_human', datetime('now'), 'prospect', datetime('now'))
            ON CONFLICT(account_id, user_id) DO UPDATE SET
                status = 'needs_human', human_takeover_at = datetime('now'),
                updated_at = datetime('now')
        """, (account_id, user_id))
        await db.commit()
        return True


async def resume_ai_for_user(account_id: int, user_id: int) -> bool:
    """Hand the chat back to the AI: clear the lock and the human-takeover stamp.

    Clears human_takeover_at too — leaving it set would keep the chat looking
    "human-owned" and silently re-lock the AI on the next inbound message.
    """
    async with get_db() as db:
        cursor = await db.execute("""
            UPDATE ai_followup_chats
            SET status = 'active', human_takeover_at = NULL, updated_at = datetime('now')
            WHERE account_id = ? AND user_id = ?
        """, (account_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


# KOL lifecycle stages, in funnel order. 'paused' is an operator hold, not a step.
PIPELINE_STAGES = ("new", "nurture", "negotiating", "signed", "paused")
_STAGE_ALIASES = {
    "n": "new", "moi": "new",
    "nu": "nurture", "nuoi": "nurture", "nurture": "nurture",
    "dam": "negotiating", "dam_phan": "negotiating", "thuong_luong": "negotiating",
    "negotiating": "negotiating", "nego": "negotiating",
    "ky": "signed", "chot": "signed", "signed": "signed", "done": "signed",
    "dung": "paused", "tam_dung": "paused", "pause": "paused", "paused": "paused",
}


def normalize_stage(value: str | None) -> str | None:
    """Map user input (vi/en, with or without accents) to a canonical stage.

    Returns None when unrecognised so callers can reject it instead of silently
    writing a bogus stage the funnel view would then miscount.
    """
    if not value:
        return None
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    # Strip Vietnamese diacritics so "nuôi"/"nuoi" both work. NFD handles the
    # tone marks, but 'đ' is a separate letter (not decomposed), so map it by hand.
    import unicodedata
    key = key.replace("đ", "d")
    key = "".join(c for c in unicodedata.normalize("NFD", key)
                  if unicodedata.category(c) != "Mn")
    if key in PIPELINE_STAGES:
        return key
    return _STAGE_ALIASES.get(key)


async def set_followup_stage(account_id: int, user_id: int, stage: str) -> bool:
    """Move a chat to a pipeline stage and keep `status` consistent with it.

    stage is the operator-facing truth; status stays the AI's gate. They are kept
    in sync here so no caller has to remember both:
      signed / paused  -> AI must not speak   (status 'needs_human')
      new / nurture / negotiating -> AI may speak (status 'active')
    """
    stage = normalize_stage(stage) or stage
    if stage not in PIPELINE_STAGES:
        return False
    new_status = "needs_human" if stage in ("signed", "paused") else "active"
    async with get_db() as db:
        cursor = await db.execute("""
            UPDATE ai_followup_chats
            SET stage = ?, status = ?, updated_at = datetime('now'),
                human_takeover_at = CASE WHEN ? = 1 THEN COALESCE(human_takeover_at, datetime('now'))
                                         ELSE NULL END
            WHERE account_id = ? AND user_id = ?
        """, (stage, new_status, 1 if stage in ("signed", "paused") else 0,
              account_id, user_id))
        await db.commit()
        ok = cursor.rowcount > 0
    # Legacy '/stage signed' = the deal is done → make sure the 5-step funnel
    # reflects at least deal_agreed (admin action, never moves a lead backwards).
    if ok and stage == "signed":
        try:
            cur = await get_kol_step(account_id, user_id)
            if kol_step_rank(cur) < kol_step_rank("deal_agreed"):
                await set_kol_step(account_id, user_id, "deal_agreed", actor="admin", reason="/stage signed")
        except Exception as exc:
            logger.warning(f"[KOLStep] sync from stage=signed failed: {exc}")
    return ok


async def get_pipeline_counts() -> dict:
    """Count leads per stage — the numbers behind /pipeline."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(f"""
            SELECT COALESCE(NULLIF(stage, ''), 'new') AS stage, COUNT(*) AS n
            FROM ai_followup_chats
            WHERE {_REAL_LEAD_SQL}
            GROUP BY COALESCE(NULLIF(stage, ''), 'new')
        """)
        counts = {r["stage"]: r["n"] for r in await cursor.fetchall()}
    return {s: counts.get(s, 0) for s in PIPELINE_STAGES}


async def find_followup_chat_by_username(username: str, account_id: int | None = None) -> dict | None:
    """Look up a chat by @username, optionally scoped to one account.

    Usernames are stored with or without the leading '@' and in mixed case, so
    compare case-insensitively on the stripped form. When several accounts have
    the same lead, prefer the one with the most replies (the real conversation).
    """
    if not username:
        return None
    u = username.lstrip("@").strip()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        sql = """
            SELECT account_id, user_id, username, name, stage, status
            FROM ai_followup_chats
            WHERE LOWER(LTRIM(COALESCE(username, ''), '@')) = LOWER(?)
        """
        params: list = [u]
        if account_id is not None:
            sql += " AND account_id = ?"
            params.append(int(account_id))
        sql += " ORDER BY COALESCE(reply_count, 0) DESC LIMIT 1"
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None


# A row is a real lead only if it is not a Telegram bot and not one of our own
# accounts/helper bots. Without this the funnel counts include ~87 automated
# accounts (SpamBot, DuckChain_bot, weexwillbot, ...) and the 'paused' column
# reads ~3x larger than the truth.
_REAL_LEAD_SQL = """
    LOWER(COALESCE(username, '')) NOT LIKE '%bot'
    AND LOWER(COALESCE(username, '')) NOT IN ('weexwill', 'weexwillbot')
    AND COALESCE(status, '') <> 'bot_ignored'
"""


async def get_leads_by_stage(stage: str, limit: int = 20) -> list:
    """Leads currently in a stage, most recently active first."""
    stage = normalize_stage(stage) or stage
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(f"""
            SELECT account_id, user_id, username, name, stage, intent_score,
                   lead_tier, reply_count, last_user_message_at, summary
            FROM ai_followup_chats
            WHERE COALESCE(NULLIF(stage, ''), 'new') = ?
              AND {_REAL_LEAD_SQL}
            ORDER BY COALESCE(last_user_message_at, updated_at) DESC
            LIMIT ?
        """, (stage, int(limit)))
        return [dict(r) for r in await cursor.fetchall()]


async def get_leads_needing_attention(limit: int = 15) -> list:
    """Leads worth the operator's eyes right now.

    Two signals: a high intent score still in 'nurture' (ready to move forward),
    or a negotiating lead (deal on the table). Ordered by intent so the hottest
    is first — this is what makes /attention useful instead of noise.

    Deduped by username: the same KOL is often contacted from several accounts,
    and showing them once (their hottest row) is the point of a short list.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(f"""
            SELECT account_id, user_id, username, name, stage, intent_score,
                   lead_tier, reply_count, last_user_message_at
            FROM (
                SELECT account_id, user_id, username, name, stage, intent_score,
                       lead_tier, reply_count, last_user_message_at, updated_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY LOWER(LTRIM(COALESCE(username, ''), '@')),
                                        COALESCE(NULLIF(name, ''), user_id)
                           ORDER BY COALESCE(intent_score, 0) DESC,
                                    COALESCE(last_user_message_at, updated_at) DESC
                       ) AS rn
                FROM ai_followup_chats
                WHERE COALESCE(NULLIF(stage, ''), 'new') IN ('nurture', 'negotiating')
                  AND (COALESCE(intent_score, 0) >= 60 OR lead_tier = 'Tier A'
                       OR COALESCE(NULLIF(stage, ''), 'new') = 'negotiating')
                  AND {_REAL_LEAD_SQL}
            )
            WHERE rn = 1
            ORDER BY COALESCE(intent_score, 0) DESC,
                     COALESCE(last_user_message_at, updated_at) DESC
            LIMIT ?
        """, (int(limit),))
        return [dict(r) for r in await cursor.fetchall()]


async def update_followup_lead_metrics(
    account_id: int,
    user_id: int,
    intent_score: int,
    lead_tier: str,
    summary: str
) -> bool:
    async with get_db() as db:
        await db.execute("""
            UPDATE ai_followup_chats
            SET intent_score = ?, lead_tier = ?, summary = ?, updated_at = datetime('now')
            WHERE account_id = ? AND user_id = ?
        """, (intent_score, lead_tier, summary, account_id, user_id))
        await db.commit()
        return True


async def update_followup_verification(account_id: int, user_id: int, status: str) -> bool:
    """Update verification_status: none, requested, submitted, verified, rejected."""
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE ai_followup_chats SET verification_status = ?, updated_at = datetime('now') WHERE account_id = ? AND user_id = ?",
            (status, account_id, user_id)
        )
        # MUST commit: an uncommitted write leaves the pooled connection with an
        # open transaction, which then breaks runtime_commands.claim()'s
        # BEGIN IMMEDIATE on the next reuse ("cannot start a transaction within
        # a transaction") and loses the write on crash.
        await db.commit()
        return cursor.rowcount > 0


async def get_followup_chat_counts() -> dict:
    """Lightweight count by status — no data transfer."""
    async with get_db() as db:
        cursor = await db.execute("SELECT status, COUNT(*) as cnt FROM ai_followup_chats GROUP BY status")
        rows = await cursor.fetchall()
        return {r[0]: r[1] for r in rows}


async def get_all_followup_chats(status_filter: str | None = None, limit: int = 50, offset: int = 0, include_history: bool = False) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        where_clause = ""
        params = []
        if status_filter:
            where_clause = "WHERE c.status = ?"
            params.append(status_filter)

        params.extend([limit, offset])
        cursor = await db.execute(f"""
            SELECT c.id, c.account_id, c.user_id, c.username, c.name, c.campaign_id, c.watcher_id,
                   c.status, c.reply_count, c.created_at, c.updated_at, c.intent_score,
                   c.lead_tier, c.summary, c.last_drip_stage, c.human_takeover_at, c.verification_status,
                   COALESCE(c.kol_step, 'prospect') AS kol_step, c.kol_step_updated_at,
                   COALESCE(c.step_reminders_sent, 0) AS step_reminders_sent,
                   p.weex_uid, p.vip_code, p.volume_claimed, p.missing_items,
                   a.name AS account_name,
                   CASE WHEN n.status = 'sent' THEN 1 ELSE 0 END AS nudged,
                   COALESCE(n.nudge_count, 0) AS nudge_count
                   {", c.history_json" if include_history else ""}
            FROM ai_followup_chats c
            LEFT JOIN accounts a ON a.id = c.account_id
            LEFT JOIN nudges n ON n.account_id = c.account_id AND n.target_user_id = c.user_id
            LEFT JOIN kol_profiles p ON p.account_id = c.account_id AND p.user_id = c.user_id
            {where_clause}
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
        """, params)
        rows = [dict(r) for r in await cursor.fetchall()]
        for r in rows:
            r["nudged"] = bool(r.get("nudged", 0))
            r["nudge_count"] = r.get("nudge_count", 0)
            try:
                r["missing_items"] = json.loads(r.get("missing_items") or "[]")
            except Exception:
                r["missing_items"] = []
        if include_history:
            for r in rows:
                r["history"] = json.loads(r.get("history_json", "[]"))
        return rows


# ── AI Agents CRUD ────────────────────────────────────────────────────────
async def get_all_ai_agents(active_only=True) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if active_only:
            cursor = await db.execute("SELECT * FROM ai_agents WHERE is_active = 1 ORDER BY created_at DESC")
        else:
            cursor = await db.execute("SELECT * FROM ai_agents ORDER BY created_at DESC")
        rows = [dict(row) for row in await cursor.fetchall()]
        for r in rows:
            try:
                r["api_keys_json"] = json.loads(r.get("api_keys_json", "[]"))
            except Exception:
                r["api_keys_json"] = []
            try:
                r["handover_keywords"] = json.loads(r.get("handover_keywords", "[]"))
            except Exception:
                r["handover_keywords"] = []
        return rows


get_ai_agents = get_all_ai_agents


async def get_ai_agent(agent_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM ai_agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        r = dict(row)
        try:
            r["api_keys_json"] = json.loads(r.get("api_keys_json", "[]"))
        except Exception:
            r["api_keys_json"] = []
        try:
            r["handover_keywords"] = json.loads(r.get("handover_keywords", "[]"))
        except Exception:
            r["handover_keywords"] = []
        return r


async def create_ai_agent(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO ai_agents
            (name, description, avatar_emoji, provider, model, base_url,
             api_keys_json, system_prompt, remix_instruction, knowledge_base,
             handover_keywords, max_replies, tone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["name"],
            data.get("description", ""),
            data.get("avatar_emoji", "🤖"),
            data.get("provider", "gemini"),
            data.get("model", ""),
            data.get("base_url", ""),
            json.dumps(data.get("api_keys_json", [])),
            data.get("system_prompt", ""),
            data.get("remix_instruction", ""),
            data.get("knowledge_base", ""),
            json.dumps(data.get("handover_keywords", [])),
            data.get("max_replies", 10),
            data.get("tone", "friendly"),
        ))
        await db.commit()
        return cursor.lastrowid


async def update_ai_agent(agent_id: int, data: dict) -> bool:
    async with get_db() as db:
        fields = []
        values = []
        allowed = ["name", "description", "avatar_emoji", "provider", "model",
                   "base_url", "system_prompt", "remix_instruction",
                   "knowledge_base", "tone", "max_replies", "is_active"]
        for key in allowed:
            if key in data:
                fields.append(f"{key} = ?")
                values.append(data[key])
        if "api_keys_json" in data:
            fields.append("api_keys_json = ?")
            values.append(json.dumps(data["api_keys_json"]) if isinstance(data["api_keys_json"], list) else data["api_keys_json"])
        if "handover_keywords" in data:
            fields.append("handover_keywords = ?")
            values.append(json.dumps(data["handover_keywords"]) if isinstance(data["handover_keywords"], list) else data["handover_keywords"])
        if not fields:
            return False
        fields.append("updated_at = datetime('now')")
        values.append(agent_id)
        await db.execute(f"UPDATE ai_agents SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()
        return True


async def delete_ai_agent(agent_id: int) -> bool:
    """Soft delete - set is_active = 0"""
    async with get_db() as db:
        await db.execute("UPDATE ai_agents SET is_active = 0, updated_at = datetime('now') WHERE id = ?", (agent_id,))
        await db.commit()
        return True


async def duplicate_ai_agent(agent_id: int) -> int | None:
    agent = await get_ai_agent(agent_id)
    if not agent:
        return None
    new_data = {
        "name": f"{agent['name']} - Copy",
        "description": agent.get("description", ""),
        "avatar_emoji": agent.get("avatar_emoji", "🤖"),
        "provider": agent.get("provider", "gemini"),
        "model": agent.get("model", ""),
        "base_url": agent.get("base_url", ""),
        "api_keys_json": agent.get("api_keys_json", []),
        "system_prompt": agent.get("system_prompt", ""),
        "remix_instruction": agent.get("remix_instruction", ""),
        "knowledge_base": agent.get("knowledge_base", ""),
        "handover_keywords": agent.get("handover_keywords", []),
        "max_replies": agent.get("max_replies", 10),
        "tone": agent.get("tone", "friendly"),
    }
    return await create_ai_agent(new_data)


async def count_campaigns_by_agent(agent_id: int) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM dm_campaigns WHERE ai_agent_id = ?",
            (agent_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def count_campaigns_by_agent_bulk(agent_ids) -> dict:
    """Campaign counts for many agents in ONE query. Returns {agent_id: count}
    with 0 filled in for agents that have no campaigns."""
    ids = list(agent_ids)
    if not ids:
        return {}
    counts = {aid: 0 for aid in ids}
    placeholders = ",".join("?" * len(ids))
    async with get_db() as db:
        cursor = await db.execute(
            f"SELECT ai_agent_id, COUNT(*) FROM dm_campaigns "
            f"WHERE ai_agent_id IN ({placeholders}) GROUP BY ai_agent_id",
            tuple(ids),
        )
        for agent_id, count in await cursor.fetchall():
            counts[agent_id] = count
    return counts


# ── KOL Profiles & Learned Knowledge CRUD ─────────────────────────────────────
# Structured columns mirrored from profile_json (spec §B2). Any key in this
# tuple written through upsert_kol_profile lands in its own column as well.
KOL_PROFILE_COLUMNS = (
    "channel_link", "platform", "followers", "weex_uid", "volume_claimed",
    "volume_verified", "offer_version", "selected_campaign", "vip_code",
    "affiliate_link", "missing_items", "last_kol_reply_at", "last_our_reply_at",
)


def _merge_profile_row(row) -> dict:
    """profile_json + structured columns (columns win when set)."""
    try:
        profile = json.loads(row["profile_json"] or "{}")
    except Exception:
        profile = {}
    if not isinstance(profile, dict):
        profile = {}
    keys = row.keys()
    for col in KOL_PROFILE_COLUMNS:
        if col not in keys:
            continue
        val = row[col]
        if val is None or (isinstance(val, str) and val.strip() == ""):
            continue
        if col == "missing_items":
            try:
                val = json.loads(val) if isinstance(val, str) else val
            except Exception:
                val = []
        profile[col] = val
    if not isinstance(profile.get("missing_items"), list):
        profile.pop("missing_items", None)
    return profile


async def get_kol_profile(account_id: int, user_id: int) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM kol_profiles WHERE account_id = ? AND user_id = ?",
            (account_id, user_id)
        )
        row = await cursor.fetchone()
        if not row:
            return {}
        return _merge_profile_row(row)


async def get_kol_profiles(user_ids: list[int] | None = None) -> list[dict]:
    """Return KOL profiles (account_id, user_id + merged profile fields)."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM kol_profiles"
        params: list = []
        if user_ids:
            query += " WHERE user_id IN (" + ",".join("?" for _ in user_ids) + ")"
            params = list(user_ids)
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    out = []
    for row in rows:
        profile = _merge_profile_row(row)
        out.append({"account_id": row["account_id"], "user_id": row["user_id"], **profile})
    return out


def _profile_col_value(key: str, value):
    """Serialise a structured profile field for its column."""
    if value is None:
        return None
    if key == "missing_items":
        return json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
    if key == "offer_version":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


async def upsert_kol_profile(account_id: int, user_id: int, profile_data: dict) -> bool:
    """Merge `profile_data` into the KOL profile (json + mirrored columns).

    Empty/None values are dropped (existing behaviour: a field is never erased
    by a blank LLM extraction). Structured keys are also written to their own
    columns so SQL can read them without parsing JSON.
    """
    if not profile_data:
        return False
    current = await get_kol_profile(account_id, user_id)
    current.update(profile_data)
    # Filter out empty/null values
    cleaned = {}
    for k, v in current.items():
        if v is None:
            continue
        if isinstance(v, (list, dict)):
            if k != "missing_items" and not v:
                continue
        elif str(v).strip() == "":
            continue
        cleaned[k] = v
    json_str = json.dumps(cleaned, ensure_ascii=False)
    col_values = [_profile_col_value(c, cleaned.get(c)) for c in KOL_PROFILE_COLUMNS]
    col_list = ", ".join(KOL_PROFILE_COLUMNS)
    col_placeholders = ", ".join("?" for _ in KOL_PROFILE_COLUMNS)
    col_updates = ", ".join(f"{c} = excluded.{c}" for c in KOL_PROFILE_COLUMNS)
    async with get_db() as db:
        await db.execute(f"""
            INSERT INTO kol_profiles (account_id, user_id, profile_json, updated_at, {col_list})
            VALUES (?, ?, ?, datetime('now'), {col_placeholders})
            ON CONFLICT(account_id, user_id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at = datetime('now'),
                {col_updates}
        """, (account_id, user_id, json_str, *col_values))
        await db.commit()
    return True


async def get_kols_for_distribution() -> list[dict]:
    """KOLs whose affiliate link is confirmed (step >= link_confirmed) and who opted into distribution."""
    async with get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("""
            SELECT p.*
            FROM ai_followup_chats c
            JOIN kol_profiles p
              ON p.account_id = c.account_id AND p.user_id = c.user_id
            WHERE COALESCE(c.kol_step, 'prospect') IN ('link_confirmed', 'first_post', 'active')
               OR c.status = 'onboarded'
        """)
        rows = await cursor.fetchall()
    result = []
    for row in rows:
        profile = _merge_profile_row(row)
        if profile.get("distribution_enabled") is True:
            result.append({
                "account_id": row["account_id"],
                "user_id": row["user_id"],
                "profile": profile,
            })
    return result


async def is_kol_broadcast_sent(source_chat_id: int, source_message_id: int) -> bool:
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM kol_broadcast_log WHERE source_chat_id = ? AND source_message_id = ?",
            (source_chat_id, source_message_id),
        )
        return await cursor.fetchone() is not None


async def log_kol_broadcast(
    source_chat_id: int, source_message_id: int, sent_count: int,
    skipped_count: int, post_preview: str = ""
) -> None:
    """Persist channel post dedup + aggregate send result."""
    async with get_db() as conn:
        await conn.execute("""
            INSERT INTO kol_broadcast_log
                (source_chat_id, source_message_id, sent_count, skipped_count, post_preview)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_chat_id, source_message_id) DO NOTHING
        """, (source_chat_id, source_message_id, sent_count, skipped_count, post_preview))
        await conn.commit()


async def add_learned_knowledge(ai_agent_id: int, source_user_id: int, question: str, answer: str, status: str = "approved") -> int:
    if not question.strip() or not answer.strip():
        return 0
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO agent_learned_knowledge (ai_agent_id, source_user_id, question_pattern, learned_answer, status)
            VALUES (?, ?, ?, ?, ?)
        """, (ai_agent_id, source_user_id, question.strip(), answer.strip(), status))
        await db.commit()
        return cursor.lastrowid


async def get_learned_knowledge_for_agent(ai_agent_id: int, status: str = "approved",
                                          limit: int | None = None) -> list[dict]:
    """All learned rules for an agent (newest first). ``limit`` optional."""
    sql = "SELECT * FROM agent_learned_knowledge WHERE ai_agent_id = ? AND status = ? ORDER BY id DESC"
    params: tuple = (ai_agent_id, status)
    if limit:
        sql += " LIMIT ?"
        params = (ai_agent_id, status, limit)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


def _rule_tokens(text: str) -> set:
    """Lowercased word tokens (len>=3) for crude relevance matching (vi+en)."""
    import re as _re
    return {w for w in _re.findall(r"\w+", (text or "").lower()) if len(w) >= 3}


async def get_relevant_learned_knowledge(ai_agent_id: int, query_text: str,
                                         top_relevant: int = 20, top_recent: int = 10,
                                         status: str = "approved") -> list[dict]:
    """Pick learned rules worth injecting into the prompt.

    Replaces the old blind ``LIMIT 30`` (newest-only): with 800+ rules the
    agent was using only a 30-rule sliding window and 'forgot' everything
    older. Now: up to ``top_relevant`` rules scored by keyword overlap with
    the user's current message + up to ``top_recent`` newest rules (fresh
    policy), deduped by question_pattern.
    ponytail: token-overlap scoring; upgrade path = embeddings/FTS5 if quality lags.
    """
    all_rules = await get_learned_knowledge_for_agent(ai_agent_id, status=status)
    if not all_rules:
        return []

    # Dedupe repeated distillations of the same question — keep the newest.
    seen_q: set = set()
    deduped: list[dict] = []
    for r in all_rules:  # already newest-first
        key = (r.get("question_pattern") or "").strip().lower()
        if key and key not in seen_q:
            seen_q.add(key)
            deduped.append(r)

    recent = deduped[:top_recent]
    recent_ids = {r["id"] for r in recent}

    q_tokens = _rule_tokens(query_text)
    scored = []
    if q_tokens:
        for r in deduped:
            if r["id"] in recent_ids:
                continue
            overlap = len(q_tokens & _rule_tokens(
                (r.get("question_pattern") or "") + " " + (r.get("learned_answer") or "")))
            if overlap >= 2:  # 1 shared common word is noise
                scored.append((overlap, r))
        scored.sort(key=lambda t: (-t[0], -t[1]["id"]))

    return recent + [r for _, r in scored[:top_relevant]]


def _sanitize_fts_query(query: str) -> str:
    """Sanitize a query string for FTS5 MATCH — escape special characters.

    FTS5 special chars: " * ( ) : ^ ~ AND OR NOT NEAR
    We quote each token to treat them as literals.
    """
    import re as _re
    if not query or not query.strip():
        return ""
    # Split into tokens, quote each to escape special chars
    tokens = _re.findall(r'\w+', query, _re.UNICODE)
    tokens = [t for t in tokens if len(t) >= 2]
    if not tokens:
        return ""
    # Join with spaces — FTS5 implicit AND
    return " ".join(f'"{t}"' for t in tokens[:20])  # limit to 20 terms


async def get_relevant_learned_knowledge_v2(
    ai_agent_id: int,
    query_text: str,
    limit: int = 20,
    top_recent: int = 5,
    status: str = "approved",
) -> list[dict]:
    """FTS5-based knowledge retrieval with BM25 ranking.

    Upgrade from token-overlap (v1) to full-text search. Falls back to v1
    if FTS5 table is not available (graceful degradation).

    Returns: list of dicts with keys: id, question_pattern, learned_answer, created_at.
    """
    fts_results: list[dict] = []

    if query_text and query_text.strip():
        safe_query = _sanitize_fts_query(query_text)
        if safe_query:
            try:
                async with get_db() as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute("""
                        SELECT k.id, k.question_pattern, k.learned_answer, k.created_at,
                               bm25(agent_learned_knowledge_fts) AS rank
                        FROM agent_learned_knowledge_fts fts
                        JOIN agent_learned_knowledge k ON k.id = fts.rowid
                        WHERE agent_learned_knowledge_fts MATCH ?
                          AND k.ai_agent_id = ?
                          AND k.status = ?
                        ORDER BY rank
                        LIMIT ?
                    """, (safe_query, ai_agent_id, status, limit))
                    fts_results = [dict(r) for r in await cursor.fetchall()]
            except Exception as _fts_err:
                logger.warning("[KnowledgeFTS] FTS5 query failed (%s), falling back to v1", _fts_err)
                return await get_relevant_learned_knowledge(ai_agent_id, query_text)

    # Always include recent rules as fallback
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, question_pattern, learned_answer, created_at
            FROM agent_learned_knowledge
            WHERE ai_agent_id = ? AND status = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (ai_agent_id, status, top_recent))
        recent = [dict(r) for r in await cursor.fetchall()]

    # Merge and deduplicate by id
    seen_ids: set[int] = set()
    results: list[dict] = []
    for row in fts_results + recent:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            results.append(row)

    return results[:limit]


async def delete_learned_knowledge(rule_id: int) -> bool:
    async with get_db() as db:
        await db.execute("DELETE FROM agent_learned_knowledge WHERE id = ?", (rule_id,))
        await db.commit()
        return True


# ═══════════════════════════════════════════════════════════════════════════
# Telegram Forum Sub-Topics (Sales War Room)
# ═══════════════════════════════════════════════════════════════════════════

async def get_telegram_outreach_proof(account_id: int, user_id: int) -> dict | None:
    """Prove the lead was actually reachable by this nick before opening a topic.

    Order matters: most authoritative evidence first. Campaign logs record the
    *target* id, which for scrape jobs is the group/channel id and NOT the human
    user id — so a campaign "success" row alone is NOT proof. Requiring it made
    the topic gate almost never match, silently dropping real conversations.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Only trust campaign/watcher rows whose target is a real user id (> 0).
        c_cur = await db.execute(
            """SELECT 'campaign' AS source, c.name, l.sent_at
               FROM dm_campaign_logs l
               LEFT JOIN dm_campaigns c ON c.id = l.campaign_id
               WHERE l.account_id = ? AND l.target_user_id = ? AND l.status = 'success'
                 AND l.target_user_id > 0
               ORDER BY l.sent_at DESC LIMIT 1""",
            (account_id, user_id),
        )
        row = await c_cur.fetchone()
        if row:
            return dict(row)

        w_cur = await db.execute(
            """SELECT 'watcher' AS source, group_title AS name, sent_at
               FROM watcher_dm_logs
               WHERE account_id = ? AND target_user_id = ? AND status = 'success'
                 AND target_user_id > 0
               ORDER BY sent_at DESC LIMIT 1""",
            (account_id, user_id),
        )
        row2 = await w_cur.fetchone()
        if row2:
            return dict(row2)

        # The lead is in the AI follow-up pipeline → we definitely contacted them.
        f_cur = await db.execute(
            """SELECT 'followup' AS source, name, updated_at AS sent_at
               FROM ai_followup_chats
               WHERE account_id = ? AND user_id = ?
               ORDER BY updated_at DESC LIMIT 1""",
            (account_id, user_id),
        )
        row3 = await f_cur.fetchone()
        if row3:
            return dict(row3)

        # Any inbound reply recorded for this (nick, lead) pair.
        r_cur = await db.execute(
            """SELECT 'reply' AS source, sender_name AS name, received_at AS sent_at
               FROM dm_replies
               WHERE account_id = ? AND sender_user_id = ?
               ORDER BY received_at DESC LIMIT 1""",
            (account_id, user_id),
        )
        row4 = await r_cur.fetchone()
        return dict(row4) if row4 else None


async def get_forum_backfill_candidates(days: int = 90, limit: int = 500) -> list:
    """Replied conversations from the last `days` that still have no topic.

    Skips status='bot_ignored' — Telegram's SpamBot, the operator's own helper
    bots (weexwillbot) and their main account all land here, and creating a
    topic for each of those would spam the group with non-lead noise.

    Default 90 days: the user wants EVERY conversation the lead replied to to
    show up as a topic, and 90d covers the full history (365 chats total, 204
    with a real lead reply). A shorter window silently drops older ones.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT c.account_id, c.user_id, c.username, c.name,
                      c.last_user_message_at, c.updated_at
               FROM ai_followup_chats c
               WHERE c.reply_count > 0
                 AND COALESCE(c.status, '') != 'bot_ignored'
                 AND COALESCE(c.last_user_message_at, c.updated_at) >= datetime('now', ?)
                 AND NOT EXISTS (
                     SELECT 1 FROM telegram_topics t
                     WHERE t.account_id = c.account_id AND t.user_id = c.user_id
                 )
               ORDER BY COALESCE(c.last_user_message_at, c.updated_at) DESC
               LIMIT ?""",
            (f"-{int(days)} days", int(limit)),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def find_telegram_topic_for_user(group_id: int, account_id: int, user_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM telegram_topics WHERE group_id = ? AND account_id = ? AND user_id = ?",
            (group_id, account_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_telegram_topics(group_id: int | None = None, limit: int = 200) -> list:
    """List topic mappings, newest activity first — used by the UI diagnostics view."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if group_id is None:
            cursor = await db.execute(
                "SELECT * FROM telegram_topics ORDER BY updated_at DESC LIMIT ?", (int(limit),))
        else:
            cursor = await db.execute(
                "SELECT * FROM telegram_topics WHERE group_id = ? ORDER BY updated_at DESC LIMIT ?",
                (group_id, int(limit)))
        return [dict(r) for r in await cursor.fetchall()]


async def get_telegram_topic_by_thread(group_id: int, topic_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM telegram_topics WHERE group_id = ? AND topic_id = ?",
            (group_id, topic_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def upsert_telegram_topic(data: dict) -> int:
    """Insert or update a topic mapping. Returns row id."""
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO telegram_topics (group_id, topic_id, account_id, user_id, username, name, campaign_name, status, last_lead_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, account_id, user_id)
            DO UPDATE SET topic_id = excluded.topic_id,
                          username = excluded.username,
                          name     = excluded.name,
                          campaign_name = COALESCE(excluded.campaign_name, campaign_name),
                          status   = excluded.status,
                          last_lead_message = COALESCE(excluded.last_lead_message, last_lead_message),
                          updated_at = datetime('now')
        """, (
            data["group_id"], data["topic_id"], data["account_id"], data["user_id"],
            data.get("username"), data.get("name"), data.get("campaign_name"),
            data.get("status", "active"), data.get("last_lead_message"),
        ))
        await db.commit()
        return cursor.lastrowid


async def update_telegram_topic_status(group_id: int, topic_id: int, status: str):
    async with get_db() as db:
        await db.execute(
            "UPDATE telegram_topics SET status = ?, updated_at = datetime('now') WHERE group_id = ? AND topic_id = ?",
            (status, group_id, topic_id),
        )
        await db.commit()


async def update_telegram_topic_last_message(group_id: int, topic_id: int, message: str):
    async with get_db() as db:
        await db.execute(
            "UPDATE telegram_topics SET last_lead_message = ?, updated_at = datetime('now') WHERE group_id = ? AND topic_id = ?",
            (message, group_id, topic_id),
        )
        await db.commit()


# ── Outreach Nudges (follow-up lần 2) ─────────────────────────────────────

async def get_nudge_candidates(delay_hours: int = 24, max_age_hours: int = 120, limit: int = 20) -> list[dict]:
    """Find DM-outreach leads who never replied and haven't been nudged yet.

    Combines watcher_dm_logs + dm_campaign_logs as the source of first-contact.
    Filters: status='success', silence >= delay_hours, age <= max_age_hours,
    NOT EXISTS in dm_replies (no inbound), NOT EXISTS in nudges (not yet nudged).
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Parameterized delay strings for SQLite datetime arithmetic
        delay_str = f"-{int(delay_hours)} hours"
        age_str = f"-{int(max_age_hours)} hours"

        cursor = await db.execute("""
            SELECT 'watcher' AS channel, w.watcher_id, NULL AS campaign_id,
                   w.account_id, w.target_user_id, w.target_username, w.sent_at
            FROM watcher_dm_logs w
            WHERE w.status = 'success'
              AND w.sent_at <= datetime('now', ?)
              AND w.sent_at >= datetime('now', ?)
              AND NOT EXISTS (
                    SELECT 1 FROM dm_replies r
                    WHERE r.sender_user_id = w.target_user_id
                      AND r.received_at >= w.sent_at
              )
              AND NOT EXISTS (
                    SELECT 1 FROM nudges n
                    WHERE n.account_id = w.account_id
                      AND n.target_user_id = w.target_user_id
              )
            UNION ALL
            SELECT 'campaign' AS channel, NULL AS watcher_id, c.campaign_id,
                   c.account_id, c.target_user_id, c.target_username, c.sent_at
            FROM dm_campaign_logs c
            WHERE c.status = 'success'
              AND c.sent_at <= datetime('now', ?)
              AND c.sent_at >= datetime('now', ?)
              AND NOT EXISTS (
                    SELECT 1 FROM dm_replies r
                    WHERE r.sender_user_id = c.target_user_id
                      AND r.received_at >= c.sent_at
              )
              AND NOT EXISTS (
                    SELECT 1 FROM nudges n
                    WHERE n.account_id = c.account_id
                      AND n.target_user_id = c.target_user_id
              )
            ORDER BY sent_at ASC
            LIMIT ?
        """, (delay_str, age_str, delay_str, age_str, limit))
        return [dict(r) for r in await cursor.fetchall()]


async def has_inbound_since(account_id: int, target_user_id: int, since_iso: str) -> bool:
    """Check whether target_user_id sent us any DM reply since `since_iso`.

    Used as the TOCTOU re-check inside the send lock to avoid nudging
    a lead who just replied between candidate-scan and actual send.
    """
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT 1 FROM dm_replies WHERE sender_user_id = ? AND received_at >= ? LIMIT 1",
            (target_user_id, since_iso),
        )).fetchone()
        return row is not None


async def record_nudge(
    account_id: int,
    target_user_id: int,
    channel: str,
    status: str,
    watcher_id: int | None = None,
    campaign_id: int | None = None,
    error_message: str | None = None,
) -> int | None:
    """Record a nudge attempt. UNIQUE(account_id, target_user_id) prevents dupes.

    Returns the row id on success, None if a duplicate was silently skipped.
    """
    async with get_db() as db:
        try:
            cursor = await db.execute(
                """INSERT INTO nudges
                   (account_id, target_user_id, channel, status, watcher_id, campaign_id, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (account_id, target_user_id, channel, status, watcher_id, campaign_id, error_message),
            )
            await db.commit()
            return cursor.lastrowid
        except Exception:
            # UNIQUE constraint violation → already nudged
            return None


async def get_nudge_stats() -> dict:
    """Count nudges by status for the stats endpoint."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) as cnt FROM nudges GROUP BY status"
        )
        rows = await cursor.fetchall()
        return {r[0]: r[1] for r in rows}


async def get_nudge_for_user(account_id: int, target_user_id: int) -> dict | None:
    """Get nudge record for a specific user (for UI badge)."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM nudges WHERE account_id = ? AND target_user_id = ? LIMIT 1",
            (account_id, target_user_id),
        )).fetchone()
        return dict(row) if row else None


# ══════════════════════════════════════════════════════════════════════════════
# ══ Engagement Campaign CRUD ═════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════


async def create_engagement_campaign(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO engagement_campaigns
               (name, group_ids, group_titles, observer_account_ids, reply_account_ids,
                ai_agent_id, knowledge_extra, reply_mode, trigger_keywords,
                max_replies_per_hour_per_group, max_replies_per_day_per_group,
                min_gap_seconds, participation_min, participation_max,
                schedule_start, schedule_end, active_days, schedule_jitter_min,
                signal_group_link, signal_group_title, status, platform,
                operator_consent_confirmed, operator_consent_at, operator_consent_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["name"],
                json.dumps(data.get("group_ids", [])),
                json.dumps(data.get("group_titles", [])),
                json.dumps(data.get("observer_account_ids", [])),
                json.dumps(data.get("reply_account_ids", [])),
                data.get("ai_agent_id"),
                data.get("knowledge_extra", ""),
                data.get("reply_mode", "reactive"),
                json.dumps(data.get("trigger_keywords", [])),
                data.get("max_replies_per_hour_per_group", 4),
                data.get("max_replies_per_day_per_group", 12),
                data.get("min_gap_seconds", 180),
                data.get("participation_min", 1),
                data.get("participation_max", 1),
                data.get("schedule_start", "13:00"),
                data.get("schedule_end", "23:00"),
                json.dumps(data.get("active_days", [1, 2, 3, 4, 5, 6, 7])),
                data.get("schedule_jitter_min", 0),
                data.get("signal_group_link", ""),
                data.get("signal_group_title", ""),
                data.get("status", "draft"),
                data.get("platform", "telegram"),
                data.get("operator_consent_confirmed", 0),
                data.get("operator_consent_at"),
                data.get("operator_consent_by", ""),
            ),
        )
        await db.commit()
        return cursor.lastrowid


def _parse_engagement_campaign(row: dict) -> dict:
    """Parse JSON fields in an engagement campaign row."""
    if not row:
        return row
    for field in ("group_ids", "group_titles", "observer_account_ids",
                  "reply_account_ids", "trigger_keywords", "active_days"):
        val = row.get(field)
        if isinstance(val, str):
            try:
                row[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                row[field] = []
    return row


async def get_all_engagement_campaigns() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM engagement_campaigns ORDER BY created_at DESC"
        )
        return [_parse_engagement_campaign(dict(row)) for row in await cursor.fetchall()]


async def get_engagement_campaign(campaign_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM engagement_campaigns WHERE id=?", (campaign_id,)
        )
        row = await cursor.fetchone()
        return _parse_engagement_campaign(dict(row)) if row else None


async def get_active_engagement_campaigns() -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM engagement_campaigns WHERE status='active' ORDER BY id"
        )
        return [_parse_engagement_campaign(dict(row)) for row in await cursor.fetchall()]


async def update_engagement_campaign(campaign_id: int, data: dict) -> bool:
    fields = []
    values = []
    for key in (
        "name", "ai_agent_id", "knowledge_extra", "reply_mode",
        "max_replies_per_hour_per_group", "max_replies_per_day_per_group",
        "min_gap_seconds", "participation_min", "participation_max",
        "schedule_start", "schedule_end", "schedule_jitter_min",
        "signal_group_link", "signal_group_title", "status", "pause_reason",
        "operator_consent_confirmed", "operator_consent_at", "operator_consent_by",
    ):
        if key in data:
            fields.append(f"{key}=?")
            values.append(data[key])
    # JSON fields
    for key in ("group_ids", "group_titles", "observer_account_ids",
                "reply_account_ids", "trigger_keywords", "active_days"):
        if key in data:
            fields.append(f"{key}=?")
            values.append(json.dumps(data[key]) if isinstance(data[key], (list, dict)) else data[key])

    if not fields:
        return False

    fields.append("updated_at=datetime('now')")
    values.append(campaign_id)

    async with get_db() as db:
        cursor = await db.execute(
            f"UPDATE engagement_campaigns SET {', '.join(fields)} WHERE id=?",
            tuple(values),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_engagement_campaign(campaign_id: int) -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM engagement_campaigns WHERE id=?", (campaign_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def toggle_engagement_campaign(campaign_id: int, active: bool) -> bool:
    """Toggle campaign status between 'active' and 'paused'.
    Cannot activate if operator_consent_confirmed is 0."""
    async with get_db() as db:
        if active:
            # Check consent gate
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT operator_consent_confirmed FROM engagement_campaigns WHERE id=?",
                (campaign_id,)
            )).fetchone()
            if not row:
                return False
            if not row["operator_consent_confirmed"]:
                return False  # consent required
            cursor = await db.execute(
                "UPDATE engagement_campaigns SET status='active', pause_reason='', updated_at=datetime('now') WHERE id=?",
                (campaign_id,),
            )
        else:
            cursor = await db.execute(
                "UPDATE engagement_campaigns SET status='paused', updated_at=datetime('now') WHERE id=?",
                (campaign_id,),
            )
        await db.commit()
        return cursor.rowcount > 0


# ── Engagement Logs ──

async def create_engagement_log(data: dict) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO engagement_logs
               (campaign_id, group_id, group_title, account_id, reply_account_ids,
                trigger_user_id, trigger_msg_id, trigger_text, reply_text,
                reply_msg_id, kind, status, skip_reason, latency_ms)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data.get("campaign_id"),
                data.get("group_id"),
                data.get("group_title"),
                data.get("account_id"),
                json.dumps(data.get("reply_account_ids", [])),
                data.get("trigger_user_id"),
                data.get("trigger_msg_id"),
                data.get("trigger_text"),
                data.get("reply_text"),
                data.get("reply_msg_id"),
                data.get("kind", "reply"),
                data.get("status", "success"),
                data.get("skip_reason"),
                data.get("latency_ms"),
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_engagement_logs(
    campaign_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    status: str | None = None,
    kind: str | None = None,
) -> list:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        conditions = []
        params = []
        if campaign_id is not None:
            conditions.append("campaign_id=?")
            params.append(campaign_id)
        if status:
            conditions.append("status=?")
            params.append(status)
        if kind:
            conditions.append("kind=?")
            params.append(kind)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        cursor = await db.execute(
            f"SELECT * FROM engagement_logs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_engagement_log_count(
    campaign_id: int | None = None,
    status: str | None = None,
    kind: str | None = None,
) -> int:
    async with get_db() as db:
        conditions = []
        params = []
        if campaign_id is not None:
            conditions.append("campaign_id=?")
            params.append(campaign_id)
        if status:
            conditions.append("status=?")
            params.append(status)
        if kind:
            conditions.append("kind=?")
            params.append(kind)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        row = await (await db.execute(
            f"SELECT COUNT(*) FROM engagement_logs {where}", tuple(params)
        )).fetchone()
        return row[0] if row else 0


# ── Engagement DM Map ──

async def create_engagement_dm_map(campaign_id: int, account_id: int, user_id: int) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO engagement_dm_map (campaign_id, account_id, user_id) VALUES (?,?,?)",
            (campaign_id, account_id, user_id),
        )
        await db.commit()
        return cursor.lastrowid


async def find_engagement_dm_map(account_id: int, user_id: int, days: int = 7) -> dict | None:
    """Find engagement attribution for a user who DM'd us within N days."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM engagement_dm_map "
            "WHERE account_id=? AND user_id=? AND created_at >= datetime('now', '-' || ? || ' days') "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, user_id, str(days)),
        )).fetchone()
        return dict(row) if row else None


# ── Engagement Stats ──

async def get_engagement_stats(campaign_id: int | None = None, days: int = 7) -> dict:
    """Aggregate engagement statistics in two parameterized passes."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        span = f"-{int(days)} days"
        camp_sql = "AND campaign_id=?" if campaign_id is not None else ""
        camp_params = (campaign_id,) if campaign_id is not None else ()

        cur = await db.execute(f"""
            SELECT
                COALESCE(SUM(CASE WHEN created_at >= datetime('now', ?) THEN 1 ELSE 0 END), 0) AS total,
                COALESCE(SUM(CASE WHEN created_at >= datetime('now', ?) AND status='success' THEN 1 ELSE 0 END), 0) AS success,
                COALESCE(SUM(CASE WHEN created_at >= datetime('now', ?) AND status='skipped' THEN 1 ELSE 0 END), 0) AS skipped,
                COALESCE(SUM(CASE WHEN created_at >= datetime('now', ?) AND status='failed'  THEN 1 ELSE 0 END), 0) AS failed,
                COALESCE(SUM(CASE WHEN created_at >= datetime('now', '+7 hours', 'start of day', '-7 hours')
                    AND kind='reply' AND status='success' THEN 1 ELSE 0 END), 0) AS today_replies,
                COALESCE(SUM(CASE WHEN created_at >= datetime('now', '-1 hour')
                    AND kind='reply' AND status='success' THEN 1 ELSE 0 END), 0) AS hour_replies,
                COUNT(DISTINCT CASE WHEN skip_reason='muted'
                    AND created_at >= datetime('now', '-1 day') THEN account_id END) AS muted_24h
            FROM engagement_logs
            WHERE 1=1 {camp_sql}
              AND created_at >= MIN(datetime('now', ?), datetime('now', '-1 day'))
        """, (span, span, span, span, *camp_params, span))
        agg = dict(await cur.fetchone())

        cursor = await db.execute(
            f"SELECT skip_reason, COUNT(*) as cnt FROM engagement_logs "
            f"WHERE status='skipped' AND created_at >= datetime('now', ?) {camp_sql} GROUP BY skip_reason",
            (span, *camp_params),
        )
        skip_reasons = {r["skip_reason"]: r["cnt"] for r in await cursor.fetchall() if r["skip_reason"]}

        return {
            "total": agg["total"],
            "success": agg["success"],
            "skipped": agg["skipped"],
            "failed": agg["failed"],
            "today_replies": agg["today_replies"],
            "hour_replies": agg["hour_replies"],
            "skip_reasons": skip_reasons,
            "muted_accounts_24h": agg["muted_24h"],
            "days": days,
        }


# ── Engagement Claims Cleanup ──

async def cleanup_engagement_claims(max_age_days: int = 2) -> int:
    """Remove old claim records and expired counter rows."""
    async with get_db() as db:
        c1 = await db.execute(
            "DELETE FROM engagement_claims WHERE created_at < datetime('now', '-' || ? || ' days')",
            (str(max_age_days),),
        )
        c2 = await db.execute(
            "DELETE FROM engagement_window_counters WHERE updated_at < datetime('now', '-' || ? || ' days')",
            (str(max_age_days),),
        )
        total = (c1.rowcount or 0) + (c2.rowcount or 0)
        await db.commit()
        return total

# ── C5: Referral / Viral Loop CRUD ─────────────────────────────────────────

import secrets as _secrets
import string as _string


def generate_referral_code() -> str:
    """Generate unique 8-char alphanumeric referral code."""
    chars = _string.ascii_uppercase + _string.digits
    return "REF-" + "".join(_secrets.choice(chars) for _ in range(8))





async def create_referral_code(
    account_id: int, referrer_user_id: int, code: str | None = None,
    max_uses: int = 10, expires_at: str | None = None,
) -> dict:
    if not code:
        code = generate_referral_code()
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO referral_codes (account_id, referrer_user_id, code, max_uses, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (account_id, referrer_user_id, code, max_uses, expires_at))
        await db.commit()
        row_id = cursor.lastrowid
    return {"id": row_id, "code": code}


async def get_referral_code(code: str) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM referral_codes WHERE code = ?", (code,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_referral_code_by_user(referrer_user_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM referral_codes WHERE referrer_user_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
            (referrer_user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_referral_codes() -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM referral_codes ORDER BY created_at DESC")
        return [dict(r) for r in await cursor.fetchall()]


async def increment_referral_uses(code_id: int):
    async with get_db() as db:
        await db.execute("UPDATE referral_codes SET uses_count = uses_count + 1 WHERE id = ?", (code_id,))
        await db.commit()


async def deactivate_referral_code(code_id: int):
    async with get_db() as db:
        await db.execute("UPDATE referral_codes SET is_active = 0 WHERE id = ?", (code_id,))
        await db.commit()


async def create_referral_attribution(
    referral_code_id: int, referrer_user_id: int,
    referred_user_id: int, referred_username: str | None = None,
) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT OR IGNORE INTO referral_attributions
            (referral_code_id, referrer_user_id, referred_user_id, referred_username)
            VALUES (?, ?, ?, ?)
        """, (referral_code_id, referrer_user_id, referred_user_id, referred_username))
        await db.commit()
        return cursor.lastrowid or 0


async def get_attribution_by_referred(referred_user_id: int) -> dict | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM referral_attributions WHERE referred_user_id = ? ORDER BY attributed_at DESC LIMIT 1",
            (referred_user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_attributions_by_referrer(referrer_user_id: int) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM referral_attributions WHERE referrer_user_id = ? ORDER BY attributed_at DESC",
            (referrer_user_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]


async def get_all_referral_attributions() -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM referral_attributions ORDER BY attributed_at DESC"
        )
        return [dict(r) for r in await cursor.fetchall()]


async def update_attribution_status(attribution_id: int, status: str, converted_at: str | None = None):
    async with get_db() as db:
        if converted_at:
            await db.execute(
                "UPDATE referral_attributions SET status = ?, converted_at = ? WHERE id = ?",
                (status, converted_at, attribution_id)
            )
        else:
            await db.execute(
                "UPDATE referral_attributions SET status = ? WHERE id = ?",
                (status, attribution_id)
            )
        await db.commit()


async def create_referral_reward(
    referrer_user_id: int, attribution_id: int,
    reward_type: str, reward_detail: str | None = None,
) -> int:
    async with get_db() as db:
        cursor = await db.execute("""
            INSERT INTO referral_rewards (referrer_user_id, attribution_id, reward_type, reward_detail)
            VALUES (?, ?, ?, ?)
        """, (referrer_user_id, attribution_id, reward_type, reward_detail))
        await db.commit()
        return cursor.lastrowid


async def get_referral_stats() -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("""
            SELECT
                (SELECT COUNT(*) FROM referral_codes WHERE is_active = 1) AS active_codes,
                COUNT(*) AS total_attributions,
                COALESCE(SUM(CASE WHEN status = 'signed' THEN 1 ELSE 0 END), 0) AS conversions
            FROM referral_attributions
        """)).fetchone()
        active_codes = row["active_codes"] if row else 0
        total_attr = row["total_attributions"] if row else 0
        conversions = row["conversions"] if row else 0
        return {
            "active_codes": active_codes,
            "total_attributions": total_attr,
            "conversions": conversions,
            "conversion_rate": round((conversions / total_attr * 100) if total_attr > 0 else 0, 1),
        }


# ── C3: Online/Offline Cycle — Active Hours Utilities ──────────────────────

import zoneinfo
from datetime import datetime as _dt


def is_account_in_active_hours(
    start: str = "08:00",
    end: str = "22:00",
    tz_name: str = "Asia/Ho_Chi_Minh",
) -> bool:
    """Check if the current time falls within the account's active hours.

    Supports overnight windows (e.g. 22:00-06:00 for night shift).
    Returns True if timezone is invalid (safe default — never blocks).
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except (KeyError, Exception):
        return True  # invalid tz → safe fallback, allow activity

    now = _dt.now(tz)
    current_time = now.strftime("%H:%M")

    if start <= end:
        # Normal range: e.g. 08:00 - 22:00
        return start <= current_time < end
    else:
        # Overnight range: e.g. 22:00 - 06:00
        return current_time >= start or current_time < end


def check_account_active_hours(account_data: dict | None) -> bool:
    """Convenience wrapper: check active hours from an account dict.

    Returns True if account_data is None or missing fields (safe default).
    """
    if not account_data:
        return True
    return is_account_in_active_hours(
        start=account_data.get("active_hours_start", "08:00"),
        end=account_data.get("active_hours_end", "22:00"),
        tz_name=account_data.get("active_timezone", "Asia/Ho_Chi_Minh"),
    )


# ── C6: Warmup Progress CRUD ───────────────────────────────────

async def get_warmup_progress(account_id: int) -> dict | None:
    """Get warmup progress for an account."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM warmup_progress WHERE account_id = ?",
            (account_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def create_or_update_warmup_progress(account_id: int, **kwargs):
    """Create or update warmup progress record."""
    async with get_db() as db:
        existing = await get_warmup_progress(account_id)
        if existing:
            sets = []
            vals = []
            for k, v in kwargs.items():
                sets.append(f"{k} = ?")
                vals.append(v)
            sets.append("updated_at = datetime('now')")
            vals.append(account_id)
            await db.execute(
                f"UPDATE warmup_progress SET {', '.join(sets)} WHERE account_id = ?",
                tuple(vals)
            )
            await db.commit()
        else:
            cols = ["account_id"] + list(kwargs.keys())
            vals = [account_id] + list(kwargs.values())
            placeholders = ", ".join(["?"] * len(vals))
            await db.execute(
                f"INSERT INTO warmup_progress ({', '.join(cols)}) VALUES ({placeholders})",
                tuple(vals)
            )
            await db.commit()


async def increment_warmup_counter(account_id: int, counter_name: str, amount: int = 1):
    """Increment a specific counter in warmup_progress."""
    allowed = {"messages_sent", "reactions_sent", "channels_joined",
               "history_reads", "forwards_sent", "profiles_viewed"}
    if counter_name not in allowed:
        raise ValueError(f"Invalid counter: {counter_name}")
    async with get_db() as db:
        await db.execute(
            f"UPDATE warmup_progress SET {counter_name} = {counter_name} + ?, updated_at = datetime('now') WHERE account_id = ?",
            (amount, account_id)
        )
        await db.commit()


async def add_warmup_action_log(account_id: int, action_type: str,
                                 target_chat_id: int = None, target_message_id: int = None,
                                 status: str = "success", error_detail: str = None):
    """Log a warmup action."""
    async with get_db() as db:
        await db.execute(
            """INSERT INTO warmup_action_log
               (account_id, action_type, target_chat_id, target_message_id, status, error_detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, action_type, target_chat_id, target_message_id, status, error_detail)
        )
        await db.commit()


async def get_warmup_stats(account_id: int) -> dict:
    """Get aggregate warmup stats for an account."""
    progress = await get_warmup_progress(account_id)
    if not progress:
        return {"stage": 0, "messages_sent": 0, "reactions_sent": 0,
                "channels_joined": 0, "history_reads": 0, "forwards_sent": 0,
                "profiles_viewed": 0, "graduated": False}
    return {
        "stage": progress.get("stage", 1),
        "messages_sent": progress.get("messages_sent", 0),
        "reactions_sent": progress.get("reactions_sent", 0),
        "channels_joined": progress.get("channels_joined", 0),
        "history_reads": progress.get("history_reads", 0),
        "forwards_sent": progress.get("forwards_sent", 0),
        "profiles_viewed": progress.get("profiles_viewed", 0),
        "graduated": progress.get("graduated_at") is not None,
        "started_at": progress.get("started_at"),
        "graduated_at": progress.get("graduated_at"),
        "last_flood_at": progress.get("last_flood_at"),
    }


async def get_recent_warmup_floods(account_id: int, days: int = 3) -> int:
    """Count FloodWait events in last N days."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT COUNT(*) as cnt FROM warmup_action_log
               WHERE account_id = ? AND status = 'flood'
               AND created_at >= datetime('now', ?)""",
            (account_id, f"-{days} days")
        )
        row = await cursor.fetchone()
        return dict(row)["cnt"] if row else 0


# ── KOL offer ladder history (spec §A1) ──────────────────────────────────────

async def add_offer_ladder_history(version: int, ladder_json: str, actor: str = "admin") -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO kol_offer_ladder_history (version, ladder_json, actor) VALUES (?, ?, ?)",
            (int(version), ladder_json, actor),
        )
        await db.commit()
        return cursor.lastrowid


async def get_offer_ladder_history(limit: int = 20) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, version, ladder_json, actor, created_at FROM kol_offer_ladder_history ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    for r in rows:
        try:
            r["ladder"] = json.loads(r.pop("ladder_json") or "{}")
        except Exception:
            r["ladder"] = {}
    return rows


# ── KOL 5-step funnel (spec §B) ──────────────────────────────────────────────
#
# kol_step is the funnel's single source of truth. The legacy `stage`
# (PIPELINE_STAGES) is kept only as the AI on/off switch for /aion /aioff.

KOL_STEPS = ("prospect", "deal_agreed", "uid_received", "link_confirmed", "first_post", "active")
KOL_STEP_TERMINAL = ("declined", "dormant")
KOL_STEP_ALL = KOL_STEPS + KOL_STEP_TERMINAL

KOL_STEP_LABELS = {
    "prospect": "Chưa chốt",
    "deal_agreed": "Đồng ý deal",
    "uid_received": "Nhận UID",
    "link_confirmed": "Đã gắn link",
    "first_post": "Bài đầu tiên",
    "active": "Hoạt động",
    "declined": "Từ chối",
    "dormant": "Ngủ đông",
}
KOL_STEP_EMOJI = {
    "prospect": "⚪", "deal_agreed": "🤝", "uid_received": "🆔", "link_confirmed": "🔗",
    "first_post": "📝", "active": "🟢", "declined": "⛔", "dormant": "💤",
}

# What is still needed at each step, and who has to supply it.
#   owner 'kol' → the AI may remind the KOL about it (step reminders)
#   owner 'ops' → an operator action in the web/Forum (never nagged to the KOL)
MISSING_ITEM_META = {
    "deal_agreement": {"owner": "kol", "label": "KOL đồng ý thang offer",
                       "ask": "whether the KOL is happy to go ahead with the offer"},
    "weex_uid": {"owner": "kol", "label": "WEEX UID",
                 "ask": "their WEEX UID so commission can be bound to their account"},
    "vip_code": {"owner": "ops", "label": "vipCode (admin gắn)", "ask": ""},
    "affiliate_link": {"owner": "ops", "label": "Link affiliate (admin gắn)", "ask": ""},
    "link_confirmation": {"owner": "ops", "label": "Admin xác nhận đã gắn link", "ask": ""},
    "selected_campaign": {"owner": "kol", "label": "Chọn campaign",
                          "ask": "which currently running campaign they want to start with"},
    "first_post_link": {"owner": "kol", "label": "Link bài đăng đầu tiên",
                        "ask": "the link to their first post / video so we can track it"},
    "volume_verified": {"owner": "ops", "label": "Volume hợp lệ kỳ đầu (admin nhập)", "ask": ""},
}


def normalize_kol_step(step) -> str | None:
    """Accept a step name or 0-5 index; return canonical name or None."""
    if step is None:
        return None
    if isinstance(step, bool):
        return None
    if isinstance(step, int):
        return KOL_STEPS[step] if 0 <= step < len(KOL_STEPS) else None
    s = str(step).strip().lower().replace("-", "_").replace(" ", "_")
    if s.isdigit():
        i = int(s)
        return KOL_STEPS[i] if 0 <= i < len(KOL_STEPS) else None
    return s if s in KOL_STEP_ALL else None


def kol_step_rank(step) -> int:
    """0..5 for funnel steps, -1 for terminal/unknown (sorting + 'advance only' checks)."""
    name = normalize_kol_step(step) or "prospect"
    return KOL_STEPS.index(name) if name in KOL_STEPS else -1


def kol_step_label(step) -> str:
    name = normalize_kol_step(step) or "prospect"
    return f"{KOL_STEP_EMOJI.get(name, '')} {KOL_STEP_LABELS.get(name, name)}".strip()


def compute_missing_items(step, profile: dict | None) -> list[str]:
    """Items still needed to leave `step`, given what the profile already holds."""
    profile = profile or {}
    name = normalize_kol_step(step) or "prospect"

    def has(key: str) -> bool:
        v = profile.get(key)
        return v is not None and str(v).strip() != ""

    if name == "prospect":
        return ["deal_agreement"]
    if name == "deal_agreed":
        return [] if has("weex_uid") else ["weex_uid"]
    if name == "uid_received":
        missing = [k for k in ("vip_code", "affiliate_link") if not has(k)]
        return missing or ["link_confirmation"]
    if name == "link_confirmed":
        missing = []
        if not has("selected_campaign"):
            missing.append("selected_campaign")
        if not has("first_post_link"):
            missing.append("first_post_link")
        return missing
    if name == "first_post":
        return [] if has("volume_verified") else ["volume_verified"]
    return []


def kol_missing_items_for_kol(missing: list[str]) -> list[str]:
    """Subset of missing items the KOL (not ops) has to provide."""
    return [m for m in missing if MISSING_ITEM_META.get(m, {}).get("owner") == "kol"]


async def get_kol_step(account_id: int, user_id: int) -> str:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT kol_step FROM ai_followup_chats WHERE account_id = ? AND user_id = ?",
            (account_id, user_id),
        )
        row = await cursor.fetchone()
    return normalize_kol_step(row[0]) if row and row[0] else "prospect"


async def refresh_missing_items(account_id: int, user_id: int, step: str | None = None) -> list[str]:
    """Recompute missing_items from the current step + profile and persist it on the profile."""
    if step is None:
        step = await get_kol_step(account_id, user_id)
    profile = await get_kol_profile(account_id, user_id)
    missing = compute_missing_items(step, profile)
    async with get_db() as db:
        await db.execute("""
            INSERT INTO kol_profiles (account_id, user_id, profile_json, missing_items, updated_at)
            VALUES (?, ?, '{}', ?, datetime('now'))
            ON CONFLICT(account_id, user_id) DO UPDATE SET
                missing_items = excluded.missing_items
        """, (account_id, user_id, json.dumps(missing, ensure_ascii=False)))
        await db.commit()
    return missing


async def set_kol_step(
    account_id: int,
    user_id: int,
    new_step,
    actor: str = "system",
    reason: str = "",
) -> dict:
    """Move a lead to a funnel step and log the transition.

    Rules (spec §B1):
      * admin (actor starting with 'admin') may set any step, forward or back;
      * automation (AI / regex / cron) may advance at most ONE step, or move the
        lead to a terminal step (declined / dormant); anything else is rejected.
    Returns {"changed", "from", "to", "error"?}.
    """
    target = normalize_kol_step(new_step)
    if target is None:
        raise ValueError(f"Bước không hợp lệ: {new_step!r}")
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT kol_step FROM ai_followup_chats WHERE account_id = ? AND user_id = ?",
            (account_id, user_id),
        )
        row = await cursor.fetchone()
    if not row:
        return {"changed": False, "from": None, "to": target, "error": "not_found"}
    current = normalize_kol_step(row[0]) or "prospect"
    if current == target:
        return {"changed": False, "from": current, "to": target}

    is_admin = str(actor or "").lower().startswith("admin")
    if not is_admin:
        if target in KOL_STEP_TERMINAL:
            pass  # automation may close a lead out
        elif current in KOL_STEP_TERMINAL:
            return {"changed": False, "from": current, "to": target, "error": "terminal_requires_admin"}
        elif kol_step_rank(target) != kol_step_rank(current) + 1:
            return {"changed": False, "from": current, "to": target, "error": "skip_not_allowed"}

    async with get_db() as db:
        await db.execute("""
            UPDATE ai_followup_chats
            SET kol_step = ?, kol_step_updated_at = datetime('now'),
                step_reminders_sent = 0, last_step_reminder_at = NULL,
                updated_at = datetime('now')
            WHERE account_id = ? AND user_id = ?
        """, (target, account_id, user_id))
        await db.execute(
            "INSERT INTO kol_step_events (account_id, user_id, from_step, to_step, actor, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, user_id, current, target, actor or "system", (reason or "")[:500]),
        )
        await db.commit()
    try:
        await refresh_missing_items(account_id, user_id, target)
    except Exception as exc:
        logger.warning(f"[KOLStep] refresh_missing_items failed: {exc}")
    logger.info(f"[KOLStep] {account_id}/{user_id}: {current} → {target} by {actor} ({reason})")
    return {"changed": True, "from": current, "to": target}


async def get_kol_step_events(account_id: int, user_id: int, limit: int = 50) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, from_step, to_step, actor, reason, created_at
            FROM kol_step_events
            WHERE account_id = ? AND user_id = ?
            ORDER BY id DESC LIMIT ?
        """, (account_id, user_id, int(limit)))
        return [dict(r) for r in await cursor.fetchall()]


async def get_kol_step_counts() -> dict:
    """Real leads per funnel step (NULL counts as prospect) — dashboard cards + /pipeline."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(f"""
            SELECT COALESCE(NULLIF(kol_step, ''), 'prospect') AS step, COUNT(*) AS n
            FROM ai_followup_chats
            WHERE {_REAL_LEAD_SQL}
            GROUP BY COALESCE(NULLIF(kol_step, ''), 'prospect')
        """)
        counts = {r["step"]: r["n"] for r in await cursor.fetchall()}
    return {s: counts.get(s, 0) for s in KOL_STEP_ALL}


async def get_leads_by_kol_step(step: str, limit: int = 20) -> list[dict]:
    name = normalize_kol_step(step)
    if name is None:
        return []
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(f"""
            SELECT account_id, user_id, username, name, kol_step, kol_step_updated_at,
                   intent_score, lead_tier, reply_count, last_user_message_at, summary
            FROM ai_followup_chats
            WHERE COALESCE(NULLIF(kol_step, ''), 'prospect') = ?
              AND {_REAL_LEAD_SQL}
            ORDER BY COALESCE(kol_step_updated_at, updated_at) DESC
            LIMIT ?
        """, (name, int(limit)))
        return [dict(r) for r in await cursor.fetchall()]


async def get_kol_state(account_id: int, user_id: int) -> dict:
    """Everything the UI / Forum / prompt needs about one KOL's funnel position."""
    chat = await get_followup_chat(account_id, user_id)
    step = normalize_kol_step(chat.get("kol_step") if chat else None) or "prospect"
    profile = await get_kol_profile(account_id, user_id)
    missing = compute_missing_items(step, profile)
    have = {k: profile.get(k) for k in ("channel_link", "platform", "followers", "weex_uid", "volume_claimed",
                                        "volume_verified", "selected_campaign", "vip_code", "affiliate_link",
                                        "first_post_link", "offer_version")
            if profile.get(k) not in (None, "", [])}
    return {
        "account_id": account_id,
        "user_id": user_id,
        "step": step,
        "step_rank": kol_step_rank(step),
        "step_label": KOL_STEP_LABELS.get(step, step),
        "steps": list(KOL_STEPS),
        "step_labels": KOL_STEP_LABELS,
        "missing_items": missing,
        "missing_labels": [MISSING_ITEM_META.get(m, {}).get("label", m) for m in missing],
        "have": have,
        "status": chat.get("status") if chat else None,
        "stage": chat.get("stage") if chat else None,
        "step_reminders_sent": (chat or {}).get("step_reminders_sent") or 0,
        "kol_step_updated_at": (chat or {}).get("kol_step_updated_at"),
    }


async def mark_step_reminder_sent(account_id: int, user_id: int) -> None:
    async with get_db() as db:
        await db.execute("""
            UPDATE ai_followup_chats
            SET step_reminders_sent = COALESCE(step_reminders_sent, 0) + 1,
                last_step_reminder_at = datetime('now'), updated_at = datetime('now')
            WHERE account_id = ? AND user_id = ?
        """, (account_id, user_id))
        await db.commit()


async def get_step_reminder_candidates(min_gap_hours: int = 48, max_per_step: int = 2, limit: int = 30) -> list[dict]:
    """Leads at step >= deal_agreed whose next KOL-owned item is overdue for a nudge.

    Filters done in SQL: AI active, funnel step (not terminal / not prospect),
    reminder cap, >= min_gap since the later of (last KOL message, last reminder,
    step change). Owner filtering (kol vs ops) happens in Python via missing_items.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(f"""
            SELECT c.account_id, c.user_id, c.username, c.name, c.kol_step, c.step_reminders_sent,
                   c.last_step_reminder_at, c.kol_step_updated_at, c.last_user_message_at,
                   c.campaign_id, c.watcher_id, c.updated_at
            FROM ai_followup_chats c
            WHERE c.status = 'active'
              AND COALESCE(c.kol_step, 'prospect') IN ('deal_agreed', 'uid_received', 'link_confirmed', 'first_post')
              AND COALESCE(c.step_reminders_sent, 0) < ?
              AND datetime(MAX(COALESCE(c.last_step_reminder_at, '1970-01-01'),
                               COALESCE(c.kol_step_updated_at, '1970-01-01'),
                               COALESCE(c.last_user_message_at, '1970-01-01'),
                               COALESCE(c.updated_at, '1970-01-01'))) <= datetime('now', ?)
              AND {_REAL_LEAD_SQL}
            ORDER BY COALESCE(c.last_step_reminder_at, c.kol_step_updated_at) ASC
            LIMIT ?
        """, (int(max_per_step), f"-{int(min_gap_hours)} hours", int(limit)))
        return [dict(r) for r in await cursor.fetchall()]


async def migrate_kol_steps_v1(force: bool = False) -> dict:
    """Backfill kol_step for legacy leads (spec §B5). Idempotent.

    Mapping: stage=signed → deal_agreed; status=onboarded → link_confirmed (+ AI
    paused); profile has weex_uid → at least uid_received; everything else →
    prospect. Only rows with kol_step IS NULL are touched, a DB backup is taken
    first (alerts.run_backup) and a settings flag short-circuits later starts.
    """
    FLAG = "kol_steps_migrated_v1"
    if not force and await get_setting(FLAG):
        return {"skipped": True, "reason": "already_migrated"}

    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM ai_followup_chats WHERE kol_step IS NULL OR kol_step = ''")
        pending = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM kol_profiles")
        profiles = (await cursor.fetchone())[0]

    # Mirror legacy profile_json fields into the new structured columns first
    # (needed for the weex_uid rule below and for every later SQL read).
    mirrored = 0
    if profiles:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT account_id, user_id, profile_json, weex_uid FROM kol_profiles")
            rows = [dict(r) for r in await cursor.fetchall()]
        for r in rows:
            try:
                pj = json.loads(r.get("profile_json") or "{}")
            except Exception:
                pj = {}
            if not isinstance(pj, dict):
                continue
            fields = {c: pj.get(c) for c in KOL_PROFILE_COLUMNS if pj.get(c) not in (None, "")}
            if not fields:
                continue
            await upsert_kol_profile(r["account_id"], r["user_id"], fields)
            mirrored += 1

    if pending == 0:
        await set_setting(FLAG, datetime.now().isoformat(timespec="seconds"))
        return {"skipped": True, "reason": "nothing_to_migrate", "mirrored_profiles": mirrored}

    backup_path = None
    try:
        import alerts as _alerts  # lazy: alerts imports database
        backup_path = _alerts.run_backup()
    except Exception as exc:
        logger.warning(f"[KOLStep] backup before migration failed: {exc}")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT c.account_id, c.user_id, c.status, c.stage,
                   COALESCE(NULLIF(p.weex_uid, ''), '') AS weex_uid
            FROM ai_followup_chats c
            LEFT JOIN kol_profiles p ON p.account_id = c.account_id AND p.user_id = c.user_id
            WHERE c.kol_step IS NULL OR c.kol_step = ''
        """)
        rows = [dict(r) for r in await cursor.fetchall()]
        counts: dict[str, int] = {}
        for r in rows:
            if (r.get("status") or "") == "onboarded":
                step = "link_confirmed"
            elif (r.get("stage") or "") == "signed":
                step = "deal_agreed"
            else:
                step = "prospect"
            if r.get("weex_uid") and kol_step_rank(step) < kol_step_rank("uid_received"):
                step = "uid_received"
            await db.execute("""
                UPDATE ai_followup_chats
                SET kol_step = ?, kol_step_updated_at = datetime('now'),
                    status = CASE WHEN status = 'onboarded' THEN 'paused_admin' ELSE status END
                WHERE account_id = ? AND user_id = ?
            """, (step, r["account_id"], r["user_id"]))
            await db.execute(
                "INSERT INTO kol_step_events (account_id, user_id, from_step, to_step, actor, reason) VALUES (?, ?, NULL, ?, 'migration:v1', ?)",
                (r["account_id"], r["user_id"], step, f"status={r.get('status')} stage={r.get('stage')} uid={'yes' if r.get('weex_uid') else 'no'}"),
            )
            counts[step] = counts.get(step, 0) + 1
        await db.commit()

    await set_setting(FLAG, datetime.now().isoformat(timespec="seconds"))
    logger.info(f"[KOLStep] migrated {len(rows)} leads → {counts} (backup: {backup_path})")
    return {"skipped": False, "migrated": len(rows), "by_step": counts, "backup": backup_path, "mirrored_profiles": mirrored}

