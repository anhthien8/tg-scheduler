"""Unit tests for personalization.apply_personalization.

Pure function — no mocking needed. (tests/test_personalization.py already
covers some cases; these add the edge cases most likely to regress outreach
copy: fallback name, unknown vars, case-insensitivity.)
"""
from personalization import apply_personalization


def test_replaces_first_name():
    assert apply_personalization("Hey {name}!", {"first_name": "John"}) == "Hey John!"


def test_falls_back_to_username_then_friend():
    assert apply_personalization("Hi {name}", {"username": "jdoe"}) == "Hi jdoe"
    assert apply_personalization("Hi {name}", {}) == "Hi friend"


def test_full_name_combines_first_and_last():
    assert apply_personalization("{full_name}", {"first_name": "A", "last_name": "B"}) == "A B"


def test_last_name_defaults_to_empty():
    assert apply_personalization("[{last_name}]", {"first_name": "A"}) == "[]"


def test_unknown_variable_is_left_intact():
    assert apply_personalization("Hi {unknown_var}", {"first_name": "A"}) == "Hi {unknown_var}"


def test_case_insensitive_replacement():
    assert apply_personalization("{NAME} {First_Name}", {"first_name": "Zed"}) == "Zed Zed"


def test_empty_text_returns_empty():
    assert apply_personalization("", {"first_name": "A"}) == ""


def test_none_member_info_returns_text_unchanged():
    assert apply_personalization("Hi {name}", None) == "Hi {name}"


def test_whitespace_only_name_uses_fallback():
    assert apply_personalization("{name}", {"first_name": "   "}) == "friend"


def test_group_placeholder_from_group_title():
    assert apply_personalization("saw you in {group}", {"group_title": "Crypto VN"}) == "saw you in Crypto VN"


def test_community_alias_matches_group():
    info = {"community_hint": "Trading Hub"}
    assert apply_personalization("{community}", info) == "Trading Hub"


def test_group_empty_when_absent():
    assert apply_personalization("[{group}]", {"first_name": "A"}) == "[]"
