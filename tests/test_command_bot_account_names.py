"""Command Bot phải hiện TÊN account, và /kol trần phải trả hướng dẫn.

Phần A: các dòng hiển thị cho founder không được in "acc3"/"acc6" — đọc id thô
không cho biết đang là nick Telegram nào.
Phần B: "/kol" thiếu tham số trước đây không khớp regex → bot im lặng hoàn toàn.
"""
import pytest
from telethon import events

import command_bot as cb
import database as db


# ── Phần A: nhãn account ────────────────────────────────────────────────────

def test_acc_label_uses_name_and_falls_back_to_id():
    assert cb._acc_label({"id": 3, "name": "Weex Linh"}) == "Weex Linh"
    assert cb._acc_label({"id": 3, "name": "  "}) == "Acc #3"
    assert cb._acc_label({"id": 6, "name": None}) == "Acc #6"
    assert cb._acc_label(None, 9) == "Acc #9"


@pytest.mark.asyncio
async def test_account_name_map_reads_names_once():
    a1 = await db.create_account({"name": "Nick Linh", "phone": "+840000000001",
                                  "api_id": "1", "api_hash": "h", "session_name": "s1"})
    a2 = await db.create_account({"name": "Nick Trang", "phone": "+840000000002",
                                  "api_id": "1", "api_hash": "h", "session_name": "s2"})
    acc_map = await cb._account_name_map()
    assert acc_map[a1] == "Nick Linh"
    assert acc_map[a2] == "Nick Trang"


@pytest.mark.asyncio
async def test_lead_line_shows_account_name_not_raw_id():
    a1 = await db.create_account({"name": "Nick Linh", "phone": "+840000000003",
                                  "api_id": "1", "api_hash": "h", "session_name": "s3"})
    acc_map = await cb._account_name_map()
    line = cb._lead_line({"username": "@kolA", "account_id": a1,
                          "lead_tier": "Tier A", "intent_score": 80,
                          "reply_count": 4}, acc_map)
    assert "Nick Linh" in line
    assert f"acc{a1}" not in line


def test_lead_line_falls_back_when_account_unknown():
    line = cb._lead_line({"username": "kolB", "account_id": 77})
    assert "Acc #77" in line and "acc77" not in line


# ── Phần B: /kol trần ───────────────────────────────────────────────────────

class _CaptureClient:
    """Thu các handler mà _register_handlers đăng ký (không cần Telegram thật)."""

    def __init__(self):
        self.handlers = []

    def on(self, builder):
        def deco(fn):
            self.handlers.append((builder, fn))
            return fn
        return deco


class _FakeEvent:
    def __init__(self, sender_id, pattern_match):
        self.sender_id = sender_id
        self.pattern_match = pattern_match
        self.replies = []
        self.is_private = True
        self.is_group = False

    async def respond(self, text, **kwargs):
        self.replies.append(text)
        self.buttons = kwargs.get("buttons")


def _kol_handler():
    client = _CaptureClient()
    cb._register_handlers(client)
    for builder, fn in client.handlers:
        if fn.__name__ == "cmd_kol":
            return builder, fn
    raise AssertionError("cmd_kol handler không được đăng ký")


@pytest.mark.asyncio
async def test_bare_kol_returns_usage_instead_of_silence():
    builder, handler = _kol_handler()
    match = builder.pattern("/kol")
    assert match, "pattern phải khớp '/kol' trần, nếu không bot im lặng"

    cb._admin_ids = {4242}
    try:
        event = _FakeEvent(4242, match)
        await handler(event)
    finally:
        cb._admin_ids = set()

    assert event.replies, "/kol trần phải có phản hồi"
    assert "/kol @username" in event.replies[0]


def test_kol_pattern_accepts_supported_forms_and_bot_suffix():
    builder, _ = _kol_handler()
    assert builder.pattern("/kol @someone").group(1) == "@someone"
    assert builder.pattern("/kol someone").group(1) == "someone"
    assert builder.pattern("/kol").group(1) is None
    assert builder.pattern("/kol@weexos_command_bot @someone").group(1) == "@someone"
    assert builder.pattern("/kol@weexos_command_bot").group(1) is None
    assert builder.pattern("/kol @bad-name") is None


def test_admin_ids_are_loaded_from_command_bot_setting_source():
    """Pin bằng chứng code-reading: quyền /kol lấy từ nguồn env-first (v3.10.2)."""
    import inspect

    source = inspect.getsource(cb.start_command_bot)
    assert "get_command_bot_admin_ids_str()" in source
    assert "_admin_ids = {int(" in source
    # Helper phải env-first với fallback settings cũ
    helper_src = inspect.getsource(cb.get_command_bot_admin_ids_str)
    assert "COMMAND_BOT_ADMIN_IDS" in helper_src
    assert 'get_setting("command_bot_admin_ids"' in helper_src


@pytest.mark.asyncio
async def test_kol_handler_reports_database_errors(monkeypatch):
    """_handle_kol_info không nuốt exception: lỗi DB luôn thành phản hồi Telegram."""
    async def fail():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(cb.db, "get_all_followup_chats", fail)
    event = _FakeEvent(4242, None)
    await cb._handle_kol_info(event, "someone")
    assert event.replies == ["❌ Lỗi: db unavailable"]


def test_command_bot_startup_has_explicit_disabled_and_missing_config_guards():
    """Bot không chạy nếu disabled/thiếu token/thiếu admin; startup đều log lý do."""
    import inspect

    source = inspect.getsource(cb.start_command_bot)
    assert 'get_setting("command_bot_enabled"' in source
    assert "get_command_bot_token()" in source
    assert "get_command_bot_admin_ids_str()" in source
    assert "[CommandBot] Disabled" in source
    assert "[CommandBot] No bot token configured" in source
    assert "[CommandBot] No admin user IDs configured" in source
    assert "COMMAND_BOT_TOKEN" in inspect.getsource(cb.get_command_bot_token)


