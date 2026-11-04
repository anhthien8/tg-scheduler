"""AI Remix: active provider first (incl. keyless 9Router), then fallback order."""
from unittest.mock import AsyncMock, patch

import pytest

import ai_remix
import database as db

pytestmark = pytest.mark.asyncio


async def _cfg_9router():
    await db.set_setting("ai_oai_compat_base_url", "https://router.example/v1")
    await db.set_setting("ai_oai_compat_model", "gpt-free")


async def test_keyless_9router_counts_as_configured():
    await _cfg_9router()
    keys, kwargs = await ai_remix.get_provider_config("openai_compatible")
    assert keys == [""]
    assert kwargs == {"base_url": "https://router.example/v1", "model": "gpt-free"}


async def test_fallback_order_setting_is_respected():
    assert await ai_remix.get_fallback_order() == ai_remix.DEFAULT_FALLBACK_ORDER
    await db.set_setting("ai_fallback_order", '["gemini", "bogus", "chatgpt_oauth"]')
    order = await ai_remix.get_fallback_order()
    assert order[:2] == ["gemini", "chatgpt_oauth"] and "bogus" not in order
    assert set(order) == set(ai_remix.DEFAULT_FALLBACK_ORDER)


async def test_active_provider_empty_result_falls_back_in_order():
    await db.set_setting("ai_keys_chatgpt_oauth", '["tok"]')
    await db.set_setting("ai_keys_gemini", '["gk"]')
    calls = []

    async def fake_call(provider, key, prompt, **kw):
        calls.append(provider)
        return "" if provider == "openai_compatible" else f"remixed by {provider}"

    with patch("ai_remix._try_call", new=AsyncMock(side_effect=fake_call)):
        out = await ai_remix.remix_message("Hello", "openai_compatible", [""],
                                           base_url="https://router.example/v1", model="m")
    assert calls == ["openai_compatible", "chatgpt_oauth"]
    assert out == "remixed by chatgpt_oauth"


async def test_no_keys_for_active_provider_still_tries_fallbacks():
    await db.set_setting("ai_keys_gemini", '["gk"]')
    with patch("ai_remix._try_call", new=AsyncMock(return_value="from gemini")) as m:
        out = await ai_remix.remix_message("Hello", "groq", [])
    assert out == "from gemini" and m.call_args[0][0] == "gemini"
