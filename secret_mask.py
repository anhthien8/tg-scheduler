"""Mask API keys before they reach the browser, and map masks back on save.

The settings / agent forms read keys back for editing. Sending the real keys
let anyone with dashboard access copy them from DevTools. The browser now gets
"••••••••abcd"; when the form is saved or a "test" button is pressed with that
masked value, it is resolved back to the stored key with the same ending.
"""
import json

MASK = "••••••••"


def mask(key: str) -> str:
    key = str(key or "")
    if not key:
        return ""
    return MASK + (key[-4:] if len(key) >= 12 else "")


def is_masked(value) -> bool:
    return isinstance(value, str) and value.startswith(MASK)


def mask_list(keys) -> list[str]:
    return [mask(k) for k in (keys or []) if k]


def resolve(submitted, stored) -> list[str]:
    """Replace masked entries in `submitted` by the matching stored key.

    New plain keys are kept as typed; a masked entry with no stored match is
    dropped (it can only come from a stale form).
    """
    pool = [str(k) for k in (stored or []) if k]
    out: list[str] = []
    for item in submitted or []:
        item = str(item or "").strip()
        if not item:
            continue
        if not is_masked(item):
            out.append(item)
            continue
        match = next((k for k in pool if mask(k) == item), None)
        if match:
            pool.remove(match)
            out.append(match)
    return out


def loads_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw) if raw else []
        return val if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []
