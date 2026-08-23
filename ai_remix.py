"""
AI Remix Module - Supports Gemini, DeepSeek, OpenAI, Groq, and OpenAI-Compatible APIs.
Round-robin rotation across multiple API keys per provider to avoid rate limits.
"""
import logging
import httpx
import json
import time
import re
import uuid

logger = logging.getLogger("tg-scheduler.ai_remix")


def _parse_openai_compatible_json(raw: str) -> dict:
    raw_str = (raw or "").strip()
    if not raw_str:
        raise ValueError("Empty response from OpenAI compatible API")

    # 1. Standard single JSON object parse
    try:
        data = json.loads(raw_str)
        if isinstance(data, dict) and "choices" in data and isinstance(data["choices"], list):
            return data
    except json.JSONDecodeError:
        pass

    # 2. SSE (Server-Sent Events) stream assembler (e.g. 9Router streaming responses)
    parts = []
    reasoning_parts = []
    for line in raw_str.splitlines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            if "reasoning_content" in delta and delta["reasoning_content"]:
                reasoning_parts.append(delta["reasoning_content"])
            elif "content" in delta and delta["content"]:
                parts.append(delta["content"])
        except (json.JSONDecodeError, IndexError, KeyError):
            continue

    if parts or reasoning_parts:
        content = "".join(parts).strip()
        if not content and reasoning_parts:
            content = "".join(reasoning_parts).strip()
        return {"choices": [{"message": {"content": content}}]}

    # 3. Last-ditch: extract using regex
    m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_str)
    if m:
        try:
            unescaped = json.loads(f'"{m.group(1)}"')
            return {"choices": [{"message": {"content": unescaped}}]}
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse OpenAI compatible response: {raw_str[:200]}")


_rr_index = {}
_key_cooldown = {}
_KEY_FAIL_COOLDOWN = 300  # 5 minutes circuit breaker cooldown for failing/rate-limited keys


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


async def _call_chatgpt_web_backend(token: str, prompt: str, model: str = "auto") -> str:
    """
    Native ChatGPT Web Backend Adapter (9Router / OmniRouter style).
    Calls https://chatgpt.com/backend-api/conversation directly using Session Token / OAuth JWT.
    """
    token_clean = token.replace("Bearer ", "").strip()
    url = "https://chatgpt.com/backend-api/conversation"
    headers = {
        "Authorization": f"Bearer {token_clean}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "oai-device-id": str(uuid.uuid4()),
        "oai-language": "en-US",
    }

    # Normalize model identifiers
    target_model = model or "auto"
    if target_model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        target_model = target_model
    elif target_model in ("gpt-4o", "gpt-4o-mini", "o3-mini", "o4-mini"):
        target_model = target_model

    payload = {
        "action": "next",
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [prompt]}
            }
        ],
        "parent_message_id": str(uuid.uuid4()),
        "model": target_model,
        "timezone_offset_min": -420,
        "history_and_training_disabled": True
    }

    full_text = ""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    chunk_str = line[6:].strip()
                    if chunk_str == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(chunk_str)
                        msg = chunk_json.get("message") or {}
                        content = msg.get("content") or {}
                        parts = content.get("parts") or []
                        if parts and isinstance(parts, list) and parts[0]:
                            full_text = str(parts[0])
                    except Exception:
                        continue

    if not full_text:
        raise ValueError("Empty response received from ChatGPT Web backend")
    return full_text.strip()


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
        data = _parse_openai_compatible_json(raw)
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
    elif provider == "chatgpt_oauth":
        base_url = (kwargs.get("base_url") or "").strip()
        model = kwargs.get("model") or "gpt-4o"
        is_default_url = not base_url or base_url in ("https://api.openai.com/v1", "https://api.openai.com", "https://chatgpt.com", "https://chatgpt.com/backend-api")
        if is_default_url:
            try:
                logger.info("[ChatGPT Web Adapter] Calling native chatgpt.com/backend-api/conversation (model=%s)...", model)
                return await _call_chatgpt_web_backend(api_key, prompt, model)
            except Exception as e:
                logger.warning("[ChatGPT Web Adapter] Native call failed (%s), attempting OpenAI compatible gateway...", e)
                return await _call_openai_compatible(api_key, prompt, "https://api.openai.com/v1", model)
        else:
            return await _call_openai_compatible(api_key, prompt, base_url, model)
    else:
        raise ValueError("Unknown provider: " + provider)


