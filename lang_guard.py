"""Deterministic language check: a message must be readable by the person getting it.

The AI is *asked* to write in the right language, but asking is not a guarantee
(and a template is sent as written). This module decides from the characters
alone, with no AI call:

* ``text_script``          – which writing system / language a text is in.
* ``lang_family``          – the writing system a language code uses.
* ``conversation_script``  – what the other person actually writes in a chat.
* ``wrong_language``       – a reason string when a text does not fit its reader.
"""
import re
import unicodedata

LATIN = "latin"

# (first code point, last code point, family)
_RANGES = (
    (0x3040, 0x30FF, "ja"),    # kana
    (0xAC00, 0xD7AF, "ko"),
    (0x4E00, 0x9FFF, "han"),
    (0x3400, 0x4DBF, "han"),
    (0x0400, 0x052F, "cyr"),
    (0x0600, 0x06FF, "ar"),
    (0x0750, 0x077F, "ar"),
    (0x0590, 0x05FF, "he"),
    (0x0900, 0x097F, "hi"),
    (0x0980, 0x09FF, "bn"),
    (0x0E00, 0x0E7F, "th"),
)

# Letters only Vietnamese uses. â ê ô ă đ are left out on purpose: French,
# Portuguese, Romanian and Croatian use them too.
_VI_ONLY = set("ơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")

_FAMILY = {
    "zh": "zh", "ja": "ja", "ko": "ko", "th": "th", "he": "he", "vi": "vi",
    "hi": "hi", "mr": "hi", "ne": "hi", "bn": "bn",
    "ru": "cyr", "uk": "cyr", "bg": "cyr", "be": "cyr", "kk": "cyr", "ky": "cyr",
    "sr": "cyr", "mk": "cyr", "mn": "cyr", "tg": "cyr",
    "ar": "ar", "fa": "ar", "ur": "ar", "ps": "ar",
}

FAMILY_NAMES = {
    "vi": "Vietnamese", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "th": "Thai",
    "he": "Hebrew", "hi": "Hindi", "bn": "Bengali", "cyr": "Russian (Cyrillic script)",
    "ar": "Arabic script", LATIN: "English / Latin script",
}

# Tags, links, @names and bracketed placeholders ("[Attached Video Proof]",
# "[No text in this message — …]") say nothing about the writer's language.
_NOISE = re.compile(r"<[^>]*>|https?://\S+|www\.\S+|t\.me/\S+|@\w+|\[[^\]]{0,300}\]")
# Vowels with a tone mark (also used by Spanish, French…, but far less densely).
_ACCENTED = set("àáâãèéêìíòóôõùúýăđĩũơư") | _VI_ONLY
# Vietnamese typed without tone marks ("khong", "duoc", "roi"): words that are
# not English. Two different ones in a message count as Vietnamese.
_VI_PLAIN = {"khong", "ko", "dc", "duoc", "nhe", "nha", "roi", "minh", "nhieu", "vay", "toi", "biet",
             "oke", "chua", "cung", "nguoi", "tien", "anh", "mua", "thue", "cach", "gi", "lam", "nhi",
             "hok", "hem", "nua", "bao", "ben", "nhan", "gui", "cam", "on"}
# A Latin text this short ("ok", "thanks bro", a UID) proves nothing about language.
_MIN_LATIN_LETTERS = 12
# Only a real sentence is judged when the check is "Latin text for a non-Latin reader".
_MIN_STRICT_LETTERS = 40
_HUMAN_ADMIN = "[Human Admin]:"


def _counts(text) -> dict:
    out: dict = {}
    for ch in _NOISE.sub(" ", str(text or "")):
        if not ch.isalpha():
            continue
        cp = ord(ch)
        fam = LATIN if cp < 0x0250 or 0x1E00 <= cp <= 0x1EFF else ""
        if not fam:
            for lo, hi, name in _RANGES:
                if lo <= cp <= hi:
                    fam = name
                    break
        if fam:
            out[fam] = out.get(fam, 0) + 1
    return out


def _letters(text) -> int:
    return sum(_counts(text).values())


