"""
Unit tests for message_merger.py — consolidating multiple message bubbles into 1 cohesive message.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from message_merger import clean_paragraph_spacing, merge_messages


class TestCleanParagraphSpacing:
    def test_normalizes_crlf(self):
        text = "Hello\r\n\r\nWorld"
        assert clean_paragraph_spacing(text) == "Hello\n\nWorld"

    def test_collapses_excessive_newlines(self):
        text = "Paragraph 1\n\n\n\n\nParagraph 2"
        assert clean_paragraph_spacing(text) == "Paragraph 1\n\nParagraph 2"

    def test_handles_empty(self):
        assert clean_paragraph_spacing("") == ""
        assert clean_paragraph_spacing(None) == ""


class TestMergeMessages:
    def test_empty_messages(self):
        assert merge_messages([]) == []

    def test_single_text_message(self):
        msgs = [{"msg_type": "text", "content": "Hello there!"}]
        res = merge_messages(msgs)
        assert len(res) == 1
        assert res[0]["content"] == "Hello there!"
        assert res[0]["msg_type"] == "text"

    def test_multiple_text_messages_merged_with_double_newline(self):
        msgs = [
            {"msg_type": "text", "content": "Hey John 👋", "msg_order": 0},
            {"msg_type": "text", "content": "I noticed your work in Web3.", "msg_order": 1},
            {"msg_type": "text", "content": "Would love to explore partnership opportunities!", "msg_order": 2},
        ]
        res = merge_messages(msgs)
        assert len(res) == 1
        expected = "Hey John 👋\n\nI noticed your work in Web3.\n\nWould love to explore partnership opportunities!"
        assert res[0]["content"] == expected
        assert res[0]["msg_type"] == "text"

    def test_media_with_text_messages_merged_into_single_caption(self):
        msgs = [
            {"msg_type": "text", "content": "Check out our latest deck:", "msg_order": 0},
            {"msg_type": "photo", "content": "", "media_path": "/path/to/deck.png", "msg_order": 1},
            {"msg_type": "text", "content": "Let me know if you have any questions!", "msg_order": 2},
        ]
        res = merge_messages(msgs)
        assert len(res) == 1
        assert res[0]["msg_type"] == "photo"
        assert res[0]["media_path"] == "/path/to/deck.png"
        expected = "Check out our latest deck:\n\nLet me know if you have any questions!"
        assert res[0]["content"] == expected

    def test_respects_msg_order(self):
        msgs = [
            {"msg_type": "text", "content": "Part 3", "msg_order": 3},
            {"msg_type": "text", "content": "Part 1", "msg_order": 1},
            {"msg_type": "text", "content": "Part 2", "msg_order": 2},
        ]
        res = merge_messages(msgs)
        assert len(res) == 1
        assert res[0]["content"] == "Part 1\n\nPart 2\n\nPart 3"

    def test_handles_empty_content_items(self):
        msgs = [
            {"msg_type": "text", "content": "Intro", "msg_order": 0},
            {"msg_type": "text", "content": "   ", "msg_order": 1},
            {"msg_type": "text", "content": "Conclusion", "msg_order": 2},
        ]
        res = merge_messages(msgs)
        assert len(res) == 1
        assert res[0]["content"] == "Intro\n\nConclusion"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
