"""
AI Remix Module - Supports Gemini, DeepSeek, OpenAI, Groq, and OpenAI-Compatible APIs.
Round-robin rotation across multiple API keys per provider to avoid rate limits.
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
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


_PUBLIC_AI_HOSTS = ("openai.com", "chatgpt.com", "groq.com", "googleapis.com", "deepseek.com",
                    "anthropic.com", "openrouter.ai", "mistral.ai", "together.xyz")


def _local_fallback_url(url: str) -> str | None:
    """Same path on the local 9router, or None if url is already local."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return None
    # Public providers are never the local router: rerouting them silently
    # bypassed the configured provider and handed its API key to the router.
    if host.endswith(_PUBLIC_AI_HOSTS):
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


_local_up_cache = {"t": 0.0, "up": False}


async def _local_router_up() -> bool:
    """Cached (60s) check that the local 9router answers."""
    now = time.time()
    if now - _local_up_cache["t"] < 60:
        return _local_up_cache["up"]
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(LOCAL_AI_ROUTER.rstrip("/") + "/v1/models")
            up = r.status_code < 500
    except Exception:
        up = False
    _local_up_cache.update(t=now, up=up)
    return up


async def ai_request(client: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    """HTTP call to an OpenAI-compatible router; if a remote router is blocked
    (Cloudflare Access 403, tunnel down) retry the same path on localhost.

    When the router also runs on this machine, call it directly: going out
    through the tunnel only to be blocked by Cloudflare wastes a round trip."""
    alt = _local_fallback_url(url)
    if alt and await _local_router_up():
        try:
            resp = await client.request(method, alt, **kw)
            resp.raise_for_status()
            return resp
        except Exception as e:
            # Local router hiccup: try the public endpoint as before.
            if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
                _local_up_cache["t"] = 0.0
            logger.warning("[AI] local %s failed (%s) — trying %s", alt, type(e).__name__, url)
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


# gemini-2.0-flash was retired by Google (404); thinking is disabled so the
# output budget is spent on the reply itself.
# Tried in order; on 404/429/5xx/timeout the next model is used (tested 2026-11-02).
GEMINI_MODELS = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-3.5-flash-lite"]


async def _gemini_post(api_key, payload, timeout):
    """POST to Gemini, falling through GEMINI_MODELS on retryable errors.
    Returns (data, model). Key goes in a header so it never lands in logs."""
    last_exc = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for model in GEMINI_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                resp = await client.post(url, json=payload, headers={"x-goog-api-key": api_key})
                if resp.status_code in (404, 429) or resp.status_code >= 500:
                    logger.warning(f"Gemini {model} HTTP {resp.status_code}, trying next model")
                    last_exc = httpx.HTTPStatusError(f"{model} HTTP {resp.status_code}",
                                                     request=resp.request, response=resp)
                    continue
                resp.raise_for_status()
                data = resp.json()
                if not data["candidates"][0]["content"]["parts"][0].get("text", "").strip():
                    logger.warning(f"Gemini {model} returned empty text, trying next model")
                    continue
                return data, model
            except httpx.TimeoutException as e:
                logger.warning(f"Gemini {model} timeout, trying next model")
                last_exc = e
            except (KeyError, IndexError) as e:
                logger.warning(f"Gemini {model} bad response, trying next model")
                last_exc = e
    raise last_exc or RuntimeError("All Gemini models failed")


async def _call_gemini(api_key, prompt):
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 1024,
                             "thinkingConfig": {"thinkingBudget": 0}}
    }
    data, _ = await _gemini_post(api_key, payload, 20)
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
        "Authorization": "Bearer " + (api_key or "none"),  # keyless 9Router
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
        "1. Keep every fact and selling point of the original (features, product names, numbers, percentages, terms). "
        "Aim for 80-120% of the original length; never shorten by dropping points.\n"
        "2. Change the WORDING and sentence construction each time so no two messages read alike. "
        "Keep the same overall flow (rule 7).\n"
        + lang_rule +
        "4. Do NOT use any emoji or icon characters. Write plain text only. Messages with emoji look like spam/bot.\n"
        "5. Keep all @usernames, links, and numbers exactly as-is unless instructed to reframe. "
        "Keep technical/brand terms in their original Latin form even when translating (e.g. No KYC, API, HFT, MetaTrader 5, USDT).\n"
        "6. Write naturally like a real person texting a colleague or collaborator: casual, friendly, authentic, peer-to-peer. Avoid robotic sales pitches or corporate jargon.\n"
        "7. FLOW of the single message: a warm greeting with context; then the core value "
        "(if there are 3+ points, list them compactly instead of summarizing them away); "
        "then one low-friction, polite open question.\n"
        "8. FORMAT: greeting on its own line, then short paragraphs of 1-2 sentences separated by a blank line; "
        "the closing question is its own paragraph. Never a dense wall of text.\n"
        "9. Output ONLY the rephrased message. No intro, no quotes, no explanation, no multiple message splits.\n"
        + _key_points_rule(original_text)
        + instruction_addon
        + name_hint
        + "\n\nOriginal message:\n---\n"
        + original_text
        + "\n---\nRephrased message:"
    )
    return prompt