def text_script(text) -> str:
    """'vi', 'zh', 'ja', 'ko', 'cyr', 'ar', 'he', 'hi', 'bn', 'th', 'latin' or ''."""
    c = _counts(text)
    total = sum(c.values())
    if total < 2:
        return ""
    if c.get("ja") and c.get("ja", 0) + c.get("han", 0) >= total / 2:
        return "ja"
    # A text that is a third Chinese / Russian... is in that language, even when
    # brand names and terms (WEEX, KOL, USDT) make Latin letters the majority.
    other = {k: v for k, v in c.items() if k != LATIN}
    top = max(other, key=other.get) if other else LATIN
    if top != LATIN and other[top] < total * 0.3:
        top = LATIN
    if top == "han":
        return "zh"
    if top == LATIN:
        low = _NOISE.sub(" ", str(text)).lower()
        if any(ch in _VI_ONLY for ch in low):
            return "vi"
        # Vietnamese marks almost every other word; Spanish / French / Portuguese do not.
        accented = sum(1 for ch in low if ch in _ACCENTED)
        if accented >= 3 and accented >= total * 0.12:
            return "vi"
    return top


def _reads_as(text) -> str:
    """Like text_script, but for a message the *other person* wrote: a mixed
    message counts for its non-Latin part, and Vietnamese typed without tone
    marks is still Vietnamese."""
    script = text_script(text)
    if script == LATIN:
        plain = unicodedata.normalize("NFD", _NOISE.sub(" ", str(text)).lower().replace("đ", "d"))
        plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
        words = set(re.findall(r"[a-z]+", plain))
        if len(words & _VI_PLAIN) >= 2:
            return "vi"
    return script


def lang_family(code) -> str:
    """Writing system of a language code ('en' -> 'latin', 'uk' -> 'cyr')."""
    code = str(code or "").strip().lower().replace("_", "-").split("-")[0]
    if not code:
        return ""
    return _FAMILY.get(code, LATIN)


def conversation_scripts(history) -> list:
    """Every language the other person has shown they write, most recent first.

    Looks at the person's recent messages (and what our human admin wrote to
    them). A non-English message is positive evidence; a Latin message only
    counts when it is a real phrase, so "Okie" after a Vietnamese conversation
    does not make the chat English. Empty list = the chat gives no evidence.
    """
    seen: list = []
    n = 0
    for m in reversed(history or []):
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        role = m.get("role")
        is_admin = role == "assistant" and content.lstrip().startswith(_HUMAN_ADMIN)
        if role != "user" and not is_admin:
            continue
        if is_admin:
            content = content.lstrip()[len(_HUMAN_ADMIN):]
        n += 1
        if n > 12:
            break
        script = _reads_as(content)
        if not script:
            continue
        if script == LATIN:
            clean = _NOISE.sub(" ", content)
            if is_admin or _letters(clean) < _MIN_LATIN_LETTERS or len(clean.split()) < 3:
                continue
        if script not in seen:
            seen.append(script)
    return seen


def conversation_script(history) -> str:
    """The language the other person used most recently ('' = no evidence)."""
    seen = conversation_scripts(history)
    return seen[0] if seen else ""


def wrong_language(text, reader, strict: bool = False) -> str:
    """Reason string when `text` is not in a language the reader uses, else ''.

    `reader` is one family or a list of them (see conversation_scripts).
    Default: only a text in a clearly different language is refused (Chinese,
    Russian, Vietnamese… for someone who does not read it). English for a
    non-English reader passes, because profile hints are weak evidence.
    ``strict=True`` is for a chat where we have seen the person write: there a
    full Latin sentence to a Vietnamese / Russian / Chinese writer is refused too.
    """
    readers = [reader] if isinstance(reader, str) else list(reader or [])
    readers = [r for r in readers if r]
    if not readers:
        return ""
    script = text_script(text)
    if not script or script in readers:
        return ""
    if script == LATIN:
        if not strict or _letters(text) < _MIN_STRICT_LETTERS:
            return ""
    return (f"message is in {FAMILY_NAMES.get(script, script)} but the reader uses "
            f"{FAMILY_NAMES.get(readers[0], readers[0])}")


def language_rule(history) -> str:
    """Prompt line telling the AI which language to write in for this chat."""
    fam = conversation_script(history)
    if not fam or fam == LATIN:
        return ("\n\nLANGUAGE (mandatory): write in the same language this person used in their own "
                "messages above. Never switch to another language.")
    return (f"\n\nLANGUAGE (mandatory): this person writes in {FAMILY_NAMES.get(fam, fam)}. "
            f"Write the whole message in that language, naturally, like a native speaker. "
            f"Do NOT write in English.")
