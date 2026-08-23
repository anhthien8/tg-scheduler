"""
Tests for personalization.py — variable substitution in DM messages.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personalization import apply_personalization


class TestApplyPersonalization:
    """Tests for apply_personalization()."""

    def test_all_variables_replaced(self):
        text = "Hey {name}! First: {first_name}, Last: {last_name}, Full: {full_name}, User: {username}"
        result = apply_personalization(text, {
            "first_name": "John",
            "last_name": "Doe",
            "username": "johndoe",
        })
        assert result == "Hey John! First: John, Last: Doe, Full: John Doe, User: johndoe"

    def test_name_fallback_to_username(self):
        """When first_name is empty, {name} should fall back to username."""
        result = apply_personalization("Hey {name}!", {
            "first_name": "",
            "last_name": "",
            "username": "crypto_king",
        })
        assert result == "Hey crypto_king!"

    def test_name_fallback_to_friend(self):
        """When both first_name and username are empty, {name} should be 'friend'."""
        result = apply_personalization("Hey {name}!", {
            "first_name": "",
            "last_name": "",
            "username": "",
        })
        assert result == "Hey friend!"

    def test_name_fallback_none_values(self):
        """When all values are None, should use fallback."""
        result = apply_personalization("Hey {name}!", {
            "first_name": None,
            "last_name": None,
            "username": None,
        })
        assert result == "Hey friend!"

    def test_full_name_first_and_last(self):
        result = apply_personalization("{full_name}", {
            "first_name": "John",
            "last_name": "Doe",
            "username": "johndoe",
        })
        assert result == "John Doe"

    def test_full_name_only_first(self):
        result = apply_personalization("{full_name}", {
            "first_name": "John",
            "last_name": "",
            "username": "johndoe",
        })
        assert result == "John"

    def test_full_name_fallback_to_username(self):
        result = apply_personalization("{full_name}", {
            "first_name": "",
            "last_name": "",
            "username": "johndoe",
        })
        assert result == "johndoe"

    def test_case_insensitive(self):
        """Variables should be case-insensitive."""
        text = "Hi {Name}! {FIRST_NAME} {Last_Name}"
        result = apply_personalization(text, {
            "first_name": "John",
            "last_name": "Doe",
            "username": "johndoe",
        })
        assert result == "Hi John! John Doe"

    def test_no_variables(self):
        """Text without variables should be returned as-is."""
        text = "Hello, this is a normal message!"
        result = apply_personalization(text, {
            "first_name": "John",
            "last_name": "Doe",
            "username": "johndoe",
        })
        assert result == text

    def test_unknown_variables_preserved(self):
        """Unknown {variables} should NOT be replaced."""
        text = "Hey {name}! Price is {price}"
        result = apply_personalization(text, {
            "first_name": "John",
            "username": "johndoe",
        })
        assert "{price}" in result
        assert "John" in result

    def test_empty_text(self):
        result = apply_personalization("", {"first_name": "John"})
        assert result == ""

    def test_none_text(self):
        result = apply_personalization(None, {"first_name": "John"})
        assert result == ""

    def test_empty_member_info(self):
        result = apply_personalization("Hey {name}!", {})
        assert result == "Hey friend!"

    def test_none_member_info(self):
        result = apply_personalization("Hey {name}!", None)
        assert result == "Hey {name}!"

    def test_multiple_occurrences(self):
        """Same variable used multiple times should all be replaced."""
        text = "{name} said hello to {name}"
        result = apply_personalization(text, {
            "first_name": "John",
            "username": "johndoe",
        })
        assert result == "John said hello to John"

    def test_with_emoji_and_formatting(self):
        """Should work with emoji and HTML formatting."""
        text = "Hey {name} 👋\n<b>{full_name}</b> welcome!"
        result = apply_personalization(text, {
            "first_name": "Nguyễn",
            "last_name": "Văn A",
            "username": "nguyenvana",
        })
        assert result == "Hey Nguyễn 👋\n<b>Nguyễn Văn A</b> welcome!"

    def test_whitespace_in_names(self):
        """Names with extra whitespace should be trimmed."""
        result = apply_personalization("{first_name}", {
            "first_name": "  John  ",
            "last_name": "  Doe  ",
            "username": "johndoe",
        })
        assert result == "John"

    def test_username_only(self):
        """When only username is available."""
        result = apply_personalization(
            "Hi {name}, your handle is @{username}",
            {"username": "trader99"},
        )
        assert result == "Hi trader99, your handle is @trader99"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
