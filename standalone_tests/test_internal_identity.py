"""Kiểm tra guard nội bộ, không kết nối Telegram thật."""
import asyncio
from unittest.mock import AsyncMock, patch
import dm_reply_tracker as tracker

async def check():
    tracker._internal_user_ids_cache.clear()
    tracker._internal_user_ids_loaded_at = 0
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
    tracker._internal_user_ids_cache.clear()
    tracker._internal_user_ids_loaded_at = 0
    with patch.object(tracker.db, 'get_all_internal_telegram_user_ids', AsyncMock(side_effect=RuntimeError('DB unavailable'))):
        assert await tracker.is_internal_account_user(333)
    print('PASS: cache rỗng, ID bền vững, khách ngoài, chặn generate/send/main, DB lỗi.')

if __name__ == '__main__':
    asyncio.run(check())
