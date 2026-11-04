"""
Zalo storage — contacts scraped from groups/events, outreach campaigns and logs.

A contact's status is global: once someone was sent a friend request by any
campaign, no other campaign touches them again.
  new → requested → friend → greeted
  (or skipped / failed)
"""
import json
from datetime import datetime, timedelta

import aiosqlite

import database as db

_schema_ready = False


async def ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS zalo_contacts (
                uid TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                avatar TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',
                campaign_id INTEGER,
                requested_at TEXT,
                friend_at TEXT,
                greeted_at TEXT,
                last_error TEXT,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_zalo_contacts_status ON zalo_contacts(status);
            CREATE TABLE IF NOT EXISTS zalo_contact_sources (
                uid TEXT NOT NULL,
                source_type TEXT NOT NULL,      -- 'group' | 'event'
                source_id TEXT NOT NULL,
                source_name TEXT DEFAULT '',
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (uid, source_type, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_zalo_sources_src ON zalo_contact_sources(source_type, source_id);
            CREATE TABLE IF NOT EXISTS zalo_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sources TEXT NOT NULL DEFAULT '[]',   -- ["group:<id>", "event:<id>"], empty = every contact
                friend_msg TEXT DEFAULT '',
                greet_msg TEXT DEFAULT '',
                daily_limit INTEGER DEFAULT 20,
                min_delay_sec INTEGER DEFAULT 300,
                max_delay_sec INTEGER DEFAULT 900,
                start_hour INTEGER DEFAULT 8,
                end_hour INTEGER DEFAULT 22,
                status TEXT DEFAULT 'paused',         -- running | paused | done
                next_action_at TEXT,
                consecutive_errors INTEGER DEFAULT 0,
                last_error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS zalo_campaign_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER,
                uid TEXT,
                name TEXT,
                action TEXT,          -- friend_request | greet | skip
                ok INTEGER,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_zalo_logs_camp ON zalo_campaign_logs(campaign_id, created_at);
        """)
        await conn.commit()
    _schema_ready = True


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rows(cur_rows) -> list[dict]:
    return [dict(r) for r in cur_rows]


# ── Contacts ──────────────────────────────────────────────────────────────────

async def upsert_contacts(members: list[dict], source_type: str, source_id: str, source_name: str) -> dict:
    """Insert scraped members; returns {'total', 'new'} where 'new' = never seen before."""
    await ensure_schema()
    new = 0
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        for m in members:
            uid = str(m.get("uid") or "").strip()
            if not uid:
                continue
            cur = await conn.execute(
                "INSERT OR IGNORE INTO zalo_contacts (uid, name, avatar) VALUES (?, ?, ?)",
                (uid, m.get("name") or "", m.get("avatar") or ""),
            )
            if cur.rowcount:
                new += 1
            elif m.get("name"):
                await conn.execute(
                    "UPDATE zalo_contacts SET name = ?, avatar = ? WHERE uid = ?",
                    (m["name"], m.get("avatar") or "", uid),
                )
            await conn.execute(
                "INSERT OR REPLACE INTO zalo_contact_sources (uid, source_type, source_id, source_name) VALUES (?, ?, ?, ?)",
                (uid, source_type, source_id, source_name),
            )
        await conn.commit()
    return {"total": len(members), "new": new}


async def list_sources() -> list[dict]:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT s.source_type, s.source_id, MAX(s.source_name) AS source_name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN c.status = 'new' THEN 1 ELSE 0 END) AS pending,
                   MAX(s.added_at) AS last_scraped
            FROM zalo_contact_sources s JOIN zalo_contacts c ON c.uid = s.uid
            GROUP BY s.source_type, s.source_id
            ORDER BY last_scraped DESC
        """)
        return _rows(await cur.fetchall())


def _source_filter(sources: list[str]) -> tuple[str, list]:
    if not sources:
        return "", []
    clauses, params = [], []
    for s in sources:
        stype, _, sid = s.partition(":")
        clauses.append("(source_type = ? AND source_id = ?)")
        params += [stype, sid]
    return f"uid IN (SELECT uid FROM zalo_contact_sources WHERE {' OR '.join(clauses)})", params


async def list_contacts(status: str = "", source: str = "", q: str = "", limit: int = 200, offset: int = 0) -> dict:
    await ensure_schema()
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if source:
        clause, p = _source_filter([source])
        where.append(clause)
        params += p
    if q:
        where.append("(name LIKE ? OR uid = ?)")
        params += [f"%{q}%", q]
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(f"SELECT COUNT(*) FROM zalo_contacts {sql_where}", params)
        total = (await cur.fetchone())[0]
        cur = await conn.execute(
            f"""SELECT c.*, (SELECT GROUP_CONCAT(source_name, ' · ') FROM zalo_contact_sources s WHERE s.uid = c.uid) AS source_names
                FROM zalo_contacts c {sql_where} ORDER BY first_seen DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        return {"total": total, "items": _rows(await cur.fetchall())}


async def contact_stats() -> dict:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT status, COUNT(*) FROM zalo_contacts GROUP BY status")
        return {r[0]: r[1] for r in await cur.fetchall()}


async def set_contact_status(uid: str, status: str, campaign_id: int | None = None, error: str | None = None):
    col = {"requested": "requested_at", "friend": "friend_at", "greeted": "greeted_at"}.get(status)
    sets = ["status = ?", "updated_at = ?", "last_error = ?"]
    params: list = [status, _now(), error]
    if col:
        sets.append(f"{col} = COALESCE({col}, ?)")
        params.append(_now())
    if campaign_id is not None:
        sets.append("campaign_id = ?")
        params.append(campaign_id)
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(f"UPDATE zalo_contacts SET {', '.join(sets)} WHERE uid = ?", params + [uid])
        await conn.commit()


async def next_new_contact(sources: list[str]) -> dict | None:
    clause, params = _source_filter(sources)
    where = "status = 'new'" + (f" AND {clause}" if clause else "")
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(f"SELECT * FROM zalo_contacts WHERE {where} ORDER BY first_seen LIMIT 1", params)
        row = await cur.fetchone()
        return dict(row) if row else None


async def contacts_awaiting_accept(max_age_days: int = 14, limit: int = 30) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """SELECT * FROM zalo_contacts WHERE status IN ('requested', 'friend') AND campaign_id IS NOT NULL
               AND COALESCE(requested_at, friend_at) >= ? ORDER BY updated_at LIMIT ?""",
            (cutoff, limit),
        )
        return _rows(await cur.fetchall())


async def touch_contact(uid: str):
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("UPDATE zalo_contacts SET updated_at = ? WHERE uid = ?", (_now(), uid))
        await conn.commit()


# ── Campaigns ─────────────────────────────────────────────────────────────────

CAMPAIGN_FIELDS = ("name", "sources", "friend_msg", "greet_msg", "daily_limit",
                   "min_delay_sec", "max_delay_sec", "start_hour", "end_hour")


def _campaign(row) -> dict:
    d = dict(row)
    d["sources"] = json.loads(d.get("sources") or "[]")
    return d


async def create_campaign(data: dict) -> int:
    await ensure_schema()
    vals = [json.dumps(data[f]) if f == "sources" else data[f] for f in CAMPAIGN_FIELDS]
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            f"INSERT INTO zalo_campaigns ({', '.join(CAMPAIGN_FIELDS)}) VALUES ({', '.join('?' * len(CAMPAIGN_FIELDS))})",
            vals,
        )
        await conn.commit()
        return cur.lastrowid


async def update_campaign(cid: int, data: dict):
    vals = [json.dumps(data[f]) if f == "sources" else data[f] for f in CAMPAIGN_FIELDS]
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            f"UPDATE zalo_campaigns SET {', '.join(f + ' = ?' for f in CAMPAIGN_FIELDS)} WHERE id = ?",
            vals + [cid],
        )
        await conn.commit()


async def set_campaign_state(cid: int, **fields):
    if not fields:
        return
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            f"UPDATE zalo_campaigns SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
            list(fields.values()) + [cid],
        )
        await conn.commit()


async def get_campaign(cid: int) -> dict | None:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM zalo_campaigns WHERE id = ?", (cid,))
        row = await cur.fetchone()
        return _campaign(row) if row else None


async def list_campaigns(status: str = "") -> list[dict]:
    await ensure_schema()
    today = datetime.now().strftime("%Y-%m-%d")
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            f"""SELECT c.*,
                  (SELECT COUNT(*) FROM zalo_contacts z WHERE z.campaign_id = c.id AND z.status IN ('requested','friend','greeted')) AS requested,
                  (SELECT COUNT(*) FROM zalo_contacts z WHERE z.campaign_id = c.id AND z.status IN ('friend','greeted')) AS accepted,
                  (SELECT COUNT(*) FROM zalo_contacts z WHERE z.campaign_id = c.id AND z.status = 'greeted') AS greeted,
                  (SELECT COUNT(*) FROM zalo_campaign_logs l WHERE l.campaign_id = c.id AND l.action = 'friend_request'
                       AND l.ok = 1 AND l.created_at >= ?) AS sent_today
                FROM zalo_campaigns c {'WHERE c.status = ?' if status else ''} ORDER BY c.id DESC""",
            [today] + ([status] if status else []),
        )
        return [_campaign(r) for r in await cur.fetchall()]


async def delete_campaign(cid: int):
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("DELETE FROM zalo_campaigns WHERE id = ?", (cid,))
        await conn.execute("DELETE FROM zalo_campaign_logs WHERE campaign_id = ?", (cid,))
        await conn.commit()


async def sent_today(cid: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT COUNT(*) FROM zalo_campaign_logs WHERE campaign_id = ? AND action = 'friend_request' AND ok = 1 AND created_at >= ?",
            (cid, today),
        )
        return (await cur.fetchone())[0]


async def add_log(cid: int, uid: str, name: str, action: str, ok: bool, error: str | None = None):
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            "INSERT INTO zalo_campaign_logs (campaign_id, uid, name, action, ok, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, uid, name, action, 1 if ok else 0, error, _now()),
        )
        await conn.commit()


async def list_logs(cid: int, limit: int = 100) -> list[dict]:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM zalo_campaign_logs WHERE campaign_id = ? ORDER BY id DESC LIMIT ?", (cid, limit)
        )
        return _rows(await cur.fetchall())
