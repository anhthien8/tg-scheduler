"""Tests for watcher template-variant grouping + rotation wiring.

Covers database.group_watcher_message_variants (pure) and the fact that
watcher_messages round-trips variant_index through create/get.
"""
import pytest

import database as db
from database import group_watcher_message_variants

# ── pure grouping ───────────────────────────────────────────────────────────

def test_empty_list_returns_no_variants():
    assert group_watcher_message_variants([]) == []


def test_messages_without_variant_index_collapse_to_one_variant():
    msgs = [{"msg_order": 0, "content": "a"}, {"msg_order": 1, "content": "b"}]
    variants = group_watcher_message_variants(msgs)
    assert len(variants) == 1
    assert [m["content"] for m in variants[0]] == ["a", "b"]


def test_messages_split_by_variant_index():
    msgs = [
        {"msg_order": 0, "variant_index": 0, "content": "a0"},
        {"msg_order": 0, "variant_index": 1, "content": "b0"},
        {"msg_order": 1, "variant_index": 0, "content": "a1"},
    ]
    variants = group_watcher_message_variants(msgs)
    assert len(variants) == 2
    assert [m["content"] for m in variants[0]] == ["a0", "a1"]
    assert [m["content"] for m in variants[1]] == ["b0"]


def test_variants_sorted_by_index_and_messages_by_order():
    msgs = [
        {"msg_order": 2, "variant_index": 1, "content": "b2"},
        {"msg_order": 0, "variant_index": 1, "content": "b0"},
        {"msg_order": 0, "variant_index": 0, "content": "a0"},
    ]
    variants = group_watcher_message_variants(msgs)
    assert [m["content"] for m in variants[0]] == ["a0"]
    assert [m["content"] for m in variants[1]] == ["b0", "b2"]


def test_missing_variant_index_defaults_to_zero():
    msgs = [{"msg_order": 0, "content": "x"}, {"msg_order": 1, "variant_index": 2, "content": "y"}]
    variants = group_watcher_message_variants(msgs)
    assert len(variants) == 2
    assert variants[1][0]["content"] == "y"


# ── DB round-trip ───────────────────────────────────────────────────────────

async def test_variant_index_round_trips_through_db():
    wid = await db.create_watcher({
        "name": "vt", "sender_account_ids": [1], "keywords": ["x"],
        "group_ids": [1], "watch_type": "keyword",
        "messages": [
            {"msg_order": 0, "msg_type": "text", "content": "var0", "variant_index": 0},
            {"msg_order": 0, "msg_type": "text", "content": "var1", "variant_index": 1},
        ],
    })
    w = await db.get_watcher(wid)
    assert len(w["message_variants"]) == 2
    assert w["messages"][0]["variant_index"] in (0, 1)


async def test_single_variant_watcher_has_one_variant():
    wid = await db.create_watcher({
        "name": "sv", "sender_account_ids": [1], "keywords": ["x"],
        "group_ids": [1], "watch_type": "keyword",
        "messages": [{"msg_order": 0, "msg_type": "text", "content": "only"}],
    })
    w = await db.get_watcher(wid)
    assert len(w["message_variants"]) == 1
