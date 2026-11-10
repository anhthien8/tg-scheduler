"""API keys are masked on the way to the browser and resolved back on save."""
import json

import pytest

import database as db
import flood_state
import secret_mask
from engines.keyword_engine import match_keyword

pytestmark = pytest.mark.asyncio

K1, K2 = "sk-aaaaaaaaaaaa1111", "sk-bbbbbbbbbbbb2222"


async def test_mask_and_resolve_roundtrip():
    masked = secret_mask.mask_list([K1, K2])
    assert all(m.startswith(secret_mask.MASK) for m in masked) and K1 not in masked[0]
    # unchanged form -> same keys; one removed, one new typed
    assert secret_mask.resolve(masked, [K1, K2]) == [K1, K2]
    assert secret_mask.resolve([masked[1], "sk-new"], [K1, K2]) == [K2, "sk-new"]
    assert secret_mask.resolve([secret_mask.MASK + "zzzz"], [K1]) == []  # stale mask dropped
    assert secret_mask.mask("short") == secret_mask.MASK  # short keys reveal nothing


async def test_settings_api_never_returns_plain_ai_keys():
    from routes import settings as st
    await db.set_setting("ai_keys_groq", json.dumps([K1, K2]))
    got = await st.get_setting("ai_keys_groq")
    assert K1 not in got["value"] and K2 not in got["value"]
    # saving the masked form back keeps the real keys
    await st.set_setting("ai_keys_groq", st.SettingPayload(value=got["value"]))
    assert json.loads(await db.get_setting("ai_keys_groq")) == [K1, K2]
    assert await st._unmask_keys(json.loads(got["value"]), "groq") == [K1, K2]


async def test_keyword_rule_plain_phrase_vs_explicit_operators():
    assert match_keyword("i want to trade and earn daily", "trade and earn")
    assert not match_keyword("i trade a lot and sometimes earn", "trade and earn")  # phrase, not AND
    assert match_keyword("solana gem alert", "solana AND gem NOT bot")
    assert not match_keyword("solana gem bot", "solana AND gem NOT bot")
    assert match_keyword("Buy SOL now", r"re:\bsol\b")


async def test_spambot_free_clears_peerflood_cooldown():
    import time

    import keyword_watcher as kw
    flood_state.peerflood_until[555] = time.time() + 3600
    assert kw._is_peerflood_blocked(555) and flood_state.peerflood_blocked(555)
    flood_state.clear_peerflood(555)
    assert not kw._is_peerflood_blocked(555)
    assert await flood_state.blocked_reason(555) is None
