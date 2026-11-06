"""A chat run by a BloFin agent gets BloFin's offer, not WEEX's: the account's own
agent answers (even for a lead from a WEEX campaign that borrowed the account),
the prompt carries the agent's own terms instead of the WEEX ladder / committee,
and the off-offer guard allows the agent's own figures only."""
from unittest.mock import AsyncMock, patch

import database as db
import dm_reply_tracker as tracker
import kol_offer

A, U = 1, 515

BLOFIN_SYS = "You are Mayko, BD at BloFin. Never import other exchanges' policy into a BloFin chat."
BLOFIN_KB = ("Starting offer: 55% Futures RevShare plus 100 USDT per 1M USDT of taker volume. "
             "60% after 300M USDT in 30 days. Example: 40M taker volume = 4,000 USDT reward.")


async def _agent(name: str, sys_prompt: str = "Sys", kb: str = "KB") -> int:
    return await db.create_ai_agent({"name": name, "provider": "gemini", "api_keys_json": ["k"],
                                     "system_prompt": sys_prompt, "knowledge_base": kb})


async def _account(name: str, agent_id: int | None = None) -> int:
    acc_id = await db.create_account({"name": name, "phone": f"+84{abs(hash(name)) % 10**9}", "api_id": "x",
                                      "api_hash": "y", "session_name": f"s_{name}"})
    if agent_id:
        await db.set_account_ai_agent(acc_id, agent_id)
    return acc_id


async def _lead(acc_id: int = A, user_id: int = U, campaign_id: int | None = None):
    await db.get_or_create_followup_chat(account_id=acc_id, user_id=user_id, username="lead", name="Lead",
                                         campaign_id=campaign_id)
    await db.append_followup_chat_message(acc_id, user_id, "user", "What commission do you offer?")


async def _blofin_account_with_weex_campaign_lead() -> int:
    blofin = await _agent("BloFin Negotiator", BLOFIN_SYS, BLOFIN_KB)
    weex = await _agent("WEEX Negotiator", "You are a WEEX BD.")
    assert await _account("Mayko | Blofin", blofin) == A
    cid = await db.create_dm_campaign({"name": "wwex - Clone", "scrape_job_id": 0, "ai_agent_id": weex})
    await _lead(campaign_id=cid)
    return cid


# ── kol_offer helpers ────────────────────────────────────────────────────────

async def test_offer_block_follows_the_exchange():
    assert kol_offer.uses_ladder(None) and kol_offer.uses_ladder("WEEX")
    assert not kol_offer.uses_ladder("BloFin")

    block, ladder = await kol_offer.offer_block_for("BloFin", "50M")
    assert "OFFICIAL BloFin KOL COMMISSION TERMS" in block and "WEEX" not in block
    assert "TIER PREDICTION ALLOWED" not in block and ladder is None

    block, ladder = await kol_offer.offer_block_for("WEEX")
    assert "OFFICIAL WEEX KOL COMMISSION LADDER" in block and ladder["tiers"]


def test_guard_allows_only_the_agents_own_figures_for_other_exchanges():
    agent = {"system_prompt": BLOFIN_SYS, "knowledge_base": BLOFIN_KB}
    ok = "55% RevShare plus 100 USDT per 1M taker volume, 60% after 300M USDT; 40M taker = 4,000 USDT. 100% organic."
    assert kol_offer.find_off_offer_figures(ok, "BloFin", agent) == []
    # WEEX ladder figures / WEEX marketing fee are not BloFin terms.
    assert kol_offer.find_off_offer_figures("up to 80% revshare + $200/month", "BloFin", agent) == ["80%", "$200"]
    assert kol_offer.find_off_offer_figures("they run a B-book", "BloFin", agent) == ["B-book"]
    # No agent text → nothing but '100%' may be quoted.
    assert kol_offer.find_off_offer_figures("55% revshare", "Bitget", None) == ["55%"]
    # WEEX chats keep the ladder guard.
    assert kol_offer.find_off_offer_figures("60% after 300M USDT", "WEEX", agent) == ["300M USDT"]
    assert kol_offer.find_off_offer_figures("up to 80% revshare", "WEEX") == []