# Fallback after the active provider. Override with the `ai_fallback_order`
# setting (JSON list) — e.g. to put a new free provider ahead of the paid ones.
DEFAULT_FALLBACK_ORDER = ["chatgpt_oauth", "gemini", "groq", "openai_compatible", "openai", "deepseek"]


async def get_fallback_order() -> list[str]:
    import database as db
    try:
        raw = await db.get_setting("ai_fallback_order", "")
        order = json.loads(raw) if raw else []
        if isinstance(order, list) and order:
            known = set(DEFAULT_FALLBACK_ORDER)
            return [p for p in order if p in known] + [p for p in DEFAULT_FALLBACK_ORDER if p not in order]
    except Exception:
        pass
    return list(DEFAULT_FALLBACK_ORDER)


async def get_provider_config(provider: str) -> tuple[list[str], dict]:
    """(keys, kwargs) for a provider. A self-hosted openai_compatible router
    (9Router) with base_url + model counts as configured even without a key."""
    import database as db
    raw = await db.get_setting(f"ai_keys_{provider}", "[]")
    try:
        keys = json.loads(raw) if raw else []
    except Exception:
        keys = []
    if not keys:
        legacy_k = await db.get_setting(f"{provider}_api_key", "")
        if legacy_k and legacy_k.strip():
            keys = [legacy_k.strip()]
    keys = [k.strip() for k in keys if k and str(k).strip()]
    kwargs = {}
    if provider in ("openai_compatible", "chatgpt_oauth"):
        prefix = "oai_compat" if provider == "openai_compatible" else "chatgpt_oauth"
        kwargs["base_url"] = (await db.get_setting(f"ai_{prefix}_base_url", "") or "").strip()
        kwargs["model"] = (await db.get_setting(f"ai_{prefix}_model", "") or "").strip()
        if provider == "openai_compatible" and not keys and kwargs["base_url"] and kwargs["model"]:
            keys = [""]
    return keys, kwargs


async def resolve_provider(agent: dict | None = None) -> tuple[str | None, list, dict]:
    """THE way to pick provider, keys and base_url/model kwargs for an AI call.

    Settings are the single source of truth (per-agent provider/model/keys are
    legacy fields that used to shadow Settings and break agents):
    1. the global `ai_provider` from Settings,
    2. the first provider in the configured fallback order that has keys,
    3. only when Settings define nothing at all: the agent's own legacy keys
       (or an agent pinned to a keyless router).
    Used by live replies, "Test agent", rule learning and scheduled-post remix,
    so they can no longer disagree.
    """
    import database as db
    primary = await db.get_setting("ai_provider", None)
    for prov in ([primary] if primary else []) + [p for p in await get_fallback_order() if p != primary]:
        keys, kwargs = await get_provider_config(prov)
        if keys:
            if prov not in ("openai_compatible", "chatgpt_oauth"):
                kwargs = {}
            return prov, keys, {k: v for k, v in kwargs.items() if v}

    if agent:
        agent_keys = agent.get("api_keys_json") or []
        if isinstance(agent_keys, str):
            try:
                agent_keys = json.loads(agent_keys)
            except Exception:
                agent_keys = []
        agent_keys = [k for k in agent_keys if k]
        legacy_kwargs = {"base_url": agent.get("base_url") or "", "model": agent.get("model") or ""}
        if agent_keys:
            return agent.get("provider") or primary or "gemini", agent_keys, legacy_kwargs
        if agent.get("provider") == "openai_compatible" and agent.get("base_url") and agent.get("model"):
            return "openai_compatible", [""], legacy_kwargs
    return primary, [], {}