def _build_prompt(original_text, sender_name=None, custom_instruction=None, auto_translate_native=False, member_info=None):
    name_hint = ""
    if sender_name:
        name_hint = (
            "\nThe recipient name is: " + str(sender_name) + "."
            " Personalize the greeting if natural (e.g. use their name in the opening)."
        )

    instruction_addon = ""
    if custom_instruction and str(custom_instruction).strip():
        instruction_addon = (
            "\nCUSTOM OUTREACH INSTRUCTION FROM USER:\n"
            + str(custom_instruction).strip() + "\n"
        )

    if auto_translate_native:
        mem_info = member_info or {}
        lang_code = mem_info.get("lang_code", "") or ""
        first_name = mem_info.get("first_name", "") or ""
        last_name = mem_info.get("last_name", "") or ""
        username = mem_info.get("username", "") or ""
        lang_rule = (
            f"\n3. AUTO-LOCALIZATION / NATIVE LANGUAGE RULE:\n"
            f"   - Target recipient Telegram lang_code: '{lang_code}'\n"
            f"   - Target recipient name: '{first_name} {last_name}', username: @{username}\n"
            f"   - DETECT the recipient's likely native language based on lang_code ('{lang_code}') and name ('{first_name} {last_name}').\n"
            f"     (e.g., 'vi' -> Vietnamese, 'zh'/'zh-hans'/'zh-hant' -> Chinese, 'ru' -> Russian, 'tr' -> Turkish, 'fa'/'ar' -> Persian/Arabic, 'ko' -> Korean, 'ja' -> Japanese, 'es' -> Spanish, 'de' -> German, 'fr' -> French, 'en' -> English).\n"
            f"   - TRANSLATE and rephrase the outreach message into their native language naturally, engagingly, and authentically.\n"
            f"   - If lang_code is unavailable or 'en', check name character script (Hanzi, Cyrillic, Arabic script). If still ambiguous, write in natural English.\n"
        )
    else:
        lang_rule = "\n3. Keep the SAME language as the original - do NOT translate.\n"

    prompt = (
        "You are an expert Telegram outreach assistant. Rephrase the message below to sound natural, authentic, and human.\n"
        "\n"
        "RULES (follow strictly):\n"
        "1. Preserve essential information and intent while prioritizing a natural, non-spammy tone.\n"
        "2. AGGRESSIVELY change wording, sentence order, and structure each time.\n"
        + lang_rule +
        "4. Do NOT use any emoji or icon characters. Write plain text only. Messages with emoji look like spam/bot.\n"
        "5. Keep all @usernames, links, and numbers exactly as-is unless instructed to reframe.\n"
        "6. Write naturally like a real person texting a colleague or collaborator: casual, friendly, authentic, peer-to-peer. Avoid robotic sales pitches or corporate jargon.\n"
        "7. STRUCTURE AS A SINGLE COHESIVE MESSAGE (3-Act Flow):\n"
        "   - Hook / Warm greeting with context\n"
        "   - Clear, concise core value (1-2 sentences)\n"
        "   - End with a low-friction, polite open-ended question to invite genuine dialogue.\n"
        "8. Output ONLY the rephrased message. No intro, no quotes, no explanation, no multiple message splits.\n"
        "9. FORMAT for visual breathing room: separate thoughts with a blank line between paragraphs (double newline). Never output a dense wall of text.\n"
        + instruction_addon
        + name_hint
        + "\n\nOriginal message:\n---\n"
        + original_text
        + "\n---\nRephrased message:"
    )
    return prompt


