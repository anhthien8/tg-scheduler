"""
Database layer - SQLite with aiosqlite
Multi-account + hourly schedule + max sends support
"""
import aiosqlite
import os
import json
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "scheduler.db")


async def init_db():
    """Initialize database and create tables."""
    os.makedirs(DB_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

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
                FOREIGN KEY (watcher_id) REFERENCES keyword_watchers(id) ON DELETE CASCADE
            )
        """)

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

        # View boost columns for reaction_targets
        try:
            await db.execute("ALTER TABLE reaction_targets ADD COLUMN view_enabled INTEGER DEFAULT 0")
            await db.execute("ALTER TABLE reaction_targets ADD COLUMN view_ratio REAL DEFAULT 1.0")
            await db.commit()
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
                is_read         INTEGER DEFAULT 0,
                received_at     TEXT DEFAULT (datetime('now'))
            )
        """)
        # Index for fast unread-count lookups
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dm_replies_unread "
            "ON dm_replies(is_read, received_at DESC)"
        )
        await db.commit()


# ── Account CRUD ──

async def create_account(data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        proxy_url = data.get("proxy_url") or None
        cursor = await db.execute(
            """INSERT INTO accounts (name, phone, api_id, api_hash, session_name, proxy_url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data["name"], data["phone"], data["api_id"], data["api_hash"], data["session_name"], proxy_url)
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_accounts() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM accounts ORDER BY id")
        return [dict(row) for row in await cursor.fetchall()]


async def get_account(account_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM accounts WHERE id=?", (account_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_account_name(account_id: int, name: str):
    """Update the display name of an account (after fetching real TG profile name)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE accounts SET name = ? WHERE id = ?",
            (name, account_id)
        )
        await conn.commit()


async def update_account_login_status(account_id: int, is_logged_in: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE accounts SET is_logged_in=? WHERE id=?",
                         (1 if is_logged_in else 0, account_id))
        await db.commit()


async def delete_account(account_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        await db.commit()


# ── Schedule CRUD ──

async def create_schedule(data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
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


async def get_all_schedules() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT s.*, a.name as account_name, a.phone as account_phone
            FROM schedules s LEFT JOIN accounts a ON s.account_id = a.id
            ORDER BY s.created_at DESC
        """)
        schedules = [dict(row) for row in await cursor.fetchall()]

        for sch in schedules:
            cursor2 = await db.execute(
                "SELECT * FROM schedule_messages WHERE schedule_id=? ORDER BY msg_order", (sch["id"],))
            sch["messages"] = [dict(r) for r in await cursor2.fetchall()]

            cursor3 = await db.execute(
                "SELECT * FROM schedule_targets WHERE schedule_id=?", (sch["id"],))
            sch["targets"] = [dict(r) for r in await cursor3.fetchall()]

            # Include count of blocked targets for UI badge
            row_block = await (await db.execute(
                "SELECT COUNT(*) as cnt FROM schedule_target_blocks WHERE schedule_id=? AND is_blocked=1",
                (sch["id"],)
            )).fetchone()
            sch["blocked_count"] = row_block["cnt"] if row_block else 0

        return schedules


async def get_schedule(schedule_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
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


async def update_schedule(schedule_id: int, data: dict) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        await db.commit()
        return True


async def toggle_schedule(schedule_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM schedules WHERE is_active=1")
        schedules = [dict(row) for row in await cursor.fetchall()]
        for sch in schedules:
            c2 = await db.execute(
                "SELECT * FROM schedule_messages WHERE schedule_id=? ORDER BY msg_order", (sch["id"],))
            sch["messages"] = [dict(r) for r in await c2.fetchall()]
            c3 = await db.execute(
                "SELECT * FROM schedule_targets WHERE schedule_id=?", (sch["id"],))
            sch["targets"] = [dict(r) for r in await c3.fetchall()]
        return schedules


async def increment_send_count(schedule_id: int) -> dict:
    """Increment current_sends and auto-deactivate if max_sends reached. Returns updated state."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE schedules SET current_sends=0 WHERE id=?", (schedule_id,))
        await db.commit()


# ── Send Logs ──

async def add_send_log(schedule_id: int, account_id: int | None, message_id: int | None,
                       chat_id: int, chat_title: str, status: str, error_message: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM send_logs WHERE status='success'")
        success = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM send_logs WHERE status='failed'")
        failed = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM send_logs WHERE status='success' AND date(sent_at)=date('now')")
        today = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM schedules WHERE is_active=1")
        active_schedules = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM schedules")
        total_schedules = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM accounts")
        total_accounts = (await cursor.fetchone())["cnt"]

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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        import json as _json
        cursor = await db.execute(
            """INSERT INTO keyword_watchers
               (name, sender_account_ids, keywords, group_ids, cooldown_hours, dm_once, excluded_usernames, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["name"],
                _json.dumps(data.get("sender_account_ids", [])),
                _json.dumps(data.get("keywords", [])),
                _json.dumps(data.get("group_ids", [])),
                data.get("cooldown_hours", 24),
                1 if data.get("dm_once") else 0,
                _json.dumps([u.lstrip("@").lower() for u in data.get("excluded_usernames", [])]),
                data.get("is_active", 1),
            )
        )
        watcher_id = cursor.lastrowid
        for msg in data.get("messages", []):
            await db.execute(
                """INSERT INTO watcher_messages
                   (watcher_id, msg_order, msg_type, content, media_path)
                   VALUES (?, ?, ?, ?, ?)""",
                (watcher_id, msg.get("msg_order", 0), msg["msg_type"],
                 msg.get("content"), msg.get("media_path"))
            )
        await db.commit()
        return watcher_id


async def _load_watcher_row(db, row: dict) -> dict:
    """Helper: attach messages to a watcher row."""
    import json as _json
    w = dict(row)
    for f in ("sender_account_ids", "keywords", "group_ids", "excluded_usernames"):
        try:
            w[f] = _json.loads(w.get(f) or "[]")
        except Exception:
            w[f] = []
    c = await db.execute(
        "SELECT * FROM watcher_messages WHERE watcher_id=? ORDER BY msg_order", (w["id"],))
    w["messages"] = [dict(r) for r in await c.fetchall()]
    return w


async def get_all_watchers() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM keyword_watchers ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [await _load_watcher_row(db, r) for r in rows]


async def get_active_watchers() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM keyword_watchers WHERE is_active=1")
        rows = await cursor.fetchall()
        return [await _load_watcher_row(db, r) for r in rows]


async def get_watcher(watcher_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM keyword_watchers WHERE id=?", (watcher_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return await _load_watcher_row(db, row)


async def update_watcher(watcher_id: int, data: dict) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        import json as _json
        await db.execute(
            """UPDATE keyword_watchers
               SET name=?, sender_account_ids=?, keywords=?, group_ids=?,
                   cooldown_hours=?, dm_once=?, excluded_usernames=?,
                   reply_in_group=?, group_reply_text=?, group_reply_account_id=?,
                   is_active=?, updated_at=datetime('now')
               WHERE id=?""",
            (
                data["name"],
                _json.dumps(data.get("sender_account_ids", data.get("account_ids", []))),
                _json.dumps(data.get("keywords", [])),
                _json.dumps(data.get("group_ids", [])),
                data.get("cooldown_hours", 24),
                1 if data.get("dm_once") else 0,
                _json.dumps([u.lstrip("@").lower() for u in data.get("excluded_usernames", [])]),
                1 if data.get("reply_in_group") else 0,
                data.get("group_reply_text", "Check my DM 😊") or "Check my DM 😊",
                data.get("group_reply_account_id"),
                data.get("is_active", 1),
                watcher_id,
            )
        )
        await db.execute("DELETE FROM watcher_messages WHERE watcher_id=?", (watcher_id,))
        for msg in data.get("messages", []):
            await db.execute(
                """INSERT INTO watcher_messages
                   (watcher_id, msg_order, msg_type, content, media_path)
                   VALUES (?, ?, ?, ?, ?)""",
                (watcher_id, msg.get("msg_order", 0), msg["msg_type"],
                 msg.get("content"), msg.get("media_path"))
            )
        await db.commit()
        return True


async def delete_watcher(watcher_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM keyword_watchers WHERE id=?", (watcher_id,))
        await db.commit()
        return True


async def toggle_watcher(watcher_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    status: str, error_message: str | None = None
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO watcher_dm_logs
               (watcher_id, account_id, target_user_id, target_username,
                group_id, group_title, matched_keyword, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (watcher_id, account_id, target_user_id, target_username,
             group_id, group_title, matched_keyword, status, error_message)
        )
        await db.commit()


async def get_watcher_dm_logs(
    limit: int = 100, offset: int = 0,
    watcher_id: int | None = None,
    status: str | None = None
) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        c = await db.execute("SELECT COUNT(*) FROM watcher_dm_logs WHERE status='success'")
        success = (await c.fetchone())[0]
        c = await db.execute("SELECT COUNT(*) FROM watcher_dm_logs WHERE status='failed'")
        failed = (await c.fetchone())[0]
        c = await db.execute("SELECT COUNT(*) FROM watcher_dm_logs WHERE status='skipped'")
        skipped = (await c.fetchone())[0]
        c = await db.execute(
            "SELECT COUNT(*) FROM watcher_dm_logs WHERE status='success' AND date(sent_at)=date('now')")
        today = (await c.fetchone())[0]
        c = await db.execute("SELECT COUNT(*) FROM keyword_watchers WHERE is_active=1")
        active = (await c.fetchone())[0]
        return {"success": success, "failed": failed, "skipped": skipped,
                "today": today, "active_watchers": active}


async def get_setting(key: str, default=None):
    """Retrieve a setting value by key."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str):
    """Insert or update a setting."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value)
        )
        await db.commit()


# ── Target Block Tracking ──────────────────────────────────────────────────

async def record_target_failure(schedule_id: int, account_id: int, chat_id: int, chat_title: str = "") -> dict:
    """
    Increment fail count for (schedule, account, chat).
    Returns {"fail_count": N, "just_blocked": bool, "is_blocked": bool}
    """
    MAX_FAILURES = 3
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE schedule_target_blocks SET is_blocked=0, fail_count=0, blocked_at=NULL
            WHERE schedule_id=? AND account_id=? AND chat_id=?
        """, (schedule_id, account_id, chat_id))
        await db.commit()
        return True


# ── Daily DM Limit Tracking ────────────────────────────────────────────────────

DM_DAILY_LIMIT_NORMAL = 10
DM_DAILY_LIMIT_PREMIUM = 50


async def get_account_daily_dm_count(account_id: int) -> int:
    """Count how many DMs this account sent today (UTC date)."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            """SELECT COUNT(*) as cnt FROM watcher_dm_logs
               WHERE account_id=? AND status='success'
               AND DATE(sent_at) = DATE('now')""",
            (account_id,)
        )).fetchone()
        return row[0] if row else 0


async def is_account_dm_limit_reached(account_id: int) -> tuple[bool, int, int]:
    """
    Check if account has reached daily DM limit.
    Returns (limit_reached: bool, count: int, limit: int)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        acc = await (await db.execute(
            "SELECT is_premium FROM accounts WHERE id=?", (account_id,)
        )).fetchone()
        is_premium = bool(acc["is_premium"]) if acc else False
        limit = DM_DAILY_LIMIT_PREMIUM if is_premium else DM_DAILY_LIMIT_NORMAL

        row = await (await db.execute(
            """SELECT COUNT(*) as cnt FROM watcher_dm_logs
               WHERE account_id=? AND status='success'
               AND DATE(sent_at) = DATE('now')""",
            (account_id,)
        )).fetchone()
        count = row["cnt"] if row else 0
        return (count >= limit, count, limit)


async def set_account_premium(account_id: int, is_premium: bool) -> bool:
    """Toggle premium status for an account."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM dm_blacklist ORDER BY created_at DESC"
        )).fetchall()
        return [dict(r) for r in rows]


async def add_to_dm_blacklist(user_id: int | None, username: str | None, reason: str = "") -> dict:
    """Insert or update a user in the DM blacklist. Returns the saved row as a dict."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO dm_blacklist (user_id, username, reason)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, reason=excluded.reason""",
            (user_id, username, reason)
        )
        await db.commit()
        row = await (await db.execute(
            "SELECT * FROM dm_blacklist WHERE user_id=?", (user_id,)
        )).fetchone()
        return dict(row) if row else {}


async def remove_from_dm_blacklist(blacklist_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM dm_blacklist WHERE id=?", (blacklist_id,))
        await db.commit()


async def is_user_blacklisted(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT id FROM dm_blacklist WHERE user_id=?", (user_id,)
        )).fetchone()
        return row is not None


# ============================================================
# ACCOUNT FLAGGING — Feature #2
# ============================================================

async def check_and_flag_account(account_id: int):
    """Flag account if it has >= 5 failures in last 24h."""
    async with aiosqlite.connect(DB_PATH) as db:
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
            return  # migration not done yet

        if fail_count >= 5:
            await db.execute(
                """UPDATE accounts SET is_flagged=1,
                   flag_reason=? WHERE id=?""",
                (f"{fail_count} lỗi trong 24h gần nhất", account_id)
            )
            await db.commit()


async def unflag_account(account_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE accounts SET is_flagged=0, flag_reason=NULL WHERE id=?",
            (account_id,)
        )
        await db.commit()


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
) -> int:
    """Insert a new reaction target. Returns new row id."""
    import json
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO reaction_targets
               (channel_link, channel_id, channel_title, account_ids, reactions, delay_min, delay_max, view_enabled, view_ratio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        await db.commit()
        return cur.lastrowid


async def get_all_reaction_targets(active_only: bool = True) -> list[dict]:
    import json
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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


ALLOWED_REACTION_COLS = {"account_ids", "reactions", "delay_min", "delay_max", "is_active", "channel_title", "channel_id", "view_enabled", "view_ratio"}


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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE reaction_targets SET {cols} WHERE id=?", vals)
        await db.commit()


async def delete_reaction_target(target_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
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
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO reaction_logs
               (target_id, account_id, channel_id, msg_id, reaction, status, error_msg)
               VALUES (?,?,?,?,?,?,?)""",
            (target_id, account_id, channel_id, msg_id, reaction, status, error_msg),
        )
        await db.commit()


async def get_reaction_logs(target_id: int | None = None, limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            """SELECT COUNT(*) FROM reaction_logs
               WHERE target_id=? AND account_id=? AND msg_id=? AND status='success'""",
            (target_id, account_id, msg_id),
        )).fetchone()
        return (row[0] or 0) > 0


async def set_account_peerflood_until(account_id: int, until_timestamp: float) -> None:
    """Persist PeerFlood cooldown end time for an account."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE accounts SET peerflood_until=? WHERE id=?",
            (until_timestamp, account_id)
        )
        await db.commit()


async def get_accounts_with_peerflood() -> list[tuple[int, float]]:
    """Return [(account_id, peerflood_until)] for accounts still in cooldown."""
    now = __import__('time').time()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT id, peerflood_until FROM accounts WHERE peerflood_until > ?", (now,)
        )).fetchall()
        return [(r[0], r[1]) for r in rows]