# ── main reply ───────────────────────────────────────────────────────────────

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_blofin_account_answers_a_weex_campaign_lead_with_blofin_terms(mock_send, mock_chat):
    cid = await _blofin_account_with_weex_campaign_lead()
    reply = "You start at 55% Futures RevShare plus 100 USDT per 1M taker volume bro, 60% after 300M USDT."
    mock_chat.return_value = reply

    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock) as mock_notify:
        assert await tracker.generate_and_send_ai_reply_for_chat(A, U, "lead", target_campaign_id=cid) is True
        mock_notify.assert_not_called()

    prompt = mock_chat.call_args_list[0][0][1]
    assert prompt.startswith(BLOFIN_SYS)  # the account's BloFin agent, not the campaign's WEEX agent
    assert "OFFICIAL BloFin KOL COMMISSION TERMS" in prompt
    assert "BloFin Partnership Committee" in prompt
    assert "@weexwill" in prompt  # internal main-account handle is not an exchange name
    assert "WEEX" not in prompt
    assert mock_send.call_args[0][2] == reply  # BloFin's own figures pass the guard


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_weex_ladder_figure_in_a_blofin_chat_is_blocked(mock_send, mock_chat):
    cid = await _blofin_account_with_weex_campaign_lead()
    mock_chat.return_value = "We go up to 80% revshare bro!"

    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock) as mock_notify:
        await tracker.generate_and_send_ai_reply_for_chat(A, U, "lead", target_campaign_id=cid)
        assert "off-ladder" in mock_notify.call_args.kwargs.get("reason", "")

    assert mock_send.call_args[0][2] == kol_offer.get_safe_offer_fallback_reply("en")
    assert (await db.get_followup_chat(A, U))["status"] == "needs_human"


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock, return_value="Sure bro!")
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_account_without_agent_still_uses_the_campaign_agent(mock_send, mock_chat):
    weex = await _agent("WEEX Negotiator", "You are a WEEX BD.")
    acc = await _account("William Been")
    cid = await db.create_dm_campaign({"name": "wwex", "scrape_job_id": 0, "ai_agent_id": weex})
    await _lead(acc_id=acc, campaign_id=cid)

    await tracker.generate_and_send_ai_reply_for_chat(acc, U, "lead", target_campaign_id=cid)
    prompt = mock_chat.call_args_list[0][0][1]
    assert prompt.startswith("You are a WEEX BD.")
    assert "OFFICIAL WEEX KOL COMMISSION LADDER" in prompt and "WEEX Partnership Committee" in prompt


# ── drip / care / step prompt ────────────────────────────────────────────────

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock, return_value="Hey bro, still keen on BloFin?")
@patch("telegram_client.send_text_message", new_callable=AsyncMock, return_value=True)
async def test_drip_for_a_blofin_chat_carries_blofin_terms(mock_send, mock_chat):
    await _blofin_account_with_weex_campaign_lead()
    async with db.get_db() as conn:
        await conn.execute("UPDATE ai_followup_chats SET last_user_message_at = datetime('now','-60 hours'), "
                           "updated_at = datetime('now','-60 hours') WHERE account_id = ? AND user_id = ?", (A, U))
        await conn.commit()

    await tracker.process_drip_followups()
    prompt = mock_chat.call_args[0][1]
    assert "OFFICIAL BloFin KOL COMMISSION TERMS" in prompt and "WEEX" not in prompt


def test_kol_care_instruction_names_the_chat_exchange():
    blofin = tracker._kol_care_instruction("BloFin", 30, " and ".join(tracker.KOL_CARE_EVENT_LINKS))
    assert "push BloFin" in blofin and "weex" not in blofin.lower()
    assert tracker._kol_care_instruction("WEEX", 30, "x") == tracker._KOL_CARE_INSTRUCTION.format(days=30, events="x")


async def test_step_prompt_predicts_weex_tiers_only_for_weex_chats():
    await _account("Mayko | Blofin", await _agent("BloFin Negotiator"))
    weex_acc = await _account("Coy | Weex", await _agent("WEEX Negotiator"))
    for acc in (A, weex_acc):
        await _lead(acc_id=acc)
        await db.upsert_kol_profile(acc, U, {"volume_claimed": "50M"})
        await db.set_kol_step(acc, U, "deal_agreed", actor="admin:test")

    blofin_prompt = await tracker.render_kol_step_prompt(A, U)
    assert "PREDICTED TIER" not in blofin_prompt and "$200" not in blofin_prompt
    weex_prompt = await tracker.render_kol_step_prompt(weex_acc, U)
    assert "PREDICTED TIER" in weex_prompt and "$200/month" in weex_prompt
