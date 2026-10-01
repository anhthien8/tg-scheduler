"""Regression test: forum-topic creation must use the CORRECT Telethon module.

Real bug: `create_forum_topic` imported CreateForumTopicRequest from
`telethon.tl.functions.channels`, where it does NOT exist on Telethon 1.44.
The ImportError was swallowed by the relay's error handler, so no topic was
EVER created — the Telegram group stayed empty forever with no visible error.

This class of bug is invisible to unit tests that mock the Telegram client:
the import happens INSIDE the method, so only executing it (or asserting on the
import) reveals the failure. These tests do the latter.
"""
import inspect

import pytest


def test_create_forum_topic_request_importable_from_messages():
    """The request class must exist in .messages (the real location)."""
    from telethon.tl.functions.messages import CreateForumTopicRequest

    assert CreateForumTopicRequest is not None
    assert CreateForumTopicRequest.CONSTRUCTOR_ID == 0x2F98C3D5


def test_create_forum_topic_request_is_not_in_channels():
    """Guard the wrong module — if this ever passes, the fix has been undone."""
    with pytest.raises(ImportError):
        from telethon.tl.functions.channels import CreateForumTopicRequest  # noqa: F401


def test_adapter_uses_peer_keyword_not_channel():
    """CreateForumTopicRequest takes `peer`; `channel=` would raise TypeError."""
    import command_bot

    src = inspect.getsource(command_bot._TelethonForumBot.create_forum_topic)
    assert "from telethon.tl.functions.messages import CreateForumTopicRequest" in src
    assert "telethon.tl.functions.channels" not in src
    assert "peer=entity" in src
    assert "channel=entity" not in src


def test_request_signature_accepts_our_arguments():
    """The call we build must match the real signature."""
    from telethon.tl.functions.messages import CreateForumTopicRequest

    params = inspect.signature(CreateForumTopicRequest.__init__).parameters
    for required in ("peer", "title", "random_id"):
        assert required in params, f"CreateForumTopicRequest lost parameter {required!r}"
