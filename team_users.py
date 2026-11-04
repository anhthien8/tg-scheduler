"""Team dashboard accounts: username/password per member, each login gets its own token.

Stored in data/team_users.json. Removing a member drops all of their tokens.
"""
import hashlib
import json
import os
import secrets
import threading
import time

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "team_users.json")
_lock = threading.Lock()
_ITER = 200_000


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("users", {})
    data.setdefault("tokens", {})
    return data


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER).hex()


def list_users() -> list[dict]:
    with _lock:
        data = _load()
    return [{"username": u, "created_at": v.get("created_at"), "last_login": v.get("last_login")}
            for u, v in sorted(data["users"].items())]


def set_user(username: str, password: str) -> None:
    """Create a member, or reset their password (which also logs them out everywhere)."""
    salt = secrets.token_hex(16)
    with _lock:
        data = _load()
        prev = data["users"].get(username, {})
        data["users"][username] = {"salt": salt, "hash": _hash(password, salt),
                                   "created_at": prev.get("created_at") or int(time.time()),
                                   "last_login": prev.get("last_login")}
        data["tokens"] = {t: u for t, u in data["tokens"].items() if u != username}
        _save(data)


def delete_user(username: str) -> bool:
    with _lock:
        data = _load()
        if username not in data["users"]:
            return False
        del data["users"][username]
        data["tokens"] = {t: u for t, u in data["tokens"].items() if u != username}
        _save(data)
    return True


def login(username: str, password: str) -> str | None:
    """Return a new token if the credentials match, else None."""
    with _lock:
        data = _load()
        u = data["users"].get(username)
        if not u or not secrets.compare_digest(_hash(password, u["salt"]), u["hash"]):
            return None
        token = "tm_" + secrets.token_urlsafe(32)
        data["tokens"][token] = username
        u["last_login"] = int(time.time())
        _save(data)
    return token


_cache: tuple[float, dict] = (0.0, {})


def token_user(token: str) -> str | None:
    """Username owning this token (cached ~2s since every request hits this)."""
    global _cache
    if not token or not token.startswith("tm_"):
        return None
    ts, tokens = _cache
    if time.time() - ts > 2:
        with _lock:
            tokens = _load()["tokens"]
        _cache = (time.time(), tokens)
    return tokens.get(token)


def invalidate_cache() -> None:
    global _cache
    _cache = (0.0, {})