async def _auto_fallback_remix(failed_provider, prompt):
    """Fallback across alternative configured providers if primary provider fails (e.g. 429 quota, auth error)."""
    try:
        import database as db
        fallback_order = ["chatgpt_oauth", "gemini", "groq", "openai_compatible", "openai", "deepseek"]
        for alt_prov in fallback_order:
            if alt_prov == failed_provider:
                continue
            alt_raw = await db.get_setting(f"ai_keys_{alt_prov}", "[]")
            alt_keys = json.loads(alt_raw) if alt_raw else []
            if not alt_keys and alt_prov == "gemini":
                legacy_k = await db.get_setting("gemini_api_key", "")
                if legacy_k and legacy_k.strip():
                    alt_keys = [legacy_k.strip()]
            alt_keys = [k.strip() for k in alt_keys if k and str(k).strip()]
            if alt_keys:
                alt_kwargs = {}
                if alt_prov == "openai_compatible":
                    alt_kwargs["base_url"] = await db.get_setting("ai_oai_compat_base_url", "")
                    alt_kwargs["model"] = await db.get_setting("ai_oai_compat_model", "")
                elif alt_prov == "chatgpt_oauth":
                    alt_kwargs["base_url"] = await db.get_setting("ai_chatgpt_oauth_base_url", "")
                    alt_kwargs["model"] = await db.get_setting("ai_chatgpt_oauth_model", "")
                logger.info("[AI Remix] 🔄 Auto-fallback from '%s' to '%s' (%d keys available)...", failed_provider, alt_prov, len(alt_keys))
                for a_key in alt_keys:
                    try:
                        res = await _try_call(alt_prov, a_key, prompt, **alt_kwargs)
                        if res and res.strip():
                            logger.info("[AI Remix] ✨ Auto-fallback to '%s' succeeded!", alt_prov)
                            return res.strip()
                    except Exception as fb_err:
                        logger.warning("[AI Remix] Fallback key for '%s' failed: %s", alt_prov, fb_err)
    except Exception as e:
        logger.warning("[AI Remix] Error during auto-fallback: %s", e)
    return None


async def remix_message(original_text, provider, api_keys, sender_name=None, custom_instruction=None, auto_translate_native=False, member_info=None, **kwargs):
    """
    Remix a DM message using round-robin AI key rotation.
    Supported providers: 'gemini', 'deepseek', 'openai', 'groq', 'openai_compatible', 'chatgpt_oauth'
    Falls back to alternative providers or original_text if all keys fail.
    """
    if not original_text or not original_text.strip():
        return original_text
    if not api_keys:
        return original_text

    prompt = _build_prompt(
        original_text,
        sender_name=sender_name,
        custom_instruction=custom_instruction,
        auto_translate_native=auto_translate_native,
        member_info=member_info
    )
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
        # On quota/auth errors try next key of same provider immediately
        if status in (429, 401, 403) and len(api_keys) > 1:
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
        
        # Auto fallback to other providers
        fb = await _auto_fallback_remix(provider, prompt)
        if fb:
            return fb
        return original_text

    except Exception as e:
        _mark_key_failed(provider, idx)
        logger.warning("[AI Remix] %s key[%d] error: %s", provider, idx, e)
        fb = await _auto_fallback_remix(provider, prompt)
        if fb:
            return fb
        return original_text


