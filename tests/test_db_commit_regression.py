"""Regression: DB write helpers must commit, or the pooled connection returns
with an open transaction and poisons the next user (breaking runtime_commands
claim's BEGIN IMMEDIATE and losing the write on crash).

Covers the specific fix for update_followup_verification, plus a guard that
scans database.py for any async function doing a write with no commit.
"""
import ast
import os

import pytest

import database as db


async def test_update_followup_verification_commits_and_is_durable():
    # Seed a follow-up chat row.
    await db.get_or_create_followup_chat(account_id=1, user_id=555, username="u", name="U")
    ok = await db.update_followup_verification(1, 555, "requested")
    assert ok is True

    # Re-read through a FRESH pool checkout to prove durability (not just the
    # in-memory cursor). Also proves the connection was released cleanly.
    chat = await db.get_followup_chat(1, 555)
    assert chat["verification_status"] == "requested"


async def test_no_open_transaction_after_verification_update():
    """The pooled connection must be back in autocommit (in_transaction False)."""
    await db.get_or_create_followup_chat(account_id=2, user_id=777, username="x", name="X")
    await db.update_followup_verification(2, 777, "verified")
    async with db.get_db() as conn:
        assert conn.in_transaction is False


async def test_child_task_does_not_share_parent_connection():
    """Regression: asyncio.create_task copies the context, so a child task used
    to inherit the parent's pooled connection. Each task must get its OWN."""
    import asyncio

    seen = {}

    async with db.get_db() as parent_conn:
        async def child():
            async with db.get_db() as child_conn:
                seen["child"] = id(child_conn)
                # A child must be able to run a write without the parent's
                # connection being reused concurrently.
                await child_conn.execute("SELECT 1")
        await asyncio.create_task(child())
        seen["parent"] = id(parent_conn)

    assert seen["child"] != seen["parent"], "child task reused parent's connection"


async def test_nested_get_db_in_same_task_reuses_connection():
    """The reuse path must still work within a single task (prevents deadlock)."""
    async with db.get_db() as outer:
        async with db.get_db() as inner:
            assert inner is outer


async def test_ai_followup_has_last_user_message_at_column():
    """Regression: the ALTER for last_user_message_at used a non-constant
    default (datetime('now')) which SQLite rejects, so the column was silently
    never added and the drip engine crashed with 'no such column'."""
    async with db.get_db() as conn:
        cur = await conn.execute("PRAGMA table_info(ai_followup_chats)")
        cols = [r[1] for r in await cur.fetchall()]
    assert "last_user_message_at" in cols


def test_database_has_no_write_without_commit():
    """Static scan: every async def in database.py that runs an INSERT/UPDATE/
    DELETE literal must also call commit() (or be wrapped in a known pattern)."""
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.py")
    tree = ast.parse(open(src_path, encoding="utf-8").read())

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        has_write = has_commit = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = getattr(sub.func, "attr", None) or getattr(sub.func, "id", None)
                if name in ("execute", "executemany") and sub.args:
                    a0 = sub.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        head = a0.value.strip().split()[0].upper() if a0.value.strip() else ""
                        if head in ("INSERT", "UPDATE", "DELETE", "REPLACE"):
                            has_write = True
                if name == "commit":
                    has_commit = True
        if has_write and not has_commit:
            offenders.append(f"{node.name}:{node.lineno}")

    assert offenders == [], f"write-without-commit in database.py: {offenders}"
