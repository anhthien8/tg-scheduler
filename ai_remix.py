"""
AI Remix Module - Supports Gemini (Google) and DeepSeek.
Round-robin rotation across multiple API keys per provider to avoid rate limits.
"""
import logging
import httpx
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


async def _try_call(provider, api_key, prompt):
    if provider == "gemini":
        return await _call_gemini(api_key, prompt)
    elif provider == "deepseek":
        return await _call_deepseek(api_key, prompt)
    elif provider == "openai":
        return await _call_openai(api_key, prompt)
    elif provider == "groq":
        return await _call_groq(api_key, prompt)
    else:
        raise ValueError("Unknown provider: " + provider)


def _build_prompt(original_text, sender_name=None):
    name_hint = ""
    if sender_name:
        name_hint = (
            "\nThe recipient name is: " + sender_name + "."
            " Personalize the greeting if natural (e.g. use their name in the opening)."
        )

    prompt = (
        "You are a messaging assistant. Rephrase the message below.\n"
        "\n"
        "RULES (follow strictly):\n"
        "1. Preserve ALL information and intent - do NOT shorten or summarize.\n"
        "2. Output length must match the original (same number of sentences/points).\n"
        "3. Only change wording and sentence structure, NOT the content.\n"
        "4. Keep the SAME language as the original - do NOT translate.\n"
        "5. Keep all emojis, @usernames, links, and numbers exactly as-is.\n"
        "6. Output ONLY the rephrased message. No intro, no quotes, no explanation.\n"
        + name_hint
        + "\n\nOriginal message:\n---\n"
        + original_text
        + "\n---\nRephrased message:"
    )
    return prompt


async def remix_message(original_text, provider, api_keys, sender_name=None):
    """
    Remix a DM message using round-robin AI key rotation.
    Supported providers: 'gemini', 'deepseek', 'openai', 'groq'
    Falls back to original_text if all keys fail.
    """
    if not original_text or not original_text.strip():
        return original_text
    if not api_keys:
        logger.warning("[AI Remix] No API keys - using original")
        return original_text

    prompt = _build_prompt(original_text, sender_name)
    idx, key = _next_key(api_keys, provider)

    try:
        result = await _try_call(provider, key, prompt)
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
                    result2 = await _try_call(provider, key2, prompt)
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
