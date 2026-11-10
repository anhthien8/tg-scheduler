"""Last-line guard: internal AI control tags must never reach a real chat.

The AI is told to append markers such as ``[HANDOVER_REQUIRED]`` or
``[METRICS: {...}]`` so the app can react to them. They are instructions for
the app, not text for the KOL. Every outbound path goes through this module:

* ``clean_ai``  – for AI-written messages: removes every control marker and
  reports whether the AI asked for a human.
* ``scrub``     – for the lowest send layer (any text / caption, also manual
  and template sends): removes only markers that can never be legitimate text.
* ``is_blank``  – True when nothing sendable is left.
"""
import logging
import re

logger = logging.getLogger("tg-scheduler.outbound_guard")

_HANDOVER = re.compile(r"\[?\s*HANDOVER[_ ]REQUIRED\s*\]?", re.IGNORECASE)
# The METRICS / META block is always the tail of an AI reply.
_METRICS = re.compile(r"\[?\s*(?:METRICS|META)\s*:\s*\{.*$", re.IGNORECASE | re.DOTALL)
_NO_TEXT = re.compile(r"\[No text in this message[^\]\n]*\]", re.IGNORECASE)
_STEP = re.compile(r"\[\s*(?:DEAL_AGREED|FIRST_POST|STOP|DECLINED|ONBOARDED)\s*\]", re.IGNORECASE)
# Any other bracketed UPPER_SNAKE token, e.g. [NEEDS_REVIEW], [STEP_DONE].
_SNAKE = re.compile(r"\[\s*[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\s*\]")
# Echoed prompt headers such as "[STEP REMINDER INSTRUCTION]:".
_INSTRUCTION = re.compile(r"\[[A-Z][A-Z0-9 _\-]{0,60}\bINSTRUCTION\]\s*:?")

_SYSTEM = (_METRICS, _HANDOVER, _NO_TEXT)
_AI_ONLY = (_STEP, _SNAKE, _INSTRUCTION)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t  ]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_blank(text) -> bool:
    """True when the text has nothing a person could read."""
    return not re.sub(r"<[^>]*>|&nbsp;|[\s​‌‍﻿]", "", str(text or ""))


def scrub(text):
    """Remove markers that are never legitimate text. Unchanged when clean."""
    if not text or not isinstance(text, str):
        return text
    out = text
    for pat in _SYSTEM:
        out = pat.sub("", out)
    if out == text:
        return text
    return _tidy(out)


def clean_ai(text) -> tuple[str, bool]:
    """Clean an AI-written message. Returns (text, ai_asked_for_a_human)."""
    raw = str(text or "")
    handover = bool(_HANDOVER.search(raw))
    out = raw
    for pat in _SYSTEM + _AI_ONLY:
        out = pat.sub("", out)
    if out == raw:
        return raw.strip(), handover
    out = _tidy(out)
    return ("" if is_blank(out) else out), handover


def guard_outgoing(text, where: str = ""):
    """For the send layer: returns the text to send, or None to send nothing.

    None means the message consisted only of internal markers.
    """
    if not text or not isinstance(text, str):
        return text
    clean = scrub(text)
    if clean is not text and clean != text:
        logger.error("[OutboundGuard] 🚫 Internal marker removed before sending (%s): %r", where, text[:120])
        if is_blank(clean):
            return None
    return clean


class BlockedOutbound(ValueError):
    """Raised instead of sending a message that was only internal markers."""


def require(text, where: str = ""):
    """Like guard_outgoing, but raises BlockedOutbound when nothing is left."""
    clean = guard_outgoing(text, where)
    if clean is None:
        raise BlockedOutbound(f"message blocked ({where}): it contained only internal markers")
    return clean
