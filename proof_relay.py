"""Volume proof (photo / video / file) a lead sends in DM.

Runs in the background after the message is stored, so it never delays the AI reply:
  1. downloads the media with the receiving account,
  2. posts it into the lead's topic in the forum support group (via the command bot),
  3. tags the chat-history entry so the dashboard shows the photo inline, or a
     t.me link to the topic message for videos/files.
Photos are kept on disk (small, shown in the app); videos/files are deleted after upload.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import database as db

logger = logging.getLogger("tg-scheduler.proof_relay")

MEDIA_DIR = os.path.join(db.DB_DIR, "proof_media")
MAX_BYTES = 200 * 1024 * 1024  # bigger files are only flagged, not relayed
_SAFE_NAME = re.compile(r"^[0-9]+_[0-9]+_[0-9]+\.[a-z0-9]{2,5}$")
_tasks: set[asyncio.Task] = set()


def media_kind(message) -> str | None:
    """photo / video / document for proof media; None for stickers, GIFs, dice or no media."""
    if getattr(message, "sticker", None) or getattr(message, "gif", None) or getattr(message, "dice", None):
        return None
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None) or getattr(message, "video_note", None):
        return "video"
    doc = getattr(message, "document", None)
    if doc is not None:
        mime = (getattr(doc, "mime_type", "") or "").lower()
        return "photo" if mime.startswith("image/") else "document"
    return None


def safe_media_path(name: str) -> str | None:
    """Disk path for a stored proof image, or None if the name is not one we wrote."""
    if not _SAFE_NAME.match(name or ""):
        return None
    path = os.path.join(MEDIA_DIR, name)
    return path if os.path.isfile(path) else None


def _file_size(message) -> int:
    doc = getattr(message, "document", None)
    return int(getattr(doc, "size", 0) or 0) if doc is not None else 0


def schedule(account_id: int, user_id: int, username: str | None, name: str | None,
             message, timestamp: str) -> None:
    """Fire-and-forget capture; safe to call from the inbound handler."""
    kind = media_kind(message)
    if not kind or not timestamp:
        return
    task = asyncio.create_task(capture(account_id, user_id, username, name, message, timestamp, kind))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def capture(account_id: int, user_id: int, username: str | None, name: str | None,
                  message, timestamp: str, kind: str) -> dict:
    media: dict = {"type": kind}
    path = None
    keep = False
    try:
        if _file_size(message) > MAX_BYTES:
            media["error"] = "too_large"
            return media
        os.makedirs(MEDIA_DIR, exist_ok=True)
        base = os.path.join(MEDIA_DIR, f"{account_id}_{user_id}_{int(message.id)}")
        path = await message.download_media(file=base)
        if not path:
            media["error"] = "download_failed"
            return media
        if kind == "photo":
            ext = os.path.splitext(path)[1].lower().lstrip(".")
            if ext in ("jpg", "jpeg", "png", "webp", "gif"):
                keep = True
                media["image_url"] = f"/api/ai-followup/proof-media/{os.path.basename(path)}"

        import command_bot
        result = await command_bot.relay_proof_to_forum(account_id, user_id, username, name, path, kind)
        if result.get("link"):
            media["topic_link"] = result["link"]
        else:
            media["forward"] = result.get("reason")
        return media
    except Exception as e:
        logger.warning("[ProofRelay] capture failed (acc=%s user=%s): %s: %s",
                       account_id, user_id, type(e).__name__, e)
        media["error"] = "error"
        return media
    finally:
        if path and not keep:
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            await db.set_followup_message_media(account_id, user_id, timestamp, media)
        except Exception as e:
            logger.warning("[ProofRelay] could not tag history (acc=%s user=%s): %s", account_id, user_id, e)
        logger.info("[ProofRelay] %s from user %s (acc=%s): %s", kind, user_id, account_id, media)
        try:
            import kol_contacts  # v3.40: save the lead as "<name>_KOL" on this nick
            kol_contacts.schedule(account_id, user_id, username, media)
        except Exception as e:
            logger.warning("[ProofRelay] KOL contact schedule failed (acc=%s user=%s): %s", account_id, user_id, e)


_BACKFILL_DONE: set[tuple[int, int]] = set()


async def backfill_chat(account_id: int, user_id: int) -> int:
    """Old proof entries ("[Attached … Proof]" stored before media capture existed):
    find the photo in the Telegram dialog by time and store it so the dashboard shows it.
    Runs once per chat per process; nothing is posted to the forum group."""
    from datetime import datetime, timezone, timedelta
    import telegram_client as tg
    if (account_id, user_id) in _BACKFILL_DONE:
        return 0
    chat = await db.get_followup_chat(account_id, user_id)
    todo = [m for m in (chat or {}).get("history") or []
            if m.get("role") == "user" and not m.get("media") and m.get("timestamp")
            and str(m.get("content") or "").lstrip().startswith("[Attached")]
    client = tg.get_client(account_id)
    if not todo or client is None:
        _BACKFILL_DONE.add((account_id, user_id))
        return 0
    try:
        msgs = [m for m in await client.get_messages(user_id, limit=300)
                if not m.out and media_kind(m) == "photo"]
    except Exception as e:
        logger.info("[ProofRelay] backfill fetch failed (acc=%s user=%s): %s", account_id, user_id, e)
        return 0
    _BACKFILL_DONE.add((account_id, user_id))
    used, n = set(), 0
    os.makedirs(MEDIA_DIR, exist_ok=True)
    for entry in todo:
        ts = datetime.fromisoformat(entry["timestamp"])
        when = ts.astimezone(timezone.utc) if ts.tzinfo is None else ts  # stored as server-local time
        best = min((m for m in msgs if m.id not in used), default=None,
                   key=lambda m: abs((m.date - when).total_seconds()))
        if best is None or abs((best.date - when).total_seconds()) > 180:
            continue
        used.add(best.id)
        try:
            path = await best.download_media(file=os.path.join(MEDIA_DIR, f"{account_id}_{user_id}_{int(best.id)}"))
        except Exception:
            path = None
        ext = os.path.splitext(path or "")[1].lower().lstrip(".")
        if not path or ext not in ("jpg", "jpeg", "png", "webp", "gif"):
            continue
        await db.set_followup_message_media(account_id, user_id, entry["timestamp"], {
            "type": "photo", "image_url": f"/api/ai-followup/proof-media/{os.path.basename(path)}"})
        n += 1
    return n
