import pytest
from unittest.mock import AsyncMock, patch

import database as db
import dm_reply_tracker as tracker
from routes.ai_followup import KOLBulkSendRequest, KOLRecipient, bulk_send_kol_messages

pytestmark = pytest.mark.asyncio


def test_personalize_weex_links_only_weex_domains():
    text = "Join https://weex.com/register?a=1 and https://foo.com/x?vipCode=OLD. Mirror https://www.weex.com/path?vipCode=OLD#top."
    out = tracker.personalize_weex_links(text, "VIP123")
    assert "https://weex.com/register?a=1&vipCode=VIP123" in out
    assert "https://foo.com/x?vipCode=OLD" in out
    assert "https://www.weex.com/path?vipCode=VIP123#top" in out


@patch("routes.ai_followup.asyncio.sleep", new_callable=AsyncMock)
@patch("routes.ai_followup.tg.send_text_message", new_callable=AsyncMock)
async def test_bulk_send_skips_weex_link_without_vip_code(mock_send, _mock_sleep):
    await db.create_account({"id": 1, "name": "Acc", "phone": "+8411", "api_id": "a", "api_hash": "h", "session_name": "s"})
    await db.upsert_kol_profile(1, 101, {"vip_code": "VIP101"})
    await db.upsert_kol_profile(1, 102, {"community_name": "No VIP"})

    res = await bulk_send_kol_messages(KOLBulkSendRequest(
        recipients=[KOLRecipient(account_id=1, user_id=101), KOLRecipient(account_id=1, user_id=102)],
        message="Use https://weex.com/register"
    ))

    assert res["sent"] == [{"account_id": 1, "user_id": 101}]
    assert res["skipped"] == [{"account_id": 1, "user_id": 102, "reason": "missing vip_code"}]
    mock_send.assert_called_once_with(1, 101, "Use https://weex.com/register?vipCode=VIP101", parse_mode=None)
