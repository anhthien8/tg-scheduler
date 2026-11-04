from datetime import datetime, timezone

import dm_reply_tracker as t


def test_local_hour_uses_language_offset():
    now = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
    assert t._kol_local_hour("vi", now) == 10
    assert t._kol_local_hour("ru", now) == 6
    assert t._kol_local_hour("pt", now) == 0
    assert t._kol_local_hour("xx", now) == 3


def test_care_instruction_mentions_events_support_and_meeting():
    text = t._KOL_CARE_INSTRUCTION.format(days=30, events=" and ".join(t.KOL_CARE_EVENT_LINKS))
    assert "weex.com/rewards" in text and "affiliates.weex.com/promotion" in text
    assert "Elite Bot" in text and "coffee" in text and "call" in text


def test_trivial_learned_rules_are_flagged():
    assert t.is_trivial_learned_rule("No", "6337958984")
    assert t.is_trivial_learned_rule("Ai z a?", "=]]")
    assert t.is_trivial_learned_rule("Do you have a demo account?", ":D :D :D :D :D :D :D :D :D")
    assert not t.is_trivial_learned_rule(
        "Do you offer a demo account?", "Yes, we can open a demo account so you can test the platform first.")


def test_blocked_user_ids_reads_list_and_fails_closed():
    import asyncio, types

    class Client:
        async def __call__(self, req):
            return types.SimpleNamespace(blocked=[types.SimpleNamespace(peer_id=types.SimpleNamespace(user_id=42))])

    class Broken:
        async def __call__(self, req):
            raise RuntimeError("offline")

    cache = {}
    assert asyncio.run(t._blocked_user_ids(1, Client(), cache)) == {42}
    assert 7 not in cache[1]
    assert 7 in asyncio.run(t._blocked_user_ids(2, Broken(), cache))