async def check_providers(primary: str | None, timeout: float = 25.0) -> list[dict]:
    """Ping the active provider then every configured fallback with a tiny prompt.
    Used as a preflight before a campaign so a dead 9Router is caught up front."""
    import asyncio
    order = ([primary] if primary else []) + [p for p in await get_fallback_order() if p != primary]
    async def _ping(prov):
        keys, kwargs = await get_provider_config(prov)
        if not keys:
            return None
        t0 = time.time()
        entry = {"provider": prov, "model": kwargs.get("model") or "", "primary": prov == primary}
        try:
            reply = await asyncio.wait_for(
                _try_call(prov, keys[0], "Reply with exactly one word: OK", **kwargs), timeout)
            entry["ok"] = bool(reply and reply.strip())
            if not entry["ok"]:
                entry["error"] = "empty reply"
        except Exception as e:
            entry["ok"] = False
            entry["error"] = (str(e) or type(e).__name__)[:200]
        entry["latency_ms"] = int((time.time() - t0) * 1000)
        return entry

    # Ping in parallel: a dead 9Router no longer delays the others by `timeout`.
    results = await asyncio.gather(*(_ping(p) for p in order))
    return [r for r in results if r]


async def _auto_fallback_remix(failed_provider, prompt):
    """Fallback across alternative configured providers if primary provider fails (e.g. 429 quota, auth error)."""
    try:
        for alt_prov in await get_fallback_order():
            if alt_prov == failed_provider:
                continue
            alt_keys, alt_kwargs = await get_provider_config(alt_prov)
            if alt_keys:
                logger.info("[AI Remix] 🔄 Auto-fallback from '%s' to '%s' (%d keys available)...", failed_provider, alt_prov, len(alt_keys))
                for a_idx, a_key in enumerate(alt_keys):
                    # A key that just failed (quota/auth) is skipped until its cooldown ends,
                    # so one bad message no longer walks every key of every provider.
                    if time.time() - _key_cooldown.get((alt_prov, a_idx), 0) <= _KEY_FAIL_COOLDOWN:
                        continue
                    try:
                        res = await _try_call(alt_prov, a_key, prompt, **alt_kwargs)
                        if res and res.strip():
                            logger.info("[AI Remix] ✨ Auto-fallback to '%s' succeeded!", alt_prov)
                            return res.strip()
                    except Exception as fb_err:
                        _mark_key_failed(alt_prov, a_idx)
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

    prompt = _build_prompt(
        original_text,
        sender_name=sender_name,
        custom_instruction=custom_instruction,
        auto_translate_native=auto_translate_native,
        member_info=member_info
    )
    if not api_keys:
        logger.warning("[AI Remix] No keys for active provider '%s' — trying fallbacks", provider)
        fb = await _auto_fallback_remix(provider, prompt)
        return ensure_paragraphs(humanize_text(fb)) if fb else original_text
    idx, key = _next_key(api_keys, provider)

    try:
        result = await _try_call(provider, key, prompt, **kwargs)
        if not result or not result.strip():
            logger.warning("[AI Remix] %s key[%d] returned empty — trying fallbacks", provider, idx)
            fb = await _auto_fallback_remix(provider, prompt)
            return ensure_paragraphs(humanize_text(fb)) if fb else original_text
        logger.info(
            "[AI Remix] %s key[%d] OK - %dc -> %dc",
            provider, idx, len(original_text), len(result)
        )
        return ensure_paragraphs(humanize_text(result))

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
                        return ensure_paragraphs(humanize_text(result2))
            except Exception as e2:
                logger.warning("[AI Remix] Retry failed: %s", e2)
        
        # Auto fallback to other providers
        fb = await _auto_fallback_remix(provider, prompt)
        if fb:
            return ensure_paragraphs(humanize_text(fb))
        return original_text

    except Exception as e:
        _mark_key_failed(provider, idx)
        logger.warning("[AI Remix] %s key[%d] error: %s", provider, idx, e)
        fb = await _auto_fallback_remix(provider, prompt)
        if fb:
            return ensure_paragraphs(humanize_text(fb))
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


def ensure_paragraphs(text, max_chars=220):
    """Break dense paragraphs into short ones (blank line between) so the DM is
    easy to read on a phone. The model often ignores the formatting rule and
    returns one wall of text, so this enforces it after the fact."""
    if not text:
        return text
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if len(para) <= max_chars or "\n" in para:
            out.append(para)
            continue
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ỹЀ-ӿ\"'(])", para)
        chunk = ""
        for sent in sentences:
            if chunk and len(chunk) + len(sent) + 1 > max_chars:
                out.append(chunk)
                chunk = sent
            else:
                chunk = f"{chunk} {sent}".strip()
        if chunk:
            out.append(chunk)
    return "\n\n".join(out)


