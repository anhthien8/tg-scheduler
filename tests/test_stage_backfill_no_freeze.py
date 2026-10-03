"""v3.7.11 — backfill không được biến needs_human thành stage 'paused'.

Bug: backfill map needs_human/paused_admin/bot_ignored -> 'paused'. Gate v3.7.0
chặn AI ở stage 'paused' vĩnh viễn -> 32 hội thoại thật bị 'đóng băng' dù
operator chưa từng tắt AI. needs_human chỉ nghĩa 'chờ người xem', không phải
'operator tắt AI'.
"""
from pathlib import Path

DB = (Path(__file__).resolve().parent.parent / "database.py").read_text(encoding="utf-8")

# Lấy đoạn backfill stage
_START = DB.find("Backfill stage from the legacy status")
BACKFILL = DB[_START:_START + 1200]


def test_needs_human_maps_to_nurture_not_paused():
    assert "WHEN status IN ('needs_human','paused_admin') THEN 'nurture'" in BACKFILL


def test_bot_ignored_still_maps_to_paused():
    assert "WHEN status = 'bot_ignored' THEN 'paused'" in BACKFILL


def test_backfill_does_not_touch_existing_nonempty_stage():
    # Không còn 'stage = new' trong WHERE (không ghi đè stage đã có).
    assert "OR stage = 'new'" not in BACKFILL
    # Và không còn nhánh giữ nguyên cũ dựa trên stage != new.
    assert "WHEN stage IS NOT NULL AND stage != '' AND stage != 'new' THEN stage" not in BACKFILL
