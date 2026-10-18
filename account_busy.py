"""
account_busy.py — Per-account busy registry with TTL.

Single source of truth for "which account is currently doing what".
Prevents engagement replies from colliding with DM sends, funnel AI, invites, etc.
Design doc §4.5.1.
"""
import asyncio
import contextlib
import logging
import time

logger = logging.getLogger("tg-scheduler.account_busy")

_busy_until: dict[int, float] = {}      # account_id -> monotonic deadline
_busy_kind: dict[int, str] = {}          # account_id -> 'dm'|'funnel'|'invite'|'warmup'|'engagement'


class AccountBusy(Exception):
    """Raised when an account is already busy with another task."""
    def __init__(self, account_id: int, current_kind: str):
        self.account_id = account_id
        self.current_kind = current_kind
        super().__init__(f"Account {account_id} busy with {current_kind}")


def is_busy(account_id: int) -> bool:
    """Check if an account is currently busy (within TTL)."""
    until = _busy_until.get(account_id, 0)
    return until > time.monotonic()


def get_busy_kind(account_id: int) -> str | None:
    """Get what task type an account is busy with."""
    if not is_busy(account_id):
        return None
    return _busy_kind.get(account_id)


@contextlib.asynccontextmanager
async def acquire(account_id: int, kind: str, ttl_secs: float = 90):
    """Mark an account as busy. Raises AccountBusy if already in use."""
    if is_busy(account_id):
        raise AccountBusy(account_id, _busy_kind.get(account_id, "?"))
    _busy_until[account_id] = time.monotonic() + ttl_secs
    _busy_kind[account_id] = kind
    try:
        yield
    finally:
        _busy_until.pop(account_id, None)
        _busy_kind.pop(account_id, None)


def release(account_id: int) -> None:
    """Manually release a busy account (e.g. after error)."""
    _busy_until.pop(account_id, None)
    _busy_kind.pop(account_id, None)


def get_all_busy() -> dict[int, str]:
    """Return dict of all currently busy accounts for UI display."""
    now = time.monotonic()
    return {
        acc_id: _busy_kind.get(acc_id, "?")
        for acc_id, until in _busy_until.items()
        if until > now
    }
