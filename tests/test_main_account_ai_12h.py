"""v3.7.5 — nick chính không còn human-only + cửa sổ takeover 12h.

Source-level: đọc dm_reply_tracker.py và khẳng định
1. Không còn hard guard `if account_id == MAIN_ACCOUNT_ID:` chặn AI / set needs_human.
2. Mọi ngưỡng human-takeover dùng 43200s (12h), không còn 86400s.
3. Job quét định kỳ dùng '-12 hours' trong SQL.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "dm_reply_tracker.py").read_text(encoding="utf-8")


def test_no_main_account_hard_guard():
    # Guard cũ: "if account_id == MAIN_ACCOUNT_ID:" rồi set needs_human / return False.
    assert "is the MAIN account — AI auto-reply blocked" not in SRC
    assert "account_id == MAIN_ACCOUNT_ID or await is_internal_account_user" not in SRC
    assert "account_id == MAIN_ACCOUNT_ID or not chat" not in SRC


def test_takeover_window_is_12h_everywhere():
    # Không còn ngưỡng 24h nào trong luồng takeover.
    assert "86400" not in SRC, "còn sót ngưỡng 24h trong dm_reply_tracker.py"
    assert SRC.count("43200") >= 4, "thiếu ngưỡng 12h ở một trong các gate"


def test_periodic_scan_uses_12_hours_sql():
    assert "'-12 hours'" in SRC
    assert "'-24 hours'" not in SRC


def test_internal_account_guard_still_present():
    # Bỏ human-only KHÔNG được bỏ luôn guard chat nội bộ giữa 2 nick.
    assert "is_internal_account_user" in SRC
