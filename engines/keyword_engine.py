"""
engines.keyword_engine — Platform-agnostic keyword matching
===========================================================

Pure utility functions extracted from ``keyword_watcher.py``.
No Telegram / Telethon imports — safe to use from any platform.
"""
import logging
import re

logger = logging.getLogger("tg-scheduler.engines.keyword")


# ── ID Helpers ──────────────────────────────────────────────────

def clean_id(telegram_id: int) -> int:
    """
    Strip the ``-100`` prefix that Telegram Bot-API IDs carry.

    >>> clean_id(-1001234567890)
    1234567890
    >>> clean_id(42)
    42
    """
    s = str(abs(telegram_id))
    if len(s) > 10 and s.startswith("100"):
        return int(s[3:])
    return int(s)


# ── Text Normalisation ─────────────────────────────────────────

def normalize_text(t: str) -> str:
    """
    Lower-case, collapse whitespace, and strip invisible Unicode
    characters (zero-width spaces, BOM, etc.).
    """
    if not t:
        return ""
    # Remove zero-width spaces and formatting marks
    t = re.sub(r'[\u200b-\u200d\ufeff]', '', t)
    # Replace any sequence of whitespaces with a single space
    t = re.sub(r'\s+', ' ', t.lower())
    return t.strip()


# ── Keyword Matching ───────────────────────────────────────────

def match_keyword_advanced(text: str, rule: str) -> bool:
    """
    Match *text* against a *rule* string.

    Supported rule formats
    ----------------------
    1. **Regex** — prefix with ``re:``
       e.g. ``re:\\b(solana|sol)\\b``
    2. **Logic expression** — ``AND``, ``OR``, ``NOT`` operators
       (case-insensitive).
       e.g. ``solana AND gem NOT bot``
    3. **Plain substring** — fallback, standard ``in`` check.
    """
    text_lower = text.lower()
    rule_trimmed = rule.strip()

    # 1. Regex Match
    if rule_trimmed.lower().startswith("re:"):
        pattern = rule_trimmed[3:].strip()
        try:
            return bool(re.search(pattern, text, re.IGNORECASE))
        except Exception as e:
            logger.warning(f"Invalid regex pattern '{pattern}' in watcher: {e}")
            return False

    # 2. Logic Operators Match (AND, OR, NOT)
    # Split by OR
    or_clauses = re.split(r'\s+or\s+', rule_trimmed, flags=re.IGNORECASE)
    for clause in or_clauses:
        # Split by NOT
        not_parts = re.split(r'\s+not\s+', clause, flags=re.IGNORECASE)
        positive_clause = not_parts[0]
        negative_clauses = not_parts[1:]

        # Check positive part (AND split)
        and_parts = re.split(r'\s+and\s+', positive_clause, flags=re.IGNORECASE)
        pos_match = all(part.strip().lower() in text_lower for part in and_parts if part.strip())

        # Check negative parts
        neg_match = not any(neg.strip().lower() in text_lower for neg in negative_clauses if neg.strip())

        if pos_match and neg_match:
            return True

    return False
