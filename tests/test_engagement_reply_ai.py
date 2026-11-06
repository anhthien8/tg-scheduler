"""Community (engagement) AI replies must call the AI with the real signature.

Regression (v3.21.1): `_generate_engagement_reply` called
`generate_chat_response(messages, provider, api_keys)`, so the provider became
the system prompt and `api_keys` was missing. Every call raised TypeError, the
error was swallowed and no Community reply was ever generated.
"""
import ai_remix
import database as db
import engagement_engine as ee


def _strict_fake(calls, reply="nhìn RSI khung 4h đang phân kỳ, chờ xác nhận rồi hẵng vào"):
    """Same signature as ai_remix._generate_chat_response_raw: wrong call shapes fail."""
    async def fake(messages_history, system_prompt, provider, api_keys, **kwargs):
        calls.append({"history": messages_history, "system": system_prompt,
                      "provider": provider, "keys": api_keys, "kwargs": kwargs})
        return reply
    return fake


async def _agent(**extra):
    return await db.create_ai_agent({
        "name": "TA bot", "provider": "gemini", "api_keys_json": ["test-key-1"],
        "knowledge_base": "BloFin hỗ trợ futures.", **extra})


async def test_engagement_reply_uses_real_ai_signature(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_remix, "_generate_chat_response_raw", _strict_fake(calls))
    agent_id = await _agent()

    reply = await ee._generate_engagement_reply(
        {"ai_agent_id": agent_id, "knowledge_extra": "Phí maker 0.02%."},
        "RSI phân kỳ thì vào lệnh được chưa?", ["tin 1", "tin 2"])

    assert reply and "RSI" in reply
    assert len(calls) == 1
    c = calls[0]
    assert c["provider"] == "gemini" and c["keys"] == ["test-key-1"]
    # The TA persona + knowledge go in the system prompt, not in the history.
    assert c["system"].startswith(ee.COMMUNITY_TA_PROMPT)
    assert "BloFin hỗ trợ futures." in c["system"] and "Phí maker 0.02%." in c["system"]
    assert [m["role"] for m in c["history"]] == ["user"]
    assert "RSI phân kỳ" in c["history"][0]["content"] and "tin 2" in c["history"][0]["content"]


async def test_engagement_reply_skip_token_returns_none(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_remix, "_generate_chat_response_raw", _strict_fake(calls, reply="<SKIP>"))
    agent_id = await _agent()
    assert await ee._generate_engagement_reply({"ai_agent_id": agent_id}, "chào cả nhà") is None
    assert len(calls) == 1


async def test_engagement_reply_without_any_ai_keys_does_not_call_ai(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_remix, "_generate_chat_response_raw", _strict_fake(calls))
    agent_id = await _agent(api_keys_json=[])
    keys = ["ai_provider"] + [f"ai_keys_{p}" for p in
                              ("gemini", "chatgpt_oauth", "groq", "openai", "deepseek", "openai_compatible")]
    saved = {k: await db.get_setting(k, None) for k in keys}
    try:
        for k in keys:
            await db.set_setting(k, "gemini" if k == "ai_provider" else "[]")

        async def no_cfg(*a, **k):
            return [], {}
        monkeypatch.setattr(ai_remix, "get_provider_config", no_cfg)

        assert await ee._generate_engagement_reply({"ai_agent_id": agent_id}, "RSI là gì?") is None
        assert calls == []
    finally:
        for k, v in saved.items():
            await db.set_setting(k, v if v is not None else ("gemini" if k == "ai_provider" else "[]"))
