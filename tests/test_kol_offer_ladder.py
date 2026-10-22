"""Spec docs/spec-kol-starter-package.md §C.1 + §C.2 — pre-approved offer ladder.

1. Render: the prompt block is generated from the ladder JSON (every tier row,
   version, effective date); saving a new ladder bumps the version, records
   history and changes the prompt.
2. Guard: an AI reply quoting a figure outside the ladder ("75%", "$200 upfront",
   "B-book") is replaced by the safe fallback + needs_human; a reply that only
   uses ladder figures goes through untouched.
"""
import copy
from unittest.mock import AsyncMock, patch

import pytest

import database as db
import dm_reply_tracker as tracker
import kol_offer


# ── helpers ──────────────────────────────────────────────────────────────────

def _custom_ladder(**overrides) -> dict:
    ladder = copy.deepcopy(kol_offer.DEFAULT_LADDER)
    ladder["tiers"][1]["min_volume_m"] = 15  # 10M → 15M
    ladder["headline"] = "up to 80% revshare (new)"
    ladder.update(overrides)
    return ladder


async def _make_bd_account_with_lead(user_id: int = 202):
    agent_id = await db.create_ai_agent({
        "name": "Ladder Agent",
        "provider": "gemini",
        "api_keys_json": ["agent-key"],
        "system_prompt": "Sys",
        "knowledge_base": "KB",
    })
    await db.create_account({
        "id": 1,
        "name": "BD Acc",
        "phone": "+84123456",
        "api_id": "api",
        "api_hash": "hash",
        "session_name": "bd_acc",
        "ai_agent_id": agent_id,
    })
    await db.set_account_ai_agent(1, agent_id)
    await db.get_or_create_followup_chat(account_id=1, user_id=user_id, username="lead", name="Lead")
    await db.append_followup_chat_message(1, user_id, "user", "What commission do you offer?")
    return agent_id


# ── C.1 render ───────────────────────────────────────────────────────────────

def test_default_ladder_is_valid_and_matches_founder_numbers():
    assert kol_offer.validate_ladder(kol_offer.DEFAULT_LADDER) == []
    tiers = kol_offer.DEFAULT_LADDER["tiers"]
    assert [(t["min_volume_m"], t["revshare_pct"], t["bonus_per_1m_taker_usd"]) for t in tiers] == [
        (0, 55, 100), (10, 60, 100), (20, 65, 100), (50, 70, 50), (100, 80, 10),
    ]


def test_render_prompt_matches_ladder_json():
    ladder = kol_offer.DEFAULT_LADDER
    text = kol_offer.render_offer_ladder_prompt(ladder)
    assert f"(v{ladder['version']}, effective {ladder['effective_from']})" in text
    for t in ladder["tiers"]:
        row = f"| {t['tier']} |"
        assert row in text
        assert f"| {t['revshare_pct']}% | ${t['bonus_per_1m_taker_usd']} |" in text
    assert ">= 10M total volume / month" in text
    assert "B-book" in text  # the ban is spelled out to the model
    # No volume stated → the model is told NOT to predict a tier.
    assert "do NOT predict a tier" in text


def test_render_prompt_predicts_tier_only_when_volume_is_known():
    text = kol_offer.render_offer_ladder_prompt(kol_offer.DEFAULT_LADDER, volume_claimed="25M")
    assert "TIER PREDICTION ALLOWED" in text
    assert "Tier 3" in text and "65% revshare" in text
    assert kol_offer.predicted_tier(kol_offer.DEFAULT_LADDER, "9M")["tier"] == 1
    assert kol_offer.predicted_tier(kol_offer.DEFAULT_LADDER, 100_000_000)["tier"] == 5
    assert kol_offer.predicted_tier(kol_offer.DEFAULT_LADDER, None) is None


