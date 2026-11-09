"""Deep Crawl account use and Telegram-limit handling.

conftest mocks `telegram_client`, so the real module is loaded from its file and
driven with fake Telethon clients that record every Telegram call.
"""
import asyncio
import importlib.util
import random
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors, utils
from telethon.tl.functions.channels import GetChannelRecommendationsRequest, GetFullChannelRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.types import InputPeerChannel, PeerChannel

import database
import tme_preview

# channel id -> (username, title, recommended channel ids)
CHANNELS = {
    100: ("srcchan", "Source Crypto", [201, 202]),
    201: ("chan_a", "Crypto Signals A", [301]),
    202: ("chan_b", "Trading Futures B", [302]),
    301: ("chan_c", "Crypto Trading C", []),
    302: ("chan_d", "Futures Signals D", []),
}

# Public t.me pages. chan_b is missing on purpose (t.me fails → GetFullChannel fallback).
TME = {
    "srcchan": {"kind": "channel", "title": "Source Crypto", "description": "", "count": 9000},
    "chan_a": {"kind": "channel", "title": "Crypto Signals A",
               "description": "Admin @alice_trader news @newschan_x", "count": 5000},
    "chan_c": {"kind": "channel", "title": "Crypto Trading C", "description": "ads @ghost_user1 @ghost_user2",
               "count": 800},
    "chan_d": {"kind": "channel", "title": "Futures Signals D", "description": "", "count": 700},
    "alice_trader": {"kind": "user", "title": "Alice", "description": "", "count": None},
    "newschan_x": {"kind": "channel", "title": "News", "description": "", "count": 100},
}


def _hash(aid, cid):
    return aid * 1_000_003 + cid


def _cid_of(username):
    return next((c for c, (u, *_) in CHANNELS.items() if u.lower() == (username or "").lower()), None)


class FakeSession:
    def __init__(self, aid, known=()):
        self.aid, self.known = aid, set(known)

    def get_input_entity(self, key):
        cid = key.channel_id if isinstance(key, PeerChannel) else _cid_of(utils.parse_username(key)[0])
        if cid in self.known:
            return InputPeerChannel(cid, _hash(self.aid, cid))
        raise ValueError("not in session")


class FakeClient:
    def __init__(self, aid, known=(), resolve_flood=0):
        self.aid = aid
        self.session = FakeSession(aid, known)
        self.resolve_flood = resolve_flood
        self.calls = []

    def is_connected(self):
        return True

    async def get_input_entity(self, peer):
        self.calls.append(("resolve", peer))
        if self.resolve_flood:
            raise errors.FloodWaitError(None, capture=self.resolve_flood)
        cid = _cid_of(utils.parse_username(peer)[0])
        if cid is None:
            raise ValueError("No user has that username")
        return InputPeerChannel(cid, _hash(self.aid, cid))

    async def __call__(self, req):
        if isinstance(req, GetChannelRecommendationsRequest):
            p = req.channel
            # access hashes are per account: a hash from another account would fail for real
            assert p.access_hash == _hash(self.aid, p.channel_id), "used another account's hash"
            self.calls.append(("recs", p.channel_id))
            return SimpleNamespace(chats=[self._chat(c) for c in CHANNELS[p.channel_id][2]])
        if isinstance(req, GetFullChannelRequest):
            self.calls.append(("full", req.channel.id))
            return SimpleNamespace(full_chat=SimpleNamespace(about="contact @bob_futures"))
        if isinstance(req, SearchRequest):
            self.calls.append(("search", req.q))
            return SimpleNamespace(chats=[])
        raise AssertionError(f"unexpected request {type(req).__name__}")

    def _chat(self, cid):
        u, t, _ = CHANNELS[cid]
        return SimpleNamespace(id=cid, access_hash=_hash(self.aid, cid), username=u, title=t,
                               broadcast=True, participants_count=5000)

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)