@pytest.mark.asyncio
async def test_kol_handler_responds_when_user_not_found(monkeypatch):
    async def no_chats():
        return []

    monkeypatch.setattr(cb.db, "get_all_followup_chats", no_chats)
    event = _FakeEvent(4242, None)
    await cb._handle_kol_info(event, "missing")
    assert event.replies == ["❌ Không tìm thấy thông tin cho @missing"]


@pytest.mark.asyncio
async def test_kol_handler_shows_account_name_and_empty_name_fallback(monkeypatch):
    rows = [
        {"username": "named", "name": "Named KOL", "account_name": "Nick Trang",
         "account_id": 6, "status": "active", "updated_at": "now"},
        {"username": "fallback", "name": "Fallback KOL", "account_name": "",
         "account_id": 7, "status": "active", "updated_at": "now"},
    ]

    async def chats():
        return rows

    monkeypatch.setattr(cb.db, "get_all_followup_chats", chats)
    named = _FakeEvent(4242, None)
    await cb._handle_kol_info(named, "named")
    assert "Nick Trang" in named.replies[0]
    fallback = _FakeEvent(4242, None)
    await cb._handle_kol_info(fallback, "fallback")
    assert "Acc #7" in fallback.replies[0]


@pytest.mark.asyncio
async def test_attention_fetches_accounts_once_and_formats_all_leads(monkeypatch):
    leads = [
        {"username": "one", "account_id": 3, "stage": "nurture"},
        {"username": "two", "account_id": 6, "stage": "negotiating"},
    ]
    calls = 0

    async def get_leads():
        return leads

    async def get_accounts():
        nonlocal calls
        calls += 1
        return [{"id": 3, "name": "Nick A"}, {"id": 6, "name": "Nick B"}]

    monkeypatch.setattr(cb.db, "get_leads_needing_attention", get_leads)
    monkeypatch.setattr(cb.db, "get_all_accounts", get_accounts)
    event = _FakeEvent(4242, None)
    await cb._handle_attention(event)
    assert calls == 1, "list view phải tra account một lần, không N+1"
    assert "Nick A" in event.replies[0] and "Nick B" in event.replies[0]
    assert "acc3" not in event.replies[0] and "acc6" not in event.replies[0]


@pytest.mark.asyncio
async def test_non_admin_gets_no_reply():
    """Quyền admin vẫn là cổng duy nhất — nghi phạm số 1 của 'không phản hồi'."""
    builder, handler = _kol_handler()
    cb._admin_ids = {4242}
    try:
        event = _FakeEvent(9999, builder.pattern("/kol"))
        await handler(event)
    finally:
        cb._admin_ids = set()
    assert event.replies == []


@pytest.mark.asyncio
async def test_render_ai_status_uses_account_name_and_fallback(monkeypatch):
    """_render_ai_status hiển thị tên account, fallback "Acc #<id>" khi không có tên."""
    chat = {"status": "active", "stage": "nurture", "username": "kol1", "reply_count": 2}

    async def get_chat(acc_id, uid):
        return chat

    monkeypatch.setattr(cb.db, "get_followup_chat", get_chat)

    # Case 1: Account có name
    async def get_acc_named(acc_id):
        return {"id": acc_id, "name": "Nick Linh"}

    monkeypatch.setattr(cb.db, "get_account", get_acc_named)
    event1 = _FakeEvent(4242, None)
    await cb._render_ai_status(event1, 3, 1001)
    assert len(event1.replies) == 1
    assert "Nick: Nick Linh" in event1.replies[0]
    assert "Nick #3" not in event1.replies[0]

    # Case 2: Account không có name (None hoặc rỗng)
    async def get_acc_empty(acc_id):
        return {"id": acc_id, "name": ""}

    monkeypatch.setattr(cb.db, "get_account", get_acc_empty)
    event2 = _FakeEvent(4242, None)
    await cb._render_ai_status(event2, 5, 1002)
    assert len(event2.replies) == 1
    assert "Nick: Acc #5" in event2.replies[0]
    assert "Nick #5" not in event2.replies[0]


@pytest.mark.asyncio
async def test_show_target_picker_formats_recent_kols_with_account_fallback(monkeypatch):
    """_show_target_picker fallback "Acc #<id>" khi account không có tên."""
    recents = [
        {"username": "kol_a", "account_id": 11, "name": "KOL Alpha"},
        {"username": "kol_b", "account_id": 12, "name": "KOL Beta"},
    ]

    async def mock_recents():
        return recents

    async def mock_acc_map():
        return {11: "Nick Alpha", 12: "Acc #12"}

    monkeypatch.setattr(cb, "_get_recent_kol_targets", mock_recents)
    monkeypatch.setattr(cb, "_account_name_map", mock_acc_map)

    event = _FakeEvent(4242, None)
    await cb._show_target_picker(event, {"account_id": 11, "step": "pick_target"})

    labels = [b[0].text for b in (event.buttons or [])]
    assert any("Nick Alpha" in lb for lb in labels), labels
    assert any("Acc #12" in lb for lb in labels), labels


def test_no_user_facing_raw_acc_id_format_remains():
    """Không tái phát chuỗi acc{id} / Nick #{id} ở command_bot.py."""
    from pathlib import Path

    source = Path(cb.__file__).read_text(encoding="utf-8")
    assert " · acc{" not in source
    assert "acc{r.get('account_id')}" not in source
    assert "Nick #{" not in source

