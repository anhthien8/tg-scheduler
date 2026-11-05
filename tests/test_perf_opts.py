"""Perf changes: KB passage selection, settings cache, chat fallback."""
from unittest.mock import AsyncMock, patch

import pytest

import ai_remix
import database as db
import kb_select

pytestmark = pytest.mark.asyncio

_KB = "\n\n".join(
    ["1. CORE POLICY\n\nStandard KOL wording: up to 80% commission, daily settlement."]
    + [f"{i}. FILLER SECTION {i}\n\n" + ("Unrelated campaign filler text. " * 40) for i in range(2, 30)]
    + ["30. TRIAL FUND\n\nTrial Fund support ceiling is case dependent and needs approval."]
)


async def test_select_kb_keeps_core_and_relevant_section_within_budget():
    out = kb_select.select_kb(_KB, "how does the trial fund work?", budget=4000, core=500)
    assert len(out) <= 4000 < len(_KB)
    assert "CORE POLICY" in out and "TRIAL FUND" in out
    assert out.index("CORE POLICY") < out.index("TRIAL FUND")  # original order


async def test_select_kb_small_kb_is_untouched():
    assert kb_select.select_kb("short kb", "anything") == "short kb"


async def test_settings_cache_invalidated_on_write():
    await db.set_setting("perf_k", "a")
    assert await db.get_setting("perf_k") == "a"
    await db.set_setting("perf_k", "b")
    assert await db.get_setting("perf_k") == "b"
    await db.set_settings_bulk({"perf_k": "c"})
    assert await db.get_setting("perf_k") == "c"
    assert await db.get_setting("perf_missing", "dflt") == "dflt"
    await db.set_setting("perf_missing", "now")
    assert await db.get_setting("perf_missing", "dflt") == "now"


async def test_chat_keyless_9router_then_fallback_order():
    await db.set_setting("ai_oai_compat_base_url", "https://router.example/v1")
    await db.set_setting("ai_oai_compat_model", "free")
    await db.set_setting("ai_keys_gemini", '["gk"]')
    calls = []

    async def fake(provider, key, messages, **kw):
        calls.append(provider)
        if provider == "openai_compatible":
            raise ConnectionError("router down")
        return "hello from " + provider

    with patch("ai_remix._call_chat_provider", new=AsyncMock(side_effect=fake)):
        out = await ai_remix._generate_chat_response_raw(
            [{"role": "user", "content": "hi"}], "sys", "openai_compatible", [])
    assert calls[0] == "openai_compatible" and out == "hello from gemini"
