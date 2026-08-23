"""
Personalization — Variable substitution for DM message content.

Replaces placeholders like {name}, {first_name}, {last_name}, {full_name},
{username} with actual recipient data before sending.

Usage:
    from personalization import apply_personalization

    text = apply_personalization("Hey {name}! 👋", {
        "first_name": "John",
        "last_name": "Doe",
        "username": "johndoe",
    })
    # => "Hey John! 👋"
"""

import re

# Default fallback when both first_name and username are unavailable
_DEFAULT_NAME_FALLBACK = "friend"


def apply_personalization(text: str, member_info: dict) -> str:
    """Replace personalization variables in message text.

    Supported variables (case-insensitive):
      {name}       — first_name, falls back to username, then "friend"
      {first_name} — Telegram first_name (or fallback)
      {last_name}  — Telegram last_name (or "")
      {full_name}  — first_name + last_name combined
      {username}   — Telegram username without @ (or "")

    Parameters
    ----------
    text : str
        The message content potentially containing {variable} placeholders.
    member_info : dict
        Recipient info with keys: first_name, last_name, username.
        All keys are optional; missing/None values use sensible fallbacks.

    Returns
    -------
    str
        The text with all recognized placeholders replaced.
    """
    if not text:
        return ""
    if member_info is None:
        return text

    first_name = (member_info.get("first_name") or "").strip()
    last_name = (member_info.get("last_name") or "").strip()
    username = (member_info.get("username") or "").strip()

    # Build derived values
    name = first_name or username or _DEFAULT_NAME_FALLBACK
    full_name = f"{first_name} {last_name}".strip() if first_name else (username or _DEFAULT_NAME_FALLBACK)

    # Replacement map (lowercase key -> value)
    replacements = {
        "name": name,
        "first_name": first_name or username or _DEFAULT_NAME_FALLBACK,
        "last_name": last_name,
        "full_name": full_name,
        "username": username,
    }

    # Case-insensitive replacement using regex
    # Matches {name}, {Name}, {NAME}, {first_name}, {FIRST_NAME}, etc.
    def _replace_match(match):
        key = match.group(1).lower()
        if key in replacements:
            return replacements[key]
        # Not a recognized variable — leave as-is
        return match.group(0)

    result = re.sub(r"\{(\w+)\}", _replace_match, text)
    return result
