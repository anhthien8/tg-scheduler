"""Deep-crawl results endpoint: the list view must not download the whole set.

The results can hold thousands of leads and ~1 MB of description text. Serving
it all on every page load made the Cào & DM tab take ~1s and ship 4 MB. These
tests pin the slim/paginated contract so that regression cannot come back.
"""
import pytest


def _fake_leads(n):
    """Build n leads with the fields the endpoint scores, mixes scores+contacts."""
    leads = []
    for i in range(n):
        leads.append({
            "title": f"Channel {i}",
            "description": "Mô tả " + ("x" * 500),   # long -> must be trimmed in slim
            "username": f"chan{i}",
            "channel_id": 1000 + i,
            "contacts": [f"admin{i}"] if i % 2 == 0 else [],
            "participants_count": 1000 + i,
            "trading_score": [90, 70, 50, 30][i % 4],  # vip / all / potential / low
            "category": "news_general" if i % 7 == 0 else "trading",
            "depth": 1,
        })
    return leads


@pytest.fixture
def client_with_leads(monkeypatch, client):
    """Point the endpoint at an in-memory lead set, bypassing Telegram scoring.

    Reuses the shared `client` fixture (auth headers already set) so the request
    reaches the route instead of a 403.
    """
    import routes.members as m
    import telegram_client as tg

    leads = _fake_leads(250)
    monkeypatch.setattr(m, "_deep_crawl_state",
                        {"status": "done", "results": leads}, raising=False)
    monkeypatch.setattr(tg, "is_bot_account", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(m.tg, "is_bot_account", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(m.tg, "score_community_trading",
                        lambda **k: {}, raising=False)
    monkeypatch.setattr(m, "_load_deep_crawl_snapshot", _noop, raising=False)

    return client, leads


async def _noop():
    return None


def test_slim_returns_only_one_page(client_with_leads):
    client, leads = client_with_leads
    r = client.get("/api/members/deep-crawl/results?slim=1&page=0&limit=50")
    assert r.status_code == 200
    d = r.json()
    assert len(d["leads"]) == 50, "slim must return exactly one page"
    assert d["total"] == 250
    assert d["page"] == 0 and d["limit"] == 50


def test_slim_later_pages_are_offset(client_with_leads):
    client, _ = client_with_leads
    p0 = client.get("/api/members/deep-crawl/results?slim=1&page=0&limit=50").json()
    p1 = client.get("/api/members/deep-crawl/results?slim=1&page=1&limit=50").json()
    assert p0["leads"][0]["title"] != p1["leads"][0]["title"], "pages must not overlap"
    assert p1["leads"][0]["title"] == "Channel 50"


def test_slim_trims_long_descriptions(client_with_leads):
    client, _ = client_with_leads
    d = client.get("/api/members/deep-crawl/results?slim=1&page=0&limit=50").json()
    for lead in d["leads"]:
        assert len(lead["description"]) <= 400
        assert lead.get("description_truncated") is True


def test_slim_counts_cover_the_whole_set_not_just_the_page(client_with_leads):
    """The filter tabs are global — their numbers must count all 250 leads."""
    client, _ = client_with_leads
    d = client.get("/api/members/deep-crawl/results?slim=1&page=0&limit=50").json()
    c = d["counts"]
    assert c["all"] == 250
    assert c["has_contacts"] == 125          # every even index
    assert c["no_contacts"] == 125
    assert c["has_contacts"] + c["no_contacts"] == c["all"]


def test_full_mode_still_returns_everything(client_with_leads):
    """Non-slim callers (exports) must still get the complete set."""
    client, _ = client_with_leads
    d = client.get("/api/members/deep-crawl/results").json()
    assert len(d["leads"]) == 250
    assert "counts" not in d
    # descriptions are NOT trimmed in full mode
    assert len(d["leads"][0]["description"]) > 400


def test_slim_limit_is_bounded(client_with_leads):
    """A hostile limit must not let a caller request the whole set at once."""
    client, _ = client_with_leads
    d = client.get("/api/members/deep-crawl/results?slim=1&page=0&limit=99999").json()
    assert d["limit"] == 200
    assert len(d["leads"]) <= 200
