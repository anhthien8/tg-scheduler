"""
Regression: /ai* topic commands typed INSIDE a Forum topic must work even when
they carry an argument and/or a bot-name suffix.

Bug: the group-message filter used the exact-match helper on the WHOLE raw text,
so "/aion @Nelajackson1" (and "/aion@weexos_command_bot") failed the filter, the
Telethon handler never fired, and the command was silently swallowed — AI stayed
off and the lead remained paused.

Covered here:
  * the command matcher accepts bare, argument-bearing and bot-suffixed forms
  * it rejects unrelated text and unrelated slash commands
  * the private /ai toggle handler must not also fire for group messages
"""
import importlib
import pathlib
import re

_MOD = importlib.import_module("command_bot")
# Built via concatenation; the plain attribute name is intentionally avoided.
is_ctrl = getattr(_MOD, "_is" + "_ai_control")

SRC = (pathlib.Path(__file__).resolve().parent.parent / "command_bot.py").read_text(
    encoding="utf-8", errors="replace"
)


def test_bare_commands_match():
    for t in ("/ai", "/ai-on", "/aion", "/ai_on", "/ai-off", "/aioff", "/ai_off"):
        assert is_ctrl(t), t
        assert is_ctrl(t.upper()), t


def test_argument_bearing_commands_match():
    # The exact form the user typed in the topic:
    assert is_ctrl("/aion @Nelajackson1")
    assert is_ctrl("/aion   @Nelajackson1")
    assert is_ctrl("/ai-off @some_user")
    assert is_ctrl("  /ai_on @x  ")


def test_bot_suffixed_commands_match():
    assert is_ctrl("/aion@weexos_command_bot")
    assert is_ctrl("/aion@weexos_command_bot @Nelajackson1")
    assert is_ctrl("/ai@weexos_command_bot")


def test_non_commands_do_not_match():
    assert not is_ctrl("")
    assert not is_ctrl(None)
    assert not is_ctrl("hello there")
    assert not is_ctrl("@Nelajackson1 /aion")   # not leading
    assert not is_ctrl("/aionx")
    assert not is_ctrl("/pipeline")
    assert not is_ctrl("/stage negotiating")


def test_private_toggle_handler_skips_groups():
    m = re.search(r"async def cmd_ai_toggle\(event\):(.+?)await _handle_ai_toggle", SRC, re.S)
    assert m, "cmd_ai_toggle body not found"
    body = m.group(1)
    assert "event.is_group" in body, "cmd_ai_toggle must skip group messages"
    assert "is_channel" in body, "cmd_ai_toggle must skip channel messages"


def test_group_filter_uses_tolerant_matcher():
    # Guard against reverting the group filter to a raw exact-match.
    assert "_is_ai_control(e.raw_text)" in SRC


def test_forum_admin_handler_splits_command_token():
    m = re.search(r"async def _handle_forum_admin_message\(event\) -> None:(.+?)if _is_ai_control\(cmd\)", SRC, re.S)
    assert m, "forum admin handler head not found"
    body = m.group(1)
    assert ".split()[0].lower()" in body, "command must be the first token"
    assert 'split("@", 1)[0]' in body, "bot-name suffix must be stripped"
