"""
Message Merger — Consolidates multiple message bubbles into a single cohesive message.

Prevents rapid-fire notification spam by combining multiple text fragments
into one beautifully formatted message with paragraph breaks, or attaching
all text as a single caption to an accompanying media file.

Usage:
    from message_merger import merge_messages

    merged = merge_messages([
        {"msg_type": "text", "content": "Hey John 👋"},
        {"msg_type": "text", "content": "I noticed your work in crypto."},
        {"msg_type": "text", "content": "Would you be open to a chat?"}
    ])
    # Returns a single-element list:
    # [{"msg_type": "text", "content": "Hey John 👋\n\nI noticed your work in crypto.\n\nWould you be open to a chat?"}]
"""

import re
from typing import List, Dict, Any


def clean_paragraph_spacing(text: str) -> str:
    """Normalize paragraph spacing, ensuring double newlines between paragraphs
    and removing excessive blank lines (> 2 newlines)."""
    if not text:
        return ""
    # Normalize Windows CRLF to LF
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    # Replace 3 or more consecutive newlines with 2 newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", normalized)
    return cleaned.strip()


def merge_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge a list of message dicts into a single cohesive message structure.

    Rules:
    1. If empty or single message, return cleaned single message.
    2. Collect all text contents in order.
    3. If there is a media message (photo/video/document), pick the first media item
       and attach the combined text as its caption.
    4. If all messages are text, return a single text message with combined content
       separated by double newlines (\\n\\n).

    Parameters
    ----------
    messages : list of dict
        List of message dicts with keys 'msg_type', 'content', 'media_path', etc.

    Returns
    -------
    list of dict
        A list containing at most 1 combined message dict (or empty if no messages).
    """
    if not messages:
        return []

    # Sort by msg_order if present
    sorted_msgs = sorted(messages, key=lambda m: m.get("msg_order", 0))

    # If only 1 message, just clean its spacing
    if len(sorted_msgs) == 1:
        single = dict(sorted_msgs[0])
        if single.get("content"):
            single["content"] = clean_paragraph_spacing(single["content"])
        return [single]

    # Collect texts and find any media
    text_parts: List[str] = []
    primary_media: Dict[str, Any] | None = None

    for m in sorted_msgs:
        content = (m.get("content") or "").strip()
        msg_type = m.get("msg_type", "text")
        media_path = m.get("media_path")

        if content:
            text_parts.append(content)

        # First media encountered becomes the primary media
        if msg_type in ("photo", "video", "document") and media_path and not primary_media:
            primary_media = m

    combined_text = clean_paragraph_spacing("\n\n".join(text_parts))

    # If we have a media file, combine all text into its caption
    if primary_media:
        return [{
            "msg_type": primary_media.get("msg_type", "photo"),
            "content": combined_text,
            "media_path": primary_media.get("media_path"),
            "msg_order": 0
        }]

    # All text messages -> single combined text message
    return [{
        "msg_type": "text",
        "content": combined_text,
        "media_path": None,
        "msg_order": 0
    }]
