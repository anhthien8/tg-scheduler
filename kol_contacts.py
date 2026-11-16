"""KOL contacts (v3.40): a lead who sent a trading proof is saved as a Telegram
contact named "<their name>_KOL" on the nick that DMed them.

- Triggered by proof_relay after a proof photo/video/file is captured (the proof
  itself is already posted into the lead's topic in the Forum Inbox group, which is
  the backup).
- Adding a contact is silent: the lead gets no message or notification.
- One row per (nick, lead) in `kol_contacts`; a saved lead is never touched again.
- Skips our own nicks and bots. A FloodWait stops contact saves on that nick until
  the wait is over; nothing retries on its own (use the backfill button).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time

import database as db
from database import get_db

logger = logging.getLogger("tg-scheduler.kol_contacts")

SUFFIX = "_KOL"
MAX_NAME = 64                # Telegram first_name limit
MIN_GAP_SECONDS = 4.0        # between two contact saves on one nick
BACKFILL_LIMIT = 100

_table_ready = False
_lock = asyncio.Lock()
_last_save_at: dict[int, float] = {}
_flood_until: dict[int, float] = {}
_tasks: set[asyncio.Task] = set()
_backfill = {"running": False, "total": 0, "saved": 0, "skipped": 0, "failed": 0}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def contact_name(first: str | None, last: str | None, username: str | None, user_id: int) -> str:
    """"John Doe" -> "John Doe_KOL"; falls back to @username, then the id. Max 64 chars."""
    base = " ".join(p.strip() for p in (first or "", last or "") if p and p.strip())
    base = base or (username or "").lstrip("@") or str(user_id)
    if base.upper().endswith(SUFFIX.upper()):
        base = base[: -len(SUFFIX)].rstrip()
    base = base[: MAX_NAME - len(SUFFIX)].rstrip() or str(user_id)
    return base + SUFFIX


def history_has_proof(history: list) -> bool:
    """True when the lead (role user) sent proof media in this chat."""
    for m in history or []:
        if m.get("role") != "user":
            continue
        if m.get("media") or str(m.get("content") or "").lstrip().startswith("[Attached"):
            return True
    return False


# ── Storage ───────────────────────────────────────────────────────────────────

async def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with get_db() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS kol_contacts (
                account_id      INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                username        TEXT,
                contact_name    TEXT,
                status          TEXT NOT NULL DEFAULT 'pending',
                reason          TEXT,
                proof_count     INTEGER NOT NULL DEFAULT 0,
                last_proof_link TEXT,
                saved_at        TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (account_id, user_id)
            )
        """)
        await conn.commit()
    _table_ready = True


async def _get_row(account_id: int, user_id: int) -> dict | None:
    async with get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM kol_contacts WHERE account_id = ? AND user_id = ?", (account_id, user_id))
        row = await cur.fetchone()
        if not row:
            return None
        return dict(zip([d[0] for d in cur.description], row))


async def _note_proof(account_id: int, user_id: int, username: str | None, link: str | None,
                      count_proof: bool = True) -> None:
    async with get_db() as conn:
        await conn.execute("""
            INSERT INTO kol_contacts (account_id, user_id, username, proof_count, last_proof_link)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id, user_id) DO UPDATE SET
                username = COALESCE(excluded.username, kol_contacts.username),
                proof_count = kol_contacts.proof_count + excluded.proof_count,
                last_proof_link = COALESCE(excluded.last_proof_link, kol_contacts.last_proof_link),
                updated_at = datetime('now')
        """, (account_id, user_id, username or None, 1 if count_proof else 0, link or None))
        await conn.commit()


async def _set_status(account_id: int, user_id: int, status: str, reason: str | None = None,
                      name: str | None = None) -> None:
    async with get_db() as conn:
        await conn.execute("""
            UPDATE kol_contacts SET status = ?, reason = ?,
                contact_name = COALESCE(?, contact_name),
                saved_at = CASE WHEN ? = 'saved' THEN datetime('now') ELSE saved_at END,
                updated_at = datetime('now')
            WHERE account_id = ? AND user_id = ?
        """, (status, reason, name, status, account_id, user_id))
        await conn.commit()


async def _enabled() -> bool:
    return str(await db.get_setting("kol_contact_autosave", "1") or "1").strip() not in ("0", "false", "off")


# ── Telegram ──────────────────────────────────────────────────────────────────

async def _is_internal(user_id: int) -> bool:
    try:
        import dm_reply_tracker
        return await dm_reply_tracker.is_internal_account_user(user_id)
    except Exception:
        return True  # fail closed: never touch what may be our own nick


