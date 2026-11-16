"""The lead list carries who spoke last, a short preview, and an "own account" flag.

The reply queue in the UI is built from these three fields, so the browser never
needs the full chat histories.
"""
import database as db

A = 1


async def _account(account_id: int = A):
    await db.create_account({
        "id": account_id, "name": f"BD{account_id}", "phone": f"+8400{account_id}", "api_id": "x",
        "api_hash": "y", "session_name": f"bd{account_id}",
    })


async def _chat(user_id: int, *messages: tuple[str, str]):
    await db.get_or_create_followup_chat(account_id=A, user_id=user_id,
                                         username=f"kol{user_id}", name="KOL")
    for role, text in messages:
        await db.append_followup_chat_message(A, user_id, role, text)


async def _rows() -> dict:
    return {r["user_id"]: r for r in await db.get_all_followup_chats(limit=100)}


async def test_last_role_and_preview_follow_the_last_message():
    await _account()
    await _chat(101, ("assistant", "hi"), ("user", "what is the commission?"))
    await _chat(102, ("user", "hello"), ("assistant", "x" * 500))
    await _chat(103)
    rows = await _rows()
    assert rows[101]["last_role"] == "user"
    assert rows[101]["last_text"] == "what is the commission?"
    assert rows[102]["last_role"] == "assistant"
    assert len(rows[102]["last_text"]) == 140
    assert rows[103]["last_role"] is None and rows[103]["last_text"] is None
    assert "history_json" not in rows[101]


async def test_broken_history_does_not_break_the_list():
    await _account()
    await _chat(104, ("user", "hello"))
    async with db.get_db() as conn:
        await conn.execute("UPDATE ai_followup_chats SET history_json = 'not json' WHERE user_id = 104")
        await conn.commit()
    rows = await _rows()
    assert rows[104]["last_role"] is None


async def test_is_own_marks_chats_with_our_own_accounts():
    await _account()
    await _chat(555, ("user", "internal ping"))
    await _chat(556, ("user", "real kol"))
    async with db.get_db() as conn:
        await conn.execute("UPDATE accounts SET telegram_user_id = 555 WHERE id = ?", (A,))
        await conn.commit()
    rows = await _rows()
    assert rows[555]["is_own"] is True
    assert rows[556]["is_own"] is False
