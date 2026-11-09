"""weex.com is blocked in India/Pakistan: AI messages to those leads use the mirror domains."""
from unittest.mock import AsyncMock, patch

import database as db
import dm_reply_tracker as tracker
import weex_mirror

MIRRORS = list(weex_mirror.DEFAULT_MIRRORS)


def test_parse_mirrors_normalizes_and_skips_weex_and_junk():
    raw = ("www.weex.io wcicisx.online, https://wqayejx.site/ \n https://www.wnenatx.online/x?y=1 "
           "weex.com https://www.weex.com bad_host")
    assert weex_mirror.parse_mirrors(raw) == MIRRORS
    assert weex_mirror.parse_mirrors('["https://wcicisx.online", "https://wcicisx.online"]') == ["https://wcicisx.online"]


def test_weex_io_is_a_mirror_kept_as_is_with_vip_code():
    assert "https://www.weex.io" in MIRRORS
    text = "Open https://www.weex.io/register?vipCode=OLD"
    assert weex_mirror.rewrite_to_mirror(text, "https://wcicisx.online") == text
    assert tracker.personalize_weex_links(text, "VIP9") == "Open https://www.weex.io/register?vipCode=VIP9"
    assert weex_mirror.parse_mirrors("") == []


def test_rewrite_keeps_path_query_and_leaves_other_hosts():
    text = ("Register: https://www.weex.com/vi/register?vipCode=ybsv. Also weex.com/rewards, "
            "https://weex.com and <a href=\"https://www.weex.com/en/register?vipCode=A1\">here</a> "
            "but not https://affiliates.weex.com/promotion/ or support@weex.com")
    out = weex_mirror.rewrite_to_mirror(text, "https://wcicisx.online")
    assert "https://wcicisx.online/vi/register?vipCode=ybsv." in out
    assert "https://wcicisx.online/rewards," in out
    assert "https://wcicisx.online and" in out
    assert 'href="https://wcicisx.online/en/register?vipCode=A1"' in out
    assert "https://affiliates.weex.com/promotion/" in out
    assert "support@weex.com" in out
    assert "www.weex.com" not in out


def test_needs_mirror_region_or_site_blocked_only():
    india = [{"role": "user", "content": "Brother, all these websites have been blocked here in India"}]
    assert weex_mirror.needs_mirror(india)
    assert weex_mirror.needs_mirror([{"role": "user", "content": "I'm from Karachi"}])
    assert weex_mirror.needs_mirror([{"role": "user", "content": "नमस्ते भाई"}])
    assert weex_mirror.needs_mirror([], {"current_cex": "MEXC (banned in India)"})
    # "doesn't open" right after we sent a weex link
    after_link = [{"role": "assistant", "content": "Here: https://www.weex.com/register?vipCode=X"},
                  {"role": "user", "content": "it doesn't open"}]
    assert weex_mirror.needs_mirror(after_link)
    # not about a website / not the lead speaking
    assert not weex_mirror.needs_mirror([{"role": "user", "content": "I blocked a spammer yesterday"}])
    assert not weex_mirror.needs_mirror([{"role": "assistant", "content": "We have KOLs in India too"}])
    assert not weex_mirror.needs_mirror([{"role": "user", "content": "Vietnam community, 20k members"}])


def test_pick_mirror_is_stable_per_lead():
    assert weex_mirror.pick_mirror(7, MIRRORS) == weex_mirror.pick_mirror(7, MIRRORS)
    assert {weex_mirror.pick_mirror(u, MIRRORS) for u in range(len(MIRRORS))} == set(MIRRORS)
    assert weex_mirror.pick_mirror(7, []) is None


def test_vip_code_is_added_on_mirror_links_too():
    out = tracker.personalize_weex_links("Join https://wqayejx.site/register?vipCode=OLD", "VIP9")
    assert out == "Join https://wqayejx.site/register?vipCode=VIP9"


def test_prompt_block_lists_the_lead_domain_first():
    block = weex_mirror.prompt_block(1, MIRRORS)
    first = weex_mirror.pick_mirror(1, MIRRORS)
    assert f"{first}/register?vipCode=CODE" in block
    assert all(m in block for m in MIRRORS)
    assert weex_mirror.prompt_block(1, []) == ""


async def test_settings_roundtrip_and_empty_turns_it_off():
    assert await weex_mirror.get_mirrors() == MIRRORS  # default when never saved
    assert await weex_mirror.save_mirrors(["wqayejx.site"]) == ["https://wqayejx.site"]
    assert await weex_mirror.get_mirrors() == ["https://wqayejx.site"]
    assert await weex_mirror.save_mirrors([]) == []
    assert await weex_mirror.get_mirrors() == []
    await weex_mirror.save_mirrors(MIRRORS)


@patch("dm_reply_tracker.tg.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_send_ai_message_rewrites_only_for_blocked_region(mock_send):
    await db.create_account({"id": 1, "name": "Acc", "phone": "+8411", "api_id": "a", "api_hash": "h", "session_name": "s"})
    link = "Sign up: https://www.weex.com/vi/register?vipCode=ybsv"
    for uid, said in ((501, "all these websites are blocked here in India"), (502, "Hi, I run a VN group")):
        await db.get_or_create_followup_chat(account_id=1, user_id=uid, username="lead", name="Lead")
        await db.append_followup_chat_message(1, uid, "user", said)
        with patch.object(tracker, "_spawn_background", lambda coro: coro.close()):
            assert await tracker._send_ai_message(1, uid, link)
    india_text = mock_send.call_args_list[0][0][2]
    other_text = mock_send.call_args_list[1][0][2]
    assert india_text == f"Sign up: {weex_mirror.pick_mirror(501, MIRRORS)}/vi/register?vipCode=ybsv"
    assert other_text == link
