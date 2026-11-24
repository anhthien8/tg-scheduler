"""
WhatsApp outreach — bridge client + background worker.

Several WhatsApp numbers share the work. The worker loop (every 30s):
  1. Drain bridge events: new members of watched groups (queued first), people
     who spoke in tracked groups, replies to our openers, chats the owner
     answered by hand, and sends WhatsApp refused.
  2. Every enabled, connected, rested number whose own random delay has elapsed
     and that is under its own daily cap sends ONE opener for a running campaign
     that allows it. The contact is pinned to that number.
  3. People who answered the opener get the campaign's second message from the
     number that wrote to them — unless the owner already answered by hand, or
     the reply is older than 48h.

Anti-ban per number (starting points, not official limits):
  - warm-up: cap starts at 5/day and grows 2/day up to the number's daily_cap
  - 3 errors in a row, or a send WhatsApp refused → the number rests 24h
  - at most 3 group joins a day

The Node bridge (wa-bridge/server.js) is spawned on demand unless WA_BRIDGE_URL
points at one already running.
"""
import asyncio
import logging
import os
import random
import re
import secrets
import shutil
import subprocess
from datetime import datetime, timedelta

import httpx

import outbound_guard
import wa_store as store
from personalization import apply_spintax
from zalo_outreach import BridgeError, _due, _in_hours, _schedule_next

logger = logging.getLogger("tg-scheduler.whatsapp")

_ROOT = os.path.dirname(os.path.abspath(__file__))
_BRIDGE_DIR = os.path.join(_ROOT, "wa-bridge")
_PORT = int(os.getenv("WA_BRIDGE_PORT", "8895"))
_URL = os.getenv("WA_BRIDGE_URL", f"http://127.0.0.1:{_PORT}")
_SECRET_FILE = os.path.join(_ROOT, "data", "wa_bridge.secret")
_TS = "%Y-%m-%d %H:%M:%S"
_MAX_CONSECUTIVE_ERRORS = 3
_REST = timedelta(hours=24)
WARMUP_START, WARMUP_STEP = 5, 2
REPLY_MAX_AGE = timedelta(hours=48)      # an older reply is a cold lead: no automatic second message
FOLLOWUP_DELAY_SEC = (30, 120)           # answering within a second looks like a bot
MAX_JOINS_PER_DAY = 3


