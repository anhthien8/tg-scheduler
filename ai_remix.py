"""
AI Remix Module - Supports Gemini, DeepSeek, OpenAI, Groq, and OpenAI-Compatible APIs.
Round-robin rotation across multiple API keys per provider to avoid rate limits.
"""
import asyncio
import json
import logging
import re
import time
import uuid

import os
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("tg-scheduler.ai_remix")


def _cf_access_headers(url: str) -> dict:
    """Cloudflare Access service-token headers for a self-hosted AI router
    (e.g. 9router on a home laptop behind a Cloudflare Tunnel).

    Sent ONLY when the request host equals CF_ACCESS_HOST, so the token never
    leaks to OpenAI or any other provider. Secrets come from env, not the DB.
    """
    host = (os.getenv("CF_ACCESS_HOST") or "").strip().lower()
    cid = os.getenv("CF_ACCESS_CLIENT_ID") or ""
    secret = os.getenv("CF_ACCESS_CLIENT_SECRET") or ""
    if not (host and cid and secret):
        return {}
    if (urlsplit(url).hostname or "").lower() != host:
        return {}
    return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret}


LOCAL_AI_ROUTER = os.getenv("AI_LOCAL_FALLBACK_URL", "http://127.0.0.1:20128")


def _local_fallback_url(url: str) -> str | None:
    """Same path on the local 9router, or None if url is already local."""
    parts = urlsplit(url)
    if (parts.hostname or "") in ("127.0.0.1", "localhost", "::1"):
        return None
    base = urlsplit(LOCAL_AI_ROUTER)
    return parts._replace(scheme=base.scheme, netloc=base.netloc).geturl()


