"""
Guard: the "Nhóm cần theo dõi" picker in the Keyword DM Rule modal must list
ONLY real groups (group / supergroup). Channels (broadcast) and bots can never
produce a member-join or keyword message we can act on, so showing them was
noise that confused users about the join-DM feature.

The frontend filter lives in static/js/app.js (`_watcherGroupOnly`). These are
source-level assertions so a future refactor cannot silently re-introduce
channels/bots into the picker.
"""
import pathlib
import re

APP_JS = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
INDEX_HTML = pathlib.Path(__file__).resolve().parent.parent / "static" / "index.html"


def _app_source() -> str:
    return APP_JS.read_text(encoding="utf-8", errors="replace")


def test_group_only_helper_exists_and_keeps_only_groups():
    src = _app_source()
    m = re.search(r"_watcherGroupOnly\(chats\)\{(.+?)\},", src, re.S)
    assert m, "_watcherGroupOnly helper not found in app.js"
    body = m.group(1)
    # keeps group + supergroup
    assert "group" in body and "supergroup" in body, body
    # must NOT whitelist channel / bot
    assert "'channel'" not in body and "\"channel\"" not in body, body
    assert "'bot'" not in body and "\"bot\"" not in body, body


def _render_body() -> str:
    src = _app_source()
    m = re.search(
        r"_renderWatcherChatList\(chats,preSelected=null\)\{(.+?)_onWatcherGroupToggle",
        src, re.S,
    )
    assert m, "_renderWatcherChatList body not found"
    return m.group(1)


def test_render_uses_the_group_only_filter():
    body = _render_body()
    assert "_watcherGroupOnly(" in body, "render must funnel through _watcherGroupOnly"
    assert "chats.map(" not in body, "render must not map over the raw chat list"


def test_no_channel_broadcast_icon_left_in_picker():
    body = _render_body()
    # the old channel speaker icon 📢 must be gone
    assert "\U0001F4E2" not in body, "channel icon still rendered in picker"


def test_filter_search_is_null_safe():
    src = _app_source()
    m = re.search(r"filterWatcherChats: debounce\(function\(\)\{(.+?)\}, 250\)", src, re.S)
    assert m, "filterWatcherChats not found"
    body = m.group(1)
    assert "String(c.chat_title||" in body or "String(c.chat_title ||" in body, (
        "title filter must be null-safe (channels can have empty titles)"
    )


def test_form_hint_mentions_groups_only():
    html = INDEX_HTML.read_text(encoding="utf-8", errors="replace")
    assert "w-chat-list" in html
    assert "Chỉ hiển thị nhóm" in html, "form hint explaining groups-only is missing"
