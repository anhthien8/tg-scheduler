"""Phone-only first-account login: phone → OTP → (2FA) → logged in.

Route-level tests drive /api/auth with the conftest telegram_client mock,
patched per test. The last tests load the REAL telegram_client module and run
its send_code/sign_in against a fake Telethon client, so the 2FA branch
(SessionPasswordNeededError → sign_in(password=...)) is exercised for real.
"""
import importlib.util
from pathlib import Path

import pytest
from telethon import errors

import database as db
import routes.auth as auth
import telegram_client as tg  # the conftest mock

pytestmark = pytest.mark.asyncio

PHONE = "+84912345678"


@pytest.fixture
def fake_tg(monkeypatch):
    """Record what the routes ask the Telegram layer to do."""
    calls = {"create": [], "send_code": [], "sign_in": []}
    state = {"needs_2fa": False, "password": "secret"}

    async def create_client(account_id, api_id, api_hash, session_name, proxy_url=None):
        calls["create"].append((account_id, api_id, api_hash, session_name))

    async def send_code(account_id, phone):
        calls["send_code"].append((account_id, phone))
        return "hash-1"

    async def sign_in(account_id, phone, code, phone_code_hash, password=None):
        calls["sign_in"].append((code, phone_code_hash, password))
        if code != "12345":
            return {"success": False, "error": "Mã OTP không đúng"}
        if state["needs_2fa"] and password != state["password"]:
            return {"success": False, "needs_password": True,
                    "error": "Mật khẩu 2FA không đúng" if password else "2FA password required"}
        return {"success": True, "user_id": 1, "first_name": "Thien", "last_name": "Nguyen", "username": "t"}

    async def get_me(account_id, *a, **k):
        return {"first_name": "Thien", "last_name": "Nguyen"}

    for name, fn in (("create_client", create_client), ("send_code", send_code),
                     ("sign_in", sign_in), ("get_me", get_me)):
        monkeypatch.setattr(tg, name, fn)
    monkeypatch.delenv("TG_DEFAULT_API_ID", raising=False)
    monkeypatch.delenv("TG_DEFAULT_API_HASH", raising=False)
    monkeypatch.delenv("API_ID", raising=False)
    monkeypatch.delenv("API_HASH", raising=False)
    return calls, state


async def _add_and_send(client, **extra):
    r = client.post("/api/auth/accounts", json={"phone": PHONE, **extra})
    assert r.status_code == 200, r.text
    acc_id = r.json()["account_id"]
    r = client.post("/api/auth/send-code", json={"phone": PHONE, "account_id": acc_id})
    assert r.status_code == 200, r.text
    return acc_id, r.json()["phone_code_hash"]


async def test_phone_only_uses_default_creds_and_logs_in(client, fake_tg):
    calls, _ = fake_tg
    acc_id, h = await _add_and_send(client)
    assert calls["create"][0][1:3] == (auth._TG_DESKTOP_API_ID, auth._TG_DESKTOP_API_HASH)

    r = client.post("/api/auth/verify", json={"phone": PHONE, "code": "12345",
                                              "phone_code_hash": h, "account_id": acc_id})
    assert r.status_code == 200 and r.json()["success"] is True
    acc = await db.get_account(acc_id)
    assert acc["is_logged_in"]
    assert acc["name"] == "Thien Nguyen"  # no name typed → Telegram display name


async def test_env_default_creds_override(client, fake_tg, monkeypatch):
    calls, _ = fake_tg
    monkeypatch.setenv("TG_DEFAULT_API_ID", "777")
    monkeypatch.setenv("TG_DEFAULT_API_HASH", "a" * 32)
    await _add_and_send(client)
    assert calls["create"][0][1:3] == (777, "a" * 32)


async def test_placeholder_env_creds_ignored(monkeypatch):
    monkeypatch.delenv("TG_DEFAULT_API_ID", raising=False)
    monkeypatch.setenv("API_ID", "your_api_id")
    monkeypatch.setenv("API_HASH", "your_api_hash")
    assert auth._default_api_creds() == (auth._TG_DESKTOP_API_ID, auth._TG_DESKTOP_API_HASH)


async def test_typed_name_is_kept(client, fake_tg):
    acc_id, h = await _add_and_send(client, name="Main")
    client.post("/api/auth/verify", json={"phone": PHONE, "code": "12345",
                                          "phone_code_hash": h, "account_id": acc_id})
    assert (await db.get_account(acc_id))["name"] == "Main"