def _should_fallback(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        r = exc.response
        return r.status_code in (401, 403, 502, 503, 530) and (
            "cloudflare" in r.text[:2000].lower() or r.status_code >= 502)
    return False


async def ai_request(client: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    """HTTP call to an OpenAI-compatible router; if a remote router is blocked
    (Cloudflare Access 403, tunnel down) retry the same path on localhost."""
    try:
        resp = await client.request(method, url, **kw)
        resp.raise_for_status()
        return resp
    except Exception as e:
        alt = _local_fallback_url(url) if _should_fallback(e) else None
        if not alt:
            raise
        logger.warning("[AI] %s failed (%s) — falling back to %s", url, type(e).__name__, alt)
        resp = await client.request(method, alt, **kw)
        resp.raise_for_status()
        return resp


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


GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"


async def _groq_model() -> str:
    try:
        import database as db
        return (await db.get_setting("ai_groq_model", "") or "").strip() or GROQ_DEFAULT_MODEL
    except Exception:
        return GROQ_DEFAULT_MODEL


async def _call_groq(api_key, prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "model": await _groq_model(),
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
        "Content-Type": "application/json",
        **_cf_access_headers(url),
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.85,
        "max_tokens": 1500
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await ai_request(client, "POST", url, headers=headers, json=payload)
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


_KEY_TERM_RE = re.compile(
    r"No[ -][A-Z]{2,}"                                  # No KYC
    r"|\b[A-Z][a-z]+[A-Z][A-Za-z]*(?: \d+)?"            # MetaTrader 5, TradingView
    r"|\b[A-Z]{2,}[A-Za-z0-9]*(?: \d+)?\b"              # API, HFT, USDT, MT5
    r"|\d+(?:[.,]\d+)?\s*(?:[-–]\s*\d+(?:[.,]\d+)?\s*)?%"  # 50-80%
)


def _key_terms(text):
    """Selling-point tokens a remix must keep: acronyms, CamelCase product names,
    'No KYC'-style claims and percentages."""
    seen, out = set(), []
    for m in _KEY_TERM_RE.finditer(text or ""):
        t = m.group(0).strip()
        if t.lower() in seen or any(t.lower() in o.lower() for o in out):
            continue
        seen.add(t.lower())
        out.append(t)
    return out[:20]


def _key_points_rule(original_text):
    terms = _key_terms(original_text)
    if not terms:
        return ""
    return ("11. MUST-KEEP KEY POINTS (each must appear in the output as the same Latin term): "
            + ", ".join(terms) + "\n")


LANGUAGE_NAMES = {
    "en": "English", "ru": "Russian", "uk": "Ukrainian", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "vi": "Vietnamese", "th": "Thai", "ar": "Arabic", "fa": "Persian",
    "he": "Hebrew", "hi": "Hindi", "tr": "Turkish", "es": "Spanish", "pt": "Portuguese",
    "fr": "French", "de": "German", "it": "Italian", "id": "Indonesian", "ms": "Malay",
    "pl": "Polish", "nl": "Dutch", "bn": "Bengali", "ur": "Urdu",
}

# Phone country code -> language (only used when Telegram exposes the number).
_PHONE_LANG = [
    ("380", "uk"), ("375", "ru"), ("998", "ru"), ("996", "ru"), ("992", "ru"), ("994", "tr"),
    ("971", "ar"), ("966", "ar"), ("965", "ar"), ("974", "ar"), ("973", "ar"), ("968", "ar"),
    ("962", "ar"), ("961", "ar"), ("964", "ar"), ("963", "ar"), ("972", "he"), ("880", "bn"),
    ("852", "zh"), ("853", "zh"), ("886", "zh"), ("351", "pt"),
    ("84", "vi"), ("86", "zh"), ("81", "ja"), ("82", "ko"), ("66", "th"), ("62", "id"),
    ("60", "ms"), ("90", "tr"), ("98", "fa"), ("91", "hi"), ("92", "ur"), ("20", "ar"),
    ("55", "pt"), ("34", "es"), ("52", "es"), ("54", "es"), ("57", "es"), ("56", "es"),
    ("51", "es"), ("58", "es"), ("33", "fr"), ("49", "de"), ("43", "de"), ("39", "it"),
    ("48", "pl"), ("31", "nl"), ("7", "ru"),
]

_VI_CHARS = set("ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")


# Script ranges (code points) -> language. Kana is checked before Han so Japanese wins.
_SCRIPTS = [
    (0x3040, 0x30FF, "ja", 1), (0x0400, 0x04FF, "ru", 2), (0x4E00, 0x9FFF, "zh", 2),
    (0xAC00, 0xD7AF, "ko", 2), (0x0E00, 0x0E7F, "th", 2), (0x0600, 0x06FF, "ar", 2),
    (0x0750, 0x077F, "ar", 2), (0x0590, 0x05FF, "he", 2), (0x0900, 0x097F, "hi", 2),
]

# Latin-script place / language words found in Telegram location, bio or channel titles.
_PLACE_WORDS = {
    "ru": ["russia", "moscow", "moskva", "saint petersburg", "st. petersburg", "kazakhstan", "almaty", "astana",
           "belarus", "minsk", "uzbekistan", "tashkent", "kyrgyzstan", "bishkek", "russian"],
    "uk": ["ukraine", "kyiv", "kiev", "kharkiv", "odesa", "odessa", "lviv", "ukrainian"],
    "vi": ["vietnam", "viet nam", "hanoi", "ha noi", "ho chi minh", "saigon", "sai gon", "da nang", "vietnamese"],
    "zh": ["china", "beijing", "shanghai", "shenzhen", "guangzhou", "hong kong", "taiwan", "taipei", "chinese"],
    "ja": ["japan", "tokyo", "osaka", "japanese"],
    "ko": ["korea", "seoul", "busan", "korean"],
    "th": ["thailand", "bangkok", "thai"],
    "id": ["indonesia", "jakarta", "bali", "surabaya", "indonesian", "bahasa"],
    "ms": ["malaysia", "kuala lumpur", "malay"],
    "tr": ["turkey", "turkiye", "türkiye", "istanbul", "ankara", "turkish"],
    "fa": ["iran", "tehran", "persian", "farsi"],
    "ar": ["uae", "dubai", "abu dhabi", "saudi", "riyadh", "egypt", "cairo", "qatar", "doha", "kuwait", "arabic"],
    "hi": ["india", "mumbai", "delhi", "bangalore", "bengaluru", "hindi"],
    "ur": ["pakistan", "karachi", "lahore", "islamabad", "urdu"],
    "bn": ["bangladesh", "dhaka", "bengali", "bangla"],
    "pt": ["brazil", "brasil", "são paulo", "sao paulo", "rio de janeiro", "portugal", "lisbon", "lisboa", "portuguese"],
    "es": ["spain", "españa", "madrid", "barcelona", "mexico", "méxico", "argentina", "buenos aires", "colombia",
           "bogota", "bogotá", "chile", "peru", "perú", "venezuela", "spanish", "español"],
    "fr": ["france", "paris", "french", "français"],
    "de": ["germany", "deutschland", "berlin", "munich", "münchen", "austria", "german", "deutsch"],
    "it": ["italy", "italia", "rome", "roma", "milan", "milano", "italian"],
    "pl": ["poland", "polska", "warsaw", "warszawa", "polish"],
    "nl": ["netherlands", "amsterdam", "dutch"],
    "he": ["israel", "tel aviv", "hebrew"],
}


def _text_lang(text: str) -> str:
    """Language suggested by one free-text field, or '' if it tells us nothing."""
    t = (text or "").strip()
    if not t:
        return ""
    for lo, hi, code, need in _SCRIPTS:
        if sum(1 for ch in t if lo <= ord(ch) <= hi) >= need:
            return code
    low = t.lower()
    if any(ch in _VI_CHARS for ch in low):
        return "vi"
    import re as _re
    for code, words in _PLACE_WORDS.items():
        for w in words:
            if _re.search(r"(?<![a-zà-ỹ])" + _re.escape(w) + r"(?![a-zà-ỹ])", low):
                return code
    return ""


def detect_user_language(member_info: dict | None, with_source: bool = False):
    """Pick the recipient's language from their own profile. Falls back to English.

    Order: Telegram lang_code, phone country code, Telegram location, bio,
    display name, personal channel, then the source group as the weakest hint.
    Campaign names are deliberately ignored (a Russian-named campaign used to
    force Russian on English speakers).
    """
    mi = member_info or {}
    def done(code, src):
        return (code, src) if with_source else code
    lc = str(mi.get("lang_code") or "").strip().lower().replace("_", "-")
    if lc and lc.split("-")[0] in LANGUAGE_NAMES:
        return done(lc.split("-")[0], "lang_code")
    phone = "".join(ch for ch in str(mi.get("phone") or "") if ch.isdigit())
    if phone:
        for pre, code in _PHONE_LANG:
            if phone.startswith(pre):
                return done(code, "phone")
    name = f"{mi.get('first_name') or ''} {mi.get('last_name') or ''}".strip()
    for field, val in (("location", mi.get("location")), ("bio", mi.get("bio")), ("name", name),
                       ("channel", mi.get("channel_title")), ("group", mi.get("community_hint"))):
        code = _text_lang(val or "")
        if code:
            return done(code, field)
    return done("en", "default")


async def fetch_profile_hints(client, peer) -> dict:
    """Location, bio and personal channel title from the user's full profile (best effort)."""
    uid = getattr(peer, "id", None)
    if not uid:
        return {}
    if uid in _PROFILE_HINT_CACHE:
        return _PROFILE_HINT_CACHE[uid]
    hints = {}
    try:
        from telethon.tl.functions.users import GetFullUserRequest
        full = await asyncio.wait_for(client(GetFullUserRequest(peer)), timeout=8)
        fu = full.full_user
        hints["bio"] = fu.about or ""
        loc = getattr(fu, "business_location", None)
        hints["location"] = getattr(loc, "address", "") or ""
        ch_id = getattr(fu, "personal_channel_id", None)
        if ch_id:
            for ch in full.chats or []:
                if ch.id == ch_id:
                    hints["channel_title"] = getattr(ch, "title", "") or ""
    except Exception as ex:
        logger.debug("[Lang] Full profile fetch failed for %s: %s", uid, ex)
    if len(_PROFILE_HINT_CACHE) > 5000:
        _PROFILE_HINT_CACHE.clear()
    _PROFILE_HINT_CACHE[uid] = hints
    return hints


_PROFILE_HINT_CACHE: dict = {}


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
        code, src = detect_user_language(member_info, with_source=True)
        logger.info("[Lang] Recipient language=%s (from %s)", code, src)
        lang_name = LANGUAGE_NAMES.get(code, "English")
        lang_rule = (
            f"\n3. LANGUAGE (mandatory): write the whole message in {lang_name}. "
            "This was decided from the recipient's own Telegram profile, so do not guess another language "
            "from the community, the campaign or the original text. Translate naturally, like a native speaker.\n"
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
        "   - Core value: carry over EVERY concrete selling point from the original (features, product names, numbers, percentages, terms).\n"
        "     If there are 3+ points, list them compactly (a short list or one tight sentence) instead of summarizing them away.\n"
        "   - End with a low-friction, polite open-ended question to invite genuine dialogue.\n"
        "10. NEVER shorten by dropping selling points. Aim for roughly the same length as the original (80-120%).\n"
        "    Keep technical/brand terms in their original Latin form even when translating (e.g. No KYC, API, HFT, MetaTrader 5, USDT).\n"
        "8. Output ONLY the rephrased message. No intro, no quotes, no explanation, no multiple message splits.\n"
        "9. FORMAT for visual breathing room: separate thoughts with a blank line between paragraphs (double newline). Never output a dense wall of text.\n"
        + _key_points_rule(original_text)
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
        return humanize_text(result) if result else original_text

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


_HUMAN_STYLE_RULE = (
    "\n\nWRITING STYLE (anti AI-detection, mandatory): write like a real person texting on Telegram. "
    "NEVER use semicolons (;), em dashes (—), en dashes as separators, or bullet-like structure in casual chat. "
    "Use short sentences, commas and periods only. No 'Furthermore/Moreover/Additionally', no perfectly balanced clauses."
    "\n\nFACT CHECK (mandatory): when the user asks about a message you sent (its language, wording, meaning), "
    "look at your earlier messages in the history and any [Quoting ...] text, and answer from that. "
    "If an earlier message was in another language, admit it naturally (e.g. 'oh sorry, that one went out in Russian by mistake') "
    "and restate it in the user's language. If an image is attached to the latest message you can see it, read it carefully "
    "and answer from what it really shows. Images from earlier messages are NOT visible to you: never describe them, "
    "ask the user to resend or type the part they mean. Never invent facts you cannot see."
)


def humanize_text(text):
    """Strip punctuation that gives away AI writing (';', em/en dashes used as separators)."""
    if not text:
        return text
    t = re.sub(r"[ \t]*;[ \t]*(?=\S)", ", ", text)
    t = re.sub(r"[ \t]*;[ \t]*$", ".", t, flags=re.M)
    t = re.sub(r"[ \t]+[—–][ \t]+", ", ", t)
    t = re.sub(r"(?<=\w)—(?=\w)", ", ", t)
    return t


async def generate_chat_response(*args, **kwargs):
    """Chat reply with anti AI-slop style rule + punctuation cleanup."""
    if len(args) >= 2:
        args = (args[0], (args[1] or "") + _HUMAN_STYLE_RULE) + tuple(args[2:])
    elif "system_prompt" in kwargs:
        kwargs["system_prompt"] = (kwargs["system_prompt"] or "") + _HUMAN_STYLE_RULE
    res = await _generate_chat_response_raw(*args, **kwargs)
    return humanize_text(res) if res else res


async def _generate_chat_response_raw(
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
        logger.warning("[AI Chat] %s key[%d] returned an empty response", provider, idx)
        _mark_key_failed(provider, idx)
    except Exception as e:
        logger.warning("[AI Chat] %s key[%d] failed: %s", provider, idx, e)
        _mark_key_failed(provider, idx)

    # Try the provider's remaining keys before giving up on the whole provider.
    # NOTE: never `return` from inside this loop — a failing retry used to short-circuit
    # the fallback block below and surface as an empty reply in the UI.
    for i in range(len(api_keys)):
        idx2, key2 = _next_key(api_keys, provider)
        if idx2 == idx or not key2:
            continue
        try:
            logger.info("[AI Chat] Retrying with %s key[%d]...", provider, idx2)
            res2 = await _call_chat_provider(provider, key2, formatted_messages, **kwargs)
            if res2:
                logger.info("[AI Chat] %s key[%d] succeeded", provider, idx2)
                return res2
        except Exception as e2:
            logger.warning("[AI Chat] %s key[%d] retry failed: %s", provider, idx2, e2)
            _mark_key_failed(provider, idx2)
        idx = idx2  # move past the key we just tried

    # ── Automatic Fallback to alternative providers if primary fails ──
    # Ensure the provider we actually failed on is tried again with its own keys
    # (it may have more than one) before moving down the chain.
    try:
        import database as db
        fallback_order = [provider, "chatgpt_oauth", "openai_compatible", "gemini", "groq", "openai", "deepseek"]
        seen = set()
        for alt_prov in fallback_order:
            if not alt_prov or alt_prov in seen:
                continue
            seen.add(alt_prov)
            alt_raw = await db.get_setting(f"ai_keys_{alt_prov}", "[]")
            try:
                alt_keys = json.loads(alt_raw) if alt_raw else []
            except (json.JSONDecodeError, TypeError):
                alt_keys = []
            # Only consult the legacy single-key setting when nothing is saved for
            # this provider at all — never as a silent replacement for a real list.
            if not alt_keys:
                legacy_k = await db.get_setting(f"{alt_prov}_api_key", "")
                if legacy_k and legacy_k.strip():
                    alt_keys = [legacy_k.strip()]
            alt_keys = [k.strip() for k in alt_keys if k and str(k).strip()]
            if not alt_keys:
                continue
            alt_kwargs = {"images": kwargs.get("images")} if kwargs.get("images") else {}
            if alt_prov == "openai_compatible":
                alt_kwargs["base_url"] = await db.get_setting("ai_oai_compat_base_url", "")
                alt_kwargs["model"] = await db.get_setting("ai_oai_compat_model", "")
            elif alt_prov == "chatgpt_oauth":
                alt_kwargs["base_url"] = await db.get_setting("ai_chatgpt_oauth_base_url", "")
                alt_kwargs["model"] = await db.get_setting("ai_chatgpt_oauth_model", "")
            logger.info("[AI Chat] 🔄 Trying provider '%s' (%d keys)...", alt_prov, len(alt_keys))
            for a_key in alt_keys:
                try:
                    res = await _call_chat_provider(alt_prov, a_key, formatted_messages, **alt_kwargs)
                    if res:
                        logger.info("[AI Chat] ✨ Provider '%s' succeeded!", alt_prov)
                        return res
                except Exception as _sub_fb_err:
                    logger.warning("[AI Chat] Provider '%s' key failed: %s", alt_prov, _sub_fb_err)
    except Exception as _fb_err:
        logger.warning("[AI Chat] Fallback provider error: %s", _fb_err)

    logger.error(
        "[AI Chat] All providers failed for primary provider '%s' (model=%r, base_url=%r). "
        "No reply produced — check Settings: AI provider, API keys, base URL and model name.",
        provider, kwargs.get("model"), kwargs.get("base_url"),
    )
    return None


_VISION_PROVIDERS = ("openai", "openai_compatible")


def _attach_images(messages: list[dict], images: list[str]) -> list[dict]:
    """Return a copy where the last user message carries the images (OpenAI vision format)."""
    out = [dict(m) for m in messages]
    for m in reversed(out):
        if m.get("role") == "user":
            parts = [{"type": "text", "text": m.get("content") or ""}]
            parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
            m["content"] = parts
            break
    return out


async def _call_chat_provider(provider: str, api_key: str, messages: list[dict], **kwargs) -> str | None:
    images = kwargs.pop("images", None)
    if images and provider in _VISION_PROVIDERS:
        messages = _attach_images(messages, images)
    if provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": kwargs.get("model", "gpt-4o-mini"),
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1500
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
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                   **_cf_access_headers(url)}
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1500
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await ai_request(client, "POST", url, headers=headers, json=payload)
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
            "max_tokens": 1500
        }
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    elif provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": await _groq_model(),
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1500
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
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                   **_cf_access_headers(url)}
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1500
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await ai_request(client, "POST", url, headers=headers, json=payload)
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
        " - trading_performance_1m: (the KOL's explicit trading volume/performance over the last 1 month on other exchanges, or empty string)\n"
        " - community_name: (explicit community/channel/group name, or empty string)\n"
        " - community_link: (Telegram/YouTube/Twitter/TikTok/community URL explicitly stated by the user, or empty string)\n"
        " - community_logo: (logo/avatar URL or empty string; never invent one)\n"
        " - weex_uid: (explicit WEEX UID, or empty string)\n"
        " - selected_campaign: (exact campaign name the KOL selected, or empty string)\n"
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