def _load_secret() -> str:
    """Stable secret kept in data/: a hard-killed server leaves its bridge running,
    and a fresh random secret on restart would make that bridge answer 403."""
    if os.getenv("WA_BRIDGE_SECRET"):
        return os.environ["WA_BRIDGE_SECRET"]
    try:
        with open(_SECRET_FILE, encoding="utf-8") as f:
            s = f.read().strip()
        if s:
            return s
    except OSError:
        pass
    s = secrets.token_urlsafe(24)
    try:
        os.makedirs(os.path.dirname(_SECRET_FILE), exist_ok=True)
        with open(_SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(s)
    except OSError:
        logger.warning("Could not persist wa-bridge secret; restarts may need a bridge restart")
    return s


_SECRET = _load_secret()
_proc: subprocess.Popen | None = None
_worker: asyncio.Task | None = None


# ── Bridge process + client ───────────────────────────────────────────────────

def _spawn_bridge():
    global _proc
    if _proc and _proc.poll() is None:
        return
    node = shutil.which("node")
    if not node:
        raise BridgeError("Không tìm thấy Node.js — cài Node 20+ để dùng WhatsApp", 503)
    if not os.path.isdir(os.path.join(_BRIDGE_DIR, "node_modules")):
        raise BridgeError("Chưa cài wa-bridge — chạy `npm install` trong thư mục wa-bridge", 503)
    env = {**os.environ, "WA_BRIDGE_SECRET": _SECRET, "WA_BRIDGE_PORT": str(_PORT)}
    log = open(os.path.join(_BRIDGE_DIR, "bridge.log"), "a", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    _proc = subprocess.Popen([node, "server.js"], cwd=_BRIDGE_DIR, env=env,
                             stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    logger.info("Spawned wa-bridge pid=%s on port %s", _proc.pid, _PORT)


async def call(method: str, path: str, json: dict | None = None, timeout: float = 60,
               account: str | None = None) -> dict | list:
    """Call the bridge (as number `account`, default = first connected), spawning it if needed."""
    headers = {"X-Bridge-Secret": _SECRET}
    if account:
        headers["X-WA-Account"] = account
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.request(method, _URL + path, json=json, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if attempt or os.getenv("WA_BRIDGE_URL"):
                raise BridgeError("Không kết nối được wa-bridge", 503)
            _spawn_bridge()
            for _ in range(20):  # wait up to ~10s for it to listen
                await asyncio.sleep(0.5)
                try:
                    async with httpx.AsyncClient(timeout=2) as client:
                        await client.get(_URL + "/status", headers={"X-Bridge-Secret": _SECRET})
                    break
                except httpx.TransportError:  # still booting
                    continue
            continue
        except httpx.TransportError as e:
            raise BridgeError(f"wa-bridge không phản hồi: {type(e).__name__}", 504)
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        if r.status_code >= 400:
            if r.status_code == 403:
                raise BridgeError("wa-bridge từ chối secret (đang chạy instance khác?)", 503)
            raise BridgeError(data.get("error") or f"HTTP {r.status_code}", r.status_code)
        return data
    raise BridgeError("Không kết nối được wa-bridge", 503)


def stop_bridge():
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
    _proc = None


# ── Worker ────────────────────────────────────────────────────────────────────

def _render(template: str, contact: dict) -> str:
    """{name} = first word of their WhatsApp name, {full_name} = all of it. Scraped
    members usually have no name yet, so an empty one leaves no gap behind."""
    name = (contact.get("name") or "").strip()
    text = (template or "").replace("{full_name}", name).replace("{name}", name.split()[0] if name else "")
    text = re.sub(r"[ \t]{2,}", " ", apply_spintax(text))
    return re.sub(r"[ \t]+([,.!?])", r"\1", text).strip()


def account_cap(acc: dict) -> tuple[int, str]:
    """Today's opener cap for a number, and why it's below daily_cap (if it is)."""
    cap = int(acc.get("daily_cap") or 20)
    first = acc.get("first_used")
    days = (datetime.now().date() - datetime.strptime(first, "%Y-%m-%d").date()).days if first else 0
    warm = WARMUP_START + WARMUP_STEP * max(days, 0)
    return (warm, f"làm ấm ngày {days + 1}") if warm < cap else (cap, "")


def _usable(acc: dict, connected: set[str]) -> bool:
    return acc["id"] in connected and int(acc.get("enabled") or 0) == 1 and _due(acc.get("paused_until"))


def account_ready(acc: dict, connected: set[str]) -> bool:
    return (_usable(acc, connected) and _due(acc.get("next_action_at"))
            and int(acc.get("sent_today") or 0) < account_cap(acc)[0])


async def _rest(acc_id: str, why: str, until: datetime | None = None):
    await store.set_account_state(acc_id, consecutive_errors=0, last_error=why,
                                  paused_until=(until or datetime.now() + _REST).strftime(_TS))
    logger.warning("WhatsApp number %s resting: %s", acc_id, why)


async def _record_account_error(acc: dict, c: dict, err: str):
    n = int(acc.get("consecutive_errors") or 0) + 1
    if n >= _MAX_CONSECUTIVE_ERRORS:
        return await _rest(acc["id"], f"Tự nghỉ 24h sau {n} lỗi liên tiếp: {err}")
    await store.set_account_state(acc["id"], consecutive_errors=n, last_error=err, next_action_at=_schedule_next(c))


async def _send(aid: str, contact: dict, text: str, where: str):
    """Every outbound message goes through here: guard, then one attempt to one id."""
    await call("POST", "/send", {"jid": contact.get("lid") or contact["uid"],
                                 "message": outbound_guard.require(text, where)}, account=aid)


async def _open_one(c: dict, acc: dict, own: set[str]) -> bool:
    """One opener from number `acc` for campaign `c`. False = campaign has nobody left."""
    contact = await store.next_new_contact(c["sources"], c["countries"])
    if not contact:
        await store.set_campaign_state(c["id"], status="done", last_error=None)
        return False
    uid, name, aid = contact["uid"], contact.get("name") or "", acc["id"]
    if contact.get("lid") in own or contact.get("phone") in own:
        await store.set_contact_status(uid, "skipped", campaign_id=c["id"], error="Số nội bộ")
        return True
    try:
        await _send(aid, contact, _render(c["open_msg"], contact), "wa-open")
    except outbound_guard.BlockedOutbound as e:
        await store.set_campaign_state(c["id"], status="paused", last_error=f"Tin mở đầu bị chặn: {e}")
        return False
    except BridgeError as e:
        if e.status == 409:  # number dropped its connection between /status and the send
            return True
        await store.set_contact_status(uid, "failed", campaign_id=c["id"], error=str(e), account_id=aid)
        await store.add_log(c["id"], uid, name, "open", False, str(e), account_id=aid)
        await _record_account_error(acc, c, str(e))
        return True
    await store.set_contact_status(uid, "sent", campaign_id=c["id"], account_id=aid)
    await store.add_log(c["id"], uid, name, "open", True, account_id=aid)
    await store.set_account_state(aid, consecutive_errors=0, last_error=None, next_action_at=_schedule_next(c),
                                  first_used=acc.get("first_used") or datetime.now().strftime("%Y-%m-%d"))
    return True


async def _send_round(connected: set[str]):
    campaigns = [c for c in await store.list_campaigns("running") if _in_hours(c)]
    if not campaigns:
        return
    accounts = await store.list_accounts()
    own = {v for a in accounts for v in (a.get("lid"), a.get("phone")) if v}
    ready = [a for a in accounts if account_ready(a, connected)]
    random.shuffle(ready)
    for i, acc in enumerate(ready):
        if i:
            await asyncio.sleep(random.uniform(5, 20))  # numbers never act in the same instant
        for c in random.sample(campaigns, len(campaigns)):
            if c["status"] != "running" or (c["accounts"] and acc["id"] not in c["accounts"]):
                continue
            if await store.sent_today(c["id"]) >= int(c["daily_limit"]):
                continue
            if await _open_one(c, acc, own):
                break
            c["status"] = "stopped"


async def _handle_events(ev: dict, watches: list[dict]):
    by_group = {w["group_id"]: w for w in watches}

    for j in ev.get("joins") or []:
        w = by_group.get(j.get("group_id"))
        members = j.get("members") or []
        if not w or not w["watch_joins"] or not members:
            continue
        name = w.get("group_name") or j.get("group_name") or w["group_id"]
        got = await store.upsert_contacts(members, "join", w["group_id"], f"🆕 Mới vào · {name}", priority=1)
        await store.record_join(w["group_id"], got["total"])
        c = await store.get_campaign(w["campaign_id"]) if w.get("campaign_id") else None
        if c and c["status"] == "done":  # ran out of contacts earlier → wake it up (paused stays paused)
            await store.set_campaign_state(c["id"], status="running", last_error=None)

    speakers: dict[str, list[dict]] = {}
    for s in ev.get("speakers") or []:
        w = by_group.get(s.get("group_id"))
        if w and w["track_active"]:
            speakers.setdefault(w["group_id"], []).append(s)
    for gid, people in speakers.items():
        await store.upsert_contacts(people, "active", gid, f"🔥 Active · {by_group[gid].get('group_name') or gid}")

    for r in ev.get("replies") or []:
        contact = await store.find_by_jids([r.get("jid"), r.get("alt_jid")], r.get("account"), ("sent",))
        if not contact:
            continue
        ts = int(r.get("ts") or 0)
        if ts and datetime.now() - datetime.fromtimestamp(ts) > REPLY_MAX_AGE:
            await store.set_contact_status(contact["uid"], "handled", name=r.get("name"),
                                           error="Trả lời đã quá 48 giờ — không tự gửi tin 2")
        else:
            await store.set_contact_status(contact["uid"], "replied", name=r.get("name"))

    for m in ev.get("manual") or []:
        contact = await store.find_by_jids([m.get("jid"), m.get("alt_jid")], m.get("account"), ("sent", "replied"))
        if contact:
            await store.set_contact_status(contact["uid"], "handled", error="Bạn đã tự nhắn — app không chen vào")

    for f in ev.get("failed") or []:
        why = f"WhatsApp từ chối tin (mã {f.get('code') or '?'})"
        contact = await store.find_by_jids([f.get("jid")], f.get("account"), ("sent", "followed"))
        if contact:
            await store.set_contact_status(contact["uid"], "failed", error=why)
            await store.add_log(contact.get("campaign_id"), contact["uid"], contact.get("name") or "", "open", False,
                                why, account_id=f.get("account"))
        if f.get("account"):
            await _rest(f["account"], f"{why} — số có thể đang bị hạn chế, tự nghỉ 24h")


async def _followups(connected: set[str]):
    accounts = {a["id"]: a for a in await store.list_accounts()}
    for contact in await store.contacts_replied():
        uid, name, aid = contact["uid"], contact.get("name") or "", contact.get("account_id")
        replied = datetime.strptime(contact["replied_at"], _TS)
        if datetime.now() - replied > REPLY_MAX_AGE:
            await store.set_contact_status(uid, "handled", error="Trả lời đã quá 48 giờ — không tự gửi tin 2")
            continue
        c = await store.get_campaign(contact["campaign_id"])
        acc = accounts.get(aid)
        if (not c or c["status"] == "paused" or not acc or not _usable(acc, connected) or not _in_hours(c)
                or datetime.now() - replied < timedelta(seconds=random.randint(*FOLLOWUP_DELAY_SEC))):
            continue
        try:
            await _send(aid, contact, _render(c["reply_msg"], contact), "wa-followup")
        except outbound_guard.BlockedOutbound as e:
            await store.set_contact_status(uid, "handled", error=f"Tin 2 bị chặn: {e}")
            continue
        except BridgeError as e:
            if e.status != 409:
                await store.set_contact_status(uid, "failed", error=str(e))
                await store.add_log(c["id"], uid, name, "followup", False, str(e), account_id=aid)
            continue
        await store.set_contact_status(uid, "followed")
        await store.add_log(c["id"], uid, name, "followup", True, account_id=aid)
        await asyncio.sleep(random.uniform(3, 8))


async def _sync_accounts() -> set[str]:
    """Mirror bridge numbers into the DB; numbers WhatsApp has restricted go to rest."""
    accounts = (await call("GET", "/status")).get("accounts") or []
    await store.sync_accounts(accounts)
    for a in accounts:
        until = a.get("restricted_until")
        if not until:
            continue
        try:
            end = datetime.fromisoformat(until.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except ValueError:
            end = datetime.now() + _REST
        row = await store.get_account(a["id"])
        if row and (row.get("paused_until") or "") < end.strftime(_TS):
            await _rest(a["id"], "WhatsApp đang hạn chế số này nhắn người lạ", end)
    return {a["id"] for a in accounts if a.get("logged_in")}


async def tick() -> None:
    """One worker iteration (exposed for tests)."""
    watches = [w for w in await store.list_watches() if w["watch_joins"] or w["track_active"]]
    if not (watches or await store.list_campaigns("running") or await store.has_open_conversations()):
        return
    connected = await _sync_accounts()
    if not connected:
        return
    ev = await call("POST", "/events", {"tracked": {w["group_id"]: w["account_id"] for w in watches}})
    await _handle_events(ev, watches)
    await _send_round(connected)
    await _followups(connected)


async def _loop():
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except BridgeError as e:
            logger.debug("WhatsApp worker: bridge unavailable: %s", e)
        except Exception:
            logger.exception("WhatsApp worker iteration failed")
        await asyncio.sleep(30)


async def start():
    global _worker
    await store.ensure_schema()
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_loop(), name="wa-outreach")


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