async def test_2fa_asked_after_otp_then_password_completes(client, fake_tg):
    calls, state = fake_tg
    state["needs_2fa"] = True
    acc_id, h = await _add_and_send(client)
    body = {"phone": PHONE, "code": "12345", "phone_code_hash": h, "account_id": acc_id}

    r = client.post("/api/auth/verify", json=body)
    assert r.status_code == 200 and r.json() == {"success": False, "needs_password": True,
                                                 "message": "2FA password required"}
    assert not (await db.get_account(acc_id))["is_logged_in"]

    r = client.post("/api/auth/verify", json={**body, "password": "wrong"})
    assert r.status_code == 400 and "2FA" in r.json()["detail"]

    r = client.post("/api/auth/verify", json={**body, "password": "secret"})
    assert r.status_code == 200 and r.json()["success"] is True
    assert (await db.get_account(acc_id))["is_logged_in"]


async def test_2fa_typed_up_front_logs_in_in_one_verify(client, fake_tg):
    calls, state = fake_tg
    state["needs_2fa"] = True
    acc_id, h = await _add_and_send(client)
    r = client.post("/api/auth/verify", json={"phone": PHONE, "code": "12345", "phone_code_hash": h,
                                              "account_id": acc_id, "password": "secret"})
    assert r.status_code == 200 and r.json()["success"] is True
    assert len(calls["sign_in"]) == 1


# ── Real telegram_client.sign_in against a fake Telethon client ──

def _load_real_tg():
    path = Path(__file__).resolve().parent.parent / "telegram_client.py"
    spec = importlib.util.spec_from_file_location("real_telegram_client_for_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _User:
    id, first_name, last_name, username = 42, "Thien", None, None


class FakeTelethon:
    def __init__(self, needs_2fa=False, password="secret"):
        self.needs_2fa, self.password, self.calls = needs_2fa, password, []

    def is_connected(self):
        return True

    async def send_code_request(self, phone):
        self.calls.append(("send_code_request", phone))
        return type("R", (), {"phone_code_hash": "h-real"})()

    async def sign_in(self, phone=None, code=None, *, phone_code_hash=None, password=None):
        self.calls.append(("sign_in", phone, code, phone_code_hash, password))
        if password is not None:
            if password != self.password:
                raise errors.PasswordHashInvalidError(request=None)
            return _User()
        if self.needs_2fa:
            raise errors.SessionPasswordNeededError(request=None)
        return _User()


@pytest.fixture(scope="module")
def real_tg():
    return _load_real_tg()


async def test_real_sign_in_without_2fa(real_tg):
    fake = FakeTelethon()
    real_tg._clients[1] = fake
    h = await real_tg.send_code(1, PHONE)
    res = await real_tg.sign_in(1, PHONE, "12345", h)
    assert h == "h-real" and res["success"] and res["first_name"] == "Thien"


async def test_real_sign_in_2fa_flow(real_tg):
    fake = FakeTelethon(needs_2fa=True)
    real_tg._clients[2] = fake
    h = await real_tg.send_code(2, PHONE)
    assert (await real_tg.sign_in(2, PHONE, "12345", h)) == {
        "success": False, "error": "2FA password required", "needs_password": True}
    bad = await real_tg.sign_in(2, PHONE, "12345", h, password="nope")
    assert bad["needs_password"] and not bad["success"]
    ok = await real_tg.sign_in(2, PHONE, "12345", h, password="secret")
    assert ok["success"] and ok["user_id"] == 42
    assert fake.calls[-1] == ("sign_in", None, None, None, "secret")


async def test_real_sign_in_refreshes_stale_auth_cache(real_tg):
    """Client created before OTP caches is_authorized=False; after sign-in
    get_me() must return the profile (else the account stays named by phone)."""
    fake = FakeTelethon()
    real_tg._clients[3] = fake
    real_tg._auth_cache[3] = False  # cached when the client first connected
    h = await real_tg.send_code(3, PHONE)
    await real_tg.sign_in(3, PHONE, "12345", h)
    me = await real_tg.get_me(3)
    assert me and me["first_name"] == "Thien" and me["user_id"] == 42


async def test_verify_stores_name_and_tg_user_id(client, fake_tg, monkeypatch):
    async def no_get_me(*a, **k):
        return None  # route must not depend on get_me — sign_in result is enough
    monkeypatch.setattr(tg, "get_me", no_get_me)
    acc_id, h = await _add_and_send(client)
    client.post("/api/auth/verify", json={"phone": PHONE, "code": "12345",
                                          "phone_code_hash": h, "account_id": acc_id})
    acc = await db.get_account(acc_id)
    assert acc["name"] == "Thien Nguyen" and acc["telegram_user_id"] == 1
