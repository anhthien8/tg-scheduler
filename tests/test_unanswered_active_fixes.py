"""Leads stuck as 'active' with an unanswered KOL message (v3.42 fixes)."""
import inspect

import dm_reply_tracker as drt

SRC = inspect.getsource(drt)


def test_human_wrote_detects_admin_and_forum_messages():
    assert drt._human_wrote({"history": [{"role": "assistant", "content": "[Human Admin]: hi"}]})
    assert drt._human_wrote({"history": [{"role": "assistant", "content": "[Human Admin via Forum]: hi"}]})
    assert drt._human_wrote({"history_json": '[{"role":"assistant","content":"[Human Admin]: x"}]'})
    assert not drt._human_wrote({"history": [{"role": "assistant", "content": "AI text"}, {"role": "user", "content": "q"}]})


def test_auto_resume_is_guarded_in_handler_and_sweep():
    assert SRC.count("_ai_can_answer(") >= 3   # handler, 12h sweep, stuck sweep
    assert "_human_wrote(chat)" in SRC


def test_stuck_sweep_picks_only_unanswered_rows_oldest_first():
    assert "json_extract(history_json, '$[#-1].role') = 'user'" in SRC
    assert "ORDER BY datetime(updated_at) ASC" in SRC
    assert "datetime('now', '-48 hours')" in SRC  # no old-lead revival


def test_auto_resume_sweep_never_revives_old_leads():
    assert "datetime(updated_at) >= datetime('now', '-7 days')" in SRC


def test_lock_collision_schedules_one_retry():
    assert "_retry_after_lock(" in SRC and "_lock_retry=False" in SRC
