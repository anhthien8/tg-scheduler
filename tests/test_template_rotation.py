"""Unit tests for template_rotation.py — variant selection + send/reply recording.

Offline: database is monkeypatched; randomness is seeded/pinned.
"""
import types
from unittest.mock import AsyncMock, patch

import pytest

import template_rotation as tr


@pytest.fixture
def fake_db(monkeypatch):
    fake = types.ModuleType("database")
    fake.get_template_performance = AsyncMock(return_value=[])
    fake.update_template_performance = AsyncMock()
    monkeypatch.setattr(tr, "db", fake)
    return fake


async def test_single_variant_returns_index_zero(fake_db):
    msgs = [["only"]]
    selected, idx = await tr.select_variant(msgs)
    assert selected == ["only"]
    assert idx == 0


async def test_empty_list_returns_empty(fake_db):
    selected, idx = await tr.select_variant([])
    assert selected == []
    assert idx == 0


async def test_no_performance_data_falls_back_to_random(fake_db):
    msgs = [["a"], ["b"], ["c"]]
    fake_db.get_template_performance = AsyncMock(return_value=[])
    with patch.object(tr.random, "randint", return_value=2):
        selected, idx = await tr.select_variant(msgs, template_id=1)
    assert selected == ["c"]
    assert idx == 2


async def test_reliable_variant_with_high_rate_is_favoured(fake_db):
    msgs = [["worst"], ["best"]]
    fake_db.get_template_performance = AsyncMock(return_value=[
        {"variant_index": 0, "total_sent": 100, "response_rate": 1.0},
        {"variant_index": 1, "total_sent": 100, "response_rate": 90.0},
    ])
    # random() near 1.0 lands on the last cumulative weight -> highest-rate variant
    with patch.object(tr.random, "random", return_value=0.999):
        selected, idx = await tr.select_variant(msgs, template_id=1)
    assert idx == 1
    assert selected == ["best"]


async def test_variants_below_min_sends_use_random_fallback(fake_db):
    """When NO variant has >= MIN_SENDS_FOR_STATS sends, there is no reliable
    data, so selection falls through to the uniform random fallback."""
    msgs = [["untested"], ["tested"]]
    fake_db.get_template_performance = AsyncMock(return_value=[
        {"variant_index": 1, "total_sent": tr.MIN_SENDS_FOR_STATS - 1, "response_rate": 50.0},
    ])
    with patch.object(tr.random, "randint", return_value=1) as ri:
        _, idx = await tr.select_variant(msgs, template_id=1)
    ri.assert_called_once()  # proves the weighted path was NOT taken
    assert idx == 1


async def test_untested_variant_keeps_exploration_weight(fake_db):
    """With one reliable variant, the untested one still has a non-zero
    exploration weight so it can be picked."""
    msgs = [["untested"], ["tested"]]
    fake_db.get_template_performance = AsyncMock(return_value=[
        {"variant_index": 1, "total_sent": tr.MIN_SENDS_FOR_STATS, "response_rate": 50.0},
    ])
    # random()=0.0 with weights [0.2, 50.1] lands in variant 0's bucket.
    with patch.object(tr.random, "random", return_value=0.0):
        _, idx = await tr.select_variant(msgs, template_id=1)
    assert idx == 0


async def test_record_send_calls_db_with_delta(fake_db):
    await tr.record_send(template_id=7, variant_index=1, campaign_id=3)
    fake_db.update_template_performance.assert_awaited_once()
    kwargs = fake_db.update_template_performance.await_args.kwargs
    assert kwargs["sent_delta"] == 1
    assert kwargs.get("reply_delta") is None


async def test_record_reply_calls_db_with_delta(fake_db):
    await tr.record_reply(template_id=7, variant_index=1, watcher_id=9)
    kwargs = fake_db.update_template_performance.await_args.kwargs
    assert kwargs["reply_delta"] == 1
    assert kwargs.get("sent_delta") is None


def test_min_sends_constant_is_positive():
    assert tr.MIN_SENDS_FOR_STATS >= 1


async def test_record_send_upserts_instead_of_duplicating_rows():
    """Regression: NULL campaign_id/watcher_id used to bypass ON CONFLICT and
    insert a new row every send. With the 0-sentinel it must upsert."""
    import database as db
    # watcher path: campaign_id is None
    for _ in range(3):
        await db.update_template_performance(9001, 0, campaign_id=None, watcher_id=77, sent_delta=1)
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*), SUM(total_sent) FROM template_performance "
            "WHERE template_id=9001 AND variant_index=0 AND watcher_id=77")
        cnt, total = await cur.fetchone()
    assert cnt == 1, f"expected 1 upserted row, got {cnt}"
    assert total == 3


async def test_record_reply_increments_existing_row():
    import database as db
    await db.update_template_performance(9002, 0, campaign_id=None, watcher_id=88, sent_delta=1)
    await db.update_template_performance(9002, 0, campaign_id=None, watcher_id=88, reply_delta=1)
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT total_sent, total_replies, response_rate FROM template_performance "
            "WHERE template_id=9002 AND variant_index=0 AND watcher_id=88")
        sent, replies, rate = await cur.fetchone()
    assert sent == 1 and replies == 1
    assert abs(rate - 1.0) < 1e-9
