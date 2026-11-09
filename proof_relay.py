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
