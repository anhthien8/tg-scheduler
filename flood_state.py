"""Process-wide FloodWait registry shared by campaigns, watchers and nudges.

Each sender used to remember a FloodWait only inside its own loop, so another
campaign or watcher kept using the same account and hit FloodWait again —
and Telegram lengthens the wait on every repeat.
"""
import time

_until: dict[int, float] = {}


def mark(account_id: int, seconds: float) -> None:
    """Rest `account_id` for `seconds` (never shortens an existing rest)."""
    end = time.time() + max(0.0, float(seconds))
    if end > _until.get(account_id, 0.0):
        _until[account_id] = end


def remaining(account_id: int) -> float:
    return max(0.0, _until.get(account_id, 0.0) - time.time())


def is_resting(account_id: int) -> bool:
    return remaining(account_id) > 0
