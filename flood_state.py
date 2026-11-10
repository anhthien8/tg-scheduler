"""Process-wide rate-limit state shared by every engine.

FloodWait: each sender used to remember a FloodWait only inside its own loop,
so another campaign or watcher kept using the same account and hit FloodWait
again — and Telegram lengthens the wait on every repeat.

PeerFlood: the escalating cooldown used to live in keyword_watcher only and was
never cleared when @SpamBot reported the account clean again.

`blocked_reason()` is the single "may this account act now?" check.
"""
import time

_until: dict[int, float] = {}
# account_id -> unix time the PeerFlood cooldown ends / consecutive PeerFlood count.
# keyword_watcher aliases these two dicts, so there is one source of truth.
peerflood_until: dict[int, float] = {}
peerflood_count: dict[int, int] = {}


def mark(account_id: int, seconds: float) -> None:
    """Rest `account_id` for `seconds` (never shortens an existing rest)."""
    end = time.time() + max(0.0, float(seconds))
    if end > _until.get(account_id, 0.0):
        _until[account_id] = end


def remaining(account_id: int) -> float:
    return max(0.0, _until.get(account_id, 0.0) - time.time())


def is_resting(account_id: int) -> bool:
    return remaining(account_id) > 0


def peerflood_blocked(account_id: int) -> bool:
    return peerflood_until.get(account_id, 0.0) > time.time()


def clear_peerflood(account_id: int) -> None:
    """Called when @SpamBot reports the account free again."""
    peerflood_until.pop(account_id, None)
    peerflood_count.pop(account_id, None)


async def blocked_reason(account_id: int) -> str | None:
    """Why this account must not send/act right now, or None when it may."""
    if is_resting(account_id):
        return f"FloodWait còn {int(remaining(account_id))}s"
    if peerflood_blocked(account_id):
        return "PeerFlood cooldown"
    import database as db
    if await db.is_account_paused(account_id):
        return "tài khoản đang tạm dừng"
    return None
