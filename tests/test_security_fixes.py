"""Tests for the security hardening: SSRF guard, settings redaction, HOST default."""
import pytest

from routes._url_guard import UrlNotAllowed, validate_outbound_url

# ── SSRF guard ──────────────────────────────────────────────────────────────

def test_blocks_loopback_by_default():
    with pytest.raises(UrlNotAllowed):
        validate_outbound_url("http://127.0.0.1:20128/v1")


def test_blocks_private_ip():
    with pytest.raises(UrlNotAllowed):
        validate_outbound_url("http://192.168.1.10:8080")


def test_blocks_link_local_metadata_even_with_allow_private():
    # 169.254.169.254 (cloud metadata) must ALWAYS be blocked.
    with pytest.raises(UrlNotAllowed):
        validate_outbound_url("http://169.254.169.254/latest/meta-data", allow_private=True)


def test_allow_private_still_permits_loopback():
    # The local-router diagnostic endpoint needs this.
    assert validate_outbound_url("http://127.0.0.1:20128/v1", allow_private=True)


def test_blocks_non_http_scheme():
    with pytest.raises(UrlNotAllowed):
        validate_outbound_url("file:///etc/passwd")


def test_blocks_empty_url():
    with pytest.raises(UrlNotAllowed):
        validate_outbound_url("")


def test_allows_public_host():
    # api.openai.com resolves to a public IP.
    assert validate_outbound_url("https://api.openai.com/v1")


# ── settings redaction ──────────────────────────────────────────────────────

def test_secret_setting_detection():
    from routes.settings import _is_secret_setting
    assert _is_secret_setting("webshare_api_key") is True
    assert _is_secret_setting("command_bot_token") is True
    assert _is_secret_setting("dashboard_secret_key") is True
    assert _is_secret_setting("foo_password") is True
    # AI keys are NOT blocked (the settings UI edits them).
    assert _is_secret_setting("ai_keys_gemini") is False
    assert _is_secret_setting("ai_provider") is False


def test_get_setting_redacts_token(client):
    import asyncio

    import database as db

    async def seed():
        await db.set_setting("webshare_api_key", "super-secret-token")
    asyncio.run(seed())

    r = client.get("/api/settings/webshare_api_key")
    assert r.status_code == 200
    body = r.json()
    assert body["value"] is None
    assert body["configured"] is True
    assert "super-secret-token" not in r.text


def test_get_setting_returns_non_secret(client):
    import asyncio

    import database as db

    async def seed():
        await db.set_setting("ai_provider", "gemini")
    asyncio.run(seed())

    r = client.get("/api/settings/ai_provider")
    assert r.status_code == 200
    assert r.json()["value"] == "gemini"


# ── HOST default ────────────────────────────────────────────────────────────

def test_main_defaults_host_to_loopback():
    import inspect

    import main
    src = inspect.getsource(main)
    # Both HOST reads must default to loopback, never 0.0.0.0.
    assert 'os.getenv("HOST", "0.0.0.0")' not in src
    assert src.count('os.getenv("HOST", "127.0.0.1")') >= 1