async def generate_response(prompt: str, provider: str, api_keys: list[str], **kwargs) -> str | None:
    """
    Generate a response to a prompt using the configured LLM provider and key rotation.
    Used for AI auto-reply rules.
    """
    if not api_keys:
        return None
    idx, key = _next_key(api_keys, provider)
    try:
        return await _try_call(provider, key, prompt, **kwargs)
    except Exception as e:
        logger.warning("[AI AutoReply] %s key[%d] failed: %s", provider, idx, e)
        _mark_key_failed(provider, idx)
        if len(api_keys) > 1:
            try:
                idx2, key2 = _next_key(api_keys, provider)
                if idx2 != idx:
                    logger.info("[AI AutoReply] Retrying with key[%d]...", idx2)
                    return await _try_call(provider, key2, prompt, **kwargs)
            except Exception as e2:
                logger.warning("[AI AutoReply] Retry failed: %s", e2)
        
        fb = await _auto_fallback_remix(provider, prompt)
        if fb:
            return fb
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

    # ── Automatic Fallback to alternative providers if primary fails ──
    try:
        import database as db
        fallback_order = ["chatgpt_oauth", "openai_compatible", "gemini", "groq", "openai", "deepseek"]
        for alt_prov in fallback_order:
            if alt_prov == provider:
                continue
            alt_raw = await db.get_setting(f"ai_keys_{alt_prov}", "[]")
            alt_keys = json.loads(alt_raw) if alt_raw else []
            if not alt_keys and alt_prov == "gemini":
                legacy_k = await db.get_setting("gemini_api_key", "")
                if legacy_k and legacy_k.strip():
                    alt_keys = [legacy_k.strip()]
            if alt_keys:
                alt_kwargs = {}
                if alt_prov == "openai_compatible":
                    alt_kwargs["base_url"] = await db.get_setting("ai_oai_compat_base_url", "")
                    alt_kwargs["model"] = await db.get_setting("ai_oai_compat_model", "")
                elif alt_prov == "chatgpt_oauth":
                    alt_kwargs["base_url"] = await db.get_setting("ai_chatgpt_oauth_base_url", "")
                    alt_kwargs["model"] = await db.get_setting("ai_chatgpt_oauth_model", "")
                logger.info("[AI Chat] 🔄 Provider '%s' failed. Auto-fallback to '%s' (%d keys)...", provider, alt_prov, len(alt_keys))
                for a_key in alt_keys:
                    try:
                        res = await _call_chat_provider(alt_prov, a_key, formatted_messages, **alt_kwargs)
                        if res:
                            logger.info("[AI Chat] ✨ Fallback to '%s' succeeded!", alt_prov)
                            return res
                    except Exception as _sub_fb_err:
                        logger.warning("[AI Chat] Fallback key for '%s' failed: %s", alt_prov, _sub_fb_err)
    except Exception as _fb_err:
        logger.warning("[AI Chat] Fallback provider error: %s", _fb_err)

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

    elif provider == "chatgpt_oauth":
        base_url = (kwargs.get("base_url") or "").strip()
        model = kwargs.get("model") or "gpt-4o"
        is_default_url = not base_url or base_url in ("https://api.openai.com/v1", "https://api.openai.com", "https://chatgpt.com", "https://chatgpt.com/backend-api")
        if is_default_url:
            try:
                prompt_lines = []
                for m in messages:
                    role = m.get("role", "user").capitalize()
                    content = m.get("content", "")
                    prompt_lines.append(f"{role}: {content}")
                full_prompt = "\n\n".join(prompt_lines)
                logger.info("[ChatGPT Web Chat] Calling native chatgpt.com/backend-api/conversation (model=%s)...", model)
                return await _call_chatgpt_web_backend(api_key, full_prompt, model)
            except Exception as e:
                logger.warning("[ChatGPT Web Chat] Native call failed (%s), falling back to OpenAI compatible endpoint...", e)

        url = (base_url or "https://api.openai.com/v1").rstrip('/') + '/chat/completions'
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1000
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            raw = resp.text
            data = _parse_openai_compatible_json(raw)
            return data["choices"][0]["message"]["content"].strip()

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
        sys_prompt = messages[0]["content"] if (messages and messages[0]["role"] == "system") else ""
        raw_msgs = messages[1:] if (messages and messages[0]["role"] == "system") else messages

        # Merge consecutive messages with the same role to strictly satisfy Gemini API requirements
        merged_contents = []
        for m in raw_msgs:
            g_role = "model" if m["role"] == "assistant" else "user"
            text = (m.get("content") or "").strip()
            if not text:
                continue
            if merged_contents and merged_contents[-1]["role"] == g_role:
                merged_contents[-1]["parts"][0]["text"] += "\n" + text
            else:
                merged_contents.append({"role": g_role, "parts": [{"text": text}]})

        if not merged_contents:
            return None

        # Ensure first message in Gemini contents is 'user'
        if merged_contents[0]["role"] == "model":
            merged_contents.insert(0, {"role": "user", "parts": [{"text": "Hello"}]})

        payload = {
            "contents": merged_contents,
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
        base_url = kwargs.get("base_url") or "https://api.openai.com/v1"
        model = kwargs.get("model") or "gpt-4o-mini"
        url = base_url.rstrip('/') + '/chat/completions'
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1000
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            raw = resp.text
            data = _parse_openai_compatible_json(raw)
            return data["choices"][0]["message"]["content"].strip()

    prompt_str = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
    return await generate_response(prompt_str, provider, [api_key])


# ── AI Memory & Self-Learning Distillation ─────────────────────────────────────
async def extract_kol_profile(history: list[dict], provider: str, api_keys: list[str], **kwargs) -> dict:
    """Extract key facts about a KOL/User from conversation history using LLM."""
    if not history or not api_keys:
        return {}

    conv_text = "\n".join([f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in history[-10:]])
    sys_prompt = (
        "You are an AI data extractor. Analyze the conversation between a BD representative and a Telegram user/KOL.\n"
        "Extract any specific facts mentioned by the user into a JSON object with keys:\n"
        " - platform: (e.g. YouTube, Telegram, Twitter, TikTok, or empty string)\n"
        " - followers: (e.g. 30k subs, 50k members, or empty string)\n"
        " - current_cex: (e.g. Binance, Bybit, OKX, or empty string)\n"
        " - revshare_request: (e.g. 75% revshare, 5000$/mo, or empty string)\n"
        " - key_demands: (e.g. daily payout, no KYC, offline event support, or empty string)\n\n"
        "Rules:\n"
        "1. Only extract facts EXPLICITLY stated by the user.\n"
        "2. Return ONLY the JSON object, wrapped in ```json ``` or raw JSON."
    )

    try:
        raw_res = await generate_chat_response(
            messages_history=[{"role": "user", "content": f"Conversation:\n{conv_text}"}],
            system_prompt=sys_prompt,
            provider=provider,
            api_keys=api_keys,
            **kwargs
        )
        if not raw_res:
            return {}
        # Clean JSON block
        clean = re.sub(r"```json\s*", "", raw_res)
        clean = re.sub(r"```\s*$", "", clean).strip()
        data = json.loads(clean)
        if isinstance(data, dict):
            return {k: str(v).strip() for k, v in data.items() if v and str(v).strip()}
    except Exception as e:
        logger.debug("[AI Extract Profile] Error extracting profile: %s", e)
    return {}


async def distill_human_takeover_rule(history: list[dict], human_reply: str, provider: str, api_keys: list[str], **kwargs) -> dict | None:
    """Distill a Q&A rule when a human admin manually answers a user."""
    if not history or not human_reply or not api_keys:
        return None

    last_user_msg = ""
    for m in reversed(history):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "")
            break

    conv_context = f"User asked: {last_user_msg}\nHuman Admin answered: {human_reply}"
    sys_prompt = (
        "You are an AI learning engine. A human admin manually replied to a user in a Telegram sales chat.\n"
        "Distill this interaction into a reusable Q&A rule for an AI Sales Agent.\n"
        "Return a JSON object:\n"
        "{\n"
        "  \"question\": \"Concise statement of the user's question or objection\",\n"
        "  \"answer\": \"Clear, accurate answer/policy provided by the human admin\"\n"
        "}\n\n"
        "Rules:\n"
        "1. Be precise and capture specific terms, rates, or conditions provided by the admin.\n"
        "2. Return ONLY valid JSON."
    )

    try:
        raw_res = await generate_chat_response(
            messages_history=[{"role": "user", "content": conv_context}],
            system_prompt=sys_prompt,
            provider=provider,
            api_keys=api_keys,
            **kwargs
        )
        if not raw_res:
            return None
        clean = re.sub(r"```json\s*", "", raw_res)
        clean = re.sub(r"```\s*$", "", clean).strip()
        data = json.loads(clean)
        if isinstance(data, dict) and data.get("question") and data.get("answer"):
            return {
                "question": str(data["question"]).strip(),
                "answer": str(data["answer"]).strip()
            }
    except Exception as e:
        logger.debug("[AI Distill Rule] Error distilling rule: %s", e)
    return None



