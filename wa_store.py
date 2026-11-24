"""
WhatsApp storage — contacts scraped from groups, outreach campaigns and logs.

A person has two WhatsApp ids: a phone number and a LID (the only one visible
when they hide their number). Whichever arrives first becomes `uid`; later
sightings are merged on uid / lid / phone so nobody is messaged twice.

A contact's status is global and the number that sent the opener is pinned in
account_id — the follow-up after their reply goes out from that number.
  new → sent → replied → followed
  (or handled = the owner answered by hand, skipped / failed / blocked)
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
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS wa_contacts (
                uid TEXT PRIMARY KEY,                 -- LID jid when known, else phone jid
                lid TEXT DEFAULT '',
                phone TEXT DEFAULT '',                -- digits, '' when the number is hidden
                name TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',
                campaign_id INTEGER,
                account_id TEXT,                      -- number that sent the opener
                priority INTEGER DEFAULT 0,
                sent_at TEXT,
                replied_at TEXT,
                followed_at TEXT,
                last_error TEXT,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_wa_contacts_status ON wa_contacts(status);
            CREATE INDEX IF NOT EXISTS idx_wa_contacts_lid ON wa_contacts(lid);
            CREATE INDEX IF NOT EXISTS idx_wa_contacts_phone ON wa_contacts(phone);
            CREATE TABLE IF NOT EXISTS wa_contact_sources (
                uid TEXT NOT NULL,
                source_type TEXT NOT NULL,            -- 'group' | 'active' | 'join'
                source_id TEXT NOT NULL,
                source_name TEXT DEFAULT '',
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (uid, source_type, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_wa_sources_src ON wa_contact_sources(source_type, source_id);
            CREATE TABLE IF NOT EXISTS wa_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sources TEXT NOT NULL DEFAULT '[]',   -- ["group:<id>", ...], empty = every contact
                accounts TEXT NOT NULL DEFAULT '[]',  -- number ids allowed to send, empty = all
                countries TEXT NOT NULL DEFAULT '[]', -- calling codes ("84"), empty = any
                open_msg TEXT DEFAULT '',
                reply_msg TEXT DEFAULT '',            -- sent once they answer the opener
                daily_limit INTEGER DEFAULT 20,
                min_delay_sec INTEGER DEFAULT 300,
                max_delay_sec INTEGER DEFAULT 900,
                start_hour INTEGER DEFAULT 8,
                end_hour INTEGER DEFAULT 22,
                status TEXT DEFAULT 'paused',         -- running | paused | done
                last_error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS wa_campaign_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER,
                uid TEXT,
                name TEXT,
                action TEXT,                          -- open | followup | join
                ok INTEGER,
                error TEXT,
                account_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_wa_logs_camp ON wa_campaign_logs(campaign_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_wa_logs_acc ON wa_campaign_logs(account_id, created_at);
            CREATE TABLE IF NOT EXISTS wa_accounts (
                id TEXT PRIMARY KEY,                  -- bridge slot id
                jid TEXT DEFAULT '',
                lid TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                name TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                daily_cap INTEGER DEFAULT 20,         -- ceiling once warmed up
                first_used TEXT,                      -- date of first opener (warm-up start)
                next_action_at TEXT,
                paused_until TEXT,
                consecutive_errors INTEGER DEFAULT 0,
                last_error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS wa_watches (
                group_id TEXT PRIMARY KEY,
                group_name TEXT DEFAULT '',
                account_id TEXT NOT NULL,             -- number inside the group that listens
                campaign_id INTEGER,                  -- newcomers are queued for this campaign
                watch_joins INTEGER DEFAULT 0,
                track_active INTEGER DEFAULT 0,
                joined_count INTEGER DEFAULT 0,
                last_join_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.commit()
    _schema_ready = True


_TS = "%Y-%m-%d %H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(_TS)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _rows(cur_rows) -> list[dict]:
    return [dict(r) for r in cur_rows]


def _ids(m: dict) -> tuple[str, str, str]:
    return str(m.get("uid") or "").strip(), str(m.get("lid") or "").strip(), str(m.get("phone") or "").strip()


_MATCH = "(uid = :uid OR (:lid != '' AND (lid = :lid OR uid = :lid)) OR (:phone != '' AND phone = :phone))"


async def _find(conn, m: dict) -> dict | None:
    """The stored contact for this person. Two rows can turn out to be one person
    (seen by phone in one group, by LID in another, then with both): the
    untouched duplicate is folded into the row that is kept."""
    uid, lid, phone = _ids(m)
    cur = await conn.execute(
        f"SELECT * FROM wa_contacts WHERE {_MATCH} ORDER BY (status != 'new') DESC, first_seen",
        {"uid": uid, "lid": lid, "phone": phone})
    rows = [dict(r) for r in await cur.fetchall()]
    for dup in rows[1:]:
        if dup["status"] != "new":
            continue
        await conn.execute("UPDATE OR IGNORE wa_contact_sources SET uid = ? WHERE uid = ?", (rows[0]["uid"], dup["uid"]))
        await conn.execute("DELETE FROM wa_contact_sources WHERE uid = ?", (dup["uid"],))
        await conn.execute("DELETE FROM wa_contacts WHERE uid = ?", (dup["uid"],))
    return rows[0] if rows else None


# ── Contacts ──────────────────────────────────────────────────────────────────

async def upsert_contacts(members: list[dict], source_type: str, source_id: str, source_name: str,
                          priority: int = 0) -> dict:
    """Insert scraped members ({uid, lid, phone, name}); returns {'total', 'new'}.
    Our own numbers are dropped. priority > 0 moves them ahead of the queue."""
    await ensure_schema()
    new = total = 0
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT lid, phone FROM wa_accounts")
        own = {v for r in await cur.fetchall() for v in r if v}
        for m in members:
            uid, lid, phone = _ids(m)
            if not uid or lid in own or phone in own:
                continue
            total += 1
            found = await _find(conn, m)
            if found:
                uid = found["uid"]
                await conn.execute(
                    "UPDATE wa_contacts SET lid = COALESCE(NULLIF(lid, ''), ?), phone = COALESCE(NULLIF(phone, ''), ?), "
                    "name = COALESCE(NULLIF(?, ''), name) WHERE uid = ?",
                    (lid, phone, m.get("name") or "", uid))
            else:
                new += 1
                await conn.execute("INSERT INTO wa_contacts (uid, lid, phone, name) VALUES (?, ?, ?, ?)",
                                   (uid, lid, phone, m.get("name") or ""))
            if priority:
                await conn.execute("UPDATE wa_contacts SET priority = MAX(COALESCE(priority, 0), ?) WHERE uid = ?",
                                   (priority, uid))
            await conn.execute(
                "INSERT OR REPLACE INTO wa_contact_sources (uid, source_type, source_id, source_name) VALUES (?, ?, ?, ?)",
                (uid, source_type, source_id, source_name))
        await conn.commit()
    return {"total": total, "new": new}


async def skip_admins(admins: list[dict]) -> int:
    """Group admins are never outreach targets: mark not-yet-contacted ones as skipped."""
    await ensure_schema()
    n = 0
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        for m in admins:
            found = await _find(conn, m)
            if found and found["status"] == "new":
                await conn.execute(
                    "UPDATE wa_contacts SET status = 'skipped', last_error = 'Quản trị viên nhóm', updated_at = ? WHERE uid = ?",
                    (_now(), found["uid"]))
                n += 1
        await conn.commit()
    return n


async def find_by_jids(jids: list[str], account_id: str, statuses: tuple[str, ...]) -> dict | None:
    """The contact behind a DM (either of its two ids), pinned to this number."""
    lids = [j for j in jids if j and j.endswith("@lid")]
    phones = [j.split("@")[0] for j in jids if j and not j.endswith("@lid")]
    # an empty list must not become `lid IN ('')`: that matches everyone without a LID
    parts = [(col, vals) for col, vals in (("uid", [j for j in jids if j]), ("lid", lids), ("phone", phones)) if vals]
    if not parts:
        return None
    q = lambda xs: ",".join("?" * len(xs))  # noqa: E731
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            f"SELECT * FROM wa_contacts WHERE account_id = ? AND status IN ({q(statuses)}) "
            f"AND ({' OR '.join(f'{col} IN ({q(vals)})' for col, vals in parts)}) LIMIT 1",
            [account_id, *statuses, *(v for _, vals in parts for v in vals)])
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_sources() -> list[dict]:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT s.source_type, s.source_id, MAX(s.source_name) AS source_name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN c.status = 'new' THEN 1 ELSE 0 END) AS pending,
                   MAX(s.added_at) AS last_scraped
            FROM wa_contact_sources s JOIN wa_contacts c ON c.uid = s.uid
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
    return f"uid IN (SELECT uid FROM wa_contact_sources WHERE {' OR '.join(clauses)})", params


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
        where.append("(name LIKE ? OR phone LIKE ?)")
        params += [f"%{q}%", f"%{q.lstrip('+')}%"]
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(f"SELECT COUNT(*) FROM wa_contacts {sql_where}", params)
        total = (await cur.fetchone())[0]
        cur = await conn.execute(
            f"""SELECT c.*, (SELECT GROUP_CONCAT(source_name, ' · ') FROM wa_contact_sources s WHERE s.uid = c.uid) AS source_names
                FROM wa_contacts c {sql_where} ORDER BY first_seen DESC LIMIT ? OFFSET ?""",
            params + [limit, offset])
        return {"total": total, "items": _rows(await cur.fetchall())}


async def contact_stats() -> dict:
    await ensure_schema()
    async with db.get_db() as conn:
        cur = await conn.execute("SELECT status, COUNT(*) FROM wa_contacts GROUP BY status")
        return {r[0]: r[1] for r in await cur.fetchall()}


async def set_contact_status(uid: str, status: str, campaign_id: int | None = None, error: str | None = None,
                             account_id: str | None = None, name: str | None = None):
    col = {"sent": "sent_at", "replied": "replied_at", "followed": "followed_at"}.get(status)
    sets = ["status = ?", "updated_at = ?", "last_error = ?"]
    params: list = [status, _now(), error]
    if col:
        sets.append(f"{col} = COALESCE({col}, ?)")
        params.append(_now())
    for field, val in (("campaign_id", campaign_id), ("account_id", account_id), ("name", name or None)):
        if val is not None:
            sets.append(f"{field} = ?")
            params.append(val)
    async with db.get_db() as conn:
        await conn.execute(f"UPDATE wa_contacts SET {', '.join(sets)} WHERE uid = ?", params + [uid])
        await conn.commit()


async def next_new_contact(sources: list[str], countries: list[str]) -> dict | None:
    """Oldest untouched contact (newcomers first). With `countries`, only people whose
    visible number starts with one of those calling codes."""
    clause, params = _source_filter(sources)
    where = "status = 'new'" + (f" AND {clause}" if clause else "")
    if countries:
        where += " AND (" + " OR ".join("phone LIKE ?" for _ in countries) + ")"
        params += [f"{c}%" for c in countries]
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            f"SELECT * FROM wa_contacts WHERE {where} ORDER BY COALESCE(priority, 0) DESC, first_seen LIMIT 1", params)
        row = await cur.fetchone()
        return dict(row) if row else None


async def contacts_replied(limit: int = 30) -> list[dict]:
    """People waiting for the second message (campaigns without one leave them at 'replied')."""
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT z.* FROM wa_contacts z JOIN wa_campaigns c ON c.id = z.campaign_id AND TRIM(c.reply_msg) != '' "
            "WHERE z.status = 'replied' ORDER BY z.replied_at LIMIT ?",
            (limit,))
        return _rows(await cur.fetchall())


async def has_open_conversations(max_age_days: int = 14) -> bool:
    """Someone we opened to recently may still answer → keep draining bridge events."""
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime(_TS)
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT 1 FROM wa_contacts WHERE status IN ('sent', 'replied') AND sent_at >= ? LIMIT 1", (cutoff,))
        return await cur.fetchone() is not None


async def block_contact(uid: str, blocked: bool = True):
    """Global do-not-contact list: no number ever sends to a blocked contact."""
    await set_contact_status(uid, "blocked" if blocked else "new", error="Chặn thủ công" if blocked else None)


# ── Campaigns ─────────────────────────────────────────────────────────────────

CAMPAIGN_FIELDS = ("name", "sources", "accounts", "countries", "open_msg", "reply_msg", "daily_limit",
                   "min_delay_sec", "max_delay_sec", "start_hour", "end_hour")
_JSON_FIELDS = ("sources", "accounts", "countries")


def _campaign(row) -> dict:
    d = dict(row)
    for f in _JSON_FIELDS:
        d[f] = json.loads(d.get(f) or "[]")
    return d


def _campaign_vals(data: dict) -> list:
    return [json.dumps(data.get(f) or []) if f in _JSON_FIELDS else data[f] for f in CAMPAIGN_FIELDS]


async def create_campaign(data: dict) -> int:
    await ensure_schema()
    async with db.get_db() as conn:
        cur = await conn.execute(
            f"INSERT INTO wa_campaigns ({', '.join(CAMPAIGN_FIELDS)}) VALUES ({', '.join('?' * len(CAMPAIGN_FIELDS))})",
            _campaign_vals(data))
        await conn.commit()
        return cur.lastrowid


async def update_campaign(cid: int, data: dict):
    async with db.get_db() as conn:
        await conn.execute(
            f"UPDATE wa_campaigns SET {', '.join(f + ' = ?' for f in CAMPAIGN_FIELDS)} WHERE id = ?",
            _campaign_vals(data) + [cid])
        await conn.commit()


async def set_campaign_state(cid: int, **fields):
    if not fields:
        return
    async with db.get_db() as conn:
        await conn.execute(
            f"UPDATE wa_campaigns SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
            list(fields.values()) + [cid])
        await conn.commit()


async def get_campaign(cid: int) -> dict | None:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM wa_campaigns WHERE id = ?", (cid,))
        row = await cur.fetchone()
        return _campaign(row) if row else None


async def list_campaigns(status: str = "") -> list[dict]:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            f"""SELECT c.*,
                  (SELECT COUNT(*) FROM wa_contacts z WHERE z.campaign_id = c.id AND z.sent_at IS NOT NULL) AS sent,
                  (SELECT COUNT(*) FROM wa_contacts z WHERE z.campaign_id = c.id AND z.replied_at IS NOT NULL) AS replied,
                  (SELECT COUNT(*) FROM wa_contacts z WHERE z.campaign_id = c.id AND z.status = 'followed') AS followed,
                  (SELECT COUNT(*) FROM wa_campaign_logs l WHERE l.campaign_id = c.id AND l.action = 'open'
                       AND l.ok = 1 AND l.created_at >= ?) AS sent_today
                FROM wa_campaigns c {'WHERE c.status = ?' if status else ''} ORDER BY c.id DESC""",
            [_today()] + ([status] if status else []))
        return [_campaign(r) for r in await cur.fetchall()]


async def delete_campaign(cid: int):
    async with db.get_db() as conn:
        await conn.execute("DELETE FROM wa_campaigns WHERE id = ?", (cid,))
        await conn.execute("DELETE FROM wa_campaign_logs WHERE campaign_id = ?", (cid,))
        await conn.commit()


async def sent_today(cid: int) -> int:
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM wa_campaign_logs WHERE campaign_id = ? AND action = 'open' AND ok = 1 AND created_at >= ?",
            (cid, _today()))
        return (await cur.fetchone())[0]


async def add_log(cid: int | None, uid: str, name: str, action: str, ok: bool, error: str | None = None,
                  account_id: str | None = None):
    await ensure_schema()
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO wa_campaign_logs (campaign_id, uid, name, action, ok, error, account_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, uid, name, action, 1 if ok else 0, error, account_id, _now()))
        await conn.commit()


async def list_logs(cid: int, limit: int = 100) -> list[dict]:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM wa_campaign_logs WHERE campaign_id = ? ORDER BY id DESC LIMIT ?", (cid, limit))
        return _rows(await cur.fetchall())


# ── Accounts (numbers) ────────────────────────────────────────────────────────

_ACCOUNT_STATS_SQL = """
    SELECT a.*,
      (SELECT COUNT(*) FROM wa_campaign_logs l WHERE l.account_id = a.id AND l.action = 'open'
           AND l.ok = 1 AND l.created_at >= :today) AS sent_today,
      (SELECT COUNT(*) FROM wa_campaign_logs l WHERE l.account_id = a.id AND l.action = 'join'
           AND l.ok = 1 AND l.created_at >= :today) AS joins_today,
      (SELECT COUNT(*) FROM wa_contacts z WHERE z.account_id = a.id AND z.sent_at IS NOT NULL) AS sent_total,
      (SELECT COUNT(*) FROM wa_contacts z WHERE z.account_id = a.id AND z.replied_at IS NOT NULL) AS replied_total
    FROM wa_accounts a
