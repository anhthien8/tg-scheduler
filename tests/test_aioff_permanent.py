"""v3.7.9 — /aioff tắt AI VĨNH VIỄN cho lead (stage 'paused'), không auto-resume.

Yêu cầu operator: "1 khi mình tắt AI cho lead chỉ định thì nó sẽ tắt luôn".
Trước đây /aioff dùng set_human_takeover → needs_human + 12h auto-resume.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = (ROOT / "command_bot.py").read_text(encoding="utf-8")
TRACKER = (ROOT / "dm_reply_tracker.py").read_text(encoding="utf-8")


def test_aioff_uses_stage_paused_not_takeover():
    # Cả handler text (/aioff trong topic) lẫn nút bấm đều phải set stage paused.
    assert BOT.count('set_followup_stage(acc_id, uid, "paused")') >= 2
    # Không còn đường /ai-off nào gọi set_human_takeover trong 2 handler đó.
    off_button = BOT.split('elif action == "off":')[1].split("elif action ==")[0]
    assert "set_human_takeover" not in off_button
    off_text = BOT.split("# /ai-off → silence the AI")[1].split("logger.info")[0]
    # Bỏ qua comment (chứa chữ 'set_human_takeover' trong giải thích).
    off_code = "\n".join(l for l in off_text.splitlines() if not l.strip().startswith("#"))
    assert "set_human_takeover" not in off_code


def test_aion_restores_stage_from_paused():
    # /aion + nút Bật AI phải đưa stage paused/signed về nurture.
    assert BOT.count('set_followup_stage(acc_id, uid, "nurture")') >= 2


def test_inbound_auto_resume_respects_operator_hold():
    # Auto-resume khi lead nhắn tin KHÔNG áp dụng cho stage paused/signed.
    assert "_held_stage" in TRACKER
    assert 'and not _held_stage' in TRACKER


def test_periodic_scan_excludes_paused_stage():
    # Job quét 12h phải loại stage paused/signed khỏi auto-resume.
    assert "NOT IN ('paused', 'signed')" in TRACKER
