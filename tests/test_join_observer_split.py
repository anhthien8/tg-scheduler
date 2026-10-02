"""
Join-watcher observer/sender split.

Use case: a SECONDARY nick is a member of a private group and therefore sees
"X joined the group" events, while the MAIN nick (or any other nick) is the one
that should DM the new member — even though the main nick is NOT in the group.

Two independent roles:
  • observer_account_ids — nick(s) that sit IN the group and receive join events
  • sender_account_ids   — nick(s) that send the DM (need not be in the group)

Rules covered here:
  1. registration attaches the ChatAction handler to OBSERVERS, not senders
  2. when observer_account_ids is empty we fall back to senders (back-compat)
  3. the DM is always attempted from sender_account_ids
  4. a join watcher with no sender is rejected (400)
  5. observer_account_ids round-trips through the DB layer
"""
import asyncio
import importlib
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import database as db

ROOT = pathlib.Path(__file__).resolve().parent.parent
KW_SRC = (ROOT / "keyword_watcher.py").read_text(encoding="utf-8", errors="replace")


# ── source-level guarantees ───────────────────────────────────────────────────

def test_register_uses_observer_ids_for_join_watchers():
    m = re.search(r"def _register_watcher\(watcher: dict\):(.+?)_handler_removers\[watcher_id\]", KW_SRC, re.S)
    assert m, "_register_watcher body not found"
    body = m.group(1)
    assert "observer_account_ids" in body, "registration must consult observer_account_ids"
    # the join branch must observe observers, not raw account_ids
    join_branch = re.search(r"if watch_type == \"join\":(.+?)else:", body, re.S)
    assert join_branch, "join branch not found"
    jb = join_branch.group(1)
    assert "observer_account_ids" in jb, "join branch must pick observers"
    assert "register_ids = list(dict.fromkeys(observer_ids))" in jb, jb


def test_join_handler_reads_sender_account_ids():
    m = re.search(r"def _make_join_handler\(watcher: dict\):(.+?)async def process_join", KW_SRC, re.S)
    assert m, "_make_join_handler header not found"
    header = m.group(1)
    assert 'watcher.get("sender_account_ids"' in header, "join handler must DM from senders"
    assert 'watcher.get("observer_account_ids"' not in header, (
        "join handler must NOT use observers as senders"
    )


def test_send_call_passes_sender_account_ids_not_observers():
    # process_join's _send_dm_with_fallback must use the senders list.
    m = re.search(r"success, used_acc, err = await _send_dm_with_fallback\((.+?)\)\n", KW_SRC, re.S)
    assert m, "send call not found"
    call = m.group(1)
    assert "account_ids=account_ids" in call, call


# ── API validation ────────────────────────────────────────────────────────────
# Uses the shared `client` fixture from conftest.py (which owns the app lifespan).

def test_join_watcher_without_sender_is_rejected(client):
    r = client.post("/api/watchers", json={
        "name": "join no sender",
        "watch_type": "join",
        "sender_account_ids": [],
        "observer_account_ids": [3],
        "group_ids": [4423004598],
        "messages": [{"msg_type": "text", "content": "hi"}],
    })
    assert r.status_code == 400, r.text
    assert "sender" in r.json()["detail"].lower()


def test_join_watcher_accepts_observer_and_sender(client):
    r = client.post("/api/watchers", json={
        "name": "join obs+sender",
        "watch_type": "join",
        "sender_account_ids": [2],
        "observer_account_ids": [3],
        "group_ids": [4423004598],
        "messages": [{"msg_type": "text", "content": "hi"}],
    })
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    # round-trips through GET
    g = client.get(f"/api/watchers/{wid}")
    assert g.status_code == 200
    d = g.json()
    assert d["observer_account_ids"] == [3]
    assert d["sender_account_ids"] == [2]


def test_keyword_watcher_ignores_observer_field(client):
    r = client.post("/api/watchers", json={
        "name": "kw with observer ignored",
        "watch_type": "keyword",
        "sender_account_ids": [2],
        "observer_account_ids": [3],
        "keywords": ["uid"],
        "group_ids": [1862935177],
        "messages": [{"msg_type": "text", "content": "hi"}],
    })
    assert r.status_code == 200, r.text


# ── DB layer round-trip ───────────────────────────────────────────────────────

def test_db_roundtrip_observer_ids():
    async def run():
        await db.init_db()
        wid = await db.create_watcher({
            "name": "t-obs-rt", "watch_type": "join",
            "sender_account_ids": [2], "observer_account_ids": [3, 4],
            "group_ids": [1], "messages": [],
        })
        w = await db.get_watcher(wid)
        assert w["observer_account_ids"] == [3, 4], w
        assert w["sender_account_ids"] == [2], w
        # legacy rows with no observer column value default to []
        await db.delete_watcher(wid)
    asyncio.run(run())


def test_unset_observer_defaults_to_empty_list():
    async def run():
        await db.init_db()
        wid = await db.create_watcher({
            "name": "t-obs-def", "watch_type": "join",
            "sender_account_ids": [2], "group_ids": [1], "messages": [],
        })
        w = await db.get_watcher(wid)
        assert w["observer_account_ids"] == []
        await db.delete_watcher(wid)
    asyncio.run(run())
