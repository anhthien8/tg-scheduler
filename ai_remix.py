"""
AI Remix Module - Supports Gemini, DeepSeek, OpenAI, Groq, and OpenAI-Compatible APIs.
Round-robin rotation across multiple API keys per provider to avoid rate limits.
"""
import logging
import httpx
import json
import time

logger = logging.getLogger("tg-scheduler.ai_remix")

_rr_index = {}
_key_cooldown = {}
_KEY_FAIL_COOLDOWN = 60


def _next_key(keys, provider):
    if not keys:
        raise ValueError("No API keys for: " + provider)
    n = len(keys)
    start = _rr_index.get(provider, 0) % n
    for i in range(n):
        idx = (start + i) % n
        last_fail = _key_cooldown.get((provider, idx), 0)
        if time.time() - last_fail > _KEY_FAIL_COOLDOWN:
            _rr_index[provider] = (idx + 1) % n
            return idx, keys[idx]
    idx = min(range(n), key=lambda i: _key_cooldown.get((provider, i), 0))
    _rr_index[provider] = (idx + 1) % n
    return idx, keys[idx]


def _mark_key_failed(provider, idx):
    _key_cooldown[(provider, idx)] = time.time()


async def _call_gemini(api_key, prompt):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-2.0-flash:generateContent?key=" + api_key)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 1024}
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