def test_validate_ladder_rejects_bad_shapes():
    bad = copy.deepcopy(kol_offer.DEFAULT_LADDER)
    bad["tiers"][0]["min_volume_m"] = 5
    assert any("Bậc 1" in e for e in kol_offer.validate_ladder(bad))

    bad = copy.deepcopy(kol_offer.DEFAULT_LADDER)
    bad["tiers"][2]["revshare_pct"] = 40  # not increasing
    assert any("tăng dần" in e for e in kol_offer.validate_ladder(bad))

    bad = copy.deepcopy(kol_offer.DEFAULT_LADDER)
    bad["tiers"][1]["revshare_pct"] = 150
    assert kol_offer.validate_ladder(bad)

    assert kol_offer.validate_ladder({"tiers": []})
    assert kol_offer.validate_ladder("nope")


async def test_get_ladder_falls_back_to_default_when_setting_missing():
    assert await kol_offer.is_ladder_customized() is False
    ladder = await kol_offer.get_ladder()
    assert ladder["version"] == 1
    assert ladder["tiers"][0]["revshare_pct"] == 55


async def test_save_ladder_bumps_version_records_history_and_changes_prompt():
    before = await kol_offer.render_current_ladder_prompt()
    saved = await kol_offer.save_ladder(_custom_ladder(), actor="admin:test")
    assert saved["version"] == 2
    assert await kol_offer.is_ladder_customized() is True

    after = await kol_offer.render_current_ladder_prompt()
    assert after != before
    assert "(v2," in after
    assert ">= 15M total volume / month" in after
    assert ">= 10M total volume / month" not in after

    history = await db.get_offer_ladder_history()
    versions = sorted(h["version"] for h in history)
    assert versions == [1, 2], "baseline v1 must be kept in history on first save"
    v2 = next(h for h in history if h["version"] == 2)
    assert v2["actor"] == "admin:test"
    assert v2["ladder"]["tiers"][1]["min_volume_m"] == 15

    # Second save → v3, history grows by exactly one.
    again = await kol_offer.save_ladder(_custom_ladder(headline="v3"), actor="admin:test")
    assert again["version"] == 3
    assert len(await db.get_offer_ladder_history()) == 3


async def test_save_ladder_rejects_invalid_ladder():
    bad = _custom_ladder()
    bad["tiers"][0]["min_volume_m"] = 3
    with pytest.raises(ValueError):
        await kol_offer.save_ladder(bad)
    assert await kol_offer.is_ladder_customized() is False


async def test_offer_ladder_routes(client):
    r = client.get("/api/ai-followup/offer-ladder")
    assert r.status_code == 200
    body = r.json()
    assert body["is_default"] is True
    assert body["ladder"]["version"] == 1
    assert "(v1," in body["prompt_preview"]

    r = client.put("/api/ai-followup/offer-ladder", json={"ladder": _custom_ladder()})
    assert r.status_code == 200, r.text
    assert r.json()["ladder"]["version"] == 2
    assert "v2" in r.json()["message"]

    bad = _custom_ladder()
    bad["tiers"][0]["min_volume_m"] = 9
    r = client.put("/api/ai-followup/offer-ladder", json={"ladder": bad})
    assert r.status_code == 400

    r = client.get("/api/ai-followup/offer-ladder/history")
    assert r.status_code == 200
    assert sorted(h["version"] for h in r.json()["history"]) == [1, 2]

    r = client.post("/api/ai-followup/offer-ladder/check", json={"text": "we pay 75% bro"})
    assert r.json() == {"ok": False, "off_ladder": ["75%"]}
    r = client.post("/api/ai-followup/offer-ladder/check", json={"text": "you start at 55%"})
    assert r.json()["ok"] is True


# ── C.2 guard (pure function) ────────────────────────────────────────────────

