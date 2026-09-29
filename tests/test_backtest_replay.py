"""Integration tests for backtest.replay — real temp SQLite, offline.

Creates the minimal schema the replay layer reads, then asserts the funnel
attribution rules (reply matched by (account_id, user_id) + time, conversion
by intent threshold, onboarding by status).
"""
import sqlite3

import pytest

from backtest.replay import build_funnels, overall_funnel

SCHEMA = """
CREATE TABLE dm_campaign_logs (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER, account_id INTEGER, target_user_id INTEGER,
    template_variant_index INTEGER DEFAULT 0, status TEXT, sent_at TEXT
);
CREATE TABLE watcher_dm_logs (
    id INTEGER PRIMARY KEY,
    watcher_id INTEGER, account_id INTEGER, target_user_id INTEGER,
    template_variant_index INTEGER DEFAULT 0, status TEXT, sent_at TEXT
);
CREATE TABLE dm_replies (
    id INTEGER PRIMARY KEY,
    account_id INTEGER, sender_user_id INTEGER, received_at TEXT
);
CREATE TABLE ai_followup_chats (
    account_id INTEGER, user_id INTEGER, intent_score INTEGER, status TEXT
);
"""


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "bt.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    rows = [
        # campaign 1, variant 0: 3 sent, 1 replied+converted+onboarded
        (1, 1, 1, 100, 0, "success", "2026-01-01 10:00:00"),
        (2, 1, 1, 101, 0, "success", "2026-01-01 10:00:05"),
        (3, 1, 1, 102, 0, "success", "2026-01-01 10:00:10"),
        # campaign 1, variant 1: 2 sent, 1 replied (no conversion)
        (4, 1, 1, 200, 1, "success", "2026-01-01 10:00:00"),
        (5, 1, 1, 201, 1, "success", "2026-01-01 10:00:05"),
        # a failed send must NOT count
        (6, 1, 1, 999, 1, "failed", "2026-01-01 10:00:00"),
    ]
    conn.executemany(
        "INSERT INTO dm_campaign_logs (id,campaign_id,account_id,target_user_id,"
        "template_variant_index,status,sent_at) VALUES (?,?,?,?,?,?,?)", rows)

    conn.executemany(
        "INSERT INTO dm_replies (account_id,sender_user_id,received_at) VALUES (?,?,?)",
        [
            (1, 100, "2026-01-01 10:05:00"),   # 5 min after send -> match variant 0
            (1, 200, "2026-01-01 10:30:00"),   # 30 min -> variant 1
        ])
    conn.executemany(
        "INSERT INTO ai_followup_chats (account_id,user_id,intent_score,status) VALUES (?,?,?,?)",
        [(1, 100, 85, "onboarded"), (1, 200, 40, "active")])
    conn.commit()
    conn.close()
    return path


def test_replies_attributed_to_correct_variant(db_path):
    funnels = build_funnels(db_path, campaign_id=1)
    assert funnels[0].sent == 3
    assert funnels[1].sent == 2
    assert funnels[0].replied == 1
    assert funnels[1].replied == 1


def test_failed_sends_excluded(db_path):
    funnels = build_funnels(db_path, campaign_id=1)
    total_sent = sum(f.sent for f in funnels.values())
    assert total_sent == 5  # the failed row is excluded


def test_conversion_and_onboarding_by_threshold(db_path):
    funnels = build_funnels(db_path, campaign_id=1)
    # user 100 intent 85 >= 70 -> converted+onboarded in variant 0
    assert funnels[0].converted == 1
    assert funnels[0].onboarded == 1
    # user 200 intent 40 -> not converted
    assert funnels[1].converted == 0


def test_reply_latency_measured(db_path):
    funnels = build_funnels(db_path, campaign_id=1)
    assert funnels[0].latencies == [300.0]   # 10:00:00 -> 10:05:00
    assert funnels[1].latencies == [1800.0]  # 10:00:00 -> 10:30:00


def test_watcher_logs_included_when_no_campaign_scope(tmp_path):
    path = str(tmp_path / "bt2.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO watcher_dm_logs (watcher_id,account_id,target_user_id,"
                 "template_variant_index,status,sent_at) VALUES (9,1,555,0,'success','2026-01-01 09:00:00')")
    conn.commit()
    conn.close()
    funnels = build_funnels(path, campaign_id=None)
    assert sum(f.sent for f in funnels.values()) == 1


def test_reply_before_send_is_not_attributed(tmp_path):
    """A reply timestamped before the send must not count for that attempt."""
    path = str(tmp_path / "bt3.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO dm_campaign_logs (campaign_id,account_id,target_user_id,"
                 "template_variant_index,status,sent_at) VALUES (1,1,100,0,'success','2026-01-01 10:00:00')")
    conn.execute("INSERT INTO dm_replies (account_id,sender_user_id,received_at) "
                 "VALUES (1,100,'2026-01-01 09:00:00')")  # BEFORE the send
    conn.commit()
    conn.close()
    funnels = build_funnels(path, campaign_id=1)
    assert funnels[0].replied == 0


def test_overall_funnel_sums_variants(db_path):
    funnels = build_funnels(db_path, campaign_id=1)
    overall = overall_funnel(funnels)
    assert overall.sent == 5
    assert overall.replied == 2
    assert overall.converted == 1
    assert overall.onboarded == 1
    assert sorted(overall.latencies) == [300.0, 1800.0]


def test_reply_credited_to_latest_send_not_oldest(tmp_path):
    """Regression: an attempt is credited to the MOST RECENT send before the
    reply, not the oldest (the old oldest-first sort misattributed replies)."""
    path = str(tmp_path / "bt4.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    # variant 0 sent at 10:00, variant 1 sent at 11:00, reply at 12:00.
    conn.execute("INSERT INTO dm_campaign_logs (campaign_id,account_id,target_user_id,"
                 "template_variant_index,status,sent_at) VALUES (1,1,100,0,'success','2026-01-01 10:00:00')")
    conn.execute("INSERT INTO dm_campaign_logs (campaign_id,account_id,target_user_id,"
                 "template_variant_index,status,sent_at) VALUES (1,1,100,1,'success','2026-01-01 11:00:00')")
    conn.execute("INSERT INTO dm_replies (account_id,sender_user_id,received_at) "
                 "VALUES (1,100,'2026-01-01 12:00:00')")
    conn.commit()
    conn.close()

    funnels = build_funnels(path, campaign_id=1)
    # The reply (12:00) belongs to variant 1 (sent 11:00), latency 3600s.
    assert funnels[1].replied == 1
    assert funnels[0].replied == 0
    assert funnels[1].latencies == [3600.0]
