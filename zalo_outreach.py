"""
Zalo outreach — bridge client + background worker.

The worker loop (every 30s):
  1. For each running campaign inside its active hours and under its daily
     limit, once its random delay has elapsed: send ONE friend request (with
     the campaign's intro message) to the next 'new' contact in its sources.
  2. Every few minutes: check contacts we requested; once they accept, send
     the campaign's greeting DM.

The Node bridge (zalo-bridge/server.js) is spawned on demand with a
per-process random secret unless ZALO_BRIDGE_URL points at one already running.
"""
import asyncio
import logging
import os
import random
import secrets
import shutil
import subprocess
from datetime import datetime, timedelta

import httpx

import zalo_store as store
from personalization import apply_spintax

logger = logging.getLogger("tg-scheduler.zalo")

_BRIDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zalo-bridge")
_PORT = int(os.getenv("ZALO_BRIDGE_PORT", "8897"))
_URL = os.getenv("ZALO_BRIDGE_URL", f"http://127.0.0.1:{_PORT}")
_SECRET = os.getenv("ZALO_BRIDGE_SECRET") or secrets.token_urlsafe(24)
_proc: subprocess.Popen | None = None
_worker: asyncio.Task | None = None
_ACCEPT_CHECK_EVERY = timedelta(minutes=5)
_MAX_CONSECUTIVE_ERRORS = 3


class BridgeError(Exception):
    def __init__(self, msg: str, status: int = 502):
        super().__init__(msg)
        self.status = status


# ── Bridge process + client ───────────────────────────────────────────────────