def test_guard_flags_off_ladder_figures():
    assert kol_offer.find_off_ladder_figures("We can do 75% revshare for you") == ["75%"]
    assert "$200" in kol_offer.find_off_ladder_figures("Plus $200 upfront to get started")
    assert "B-book" in kol_offer.find_off_ladder_figures("Unlike them we are not a B-book exchange")
    assert kol_offer.find_off_ladder_figures("Somewhere between 50-60% depending on volume") == ["50%"]
    assert "500 USDT" in kol_offer.find_off_ladder_figures("We'll send 500 USDT as trading funds")
    assert kol_offer.find_off_ladder_figures("Standard affiliates get 48 percent") == ["48 percent"]


def test_guard_passes_ladder_figures():
    ok = ("You start at 55% revshare, 60% once you hit 10M total monthly volume, "
          "65% at 20M, 70% at 50M and 80% at 100M. Plus $100 per 1M taker volume "
          "(drops to $50 then $10 at the top tiers). That's 5% above a standard affiliate "
          "and 100% transparent.")
    assert kol_offer.find_off_ladder_figures(ok) == []
    assert kol_offer.find_off_ladder_figures("") == []
    assert kol_offer.find_off_ladder_figures("Send me your UID bro 👍") == []


def test_guard_follows_custom_ladder():
    custom = _custom_ladder()
    custom["tiers"][0]["revshare_pct"] = 50
    assert kol_offer.find_off_ladder_figures("start at 50%", custom) == []
    assert kol_offer.find_off_ladder_figures("start at 50%") == ["50%"]


def test_safe_fallback_reply_has_no_figures():
    for lang in ("en", "vi"):
        text = kol_offer.get_safe_offer_fallback_reply(lang)
        assert kol_offer.find_off_ladder_figures(text) == []
        assert tracker.contains_ai_disclosure(text) is False
    assert "team" in kol_offer.get_safe_offer_fallback_reply("vi")


# ── C.2 guard (wired into the reply pipeline) ────────────────────────────────

@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_off_ladder_reply_is_replaced_and_escalated(mock_send, mock_chat):
    await _make_bd_account_with_lead()
    mock_chat.return_value = "Great news bro, we can do 75% revshare plus $200 upfront!"
    mock_send.return_value = True

    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock) as mock_notify:
        res = await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")
        mock_notify.assert_called_once()
        assert "off-ladder" in mock_notify.call_args.kwargs.get("reason", "")

    assert res is True
    sent_text = mock_send.call_args[0][2]
    assert "75%" not in sent_text and "$200" not in sent_text
    assert sent_text == kol_offer.get_safe_offer_fallback_reply("en")

    chat = await db.get_followup_chat(1, 202)
    assert chat["status"] == "needs_human"


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_on_ladder_reply_goes_through(mock_send, mock_chat):
    await _make_bd_account_with_lead()
    reply = "You start at 55% revshare bro, and from 10M total monthly volume you move to 60% — plus $100 per 1M taker volume."
    mock_chat.return_value = reply
    mock_send.return_value = True

    with patch("dm_reply_tracker._notify_main_account_handover", new_callable=AsyncMock) as mock_notify:
        res = await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")
        mock_notify.assert_not_called()

    assert res is True
    assert mock_send.call_args[0][2] == reply
    chat = await db.get_followup_chat(1, 202)
    assert chat["status"] == "active"


@patch("ai_remix.generate_chat_response", new_callable=AsyncMock)
@patch("telegram_client.send_text_message", new_callable=AsyncMock)
async def test_prompt_sent_to_model_contains_ladder_block(mock_send, mock_chat):
    await _make_bd_account_with_lead()
    mock_chat.return_value = "Sure bro!"
    mock_send.return_value = True
    await tracker.generate_and_send_ai_reply_for_chat(1, 202, "lead")
    # First call = the reply itself (later calls come from background profile extraction).
    system_prompt = mock_chat.call_args_list[0][0][1]
    assert "OFFICIAL WEEX KOL COMMISSION LADDER" in system_prompt
    assert "KOL ONBOARDING STATE" in system_prompt
    assert "| 1 |" in system_prompt and "| 5 |" in system_prompt
