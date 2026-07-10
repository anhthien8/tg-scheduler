"""
Telegram Platform Adapter
Wraps the existing telegram_client module behind the
BasePlatformClient interface.
"""
import logging
import os
from typing import Callable

from platforms.base import BasePlatformClient

logger = logging.getLogger("tg-scheduler.platforms.telegram")


class TelegramAdapter(BasePlatformClient):
    """Adapter that delegates to :mod:`telegram_client`."""

    platform = "telegram"

    # ── Messaging ───────────────────────────────────────────────

    async def send_dm(
        self,
        account_id: int,
        user_id: int | str,
        text: str,
        media_path: str | None = None,
    ) -> bool:
        """
        Send a DM to a Telegram user.

        If *media_path* is provided the file type is inferred from the
        extension and the appropriate ``send_*_message`` helper is used.
        """
        import telegram_client as tg

        try:
            if media_path and os.path.isfile(media_path):
                return await self._send_with_media(
                    tg, account_id, int(user_id), media_path, caption=text,
                )
            return await tg.send_text_message(account_id, int(user_id), text)
        except Exception as exc:
            logger.error(
                "send_dm failed — account=%s user=%s: %s",
                account_id, user_id, exc,
            )
            return False

    async def send_to_channel(
        self,
        account_id: int,
        channel_id: int | str,
        text: str,
        media_path: str | None = None,
    ) -> bool:
        """Send a message to a Telegram group / channel."""
        import telegram_client as tg

        try:
            if media_path and os.path.isfile(media_path):
                return await self._send_with_media(
                    tg, account_id, int(channel_id), media_path, caption=text,
                )
            return await tg.send_text_message(account_id, int(channel_id), text)
        except Exception as exc:
            logger.error(
                "send_to_channel failed — account=%s channel=%s: %s",
                account_id, channel_id, exc,
            )
            return False

    # ── Reactions ───────────────────────────────────────────────

    async def add_reaction(
        self,
        account_id: int,
        channel_id: int | str,
        msg_id: int,
        emoji: str,
    ) -> bool:
        """
        Placeholder — Telegram reactions are dispatched directly via
        ``SendReactionRequest`` inside ``reaction_watcher.py``.
        """
        logger.warning(
            "add_reaction is a placeholder for Telegram. "
            "Use reaction_watcher for real reaction logic."
        )
        return False

    # ── Event Handlers ──────────────────────────────────────────

    async def register_message_handler(
        self,
        account_id: int,
        channel_ids: list,
        callback: Callable,
    ) -> None:
        """
        Placeholder — Telegram uses Telethon event decorators
        registered directly in ``keyword_watcher.py``.
        """
        logger.warning(
            "register_message_handler is a placeholder for Telegram. "
            "Use keyword_watcher for Telethon-based event handling."
        )

    # ── Account Introspection ───────────────────────────────────

    async def get_account_info(self, account_id: int) -> dict | None:
        """Delegate to ``telegram_client.get_me``."""
        import telegram_client as tg

        try:
            return await tg.get_me(account_id)
        except Exception as exc:
            logger.error("get_account_info failed — account=%s: %s", account_id, exc)
            return None

    async def is_connected(self, account_id: int) -> bool:
        """Delegate to ``telegram_client.is_authorized``."""
        import telegram_client as tg

        try:
            return await tg.is_authorized(account_id)
        except Exception:
            return False

    # ── Lifecycle ───────────────────────────────────────────────

    async def disconnect_all(self) -> None:
        """Delegate to ``telegram_client.disconnect_all``."""
        import telegram_client as tg

        await tg.disconnect_all()

    # ── Private Helpers ─────────────────────────────────────────

    @staticmethod
    async def _send_with_media(
        tg,
        account_id: int,
        chat_id: int,
        media_path: str,
        caption: str = "",
    ) -> bool:
        """Pick the right sender based on file extension."""
        ext = os.path.splitext(media_path)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            return await tg.send_photo_message(account_id, chat_id, media_path, caption)
        elif ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
            return await tg.send_video_message(account_id, chat_id, media_path, caption)
        else:
            return await tg.send_document_message(account_id, chat_id, media_path, caption)