# ── DM Reply Tracker CRUD ──────────────────────────────────────────────────────

async def add_dm_reply(data: dict) -> int:
    """
    Insert a new DM reply into dm_replies.
    data keys: account_id, sender_user_id, sender_username, sender_name,
               message_text, watcher_id (optional)
    Returns the inserted row id.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO dm_replies
               (watcher_id, account_id, sender_user_id, sender_username,
                sender_name, message_text, is_read)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (
                data.get("watcher_id"),
                data["account_id"],
                data["sender_user_id"],
                data.get("sender_username"),
                data.get("sender_name"),
                data.get("message_text"),
            )
        )
        await db.commit()
        return cursor.lastrowid


async def get_dm_replies(
    limit: int = 50,
    offset: int = 0,
    is_read: int | None = None,
    watcher_id: int | None = None,
) -> list[dict]:
    """
    Fetch DM replies with optional filters.
    is_read: None=all, 0=unread only, 1=read only
    watcher_id: filter to a specific watcher
    """
    conditions = []
    params: list = []
    if is_read is not None:
        conditions.append("is_read = ?")
        params.append(is_read)
    if watcher_id is not None:
        conditions.append("watcher_id = ?")
        params.append(watcher_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE dm_replies SET is_read = 1 WHERE id = ?", (reply_id,)
        )
        await db.commit()
        return True


async def mark_all_replies_read() -> int:
    """Mark all unread replies as read. Returns number of rows updated."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE dm_replies SET is_read = 1 WHERE is_read = 0"
        )
        await db.commit()
        return cursor.rowcount


async def count_unread_replies() -> int:
    """Return the count of unread DM replies (for the inbox badge)."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM dm_replies WHERE is_read = 0"
        )).fetchone()
        return row[0] if row else 0


async def find_watcher_id_for_user(user_id: int) -> int | None:
    """
    Return the watcher_id of the most recent successful DM sent to user_id,
    or None if the user was never DM'd by any watcher.
    Used by dm_reply_tracker to link a reply back to the originating watcher.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            """SELECT watcher_id FROM watcher_dm_logs
               WHERE target_user_id = ? AND status = 'success'
               ORDER BY sent_at DESC LIMIT 1""",
            (user_id,)
        )).fetchone()
        return row[0] if row else None

