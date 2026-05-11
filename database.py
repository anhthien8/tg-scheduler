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

        await db.commit()


# ── Account CRUD ──

async def create_account(data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO accounts (name, phone, api_id, api_hash, session_name)
               VALUES (?, ?, ?, ?, ?)""",
            (data["name"], data["phone"], data["api_id"], data["api_hash"], data["session_name"])
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
                        status: str | None = None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where = []
        params = []
        if schedule_id:
            where.append("schedule_id=?")
            params.append(schedule_id)
        if status:
            where.append("status=?")
            params.append(status)

        where_str = " WHERE " + " AND ".join(where) if where else ""

        count_cursor = await db.execute(f"SELECT COUNT(*) as cnt FROM send_logs{where_str}", params)
        total = (await count_cursor.fetchone())["cnt"]

        cursor = await db.execute(
            f"SELECT * FROM send_logs{where_str} ORDER BY sent_at DESC LIMIT ? OFFSET ?",
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
