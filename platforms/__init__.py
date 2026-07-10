"""
platforms — Platform Abstraction Layer
======================================

Registry of platform adapters and factory helper.

Usage::

    from platforms import get_adapter

    tg = get_adapter("telegram")
    await tg.send_dm(account_id=1, user_id=123, text="hello")
"""
from platforms.base import BasePlatformClient
from platforms.telegram_adapter import TelegramAdapter
from platforms.discord_adapter import DiscordAdapter

# ── Registry ────────────────────────────────────────────────────
# Maps lowercase platform name → adapter class.
# Add new adapters here as they are implemented.
ADAPTERS: dict[str, type[BasePlatformClient]] = {
    "telegram": TelegramAdapter,
    "discord": DiscordAdapter,
}



def get_adapter(platform: str) -> BasePlatformClient:
    """
    Instantiate and return the adapter for *platform*.

    Raises ``ValueError`` if no adapter is registered for the name.
    """
    key = platform.strip().lower()
    cls = ADAPTERS.get(key)
    if cls is None:
        registered = ", ".join(sorted(ADAPTERS)) or "(none)"
        raise ValueError(
            f"Unknown platform '{platform}'. "
            f"Registered adapters: {registered}"
        )
    return cls()
