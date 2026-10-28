"""Dashboard username/password login → API key exchange."""
import os

import pytest

import main

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(main, "DASH_USER", "anhthien8")
    monkeypatch.setattr(main, "DASH_PASS", "112233Aa@")


async def test_login_ok_returns_api_key(client):
    r = client.post("/api/login", json={"username": "anhthien8", "password": "112233Aa@"})
    assert r.status_code == 200
    assert r.json()["api_key"] == os.environ["DASHBOARD_SECRET_KEY"]


async def test_login_wrong_password_401(client):
    r = client.post("/api/login", json={"username": "anhthien8", "password": "sai"})
    assert r.status_code == 401


async def test_login_unconfigured_creds_401(client, monkeypatch):
    monkeypatch.setattr(main, "DASH_USER", "")
    monkeypatch.setattr(main, "DASH_PASS", "")
    r = client.post("/api/login", json={"username": "", "password": ""})
    assert r.status_code == 401