async def _call_deepseek(api_key, prompt):
    url = "https://api.deepseek.com/chat/completions"
    headers = {"Authorization": "Bearer " + api_key,
               "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.9,
        "max_tokens": 1024
    }
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _call_openai(api_key, prompt):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.85,
        "max_tokens": 1500
    }
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _call_groq(api_key, prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.85,
        "max_tokens": 1500
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _call_openai_compatible(api_key, prompt, base_url, model):
    """Call any OpenAI-compatible API endpoint."""
    url = base_url.rstrip('/') + '/chat/completions'
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.85,
        "max_tokens": 1500
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        # Some local APIs (LM Studio, Ollama, vLLM) may return extra data after JSON
        # Use strict=False and handle partial JSON
        raw = resp.text
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract just the first JSON object
            import re
            match = re.search(r'\{.*?"choices"\s*:\s*\[.*?\]\s*\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError(f"Cannot parse API response: {raw[:200]}")
        content = data["choices"][0]["message"]["content"]
        return (content or "").strip()


async def _try_call(provider, api_key, prompt, **kwargs):
    if provider == "gemini":
        return await _call_gemini(api_key, prompt)
    elif provider == "deepseek":
        return await _call_deepseek(api_key, prompt)
    elif provider == "openai":
        return await _call_openai(api_key, prompt)
    elif provider == "groq":
        return await _call_groq(api_key, prompt)
    elif provider == "openai_compatible":
        base_url = kwargs.get("base_url", "")
        model = kwargs.get("model", "")
        if not base_url or not model:
            raise ValueError("openai_compatible requires base_url and model")
        return await _call_openai_compatible(api_key, prompt, base_url, model)
    else:
        raise ValueError("Unknown provider: " + provider)


def _build_prompt(original_text, sender_name=None, custom_instruction=None):
    name_hint = ""
    if sender_name:
        name_hint = (
            "\nThe recipient name is: " + sender_name + "."
            " Personalize the greeting if natural (e.g. use their name in the opening)."
        )

    instruction_addon = ""
    if custom_instruction and str(custom_instruction).strip():
        instruction_addon = (
            "\nCUSTOM OUTREACH INSTRUCTION FROM USER:\n"
            + str(custom_instruction).strip() + "\n"
        )

    prompt = (
        "You are an expert Telegram outreach assistant. Rephrase the message below to sound natural, authentic, and human.\n"
        "\n"
        "RULES (follow strictly):\n"
        "1. Preserve essential information and intent while prioritizing a natural, non-spammy tone.\n"
        "2. AGGRESSIVELY change wording, sentence order, and structure each time.\n"
        "3. Keep the SAME language as the original - do NOT translate.\n"
        "4. Do NOT use any emoji or icon characters. Write plain text only. Messages with emoji look like spam/bot.\n"
        "5. Keep all @usernames, links, and numbers exactly as-is unless instructed to reframe.\n"
        "6. Write naturally like a real person texting a colleague or friend - casual, friendly, and authentic. Avoid robotic sales pitches.\n"
        "7. Output ONLY the rephrased message. No intro, no quotes, no explanation.\n"
        "8. FORMAT for readability: add a blank line between paragraphs. Group related sentences into short paragraphs.\n"
        + instruction_addon
        + name_hint
        + "\n\nOriginal message:\n---\n"
        + original_text
        + "\n---\nRephrased message:"
    )
    return prompt


async def remix_message(original_text, provider, api_keys, sender_name=None, custom_instruction=None, **kwargs):
    """
    Remix a DM message using round-robin AI key rotation.
    Supported providers: 'gemini', 'deepseek', 'openai', 'groq', 'openai_compatible'
    Falls back to original_text if all keys fail.
    For openai_compatible, pass base_url and model in kwargs.
    """
    if not original_text or not original_text.strip():
        return original_text
    if not api_keys:
        logger.warning("[AI Remix] No API keys - using original")
        return original_text

    prompt = _build_prompt(original_text, sender_name, custom_instruction=custom_instruction)
    idx, key = _next_key(api_keys, provider)

    try:
        result = await _try_call(provider, key, prompt, **kwargs)
        logger.info(
            "[AI Remix] %s key[%d] OK - %dc -> %dc",
            provider, idx, len(original_text), len(result)
        )
        return result if result else original_text

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        _mark_key_failed(provider, idx)
        logger.warning(
            "[AI Remix] %s key[%d] HTTP %d: %s",
            provider, idx, status, e.response.text[:200]
        )
        # On quota/auth errors try next key immediately
        if status in (429, 403) and len(api_keys) > 1:
            try:
                idx2, key2 = _next_key(api_keys, provider)
                if idx2 != idx:
                    logger.info("[AI Remix] Retrying with %s key[%d]...", provider, idx2)
                    result2 = await _try_call(provider, key2, prompt, **kwargs)
                    if result2:
                        logger.info("[AI Remix] Retry key[%d] succeeded", idx2)
                        return result2
            except Exception as e2:
                logger.warning("[AI Remix] Retry failed: %s", e2)
        return original_text

    except Exception as e:
        _mark_key_failed(provider, idx)
        logger.warning("[AI Remix] %s key[%d] error: %s - using original", provider, idx, e)
        return original_text


async def generate_response(prompt: str, provider: str, api_keys: list[str]) -> str | None:
    """
    Generate a response to a prompt using the configured LLM provider and key rotation.
    Used for AI auto-reply rules.
    """
    if not api_keys:
        return None
    idx, key = _next_key(api_keys, provider)
    try:
        return await _try_call(provider, key, prompt)
    except Exception as e:
        logger.warning("[AI AutoReply] %s key[%d] failed: %s", provider, idx, e)
        _mark_key_failed(provider, idx)
        if len(api_keys) > 1:
            try:
                idx2, key2 = _next_key(api_keys, provider)
                if idx2 != idx:
                    logger.info("[AI AutoReply] Retrying with key[%d]...", idx2)
                    return await _try_call(provider, key2, prompt)
            except Exception as e2:
                logger.warning("[AI AutoReply] Retry failed: %s", e2)
        return None


async def generate_chat_response(
    messages_history: list[dict],
    system_prompt: str,
    provider: str,
    api_keys: list[str],
    **kwargs
) -> str | None:
    """
    Generate a contextual multi-turn chat response using LLMs (OpenAI/Codex, Gemini, DeepSeek, Groq).
    `messages_history`: list of dicts [{"role": "user"|"assistant", "content": "..."}, ...]
    `system_prompt`: Sales Persona + Knowledge Base + Onboarding CTA
    """
    if not api_keys or not provider:
        return None

    # Construct unified message list with system prompt
    formatted_messages = [{"role": "system", "content": system_prompt}]
    for m in messages_history:
        role = "assistant" if m.get("role") in ("assistant", "model") else "user"
        content = m.get("content", "").strip()
        if content:
            formatted_messages.append({"role": role, "content": content})

    idx, key = _next_key(api_keys, provider)
    try:
        res = await _call_chat_provider(provider, key, formatted_messages, **kwargs)
        if res:
            logger.info("[AI Chat] %s key[%d] generated %dc response", provider, idx, len(res))
            return res
    except Exception as e:
        logger.warning("[AI Chat] %s key[%d] failed: %s", provider, idx, e)
        _mark_key_failed(provider, idx)
        if len(api_keys) > 1:
            try:
                idx2, key2 = _next_key(api_keys, provider)
                if idx2 != idx:
                    logger.info("[AI Chat] Retrying with %s key[%d]...", provider, idx2)
                    return await _call_chat_provider(provider, key2, formatted_messages, **kwargs)
            except Exception as e2:
                logger.warning("[AI Chat] Retry failed: %s", e2)

    return None


async def _call_chat_provider(provider: str, api_key: str, messages: list[dict], **kwargs) -> str | None:
    if provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": kwargs.get("model", "gpt-4o-mini"),
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1000
        }
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    elif provider == "deepseek":
        url = "https://api.deepseek.com/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1000
        }
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    elif provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1000
        }
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    elif provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        sys_prompt = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        contents = []
        for m in (messages[1:] if sys_prompt else messages):
            g_role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": g_role, "parts": [{"text": m["content"]}]})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": sys_prompt}]} if sys_prompt else None,
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1000}
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()

    elif provider == "openai_compatible":
        base_url = kwargs.get("base_url", "https://api.openai.com/v1")
        url = base_url.rstrip('/') + '/chat/completions'
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": kwargs.get("model", "gpt-4o-mini"),
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1000
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            raw = resp.text
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                import re
                match = re.search(r'\{.*?\"choices\"\s*:\s*\[.*?\]\s*\}', raw, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                else:
                    raise ValueError(f"Cannot parse API response: {raw[:200]}")
            return data["choices"][0]["message"]["content"].strip()

    prompt_str = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
    return await generate_response(prompt_str, provider, [api_key])