def humanize_text(text):
    """Strip punctuation that gives away AI writing (';', em/en dashes used as separators)."""
    if not text:
        return text
    t = re.sub(r"[ \t]*;[ \t]*(?=\S)", ", ", text)
    t = re.sub(r"[ \t]*;[ \t]*$", ".", t, flags=re.M)
    t = re.sub(r"[ \t]+[—–][ \t]+", ", ", t)
    t = re.sub(r"(?<=\w)—(?=\w)", ", ", t)
    return t


async def generate_chat_response(*args, style: bool = True, **kwargs):
    """Chat reply with anti AI-slop style rule + punctuation cleanup.

    style=False is for machine-read output (JSON extraction): no style rule,
    no punctuation rewriting.
    """
    if not style:
        return await _generate_chat_response_raw(*args, **kwargs)
    if len(args) >= 2:
        args = (args[0], (args[1] or "") + _HUMAN_STYLE_RULE) + tuple(args[2:])
    elif "system_prompt" in kwargs:
        kwargs["system_prompt"] = (kwargs["system_prompt"] or "") + _HUMAN_STYLE_RULE
    res = await _generate_chat_response_raw(*args, **kwargs)
    return humanize_text(res) if res else res


# Leading role tags that must never reach a lead: "[Human Admin]:",
# "[Human Admin via Forum]:", "[Admin]", "[AI]", "[Assistant]", "[Bot]".
ROLE_PREFIX_RE = re.compile(
    r"^\s*(?:\[(?:human\s*admin[^\]]*|admin|ai|assistant|bot|agent)\]\s*:?\s*)+", re.IGNORECASE)


def strip_role_prefix(text: str) -> str:
    return ROLE_PREFIX_RE.sub("", text or "")


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
    api_keys = list(api_keys or [])
    if not api_keys and provider:
        # Keyless 9Router or missing keys: still try the active provider's
        # stored config, then the fallback chain.
        api_keys, _cfg_kw = await get_provider_config(provider)
        for k, v in _cfg_kw.items():
            kwargs.setdefault(k, v)

    # Construct unified message list with system prompt
    formatted_messages = [{"role": "system", "content": system_prompt}]
    for m in messages_history:
        role = "assistant" if m.get("role") in ("assistant", "model") else "user"
        # History marks admin-typed turns "[Human Admin]: ..."; the model copied
        # that tag into its own replies, so it never sees it.
        content = ROLE_PREFIX_RE.sub("", m.get("content", "") or "").strip()
        if content:
            formatted_messages.append({"role": role, "content": content})

    idx = -1
    if api_keys and provider:
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
    for _ in range(len(api_keys) if provider else 0):
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
    # The active provider's own keys were tried above; walk the shared,
    # configurable fallback order (same as AI Remix).
    try:
        for alt_prov in await get_fallback_order():
            if not alt_prov or alt_prov == provider:
                continue
            alt_keys, alt_kwargs = await get_provider_config(alt_prov)
            if not alt_keys:
                continue
            if kwargs.get("images"):
                alt_kwargs["images"] = kwargs.get("images")
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


import contextvars as _cv

_LAST_MODEL = _cv.ContextVar("ai_last_model", default="")


def _record_model(used: str) -> None:
    _LAST_MODEL.set(used)
    logger.info("[AI] Reply generated by %s", used)


def last_model_used() -> str:
    """'model @ host' of the last successful chat call in this task ('' if unknown)."""
    return _LAST_MODEL.get()


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
            data = resp.json()
            _record_model(f"{data.get('model') or payload['model']} @ {resp.url.host}")
            return data["choices"][0]["message"]["content"].strip()

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
            _record_model(f"{data.get('model') or model} @ {resp.url.host}")
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
            data = resp.json()
            _record_model(f"{data.get('model') or payload['model']} @ {resp.url.host}")
            return data["choices"][0]["message"]["content"].strip()

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
            data = resp.json()
            _record_model(f"{data.get('model') or payload['model']} @ {resp.url.host}")
            return data["choices"][0]["message"]["content"].strip()

    elif provider == "gemini":
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
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1000,
                                 "thinkingConfig": {"thinkingBudget": 0}}
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        data, model = await _gemini_post(api_key, payload, 25)
        _record_model(f"{data.get('modelVersion') or model} @ generativelanguage.googleapis.com")
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
            _record_model(f"{data.get('model') or model} @ {resp.url.host}")
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
        # JSON task: skip the chat style rule and punctuation humanizer.
        raw_res = await generate_chat_response(
            style=False,
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
        # JSON task: skip the chat style rule and punctuation humanizer.
        raw_res = await generate_chat_response(
            style=False,
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



