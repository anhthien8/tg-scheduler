"""Owed answers — "our team will review and get back to you" promises (v3.36).

When our side tells a KOL that the team will check / review / come back, the chat
then waits on US. Nothing used to track that, so promises went unanswered for days.
This module only detects the promise in an outgoing message; storage lives on
ai_followup_chats (owed_since / owed_note) and the reminder in dm_reply_tracker.
Pure functions, no I/O.
"""
import re

OWED_DEADLINE_HOURS = 24
HUMAN_PREFIX = "[Human Admin]:"

_PROMISE_PATTERNS = [
    # "our team / manager will review, check, get back, reach out ..."
    r"\b(team|manager|admin|colleagues?|boss|committee)\b[^.!?\n]{0,60}"
    r"\b(review\w*|check\w*|look\w*|get back|come back|reach out|confirm\w*|follow up|contact you|verif\w+)\b",
    # "I'll / let me / we'll check, confirm, pass it on, get back, let you know ..."
    r"\b(i'?ll|i will|let me(?! know)|we'?ll|we will|i'?m going to)\b[^.!?\n]{0,40}"
    r"\b(check|double[- ]check|confirm|verify|pass (this|it|that)|forward|get back|come back|"
    r"let you know|update you|loop in|ask (the|my|our) team|take a look|look into)\b",
    r"\b(get|come) back to you\b",
    r"\bloop(ing)? in\b",
    r"\b(is|are|we'?re|i'?m) (reviewing|checking|looking into)\b",
    # Vietnamese
    r"(team|sếp|quản lý|bên (mình|em|anh)|mình|em|anh)\b[^.!?\n]{0,50}"
    r"(kiểm tra|check lại|xem lại|xem xét|báo lại|phản hồi lại|xác nhận lại|hỏi lại|trả lời lại)",
    r"sẽ (báo|phản hồi|trả lời|liên hệ) lại",
    # Russian
    r"(команд\w+|менеджер\w*|коллег\w*|руководств\w+)[^.!?\n]{0,60}"
    r"(провер\w+|рассмотр\w+|свяж\w+|верн\w+|ответ\w+|уточн\w+|посмотр\w+)",
    r"\b(уточню|проверю|передам|вернусь|сообщу|дам знать|посмотрю)\b",
]
_PROMISE_RE = re.compile("|".join(f"(?:{p})" for p in _PROMISE_PATTERNS), re.IGNORECASE)
# Tags the AI appends for the app only (never part of the promise text).
_TAG_RE = re.compile(r"\[[A-Z_]{3,}(?::[^\]]*)?\]")


def is_human_message(text) -> bool:
    return str(text or "").lstrip().startswith(HUMAN_PREFIX)


_HUMAN_TYPED_RE = re.compile(r"^\s*\[Human Admin[^\]]*\]:")   # also "[Human Admin via Forum]:"


def lead_waiting_on_us(row: dict) -> bool:
    """A person on our side owes this lead an answer — the 💬 Hàng chờ trả lời rule
    (ai_followup.js _queueGroup > 0): an open promise, the KOL spoke last on a
    human-owned lead, or the AI handed over and its own message is still the last one.
    `row` needs status, owed_since, last_role, last_text (closed leads are the caller's call)."""
    if row.get("owed_since"):
        return True
    status, last_role = row.get("status"), row.get("last_role")
    if status in ("needs_human", "paused_admin") and last_role == "user":
        return True
    return (status == "needs_human" and last_role == "assistant"
            and not _HUMAN_TYPED_RE.match(row.get("last_text") or ""))


def detect_promise(text) -> str | None:
    """Short note (the sentence carrying the promise) when `text` promises a later answer."""
    clean = _TAG_RE.sub(" ", str(text or ""))
    m = _PROMISE_RE.search(clean)
    if not m:
        return None
    start = max(clean.rfind(c, 0, m.start()) for c in ".!?\n") + 1
    ends = [i for i in (clean.find(c, m.end()) for c in ".!?\n") if i != -1]
    end = min(ends) + 1 if ends else len(clean)
    return " ".join(clean[start:end].split())[:200] or None


def owed_from_history(history) -> dict | None:
    """Replay a stored history: {'since': iso timestamp, 'note': str} when a promise
    from our side is still open (no human admin message after it), else None."""
    owed = None
    for m in history or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        content = m.get("content") or ""
        if is_human_message(content):
            owed = None
            continue
        if owed is None:
            note = detect_promise(content)
            if note:
                owed = {"since": m.get("timestamp") or "", "note": note}
    return owed
