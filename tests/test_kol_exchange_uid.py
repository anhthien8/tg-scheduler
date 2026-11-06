"""The KOL UID belongs to the exchange the chat's AI Agent sells (BloFin agent →
BloFin UID), not always WEEX: step panel labels, the AI's step prompt and the
step reminder sent to the KOL all follow it."""
from unittest.mock import AsyncMock, patch

import database as db
import dm_reply_tracker as tracker

A, U = 1, 404


async def _agent(name: str) -> int:
    return await db.create_ai_agent({"name": name, "provider": "gemini", "api_keys_json": ["k"],
                                     "system_prompt": "Sys", "knowledge_base": "KB"})


async def _account(name: str = "Mayko | Blofin", agent_id: int | None = None) -> int:
    """create_account auto-numbers ids (first account = 1 = A); returns the real id."""
    acc_id = await db.create_account({"name": name, "phone": f"+84{abs(hash(name)) % 10**9}", "api_id": "x",
                                      "api_hash": "y", "session_name": f"s_{name}"})
    if agent_id:
        await db.set_account_ai_agent(acc_id, agent_id)
    return acc_id


async def _lead(user_id: int = U, step: str = "deal_agreed", campaign_id: int | None = None, acc_id: int = A):
    await db.get_or_create_followup_chat(account_id=acc_id, user_id=user_id, username=f"kol{user_id}",
                                         name="KOL", campaign_id=campaign_id)
    await db.append_followup_chat_message(acc_id, user_id, "user", "ok deal, let's go")
    if step != "prospect":
        await db.set_kol_step(acc_id, user_id, step, actor="admin:test", reason="fixture")
    return user_id


def test_exchange_is_read_from_names_only():
    assert db.exchange_from_name("BloFin Negotiator") == "BloFin"
    assert db.exchange_from_name("AI BD Weex Test") == "WEEX"
    assert db.exchange_from_name("PU Prime IB Negotiator") == "PU Prime"
    assert db.exchange_from_name("Coy |🐍 Weex") == "WEEX"
    assert db.exchange_from_name("BlofinBackCom") == "BloFin"
    assert db.exchange_from_name("Kingston", None, "") is None
    assert db.exchange_from_name("Kingston", "Kaos | Bitget") == "Bitget"


async def test_chat_exchange_follows_the_agent_that_runs_the_chat():
    blofin, weex = await _agent("BloFin Negotiator"), await _agent("WEEX Negotiator")
    assert await _account("Mayko | Blofin", blofin) == A
    await _lead(U)
    assert await db.get_chat_exchange(A, U) == "BloFin"
    assert await db.get_chat_exchange(A) == "BloFin"  # no chat yet → account agent

    # A WEEX campaign that borrowed the BloFin account as a sender: the account's agent wins
    # (the KOL talks to "Mayko | Blofin" about BloFin).
    cid = await db.create_dm_campaign({"name": "wwex - Clone", "scrape_job_id": 0, "ai_agent_id": weex})
    await _lead(U + 1, campaign_id=cid)
    assert await db.get_chat_exchange(A, U + 1) == "BloFin"

    # Account without its own agent → the campaign's agent.
    plain = await _account("William Been")
    await _lead(U, campaign_id=cid, acc_id=plain)
    assert await db.get_chat_exchange(plain, U) == "WEEX"

    # No agent anywhere → account name; nothing at all → WEEX (historic default).
    assert await db.get_chat_exchange(await _account("Kaos | Bitget"), U) == "Bitget"
    assert await db.get_chat_exchange(await _account("Kingston"), U) == "WEEX"
    assert await db.get_chat_exchange(999, U) == "WEEX"


async def test_step_state_and_ai_prompt_name_the_right_exchange():
    await _account("Mayko | Blofin", await _agent("BloFin Negotiator"))
    await _lead(U, step="deal_agreed")
    st = await db.get_kol_state(A, U)
    assert st["exchange"] == "BloFin" and st["uid_label"] == "BloFin UID"
    assert st["missing_labels"] == ["BloFin UID"]

    prompt = await tracker.render_kol_step_prompt(A, U)
    assert "BloFin UID" in prompt and "WEEX" not in prompt

    await db.upsert_kol_profile(A, U, {"weex_uid": "51232101125"})
    await db.set_kol_step(A, U, "uid_received", actor="admin:test")
    prompt = await tracker.render_kol_step_prompt(A, U)
    assert "BloFin UID=51232101125" in prompt and "WEEX" not in prompt


async def test_weex_chats_are_unchanged():
    await _account("Coy | Weex", await _agent("WEEX Negotiator"))
    await _lead(U, step="deal_agreed")
    st = await db.get_kol_state(A, U)
    assert st["missing_labels"] == ["WEEX UID"]
    assert "their WEEX UID" in await tracker.render_kol_step_prompt(A, U)
    assert tracker._step_reminder_template("weex_uid", "en") == tracker._step_reminder_template("weex_uid", "en", "WEEX")


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock, return_value="")
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_step_reminder_asks_a_blofin_kol_for_the_blofin_uid(mock_send, mock_gen):
    await _account("Mayko | Blofin", await _agent("BloFin Negotiator"))
    await _lead(U, step="deal_agreed")
    async with db.get_db() as conn:
        await conn.execute("UPDATE ai_followup_chats SET kol_step_updated_at = datetime('now','-72 hours'), "
                           "updated_at = datetime('now','-72 hours'), last_user_message_at = datetime('now','-72 hours') "
                           "WHERE account_id = ? AND user_id = ?", (A, U))
        await conn.commit()
    with patch("database.check_account_active_hours", return_value=True):
        assert (await tracker.process_step_reminders())["sent"] == 1
    instruction = mock_gen.call_args[0][1]
    assert "their BloFin UID" in instruction and "WEEX" not in instruction.split("[STEP REMINDER INSTRUCTION]")[1]
    sent = mock_send.call_args[0][2]  # AI gave nothing → template
    assert "BloFin UID" in sent and "WEEX" not in sent


async def test_profile_route_returns_the_exchange(client):
    await _account("Mayko | Blofin", await _agent("BloFin Negotiator"))
    await _lead(U)
    r = client.get(f"/api/ai-followup/chats/{A}/{U}/profile")
    assert r.status_code == 200 and r.json()["exchange"] == "BloFin"
    r = client.get(f"/api/ai-followup/chats/{A}/{U}/step")
    assert r.status_code == 200 and r.json()["uid_label"] == "BloFin UID"
