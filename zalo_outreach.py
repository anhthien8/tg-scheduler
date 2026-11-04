"""
Zalo outreach — bridge client + background worker.

Several Zalo nicks share the work. The worker loop (every 30s):
  1. Every enabled, logged-in, rested nick whose own random delay has elapsed
     and that is under its own daily cap sends ONE friend request for a running
     campaign that allows it (campaign total daily limit also applies). Nicks
     go one after another with a random gap — never in the same instant.
     The contact is pinned to that nick, so nobody is invited by two nicks.
  2. Every few minutes: check requested contacts *from the nick that invited
     them*; once they accept, that nick sends the campaign's greeting DM.

Anti-block per nick:
  - warm-up: cap starts at 5/day and grows 3/day up to the nick's daily_cap
  - 3 errors in a row → the nick rests 24h (its campaigns continue on others)
  - accept rate < 20% after 20+ requests → cap halved (message looks spammy)

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
_REST_AFTER_ERRORS = timedelta(hours=24)
WARMUP_START, WARMUP_STEP = 5, 3
LOW_ACCEPT_MIN_SAMPLE, LOW_ACCEPT_RATE = 20, 0.2
_TS = "%Y-%m-%d %H:%M:%S"


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


async def call(method: str, path: str, json: dict | None = None, timeout: float = 60,
               account: str | None = None) -> dict | list:
    """Call the bridge (as nick `account`, default = first logged-in), spawning it if needed."""
    headers = {"X-Bridge-Secret": _SECRET}
    if account:
        headers["X-Zalo-Account"] = account
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.request(method, _URL + path, json=json, headers=headers)
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


def _due(ts: str | None) -> bool:
    return not ts or datetime.now() >= datetime.strptime(ts, _TS)


def _schedule_next(c: dict) -> str:
    lo, hi = sorted((int(c["min_delay_sec"]), int(c["max_delay_sec"])))
    return (datetime.now() + timedelta(seconds=random.randint(lo, max(lo, hi)))).strftime(_TS)


def account_cap(acc: dict) -> tuple[int, str]:
    """Today's friend-request cap for a nick, and why it's below daily_cap (if it is)."""
    cap = int(acc.get("daily_cap") or 25)
    note = ""
    first = acc.get("first_used")
    days = (datetime.now().date() - datetime.strptime(first, "%Y-%m-%d").date()).days if first else 0
    warm = WARMUP_START + WARMUP_STEP * max(days, 0)
    if warm < cap:
        cap, note = warm, f"làm ấm ngày {days + 1}"
    req, acc_ok = int(acc.get("requested_total") or 0), int(acc.get("accepted_total") or 0)
    if req >= LOW_ACCEPT_MIN_SAMPLE and acc_ok / req < LOW_ACCEPT_RATE:
        cap, note = max(1, cap // 2), f"tỷ lệ đồng ý thấp ({acc_ok}/{req}) — giảm một nửa"
    return cap, note


def account_ready(acc: dict, logged_in: set[str]) -> bool:
    return (acc["id"] in logged_in and int(acc.get("enabled") or 0) == 1
            and _due(acc.get("paused_until")) and _due(acc.get("next_action_at"))
            and int(acc.get("sent_today") or 0) < account_cap(acc)[0])


async def _record_account_error(acc: dict, c: dict, err: str):
    n = int(acc.get("consecutive_errors") or 0) + 1
    fields = {"consecutive_errors": n, "last_error": err, "next_action_at": _schedule_next(c)}
    if n >= _MAX_CONSECUTIVE_ERRORS:
        fields.update(consecutive_errors=0,
                      paused_until=(datetime.now() + _REST_AFTER_ERRORS).strftime(_TS),
                      last_error=f"Tự nghỉ 24h sau {n} lỗi liên tiếp: {err}")
        logger.warning("Zalo nick %s resting 24h: %s", acc["id"], err)
    await store.set_account_state(acc["id"], **fields)


async def _send_one(c: dict, acc: dict) -> bool:
    """One friend request from nick `acc` for campaign `c`. False = campaign has nobody left."""
    contact = await store.next_new_contact(c["sources"])
    if not contact:
        await store.set_campaign_state(c["id"], status="done", last_error=None)
        return False
    uid, name, aid = contact["uid"], contact.get("name") or "", acc["id"]

    try:
        st = await call("GET", f"/friend-status/{uid}", account=aid)
    except BridgeError as e:
        if e.status != 409:
            await _record_account_error(acc, c, str(e))
        return True
    if st.get("is_friend") == 1:
        # Already friends with this nick — no request; the accept checker greets them.
        await store.set_contact_status(uid, "friend", campaign_id=c["id"], account_id=aid)
        await store.add_log(c["id"], uid, name, "skip", True, "Đã là bạn bè", account_id=aid)
        return True
    if st.get("is_requested") == 1 or st.get("is_requesting") == 1:
        await store.set_contact_status(uid, "requested", campaign_id=c["id"], account_id=aid)
        await store.add_log(c["id"], uid, name, "skip", True, "Đã có lời mời kết bạn trước đó", account_id=aid)
        return True

    try:
        await call("POST", "/friend-request", {"uid": uid, "message": _render(c["friend_msg"], contact)}, account=aid)
    except BridgeError as e:
        if e.status == 409:  # nick logged out between status check and send
            return True
        await store.set_contact_status(uid, "failed", campaign_id=c["id"], error=str(e), account_id=aid)
        await store.add_log(c["id"], uid, name, "friend_request", False, str(e), account_id=aid)
        await _record_account_error(acc, c, str(e))
        return True
    await store.set_contact_status(uid, "requested", campaign_id=c["id"], account_id=aid)
    await store.add_log(c["id"], uid, name, "friend_request", True, account_id=aid)
    await store.set_account_state(aid, consecutive_errors=0, last_error=None, next_action_at=_schedule_next(c),
                                  first_used=acc.get("first_used") or datetime.now().strftime("%Y-%m-%d"))
    return True


async def _logged_in_accounts() -> set[str]:
    status = await call("GET", "/status")
    accounts = status.get("accounts") or []
    await store.sync_accounts(accounts)
    return {a["id"] for a in accounts if a.get("logged_in")}


async def _send_round(logged_in: set[str]):
    campaigns = [c for c in await store.list_campaigns("running") if _in_hours(c)]
    if not campaigns:
        return
    ready = [a for a in await store.list_accounts() if account_ready(a, logged_in)]
    random.shuffle(ready)
    for i, acc in enumerate(ready):
        if i:
            await asyncio.sleep(random.uniform(5, 20))  # nicks never act in the same instant
        for c in random.sample(campaigns, len(campaigns)):
            if c["status"] != "running" or (c["accounts"] and acc["id"] not in c["accounts"]):
                continue
            if await store.sent_today(c["id"]) >= int(c["daily_limit"]):
                continue
            if await _send_one(c, acc):
                break
            c["status"] = "done"


async def _check_accepts(logged_in: set[str]):
    for contact in await store.contacts_awaiting_accept():
        c = await store.get_campaign(contact["campaign_id"])
        uid, name = contact["uid"], contact.get("name") or ""
        aid = contact.get("account_id")  # None = invited before multi-nick → default nick
        if aid and aid not in logged_in:
            await store.touch_contact(uid)
            continue
        try:
            if contact["status"] == "requested":
                st = await call("GET", f"/friend-status/{uid}", account=aid)
                if st.get("is_friend") != 1:
                    await store.touch_contact(uid)
                    continue
                await store.set_contact_status(uid, "friend")
            if not c or not (c.get("greet_msg") or "").strip() or not _in_hours(c):
                await store.touch_contact(uid)
                continue
            await call("POST", "/send", {"uid": uid, "message": _render(c["greet_msg"], contact)}, account=aid)
            await store.set_contact_status(uid, "greeted")
            await store.add_log(c["id"], uid, name, "greet", True, account_id=aid)
        except BridgeError as e:
            await store.touch_contact(uid)
            if c and e.status != 409:
                await store.add_log(c["id"], uid, name, "greet", False, str(e), account_id=aid)
        await asyncio.sleep(random.uniform(3, 8))


async def _watch_joins(watches: list[dict], logged_in: set[str]):
    """Keep the watching nicks listening, then queue new group members (ahead of everyone)."""
    for aid in {w["account_id"] for w in watches}:
        if aid and aid not in logged_in:
            continue
        try:
            await call("POST", "/listen", account=aid)
        except BridgeError as e:
            logger.debug("Zalo listen failed for %s: %s", aid, e)
    by_group = {w["group_id"]: w for w in watches}
    for ev in await call("GET", "/joins") or []:
        w = by_group.get(str(ev.get("group_id")))
        members = ev.get("members") or []
        if not w or not members:
            continue
        name = w.get("group_name") or ev.get("group_name") or w["group_id"]
        await store.upsert_contacts(members, "join", w["group_id"], f"🆕 Mới vào · {name}", priority=1)
        await store.record_join(w["group_id"], len(members))
        c = await store.get_campaign(w["campaign_id"]) if w.get("campaign_id") else None
        if c and c["status"] == "done":  # ran out of contacts earlier → wake it up (paused stays paused)
            await store.set_campaign_state(c["id"], status="running", last_error=None)
        logger.info("Zalo: %d new member(s) in %s queued", len(members), name)


async def tick(check_accepts: bool) -> None:
    """One worker iteration (exposed for tests)."""
    watches = await store.list_watches(enabled_only=True)
    if not watches and not await store.list_campaigns("running") and not (
            check_accepts and await store.contacts_awaiting_accept(limit=1)):
        return
    logged_in = await _logged_in_accounts()
    if not logged_in:
        return
    if watches:
        await _watch_joins(watches, logged_in)
    await _send_round(logged_in)
    if check_accepts:
        await _check_accepts(logged_in)


async def _loop():
    last_accept_check = datetime.min
    while True:
        try:
            due = datetime.now() - last_accept_check >= _ACCEPT_CHECK_EVERY
            if due:
                last_accept_check = datetime.now()
            await tick(due)
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