async def save_contact(account_id: int, user_id: int, username: str | None = None) -> str:
    """Add the lead as "<name>_KOL" on `account_id`. Returns the resulting status."""
    from telethon import errors, functions
    import telegram_client as tg

    await _ensure_table()
    row = await _get_row(account_id, user_id)
    if row and row.get("status") == "saved":
        return "saved"
    if await _is_internal(user_id):
        await _set_status(account_id, user_id, "skipped", "internal")
        return "skipped"
    if _flood_until.get(account_id, 0) > time.time():
        await _set_status(account_id, user_id, "failed", "flood_wait")
        return "failed"
    client = tg.get_client(account_id)
    if client is None or not client.is_connected():
        await _set_status(account_id, user_id, "pending", "nick_offline")
        return "pending"

    async with _lock:
        wait = MIN_GAP_SECONDS - (time.time() - _last_save_at.get(account_id, 0))
        if wait > 0:
            await asyncio.sleep(wait + random.uniform(0, 2))
        try:
            entity = await client.get_entity(user_id)
            if getattr(entity, "bot", False):
                await _set_status(account_id, user_id, "skipped", "bot")
                return "skipped"
            name = contact_name(getattr(entity, "first_name", None), getattr(entity, "last_name", None),
                                getattr(entity, "username", None) or username, user_id)
            await client(functions.contacts.AddContactRequest(
                id=entity, first_name=name, last_name="", phone="",
                add_phone_privacy_exception=False))
            await _set_status(account_id, user_id, "saved", None, name)
            logger.info("[KOLContact] saved user %s as %r on nick #%s", user_id, name, account_id)
            return "saved"
        except errors.FloodWaitError as e:
            _flood_until[account_id] = time.time() + int(getattr(e, "seconds", 60) or 60)
            await _set_status(account_id, user_id, "failed", f"flood_wait_{getattr(e, 'seconds', '?')}s")
            logger.warning("[KOLContact] FloodWait %ss on nick #%s — contact saves stop on this nick",
                           getattr(e, "seconds", "?"), account_id)
            return "failed"
        except Exception as e:
            await _set_status(account_id, user_id, "failed", f"{type(e).__name__}: {e}"[:200])
            logger.warning("[KOLContact] could not save user %s on nick #%s: %s: %s",
                           user_id, account_id, type(e).__name__, e)
            return "failed"
        finally:
            _last_save_at[account_id] = time.time()


async def on_proof(account_id: int, user_id: int, username: str | None, media: dict | None) -> str:
    """Called after a proof is captured: count it, then save the lead as a contact."""
    await _ensure_table()
    await _note_proof(account_id, user_id, username, (media or {}).get("topic_link"))
    if not await _enabled():
        return "disabled"
    return await save_contact(account_id, user_id, username)


def schedule(account_id: int, user_id: int, username: str | None, media: dict | None) -> None:
    """Fire-and-forget; never raises into the caller."""
    async def _run():
        try:
            await on_proof(account_id, user_id, username, media)
        except Exception as e:
            logger.warning("[KOLContact] on_proof failed (acc=%s user=%s): %s", account_id, user_id, e)
    task = asyncio.create_task(_run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── Backfill: leads who sent proof before this feature ────────────────────────

async def backfill_candidates(limit: int = BACKFILL_LIMIT) -> list[dict]:
    await _ensure_table()
    async with get_db() as conn:
        cur = await conn.execute("""
            SELECT c.account_id, c.user_id, c.username, c.history_json
            FROM ai_followup_chats c
            LEFT JOIN kol_contacts k ON k.account_id = c.account_id AND k.user_id = c.user_id
            WHERE c.status != 'bot_ignored'
              AND (c.history_json LIKE '%"media"%' OR c.history_json LIKE '%[Attached%')
              AND (k.status IS NULL OR k.status IN ('pending', 'failed'))
            ORDER BY c.updated_at DESC
        """)
        rows = await cur.fetchall()
    out = []
    for account_id, user_id, username, hist in rows:
        try:
            history = json.loads(hist or "[]")
        except (TypeError, ValueError):
            continue
        if history_has_proof(history):
            out.append({"account_id": account_id, "user_id": user_id, "username": username})
            if len(out) >= limit:
                break
    return out


async def run_backfill(limit: int = BACKFILL_LIMIT) -> dict:
    if _backfill["running"]:
        return dict(_backfill)
    cands = await backfill_candidates(limit)
    _backfill.update(running=True, total=len(cands), saved=0, skipped=0, failed=0)

    async def _go():
        try:
            for c in cands:
                await _note_proof(c["account_id"], c["user_id"], c["username"], None, count_proof=False)
                status = await save_contact(c["account_id"], c["user_id"], c["username"])
                key = {"saved": "saved", "skipped": "skipped"}.get(status, "failed")
                _backfill[key] += 1
        finally:
            _backfill["running"] = False
            logger.info("[KOLContact] backfill done: %s", _backfill)

    task = asyncio.create_task(_go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return dict(_backfill)


def backfill_status() -> dict:
    return dict(_backfill)


async def list_contacts(limit: int = 500) -> list[dict]:
    await _ensure_table()
    async with get_db() as conn:
        cur = await conn.execute("""
            SELECT k.*, a.name AS account_name
            FROM kol_contacts k LEFT JOIN accounts a ON a.id = k.account_id
            ORDER BY COALESCE(k.saved_at, k.updated_at) DESC LIMIT ?
        """, (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in await cur.fetchall()]