def _load_real_tg():
    path = Path(__file__).resolve().parent.parent / "telegram_client.py"
    spec = importlib.util.spec_from_file_location("real_telegram_client_deep_crawl", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def crawl(monkeypatch):
    """Real deep_crawl_similar_channels with fake clients, no sleeps, fake t.me."""
    tg = _load_real_tg()
    real_sleep = asyncio.sleep

    async def no_sleep(_d=0, result=None):
        await real_sleep(0)
        return result

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(random, "uniform", lambda a, b: 0)

    tme_calls = []

    async def fake_fetch_many(names):
        names = [n.lstrip("@").lower() for n in names]
        tme_calls.extend(names)
        return {n: TME[n] for n in names if n in TME}

    async def fake_fetch(name):
        return TME.get(name.lstrip("@").lower())

    monkeypatch.setattr(tme_preview, "fetch_many", fake_fetch_many)
    monkeypatch.setattr(tme_preview, "fetch", fake_fetch)

    classify_calls = []

    async def run(clients, paused=(), classify=None, max_depth=3, reasons=None, allow_peerflood=False, **kw):
        monkeypatch.setattr(tg, "_clients", {c.aid: c for c in clients})
        reasons = reasons or {}

        async def all_accounts():
            return [{"id": c.aid, "name": f"Acc{c.aid}", "is_paused": int(c.aid in paused),
                     "pause_reason": reasons.get(c.aid)} for c in clients]

        async def is_paused(aid):
            return aid in paused

        async def get_setting(key, default=None):
            if key == tg.PEERFLOOD_CRAWL_SETTING:
                return "1" if allow_peerflood else "0"
            return default

        async def default_classify(client, username):
            classify_calls.append(username)
            return "user"

        monkeypatch.setattr(database, "get_all_accounts", all_accounts)
        monkeypatch.setattr(database, "is_account_paused", is_paused)
        monkeypatch.setattr(database, "get_setting", get_setting)
        monkeypatch.setattr(tg, "classify_contact_username", classify or default_classify)
        state = {}

        async def cb(st):
            state.update(st)

        leads = await tg.deep_crawl_similar_channels(
            [c.aid for c in clients], "https://t.me/srcchan", max_depth=max_depth,
            progress_callback=cb, min_expand_score=0, **kw)
        return leads, state

    return SimpleNamespace(tg=tg, run=run, tme_calls=tme_calls, classify_calls=classify_calls)


async def test_paused_skipped_flagged_used(crawl):
    paused_acc = FakeClient(1, known=[100])
    flagged_acc = FakeClient(2, known=[100])  # flagged but not paused: may crawl (read-only)
    leads, state = await crawl.run([paused_acc, flagged_acc], paused={1})

    assert paused_acc.calls == []
    assert flagged_acc.count("recs") == 5  # every node, depth 0..2
    assert {lead["channel_id"] for lead in leads} == {201, 202, 301, 302}
    assert any("tạm dừng" in e and "#1 Acc1" in e for e in state["errors"])
    assert state["status"] == "completed"


async def test_all_paused_raises(crawl):
    with pytest.raises(Exception, match="tạm dừng"):
        await crawl.run([FakeClient(1, known=[100])], paused={1})


PF = "PeerFlood — tự động tạm dừng để bảo vệ tài khoản"


async def test_peerflood_paused_crawls_only_when_opted_in(crawl):
    # Off (default): a PeerFlood-paused account stays out, and the error says how to use it.
    with pytest.raises(Exception, match="PeerFlood"):
        await crawl.run([FakeClient(1, known=[100])], paused={1}, reasons={1: PF})

    pf_acc = FakeClient(1, known=[100])
    leads, state = await crawl.run([pf_acc], paused={1}, reasons={1: PF}, allow_peerflood=True)
    assert pf_acc.count("recs") == 5
    assert {lead["channel_id"] for lead in leads} == {201, 202, 301, 302}
    assert any("PeerFlood (chỉ đọc)" in e and "#1 Acc1" in e for e in state["errors"])
    assert state["status"] == "completed"


async def test_opt_in_never_uses_other_pauses(crawl):
    manual = FakeClient(1, known=[100])
    floodwait = FakeClient(2, known=[100])
    pf_acc = FakeClient(3, known=[100])
    reasons = {1: "Tắt thủ công bởi người dùng", 2: "FloodWait — 3600s", 3: PF}
    await crawl.run([manual, floodwait, pf_acc], paused={1, 2, 3}, reasons=reasons, allow_peerflood=True)
    assert manual.calls == [] and floodwait.calls == []
    assert pf_acc.count("recs") == 5


async def test_nodes_open_by_known_hash_without_username_lookups(crawl):
    acc = FakeClient(7, known=[100])  # source already in the session (an earlier crawl)
    leads, state = await crawl.run([acc])

    assert acc.count("resolve") == 0  # no ResolveUsername at all
    assert acc.count("recs") == 5  # every node, depth 0..2
    assert state["resolve_calls"] == len(crawl.classify_calls) == 3  # only contacts t.me could not check
    assert len(leads) == 4


async def test_descriptions_and_contacts_come_from_tme(crawl):
    acc = FakeClient(3, known=[100])
    leads, state = await crawl.run([acc], max_depth=1)
    by_id = {lead["channel_id"]: lead for lead in leads}

    # chan_a: description from t.me, @newschan_x is a channel → dropped, no account request
    assert by_id[201]["description"] == "Admin @alice_trader news @newschan_x"
    assert by_id[201]["contacts"] == ["@alice_trader"]
    assert state["contacts_removed"] >= 1
    # chan_b: t.me had nothing → GetFullChannel fallback on the account
    assert acc.calls.count(("full", 202)) == 1
    assert acc.calls.count(("full", 201)) == 0
    assert "alice_trader" in crawl.tme_calls
    # @bob_futures (from the GetFullChannel fallback) was unknown to t.me → capped resolve
    assert crawl.classify_calls == ["@bob_futures"]


async def test_username_limit_does_not_stop_the_crawl(crawl):
    async def flooded_classify(client, username):
        crawl.classify_calls.append(username)
        raise errors.FloodWaitError(None, capture=70_000)  # ~19h, like the real case

    acc = FakeClient(5, known=[100])
    leads, state = await crawl.run([acc], classify=flooded_classify)

    assert state["status"] == "completed"
    assert len(leads) == 4  # the whole tree was still crawled
    assert len(crawl.classify_calls) == 1  # no more username lookups once limited
    assert crawl.tg._resolve_flood_until[5] > time.time() + 60_000
    # contacts t.me could not check are kept, marked unverified
    lead_c = next(lead for lead in leads if lead["channel_id"] == 301)
    assert set(lead_c["contacts_unverified"]) == {"@ghost_user1", "@ghost_user2"}


async def test_limited_account_keeps_crawling_nodes_it_knows(crawl):
    limited = FakeClient(1, resolve_flood=50_000)  # its username lookups are limited
    healthy = FakeClient(2)
    leads, state = await crawl.run([limited, healthy])

    assert limited.count("resolve") == 1  # one failed lookup on the source, then never again
    assert healthy.count("resolve") == 1  # opened the source
    assert healthy.count("recs") == 5
    assert len(leads) == 4
    assert state["status"] == "completed"
    assert not any("dừng sớm" in e for e in state["errors"])


async def test_source_blocked_only_by_limits_asks_for_retry(crawl):
    acc = FakeClient(4, resolve_flood=3600)
    with pytest.raises(crawl.tg.DeepCrawlRetryLater) as exc:
        await crawl.run([acc])
    assert 3500 < exc.value.retry_after <= 3601
    assert ("search", "srcchan") in acc.calls  # contacts.Search was tried before giving up


async def test_second_crawl_skips_account_still_limited(crawl):
    limited = FakeClient(1, resolve_flood=50_000)
    healthy = FakeClient(2)
    await crawl.run([limited, healthy])
    limited.calls.clear()
    await crawl.run([limited, healthy])
    assert limited.count("resolve") == 0  # the limit is remembered across crawls


class SlowRecsClient(FakeClient):
    """Recommendation calls take a few event-loop turns, so overlapping calls are visible."""
    in_flight = 0
    peak = 0

    async def __call__(self, req):
        if isinstance(req, GetChannelRecommendationsRequest):
            cls = SlowRecsClient
            cls.in_flight += 1
            cls.peak = max(cls.peak, cls.in_flight)
            try:
                for _ in range(5):
                    await asyncio.sleep(0)
                return await super().__call__(req)
            finally:
                cls.in_flight -= 1
        return await super().__call__(req)


@pytest.mark.parametrize("parallel,expect_peak", [(3, 2), (1, 1)])
async def test_accounts_crawl_in_parallel_without_duplicates(crawl, parallel, expect_peak):
    SlowRecsClient.in_flight = SlowRecsClient.peak = 0
    accs = [SlowRecsClient(1, known=[100]), SlowRecsClient(2), SlowRecsClient(3)]
    leads, state = await crawl.run(accs, parallel=parallel)

    assert state["status"] == "completed"
    assert {lead["channel_id"] for lead in leads} == {201, 202, 301, 302}
    assert len(leads) == 4  # no channel saved twice
    assert sum(a.count("recs") for a in accs) == 5  # every node crawled exactly once
    if parallel == 1:
        assert SlowRecsClient.peak == 1
    else:
        assert expect_peak <= SlowRecsClient.peak <= parallel
        assert sum(1 for a in accs if a.count("recs")) >= 2  # work spread over accounts


@pytest.mark.parametrize("page,kind", [
    ('<div class="tgme_page"><div class="tgme_page_title"><span dir="auto">Chan</span></div>'
     '<div class="tgme_page_extra">12 345 subscribers</div>'
     '<div class="tgme_page_description">Hi<br/>@owner</div></div>', "channel"),
    ('<div class="tgme_page"><div class="tgme_page_title"><span>Grp</span></div>'
     '<div class="tgme_page_extra">1 200 members, 30 online</div></div>', "group"),
    ('<div class="tgme_page"><div class="tgme_page_title"><span>Bob</span></div>'
     '<div class="tgme_page_extra">@bob</div>'
     '<a class="tgme_action_button_new shine" href="tg://resolve?domain=bob">Send Message</a></div>', "user"),
    ('<div class="tgme_page"><div class="tgme_page_title"><span>Helper</span></div>'
     '<a class="tgme_action_button_new shine" href="#">Start Bot</a></div>', "bot"),
    ('<div class="tgme_page"><a class="tgme_action_button_new shine" href="#">Send Message</a></div>', "invalid"),
])
def test_tme_parse_page_kinds(page, kind):
    assert tme_preview.parse_page(page)["kind"] == kind


def test_tme_parse_page_unknown_layout_is_none():
    assert tme_preview.parse_page("<html>captcha</html>") is None


def test_tme_parse_page_description_and_count():
    info = tme_preview.parse_page(
        '<div class="tgme_page"><div class="tgme_page_title"><span>A &amp; B</span></div>'
        '<div class="tgme_page_extra">12 345 subscribers</div>'
        '<div class="tgme_page_description">Line 1<br/>DM @owner_x</div></div>')
    assert info["title"] == "A & B"
    assert info["description"] == "Line 1\nDM @owner_x"
    assert info["count"] == 12345
