"""Pick the knowledge-base passages relevant to the current conversation.

Sending a 20k+ character KB on every AI turn is slow, costly and makes the
model parrot internal wording. This keeps the KB's opening (core policy) and
adds the paragraphs that share the most words with the lead's latest
messages, within a character budget, in their original order.
"""
import re

_WORD_RE = re.compile(r"[^\W\d_]{3,}|\d{2,}", re.UNICODE)
_STOP = {
    "the", "and", "for", "you", "your", "are", "with", "that", "this", "have", "not", "can",
    "what", "how", "about", "from", "will", "our", "but", "any", "all", "its", "was",
    "của", "và", "cho", "các", "những", "được", "không", "với", "này", "mình", "bạn",
}


def _words(text: str) -> set[str]:
    return {w for w in (m.lower() for m in _WORD_RE.findall(text or "")) if w not in _STOP}


def _chunks(kb: str) -> list[str]:
    """Blank-line paragraphs; a short heading line sticks to the paragraph after it."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", kb) if p.strip()]
    out: list[str] = []
    carry = ""
    for p in paras:
        if "\n" not in p and len(p) < 90:  # heading
            carry = (carry + "\n" + p).strip()
            continue
        out.append((carry + "\n" + p).strip() if carry else p)
        carry = ""
    if carry:
        out.append(carry)
    return out


def select_kb(kb: str, query: str, budget: int = 8000, core: int = 2500) -> str:
    kb = (kb or "").strip()
    if len(kb) <= budget:
        return kb
    chunks = _chunks(kb)
    chosen: set[int] = set()
    used = 0
    # Core: the KB's opening paragraphs (headline policy) always go in.
    for i, c in enumerate(chunks):
        if used + len(c) > core and chosen:
            break
        chosen.add(i)
        used += len(c)
    q = _words(query)
    scored = sorted(
        ((len(q & _words(c)), i) for i, c in enumerate(chunks) if i not in chosen),
        key=lambda t: (-t[0], t[1]),
    )
    for score, i in scored:
        if score == 0:
            break
        if used + len(chunks[i]) > budget:
            continue
        chosen.add(i)
        used += len(chunks[i])
    return "\n\n".join(chunks[i] for i in sorted(chosen))