"""


async def sync_accounts(bridge_accounts: list[dict]):
    """Mirror bridge slots into wa_accounts (keeps per-number settings and stats)."""
    await ensure_schema()
    async with db.get_db() as conn:
        for a in bridge_accounts:
            me = a.get("me")
            if not me:
                continue  # slot still waiting for its first QR scan
            await conn.execute(
                "INSERT INTO wa_accounts (id, jid, lid, phone, name) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET jid = COALESCE(NULLIF(excluded.jid, ''), jid), "
                "lid = COALESCE(NULLIF(excluded.lid, ''), lid), phone = COALESCE(NULLIF(excluded.phone, ''), phone), "
                "name = COALESCE(NULLIF(excluded.name, ''), name)",
                (a["id"], me.get("jid") or "", me.get("lid") or "", me.get("phone") or "", me.get("name") or ""))
        await conn.commit()


async def list_accounts() -> list[dict]:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(_ACCOUNT_STATS_SQL + " ORDER BY a.created_at, a.id", {"today": _today()})
        return _rows(await cur.fetchall())


async def get_account(acc_id: str) -> dict | None:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(_ACCOUNT_STATS_SQL + " WHERE a.id = :id", {"today": _today(), "id": acc_id})
        row = await cur.fetchone()
        return dict(row) if row else None


async def set_account_state(acc_id: str, **fields):
    if not fields:
        return
    await ensure_schema()
    async with db.get_db() as conn:
        await conn.execute(
            f"UPDATE wa_accounts SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
            list(fields.values()) + [acc_id])
        await conn.commit()


async def delete_account(acc_id: str):
    async with db.get_db() as conn:
        await conn.execute("DELETE FROM wa_accounts WHERE id = ?", (acc_id,))
        await conn.execute("DELETE FROM wa_watches WHERE account_id = ?", (acc_id,))
        await conn.commit()


# ── Group watches (newcomers / active speakers) ───────────────────────────────

async def list_watches() -> list[dict]:
    await ensure_schema()
    async with db.get_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT w.*, c.name AS campaign_name, c.status AS campaign_status, "
            "(SELECT COUNT(*) FROM wa_contact_sources s WHERE s.source_type = 'active' AND s.source_id = w.group_id) AS active_count "
            "FROM wa_watches w LEFT JOIN wa_campaigns c ON c.id = w.campaign_id ORDER BY w.created_at")
        return _rows(await cur.fetchall())


async def save_watch(group_id: str, group_name: str, account_id: str, campaign_id: int | None,
                     watch_joins: bool, track_active: bool):
    await ensure_schema()
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO wa_watches (group_id, group_name, account_id, campaign_id, watch_joins, track_active) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET group_name = excluded.group_name, "
            "account_id = excluded.account_id, campaign_id = excluded.campaign_id, "
            "watch_joins = excluded.watch_joins, track_active = excluded.track_active",
            (group_id, group_name, account_id, campaign_id, int(watch_joins), int(track_active)))
        await conn.commit()


async def delete_watch(group_id: str):
    await ensure_schema()
    async with db.get_db() as conn:
        await conn.execute("DELETE FROM wa_watches WHERE group_id = ?", (group_id,))
        await conn.commit()


async def record_join(group_id: str, n: int):
    async with db.get_db() as conn:
        await conn.execute("UPDATE wa_watches SET joined_count = joined_count + ?, last_join_at = ? WHERE group_id = ?",
                           (n, _now(), group_id))
        await conn.commit()