def _spawn_bridge():
    global _proc
    if _proc and _proc.poll() is None:
        return
    node = shutil.which("node")
    if not node:
        raise BridgeError("Không tìm thấy Node.js — cài Node 18+ để dùng Zalo", 503)
    if not os.path.isdir(os.path.join(_BRIDGE_DIR, "node_modules")):
        raise BridgeError("Chưa cài zalo-bridge — chạy `npm install` trong thư mục zalo-bridge", 503)
    env = {**os.environ, "ZALO_BRIDGE_SECRET": _SECRET, "ZALO_BRIDGE_PORT": str(_PORT)}
    log = open(os.path.join(_BRIDGE_DIR, "bridge.log"), "a", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    _proc = subprocess.Popen([node, "server.js"], cwd=_BRIDGE_DIR, env=env,
                             stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    logger.info("Spawned zalo-bridge pid=%s on port %s", _proc.pid, _PORT)


async def call(method: str, path: str, json: dict | None = None, timeout: float = 60) -> dict | list:
    """Call the bridge, spawning it first if needed."""
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.request(method, _URL + path, json=json, headers={"X-Bridge-Secret": _SECRET})
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if attempt or os.getenv("ZALO_BRIDGE_URL"):
                raise BridgeError("Không kết nối được zalo-bridge", 503)
            _spawn_bridge()
            for _ in range(20):  # wait up to ~10s for it to listen
                await asyncio.sleep(0.5)
                try:
                    async with httpx.AsyncClient(timeout=2) as client:
                        await client.get(_URL + "/status", headers={"X-Bridge-Secret": _SECRET})
                    break
                except httpx.TransportError:  # still booting (refused / connect timeout)
                    continue
            continue
        except httpx.TransportError as e:
            raise BridgeError(f"zalo-bridge không phản hồi: {type(e).__name__}", 504)
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        if r.status_code >= 400:
            if r.status_code == 403:
                raise BridgeError("zalo-bridge từ chối secret (đang chạy instance khác?)", 503)
            raise BridgeError(data.get("error") or f"HTTP {r.status_code}", r.status_code)
        return data
    raise BridgeError("Không kết nối được zalo-bridge", 503)


def stop_bridge():
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
    _proc = None


# ── Worker ────────────────────────────────────────────────────────────────────

def _render(template: str, contact: dict) -> str:
    name = (contact.get("name") or "").strip()
    first = name.split()[-1] if name else "bạn"  # Vietnamese given name is the last word
    text = (template or "").replace("{full_name}", name or "bạn").replace("{name}", first)
    return apply_spintax(text).strip()


def _in_hours(c: dict) -> bool:
    h = datetime.now().hour
    start, end = int(c["start_hour"]), int(c["end_hour"])
    return start <= h < end if start <= end else (h >= start or h < end)


def _due(c: dict) -> bool:
    nxt = c.get("next_action_at")
    return not nxt or datetime.now() >= datetime.strptime(nxt, "%Y-%m-%d %H:%M:%S")


def _schedule_next(c: dict) -> str:
    lo, hi = sorted((int(c["min_delay_sec"]), int(c["max_delay_sec"])))
    return (datetime.now() + timedelta(seconds=random.randint(lo, max(lo, hi)))).strftime("%Y-%m-%d %H:%M:%S")


async def _record_error(c: dict, err: str):
    n = int(c.get("consecutive_errors") or 0) + 1
    fields = {"consecutive_errors": n, "last_error": err, "next_action_at": _schedule_next(c)}
    if n >= _MAX_CONSECUTIVE_ERRORS:
        fields["status"] = "paused"
        fields["last_error"] = f"Tự tạm dừng sau {n} lỗi liên tiếp: {err}"
        logger.warning("Zalo campaign %s auto-paused: %s", c["id"], err)
    await store.set_campaign_state(c["id"], **fields)


async def _step_campaign(c: dict):
    if not _in_hours(c) or not _due(c):
        return
    if await store.sent_today(c["id"]) >= int(c["daily_limit"]):
        return
    contact = await store.next_new_contact(c["sources"])
    if not contact:
        await store.set_campaign_state(c["id"], status="done", last_error=None)
        return
    uid, name = contact["uid"], contact.get("name") or ""

    try:
        st = await call("GET", f"/friend-status/{uid}")
    except BridgeError as e:
        await _record_error(c, str(e))
        return
    if st.get("is_friend") == 1:
        # Already friends — no request needed; the accept checker will greet them.
        await store.set_contact_status(uid, "friend", campaign_id=c["id"])
        await store.add_log(c["id"], uid, name, "skip", True, "Đã là bạn bè")
        return
    if st.get("is_requested") == 1 or st.get("is_requesting") == 1:
        await store.set_contact_status(uid, "requested", campaign_id=c["id"])
        await store.add_log(c["id"], uid, name, "skip", True, "Đã có lời mời kết bạn trước đó")
        return

    try:
        await call("POST", "/friend-request", {"uid": uid, "message": _render(c["friend_msg"], contact)})
    except BridgeError as e:
        await store.set_contact_status(uid, "failed", campaign_id=c["id"], error=str(e))
        await store.add_log(c["id"], uid, name, "friend_request", False, str(e))
        await _record_error(c, str(e))
        return
    await store.set_contact_status(uid, "requested", campaign_id=c["id"])
    await store.add_log(c["id"], uid, name, "friend_request", True)
    await store.set_campaign_state(c["id"], consecutive_errors=0, last_error=None, next_action_at=_schedule_next(c))


async def _check_accepts():
    for contact in await store.contacts_awaiting_accept():
        c = await store.get_campaign(contact["campaign_id"])
        uid, name = contact["uid"], contact.get("name") or ""
        try:
            if contact["status"] == "requested":
                st = await call("GET", f"/friend-status/{uid}")
                if st.get("is_friend") != 1:
                    await store.touch_contact(uid)
                    continue
                await store.set_contact_status(uid, "friend")
            if not c or not (c.get("greet_msg") or "").strip() or not _in_hours(c):
                await store.touch_contact(uid)
                continue
            await call("POST", "/send", {"uid": uid, "message": _render(c["greet_msg"], contact)})
            await store.set_contact_status(uid, "greeted")
            await store.add_log(c["id"], uid, name, "greet", True)
        except BridgeError as e:
            if e.status == 409:  # not logged in — nothing else will work either
                return
            await store.touch_contact(uid)
            if c:
                await store.add_log(c["id"], uid, name, "greet", False, str(e))
        await asyncio.sleep(random.uniform(3, 8))


async def _loop():
    last_accept_check = datetime.min
    while True:
        try:
            campaigns = await store.list_campaigns("running")
            if campaigns:
                status = await call("GET", "/status")
                if status.get("logged_in"):
                    for c in campaigns:
                        await _step_campaign(c)
            if datetime.now() - last_accept_check >= _ACCEPT_CHECK_EVERY:
                last_accept_check = datetime.now()
                if await store.contacts_awaiting_accept(limit=1):
                    status = await call("GET", "/status")
                    if status.get("logged_in"):
                        await _check_accepts()
        except asyncio.CancelledError:
            raise
        except BridgeError as e:
            logger.debug("Zalo worker: bridge unavailable: %s", e)
        except Exception:
            logger.exception("Zalo worker iteration failed")
        await asyncio.sleep(30)


async def start():
    global _worker
    await store.ensure_schema()
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_loop(), name="zalo-outreach")


async def stop():
    global _worker
    if _worker:
        _worker.cancel()
        try:
            await _worker
        except (asyncio.CancelledError, Exception):
            pass
        _worker = None
    stop_bridge()
