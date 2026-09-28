"""Unit tests for proxy_manager.py — parsing + round-robin assignment.

Offline: no Webshare calls, no network. Only pure parsing/assignment logic.
"""
from unittest.mock import AsyncMock, patch

import pytest

import proxy_manager as pm

# ── _parse_single_proxy ─────────────────────────────────────────────────────

def test_parse_ip_port_user_pass():
    assert pm._parse_single_proxy("1.2.3.4:8080:bob:secret") == "http://bob:secret@1.2.3.4:8080"


def test_parse_user_pass_at_ip_port():
    assert pm._parse_single_proxy("bob:secret@1.2.3.4:8080") == "http://bob:secret@1.2.3.4:8080"


def test_parse_socks5_url_with_auth():
    assert pm._parse_single_proxy("socks5://bob:secret@1.2.3.4:1080") == "socks5://bob:secret@1.2.3.4:1080"


def test_parse_http_url_without_auth():
    assert pm._parse_single_proxy("http://1.2.3.4:8080") == "http://1.2.3.4:8080"


def test_parse_plain_ip_port():
    assert pm._parse_single_proxy("1.2.3.4:8080") == "http://1.2.3.4:8080"


def test_parse_custom_default_scheme():
    assert pm._parse_single_proxy("1.2.3.4:1080", default_scheme="socks5") == "socks5://1.2.3.4:1080"


def test_parse_invalid_line_returns_none():
    assert pm._parse_single_proxy("garbage line here") is None


def test_parse_proxy_list_skips_blanks_and_comments():
    raw = """
    # a comment
    1.2.3.4:8080

    bob:secret@5.6.7.8:9090
    """
    result = pm.parse_proxy_list(raw)
    assert result == ["http://1.2.3.4:8080", "http://bob:secret@5.6.7.8:9090"]


# ── auto_assign_proxies ─────────────────────────────────────────────────────

async def test_auto_assign_round_robin():
    accounts = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 3, "name": "c"}]
    proxies = ["p1", "p2"]
    res = await pm.auto_assign_proxies(proxies, accounts)
    urls = [a["proxy_url"] for a in res["assignments"]]
    assert urls == ["p1", "p2", "p1"]


async def test_auto_assign_no_proxies_returns_all_unassigned():
    accounts = [{"id": 1}, {"id": 2}]
    res = await pm.auto_assign_proxies([], accounts)
    assert res["assignments"] == []
    assert res["unassigned_accounts"] == 2


async def test_auto_assign_more_proxies_than_accounts():
    accounts = [{"id": 1}]
    res = await pm.auto_assign_proxies(["p1", "p2"], accounts)
    assert res["assignments"][0]["proxy_url"] == "p1"
    assert res["unassigned_accounts"] == 0


# ── test_single_proxy ───────────────────────────────────────────────────────

async def test_single_proxy_ok_on_2xx():
    class FakeResp:
        status_code = 200

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    with patch.object(pm.httpx, "AsyncClient", FakeClient):
        res = await pm.test_single_proxy("http://1.2.3.4:8080")
    assert res["ok"] is True
    assert res["error"] is None


async def test_single_proxy_reports_error_on_exception():
    class BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise ConnectionError("refused")

    with patch.object(pm.httpx, "AsyncClient", BoomClient):
        res = await pm.test_single_proxy("http://1.2.3.4:8080")
    assert res["ok"] is False
    assert "refused" in res["error"]


async def test_test_proxies_batch_counts_pass_fail():
    async def fake_test(url):
        return {"proxy": url, "ok": url.endswith("good"), "latency_ms": 1, "error": None}

    with patch.object(pm, "test_single_proxy", fake_test):
        res = await pm.test_proxies_batch(["http://a.good", "http://b.bad", "http://c.good"])
    assert res["total"] == 3
    assert res["passed"] == 2
    assert res["failed"] == 1
    assert res["valid_proxies"] == ["http://a.good", "http://c.good"]
