from types import SimpleNamespace

import pytest

import database as db
import kol_contacts as kc


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    kc._table_ready = False          # tests drop tables between runs
    kc._flood_until.clear()
    kc._last_save_at.clear()
    monkeypatch.setattr(kc, "MIN_GAP_SECONDS", 0)

    async def not_internal(uid):
        return False
    import dm_reply_tracker
    monkeypatch.setattr(dm_reply_tracker, "is_internal_account_user", not_internal)


def test_contact_name():
    assert kc.contact_name("John", "Doe", "jd", 1) == "John Doe_KOL"
    assert kc.contact_name("", None, "@trader", 1) == "trader_KOL"
    assert kc.contact_name(None, None, None, 42) == "42_KOL"
    assert kc.contact_name("Ann_KOL", None, None, 1) == "Ann_KOL"          # no double suffix
    long = kc.contact_name("x" * 100, None, None, 1)
    assert len(long) == 64 and long.endswith("_KOL")


def test_history_has_proof():
    assert kc.history_has_proof([{"role": "user", "content": "[Attached Video Proof]"}])
    assert kc.history_has_proof([{"role": "user", "content": "hi", "media": {"type": "photo"}}])
    assert not kc.history_has_proof([{"role": "assistant", "content": "[Attached x]"}])
    assert not kc.history_has_proof([{"role": "user", "content": "hello"}])


class _Client:
    def __init__(self, entity, fail=None):
        self.entity, self.fail, self.requests = entity, fail, []

    def is_connected(self):
        return True

    async def get_entity(self, uid):
        return self.entity

    async def __call__(self, req):
        if self.fail:
            raise self.fail
        self.requests.append(req)
        return True


def _use(monkeypatch, client):
    import telegram_client as tg
    monkeypatch.setattr(tg, "get_client", lambda a: client, raising=False)


@pytest.mark.asyncio
async def test_proof_saves_contact_once(monkeypatch):
    c = _Client(SimpleNamespace(first_name="Lewis", last_name="K", username="lewis", bot=False))
    _use(monkeypatch, c)
    assert await kc.on_proof(3, 900, "lewis", {"topic_link": "https://t.me/c/1/2/3"}) == "saved"
    req = c.requests[0]
    assert req.first_name == "Lewis K_KOL" and req.last_name == "" and req.phone == ""
    assert req.add_phone_privacy_exception is False

    assert await kc.on_proof(3, 900, "lewis", {}) == "saved"   # second proof: no new request
    assert len(c.requests) == 1
    row = (await kc.list_contacts())[0]
    assert row["status"] == "saved" and row["proof_count"] == 2
    assert row["contact_name"] == "Lewis K_KOL"
    assert row["last_proof_link"] == "https://t.me/c/1/2/3"


@pytest.mark.asyncio
async def test_internal_nick_and_bot_are_skipped(monkeypatch):
    import dm_reply_tracker

    async def internal(uid):
        return uid == 1
    monkeypatch.setattr(dm_reply_tracker, "is_internal_account_user", internal)
    c = _Client(SimpleNamespace(first_name="B", last_name=None, username="b", bot=True))
    _use(monkeypatch, c)
    assert await kc.on_proof(3, 1, None, {}) == "skipped"
    assert await kc.on_proof(3, 2, None, {}) == "skipped"
    assert c.requests == []


@pytest.mark.asyncio
async def test_disabled_setting_only_counts(monkeypatch):
    c = _Client(SimpleNamespace(first_name="A", last_name=None, username=None, bot=False))
    _use(monkeypatch, c)
    await db.set_setting("kol_contact_autosave", "0")
    assert await kc.on_proof(3, 5, None, {}) == "disabled"
    assert c.requests == []


@pytest.mark.asyncio
async def test_flood_wait_stops_saves_on_that_nick(monkeypatch):
    from telethon import errors
    err = errors.FloodWaitError(request=None, capture=120)
    c = _Client(SimpleNamespace(first_name="A", last_name=None, username=None, bot=False), fail=err)
    _use(monkeypatch, c)
    assert await kc.on_proof(4, 6, None, {}) == "failed"
    c.fail = None
    assert await kc.on_proof(4, 7, None, {}) == "failed"      # still in the wait window
    assert c.requests == []


@pytest.mark.asyncio
async def test_backfill_candidates_find_old_proof_leads():
    await kc._ensure_table()
    await db.get_or_create_followup_chat(account_id=1, user_id=11, username="a", name="A")
    await db.append_followup_chat_message(1, 11, "user", "[Attached Screenshot/Photo Proof]")
    await db.get_or_create_followup_chat(account_id=1, user_id=12, username="b", name="B")
    await db.append_followup_chat_message(1, 12, "user", "hello")
    cands = await kc.backfill_candidates()
    assert [c["user_id"] for c in cands] == [11]
