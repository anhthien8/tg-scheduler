"""A watcher whose senders are all unavailable must say why.

Before: a paused sender was skipped with a debug log only. The rule detected the
join/keyword, sent nothing, and the UI showed "—" (no failure, no reason).
Now one 'skipped' log row carries the reason, and the error summary exposes it.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import database as db
import keyword_watcher as kw
import telegram_client as tg

USER = 987654


async def _setup() -> tuple[int, int]:
    ACC = await db.create_account({
        "name": "Will", "phone": "+840031", "api_id": "x",
        "api_hash": "y", "session_name": "will31",
    })
    wid = await db.create_watcher({
        "name": "VN COCO", "sender_account_ids": [ACC], "observer_account_ids": [ACC],
        "keywords": [], "group_ids": [3108480914], "cooldown_hours": 24, "dm_once": True,
        "excluded_usernames": [], "is_active": True, "watch_type": "join",
    })
    return wid, ACC


async def _send(wid: int, ACC: int):
    with patch.object(tg, "get_client", MagicMock(return_value=MagicMock()), create=True), \
         patch.object(tg, "ensure_connected", AsyncMock(return_value=True), create=True):
        return await kw._do_send_dm_with_fallback(
            [ACC], USER, [{"msg_type": "text", "content": "hi"}], wid,
            "newbie", 3108480914, "COCO CAMPAIGN", "[JOIN]", None, MagicMock())


async def test_paused_sender_leaves_a_skipped_row_with_the_reason():
    wid, ACC = await _setup()
    await db.pause_account(ACC, "PeerFlood — tự động tạm dừng để bảo vệ tài khoản")
    try:
        ok, used, err = await _send(wid, ACC)
    finally:
        await db.unpause_account(ACC)
    assert ok is False and used is None
    assert "Will" in err and "tạm dừng" in err and "PeerFlood" in err

    logs = await db.get_watcher_dm_logs(watcher_id=wid)
    rows = logs["logs"] if isinstance(logs, dict) else logs
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped" and "tạm dừng" in rows[0]["error_message"]

    summary = (await db.get_watcher_error_summary())[wid]
    assert summary["failed"] == 0            # not a failure: no auto-pause, no strike on the target
    assert summary["skipped_24h"] == 1
    assert "Will" in summary["last_skip_reason"]
    assert (await db.get_watcher(wid))["is_active"]


async def test_skipped_row_does_not_count_as_target_failure():
    wid, ACC = await _setup()
    await db.pause_account(ACC, "PeerFlood")
    try:
        for _ in range(6):
            await _send(wid, ACC)
    finally:
        await db.unpause_account(ACC)
    assert await db.count_user_dm_failures(wid, USER, hours=24) == 0
    # dm_once rule: the newcomer is still eligible once a sender is back
    assert await db.was_user_dmed_recently(wid, USER, 24, dm_once=True) is False
