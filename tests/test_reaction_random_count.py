"""
Tests for random react-count feature (react_count_min / react_count_max).
Engine: reaction_watcher._react_all_accounts subset selection.
API: validation in routes/reactions.py.
"""
import pytest
import database as db
import reaction_watcher as rw

pytestmark = pytest.mark.asyncio


def _target(acc_ids, rc_min=0, rc_max=0):
    return {
        "id": 1,
        "account_ids": acc_ids,
        "delay_min": 0,
        "delay_max": 0,
        "channel_id": 123,
        "react_count_min": rc_min,
        "react_count_max": rc_max,
    }


@pytest.fixture
def reacted(monkeypatch):
    """Patch _do_react to record which accounts reacted."""
    calls = []

    async def fake_do_react(target, client, acc_id, msg_id, channel_entity):
        calls.append(acc_id)

    monkeypatch.setattr(rw, "_do_react", fake_do_react)
    return calls


# (a) rc_max=0 → legacy behavior: all accounts react
async def test_rc_zero_all_accounts_react(reacted):
    await rw._react_all_accounts(_target([1, 2, 3, 4, 5]), 100, "https://t.me/x")
    assert sorted(reacted) == [1, 2, 3, 4, 5]


# (b) rc 2-3 with 5 accounts → reacted count in {2, 3}
async def test_rc_range_subset(reacted, monkeypatch):
    seen_args = []
    real_randint = rw.random.randint

    def spy_randint(a, b):
        seen_args.append((a, b))
        return real_randint(a, b)

    monkeypatch.setattr(rw.random, "randint", spy_randint)
    for _ in range(10):
        reacted.clear()
        await rw._react_all_accounts(_target([1, 2, 3, 4, 5], 2, 3), 100, "https://t.me/x")
        assert len(reacted) in {2, 3}
    assert all(args == (2, 3) for args in seen_args)


# (b bis) deterministic: randint pinned to min and to max
async def test_rc_range_deterministic(reacted, monkeypatch):
    monkeypatch.setattr(rw.random, "randint", lambda a, b: a)
    await rw._react_all_accounts(_target([1, 2, 3, 4, 5], 2, 3), 100, "https://t.me/x")
    assert len(reacted) == 2

    reacted.clear()
    monkeypatch.setattr(rw.random, "randint", lambda a, b: b)
    await rw._react_all_accounts(_target([1, 2, 3, 4, 5], 2, 3), 100, "https://t.me/x")
    assert len(reacted) == 3


# (c) clamp: rc 3-10 with 4 accounts → k never exceeds 4
async def test_rc_clamp_to_account_count(reacted, monkeypatch):
    monkeypatch.setattr(rw.random, "randint", lambda a, b: b)
    await rw._react_all_accounts(_target([1, 2, 3, 4], 3, 10), 100, "https://t.me/x")
    assert len(reacted) == 4


# (d) API validation
async def _mk_account():
    return await db.create_account({
        "name": "React Acc",
        "phone": "+844****4444",
        "api_id": "2040",
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session_name": "react_rc_test",
        "proxy_url": None,
    })


def _payload(acc_id, rc_min, rc_max):
    return {
        "channel_link": "https://t.me/testchannel",
        "account_ids": [acc_id],
        "reactions": ["👍"],
        "delay_min": 1,
        "delay_max": 5,
        "auto_join": False,
        "react_count_min": rc_min,
        "react_count_max": rc_max,
    }


async def test_api_min_without_max_400(client):
    acc_id = await _mk_account()
    r = client.post("/api/reactions/targets", json=_payload(acc_id, 3, 0))
    assert r.status_code == 400


async def test_api_max_less_than_min_400(client):
    acc_id = await _mk_account()
    r = client.post("/api/reactions/targets", json=_payload(acc_id, 5, 2))
    assert r.status_code == 400


async def test_api_valid_range_200_and_persisted(client):
    acc_id = await _mk_account()
    r = client.post("/api/reactions/targets", json=_payload(acc_id, 2, 6))
    assert r.status_code == 200
    target = r.json()["target"]
    assert target["react_count_min"] == 2
    assert target["react_count_max"] == 6

    # update: invalid → 400
    tid = target["id"]
    r2 = client.put(f"/api/reactions/targets/{tid}", json={"react_count_min": 4, "react_count_max": 2})
    assert r2.status_code == 400

    # update: valid → 200, persisted
    r3 = client.put(f"/api/reactions/targets/{tid}", json={"react_count_min": 1, "react_count_max": 3})
    assert r3.status_code == 200
    assert r3.json()["target"]["react_count_min"] == 1
    assert r3.json()["target"]["react_count_max"] == 3
