"""Bug reproduction: _next_key ignores the cooldown of the failing key.

Root cause of "AI Provider 'openai_compatible' không trả về kết quả" in
the Test-AI-Agent modal (see image_f46db2.png):

_next_key() walks keys in round-robin order, but its cooldown check is wrong.
"""
import sys, time
sys.path.insert(0, '.')
import ai_remix


def test_next_key_skips_cooled_down_key():
    """A key marked as failed must not be handed out again until cooldown expires."""
    keys = ["good-key", "bad-key"]
    # Reset global state
    ai_remix._rr_index.clear()
    ai_remix._key_cooldown.clear()

    # Simulate: bad-key (index 1) just failed -> marked with a fresh cooldown.
    ai_remix._mark_key_failed("openai_compatible", 1)
    # Force the round-robin pointer to point at the bad key.
    ai_remix._rr_index["openai_compatible"] = 0

    idx, key = ai_remix._next_key(keys, "openai_compatible")
    assert idx == 0, (
        f"_next_key returned index {idx} ({key!r}); the failed key at index 1 "
        "is in cooldown and must be skipped"
    )


def test_next_key_returns_key_when_all_cooling_down():
    """When every key is in cooldown, return the one whose cooldown expires soonest."""
    keys = ["k0", "k1", "k2"]
    ai_remix._rr_index.clear()
    ai_remix._key_cooldown.clear()

    now = time.time()
    # k0 failed 100s ago, k1 failed 10s ago (most recent), k2 failed 50s ago.
    ai_remix._key_cooldown[("prov", 0)] = now - 100
    ai_remix._key_cooldown[("prov", 1)] = now - 10
    ai_remix._key_cooldown[("prov", 2)] = now - 50
    ai_remix._rr_index["prov"] = 0

    idx, key = ai_remix._next_key(keys, "prov")
    # k0 has the OLDEST cooldown -> cleanest -> should be picked first.
    assert idx == 0, (
        f"expected the cleanest key (index 0, oldest cooldown) but got index {idx} ({key!r})"
    )
