"""Kiểm tra guard nội bộ, không kết nối Telegram thật."""
from unittest.mock import AsyncMock, patch

import pytest

import dm_reply_tracker as tracker


@pytest.fixture(autouse=True)
async def _close_db_pool():
    """generate_and_send_ai_reply_for_chat mở pool aiosqlite thật; mỗi
    connection là một Thread non-daemon nên nếu không đóng, pytest in kết quả
    xong vẫn treo mãi không thoát (run_tests.py không bao giờ kết thúc)."""
    yield
    await tracker.db.close_db()


def _reset_cache():
    tracker._internal_user_ids_cache.clear()
    tracker._internal_user_ids_loaded_at = 0


async def test_internal_user_guard_blocks_internal_and_sends_nothing():
    """Internal accounts must be recognized; AI must never send to them."""
    _reset_cache()
    with patch.dict(tracker.tg._me_cache, {}, clear=True), patch.object(
        tracker.db, 'get_all_internal_telegram_user_ids', AsyncMock(return_value={111, 222})
    ), patch.object(tracker.db, 'update_followup_chat_status', AsyncMock()), patch.object(
        tracker.tg, 'send_text_message', AsyncMock()
    ) as send:
        assert await tracker.is_internal_account_user(111)
        assert not await tracker.is_internal_account_user(333)
        assert not await tracker.generate_and_send_ai_reply_for_chat(2, 111)
        assert not await tracker._send_ai_message(2, 222, 'Không được gửi')
        assert not await tracker._send_ai_message(tracker.MAIN_ACCOUNT_ID, 333, 'Không được gửi')
        send.assert_not_awaited()
    _reset_cache()


async def test_internal_lookup_fails_safe_when_db_unavailable():
    """If the identity lookup errors, treat the user as internal (fail-closed)."""
    _reset_cache()
    with patch.object(
        tracker.db, 'get_all_internal_telegram_user_ids',
        AsyncMock(side_effect=RuntimeError('DB unavailable')),
    ):
        assert await tracker.is_internal_account_user(333)
    _reset_cache()
