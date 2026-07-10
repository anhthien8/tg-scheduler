"""
Discord Bot Adapter
───────────────────
Multi-bot management for Discord.  Each "account" is a Discord bot token.
Uses discord.py library with Gateway intents for real-time events.

Architecture
~~~~~~~~~~~~
* One :class:`discord.Client` per ``account_id``.
* Each client runs ``bot.start(token)`` in its own :class:`asyncio.Task`
  (the Gateway connection blocks forever).
* ``connect_bot`` waits for the ``on_ready`` event (max 30 s) so the caller
  knows the bot is live before returning.
* ``disconnect_bot`` cleanly closes the websocket + cancels the task.

Example
~~~~~~~
::

    adapter = DiscordAdapter()
    ok = await adapter.connect_bot(account_id=1, bot_token="MTIz...")
    if ok:
        await adapter.send_to_channel(1, 123456789, "Hello from TG Scheduler!")
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

import discord
from discord import Intents

from platforms.base import BasePlatformClient

logger = logging.getLogger("tg-scheduler.discord")


class DiscordAdapter(BasePlatformClient):
    """
    Platform adapter that wraps one or more Discord bot clients.

    Each ``account_id`` maps to a unique :class:`discord.Client` instance
    authenticated with the corresponding bot token.
    """

    platform = "discord"

    def __init__(self) -> None:
        # account_id → discord.Client
        self._bots: dict[int, discord.Client] = {}
        # account_id → bot token (kept for potential reconnection)
        self._tokens: dict[int, str] = {}
        # account_id → asyncio.Task running bot.start()
        self._tasks: dict[int, asyncio.Task] = {}
        # account_id → list of registered handler references
        self._handlers: dict[int, list[Any]] = {}

    # ── Connection Management ────────────────────────────────────

    async def connect_bot(self, account_id: int, bot_token: str) -> bool:
        """
        Create a :class:`discord.Client`, start the Gateway connection in a
        background task, and wait up to 30 s for the ``on_ready`` event.

        Parameters
        ----------
        account_id:
            Internal DB id that uniquely identifies this bot slot.
        bot_token:
            The Discord bot token (from the Developer Portal).

        Returns
        -------
        bool
            ``True`` if the bot reached ``on_ready`` within the timeout.
        """
        # Tear down any stale session for this account_id first
        if account_id in self._bots:
            await self.disconnect_bot(account_id)

        intents = Intents.default()
        intents.message_content = True   # privileged — required for reading text
        intents.members = True           # privileged — required for DM / member info
        intents.guilds = True

        bot = discord.Client(intents=intents)
        self._bots[account_id] = bot
        self._tokens[account_id] = bot_token
        self._handlers[account_id] = []

        # Signalled when the Gateway fires ``on_ready``
        ready_event = asyncio.Event()

        @bot.event
        async def on_ready() -> None:
            logger.info(
                "Discord bot %d: logged in as %s (guilds: %d)",
                account_id,
                bot.user,
                len(bot.guilds),
            )
            ready_event.set()

        @bot.event
        async def on_error(event: str, *args: Any, **kwargs: Any) -> None:
            logger.error(
                "Discord bot %d: unhandled error in event '%s'",
                account_id,
                event,
                exc_info=True,
            )

        # Launch the blocking ``bot.start()`` in a background task
        task = asyncio.create_task(
            self._run_bot(account_id, bot, bot_token),
            name=f"discord-bot-{account_id}",
        )
        self._tasks[account_id] = task

        # Wait for the bot to become ready
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=30)
            return True
        except asyncio.TimeoutError:
            logger.error("Discord bot %d: connect timeout (30 s)", account_id)
            # Clean up the half-started bot
            await self.disconnect_bot(account_id)
            return False

    async def _run_bot(
        self,
        account_id: int,
        bot: discord.Client,
        token: str,
    ) -> None:
        """
        Run ``bot.start(token)`` — this coroutine never returns normally;
        it keeps the Gateway connection alive until ``bot.close()`` or
        the task is cancelled.
        """
        try:
            await bot.start(token)
        except discord.LoginFailure:
            logger.error("Discord bot %d: invalid token", account_id)
        except asyncio.CancelledError:
            logger.debug("Discord bot %d: task cancelled", account_id)
        except Exception:
            logger.error(
                "Discord bot %d: unexpected error in _run_bot",
                account_id,
                exc_info=True,
            )
        finally:
            if not bot.is_closed():
                await bot.close()

    async def disconnect_bot(self, account_id: int) -> None:
        """
        Disconnect and clean up a specific bot.

        Safe to call even if the account was never connected.
        """
        bot = self._bots.pop(account_id, None)
        task = self._tasks.pop(account_id, None)
        self._tokens.pop(account_id, None)
        self._handlers.pop(account_id, None)

        if bot and not bot.is_closed():
            try:
                await bot.close()
            except Exception:
                logger.debug(
                    "Discord bot %d: error during close (ignored)",
                    account_id,
                    exc_info=True,
                )

        if task and not task.done():
            task.cancel()
            # Give the task a moment to handle CancelledError
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        logger.info("Discord bot %d: disconnected", account_id)

    # ── BasePlatformClient — Messaging ───────────────────────────

    async def send_dm(
        self,
        account_id: int,
        user_id: int | str,
        text: str,
        media_path: str | None = None,
    ) -> bool:
        """
        Send a direct message to a Discord user.

        The bot must share at least one guild with the target user *and*
        the user must have DMs enabled for that guild.

        Parameters
        ----------
        account_id:
            Bot account that should send the message.
        user_id:
            Target Discord user snowflake ID.
        text:
            Message body text.
        media_path:
            Optional path to a local file to attach.

        Returns
        -------
        bool
            ``True`` on success.

        Raises
        ------
        Exception
            If the bot is not connected, the user is not found, or Discord
            returns a Forbidden / HTTP error.
        """
        bot = self._bots.get(account_id)
        if not bot:
            raise Exception(f"Discord bot {account_id} not connected")

        try:
            user = await bot.fetch_user(int(user_id))
        except discord.NotFound:
            raise Exception(f"User {user_id} not found")
        except discord.HTTPException as exc:
            logger.error(
                "Discord bot %d: failed to fetch user %s: %s",
                account_id, user_id, exc,
            )
            raise

        try:
            if media_path and os.path.isfile(media_path):
                await user.send(
                    content=text or None,
                    file=discord.File(media_path),
                )
            else:
                await user.send(text)
            return True
        except discord.Forbidden:
            logger.warning(
                "Discord bot %d: cannot DM user %s "
                "(no shared server or DMs disabled)",
                account_id, user_id,
            )
            raise Exception(
                "Cannot DM user — no shared server or DMs disabled"
            )
        except discord.HTTPException as exc:
            logger.error(
                "Discord bot %d: DM to user %s failed: %s",
                account_id, user_id, exc,
            )
            raise

    async def send_to_channel(
        self,
        account_id: int,
        channel_id: int | str,
        text: str,
        media_path: str | None = None,
    ) -> bool:
        """
        Send a message to a Discord text channel.

        Parameters
        ----------
        account_id:
            Bot account that should send the message.
        channel_id:
            Target channel snowflake ID.
        text:
            Message body text.
        media_path:
            Optional path to a local file to attach.

        Returns
        -------
        bool
            ``True`` on success.

        Raises
        ------
        Exception
            If the bot is not connected, the channel is not found, or the
            bot lacks send permissions.
        """
        bot = self._bots.get(account_id)
        if not bot:
            raise Exception(f"Discord bot {account_id} not connected")

        try:
            channel = bot.get_channel(int(channel_id))
            if channel is None:
                channel = await bot.fetch_channel(int(channel_id))
        except discord.NotFound:
            raise Exception(f"Channel {channel_id} not found")
        except discord.Forbidden:
            raise Exception(
                f"Discord bot {account_id}: no access to channel {channel_id}"
            )
        except discord.HTTPException as exc:
            logger.error(
                "Discord bot %d: failed to fetch channel %s: %s",
                account_id, channel_id, exc,
            )
            raise

        try:
            if media_path and os.path.isfile(media_path):
                await channel.send(
                    content=text or None,
                    file=discord.File(media_path),
                )
            else:
                await channel.send(text)
            return True
        except discord.Forbidden:
            logger.error(
                "Discord bot %d: no permission to send in channel %s",
                account_id, channel_id,
            )
            raise Exception(
                f"Bot lacks 'Send Messages' permission in channel {channel_id}"
            )
        except discord.HTTPException as exc:
            logger.error(
                "Discord bot %d: send to channel %s failed: %s",
                account_id, channel_id, exc,
            )
            raise

    # ── BasePlatformClient — Reactions ───────────────────────────

    async def add_reaction(
        self,
        account_id: int,
        channel_id: int | str,
        msg_id: int,
        emoji: str,
    ) -> bool:
        """
        Add an emoji reaction to a message.

        Parameters
        ----------
        account_id:
            Bot account to act with.
        channel_id:
            Channel containing the target message.
        msg_id:
            Message snowflake ID.
        emoji:
            Unicode emoji (e.g. ``"👍"``) or a custom emoji string
            (``"<:name:id>"``).

        Returns
        -------
        bool
            ``True`` on success.
        """
        bot = self._bots.get(account_id)
        if not bot:
            raise Exception(f"Discord bot {account_id} not connected")

        try:
            channel = bot.get_channel(int(channel_id))
            if channel is None:
                channel = await bot.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            logger.error(
                "Discord bot %d: failed to fetch channel %s for reaction: %s",
                account_id, channel_id, exc,
            )
            raise

        try:
            message = await channel.fetch_message(msg_id)
        except discord.NotFound:
            raise Exception(f"Message {msg_id} not found in channel {channel_id}")
        except discord.HTTPException as exc:
            logger.error(
                "Discord bot %d: failed to fetch message %d: %s",
                account_id, msg_id, exc,
            )
            raise

        try:
            await message.add_reaction(emoji)
            return True
        except discord.Forbidden:
            logger.error(
                "Discord bot %d: no permission to react in channel %s",
                account_id, channel_id,
            )
            raise Exception(
                f"Bot lacks 'Add Reactions' permission in channel {channel_id}"
            )
        except discord.HTTPException as exc:
            logger.error(
                "Discord bot %d: reaction error on msg %d: %s",
                account_id, msg_id, exc,
            )
            raise

    # ── BasePlatformClient — Event Handlers ──────────────────────

    async def register_message_handler(
        self,
        account_id: int,
        channel_ids: list,
        callback: Callable,
    ) -> None:
        """
        Register a callback that fires on every new message in the
        specified channels.

        The *callback* signature must be::

            async def callback(account_id: int, message: discord.Message) -> None

        Messages from the bot itself are automatically filtered out.

        Parameters
        ----------
        account_id:
            Bot account to install the handler on.
        channel_ids:
            List of channel snowflake IDs to listen to.
        callback:
            Coroutine invoked for each matching message.
        """
        bot = self._bots.get(account_id)
        if not bot:
            logger.warning(
                "Discord bot %d: cannot register handler — bot not connected",
                account_id,
            )
            return

        channel_id_set: set[int] = {int(c) for c in channel_ids}

        @bot.event
        async def on_message(message: discord.Message) -> None:
            # Never react to the bot's own messages
            if message.author == bot.user:
                return
            # Only fire for the configured channels
            if message.channel.id not in channel_id_set:
                return
            try:
                await callback(account_id, message)
            except Exception:
                logger.error(
                    "Discord bot %d: message handler error in channel %s",
                    account_id,
                    message.channel.id,
                    exc_info=True,
                )

        self._handlers.setdefault(account_id, []).append(on_message)

    # ── BasePlatformClient — Account Introspection ───────────────

    async def get_account_info(self, account_id: int) -> dict | None:
        """
        Return basic profile info for the connected bot, or ``None``
        if the bot is not ready.

        Returns
        -------
        dict | None
            Keys: ``user_id``, ``username``, ``display_name``,
            ``guild_count``.
        """
        bot = self._bots.get(account_id)
        if not bot or not bot.user:
            return None
        return {
            "user_id": bot.user.id,
            "username": str(bot.user),
            "display_name": bot.user.display_name,
            "guild_count": len(bot.guilds),
        }

    async def is_connected(self, account_id: int) -> bool:
        """Return ``True`` if the bot's Gateway connection is live and ready."""
        bot = self._bots.get(account_id)
        if bot is None:
            return False
        return not bot.is_closed() and bot.is_ready()

    # ── BasePlatformClient — Lifecycle ───────────────────────────

    async def disconnect_all(self) -> None:
        """
        Gracefully disconnect every managed Discord bot.

        Safe to call at application shutdown — errors on individual bots
        are logged but do not prevent other bots from shutting down.
        """
        account_ids = list(self._bots.keys())
        for account_id in account_ids:
            try:
                await self.disconnect_bot(account_id)
            except Exception:
                logger.error(
                    "Discord bot %d: error during disconnect_all",
                    account_id,
                    exc_info=True,
                )
        logger.info("All Discord bots disconnected (%d total)", len(account_ids))

    # ── Discord-specific helpers ─────────────────────────────────

    def get_bot(self, account_id: int) -> discord.Client | None:
        """
        Return the raw :class:`discord.Client` for advanced usage,
        or ``None`` if the account is not connected.
        """
        return self._bots.get(account_id)

    async def get_guilds(self, account_id: int) -> list[dict]:
        """
        Return a list of guilds (servers) the bot is a member of,
        including their text channels.

        Returns
        -------
        list[dict]
            Each dict contains ``id``, ``name``, ``member_count``, and
            ``channels`` (list of dicts with ``id``, ``name``,
            ``category``).
        """
        bot = self._bots.get(account_id)
        if not bot:
            return []

        result: list[dict] = []
        for guild in bot.guilds:
            channels: list[dict] = []
            for ch in guild.text_channels:
                channels.append({
                    "id": ch.id,
                    "name": ch.name,
                    "category": ch.category.name if ch.category else None,
                })
            result.append({
                "id": guild.id,
                "name": guild.name,
                "member_count": guild.member_count,
                "channels": channels,
            })
        return result
