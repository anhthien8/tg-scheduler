"""
Platform Abstraction Layer — Base Class
Defines the interface contract for all platform adapters.
"""
from abc import ABC, abstractmethod
from typing import Any, Callable


class BasePlatformClient(ABC):
    """
    Abstract base for every platform adapter.

    Subclasses MUST set `platform` to a lowercase identifier
    (e.g. 'telegram', 'discord', 'whatsapp', 'x') and implement
    every abstract method.
    """

    platform: str  # 'telegram', 'discord', 'whatsapp', 'x'

    # ── Messaging ───────────────────────────────────────────────

    @abstractmethod
    async def send_dm(
        self,
        account_id: int,
        user_id: int | str,
        text: str,
        media_path: str | None = None,
    ) -> bool:
        """Send a direct / private message to a user."""
        ...

    @abstractmethod
    async def send_to_channel(
        self,
        account_id: int,
        channel_id: int | str,
        text: str,
        media_path: str | None = None,
    ) -> bool:
        """Send a message to a group / channel / server-channel."""
        ...

    # ── Reactions ───────────────────────────────────────────────

    @abstractmethod
    async def add_reaction(
        self,
        account_id: int,
        channel_id: int | str,
        msg_id: int,
        emoji: str,
    ) -> bool:
        """React to a message with the given emoji."""
        ...

    # ── Event Handlers ──────────────────────────────────────────

    @abstractmethod
    async def register_message_handler(
        self,
        account_id: int,
        channel_ids: list,
        callback: Callable,
    ) -> None:
        """
        Register a callback that fires on every new message in
        the specified channels / groups.
        """
        ...

    # ── Account Introspection ───────────────────────────────────

    @abstractmethod
    async def get_account_info(self, account_id: int) -> dict | None:
        """Return basic profile info for the account, or None."""
        ...

    @abstractmethod
    async def is_connected(self, account_id: int) -> bool:
        """Return True if the account is online and authorised."""
        ...

    # ── Lifecycle ───────────────────────────────────────────────

    @abstractmethod
    async def disconnect_all(self) -> None:
        """Gracefully disconnect every managed account/session."""
        ...
