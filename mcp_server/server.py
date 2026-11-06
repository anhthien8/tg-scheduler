"""TG Scheduler MCP server (stdio).

Lets AI agents (Claude Code/Desktop, Codex, Antigravity, Hermes, ...) drive the
local TG Scheduler dashboard through its HTTP API instead of clicking the UI.

Safety model:
  * Talks ONLY to the HTTP API (default http://127.0.0.1:8888); never opens the DB.
  * No tool can START a campaign. Campaigns are created as `draft`; a human
    presses Start on the dashboard. The only state-changing tools are
    scrape / create filtered list / create draft / pause.
  * The API key is read from env (TGS_API_KEY or DASHBOARD_SECRET_KEY) or the
    repo's .env, sent only as the X-API-Key header, and never logged or returned.

Run:  python <repo>/mcp_server/server.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

import tgs_logic as L

# stdio transport: stdout is the protocol channel, log to stderr only.
logging.basicConfig(stream=sys.stderr, level=os.getenv("TGS_MCP_LOG", "WARNING"))
log = logging.getLogger("tgs-mcp")

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.getenv("TGS_BASE_URL", "http://127.0.0.1:8888").rstrip("/")


def _read_env_file(key: str) -> str:
    env = Path(os.getenv("TGS_ENV_FILE") or REPO_ROOT / ".env")
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _api_key() -> str:
    return (os.getenv("TGS_API_KEY") or os.getenv("DASHBOARD_SECRET_KEY")
            or _read_env_file("DASHBOARD_SECRET_KEY"))


_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=BASE_URL, timeout=httpx.Timeout(60.0, connect=5.0),
                                    headers={"X-API-Key": _api_key()})
    return _client


async def _call(method: str, path: str, **kw) -> Any:
    try:
        r = await _http().request(method, path, **kw)
    except httpx.ConnectError:
        raise RuntimeError(f"Không kết nối được TG Scheduler ở {BASE_URL}. App có đang chạy không?")
    if r.status_code == 401 or r.status_code == 403:
        raise RuntimeError("Sai hoặc thiếu API key (đặt TGS_API_KEY hoặc DASHBOARD_SECRET_KEY trong .env).")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text[:300]
        raise RuntimeError(f"{method} {path} → HTTP {r.status_code}: {detail}")
    return r.json()


# ── small TTL caches (performance rule: no heavy polling, long TTL) ──────────
_cache: dict[str, tuple[float, Any]] = {}


async def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = await fn()
    _cache[key] = (time.time(), val)
    return val


async def _accounts_raw(refresh: bool = False) -> tuple[list[dict], dict]:
    if refresh:
        _cache.pop("accounts", None)
        _cache.pop("spam", None)
    accs = await _cached("accounts", 60, lambda: _call("GET", "/api/auth/accounts"))
    spam = await _cached("spam", 600, lambda: _call("GET", "/api/members/accounts/spam-status"))
    return accs.get("accounts", []), spam.get("statuses", {})


def _spam_for(spam: dict, aid) -> dict | None:
    return spam.get(str(aid)) or spam.get(aid)


mcp = FastMCP(
    "tg-scheduler",
    instructions=(
        "Điều khiển TG Scheduler (Telegram marketing tool chạy local). Quy trình DM chuẩn: "
        "list_accounts → find_groups → scrape_group_members → preview_targets (dry-run) → "
        "create_target_list → create_dm_campaign_draft → campaign_progress. "
        "Server KHÔNG có tool chạy campaign: người dùng tự bấm Start trên dashboard. "
        "Chỉ chọn account có health.ok=true làm người gửi, ưu tiên Premium. "
        "Không bao giờ gửi DM tới account nội bộ (preview_targets tự loại)."
    ),
)

RO = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


# ── Accounts ────────────────────────────────────────────────────────────────

@mcp.tool(annotations=RO)
async def list_accounts(
    only_healthy: Annotated[bool, Field(description="Chỉ trả account khỏe (dùng được để gửi)")] = False,
    refresh: Annotated[bool, Field(description="Bỏ qua cache 60s")] = False,
) -> dict:
    """Liệt kê account Telegram kèm trạng thái khỏe (session, offline, tạm dừng, flag,
    PeerFlood, SpamBot giới hạn) và Premium. Không trả SĐT, api_hash, proxy."""
    accs, spam = await _accounts_raw(refresh)
    now = time.time()
    rows = [L.public_account(a, _spam_for(spam, a.get("id")), now) for a in accs]
    if only_healthy:
        rows = [r for r in rows if r["health"]["ok"]]
    rows.sort(key=lambda r: (not r["health"]["ok"], not r["is_premium"], r["id"] or 0))
    return {"count": len(rows),
            "healthy": sum(r["health"]["ok"] for r in rows),
            "accounts": rows}


# ── Groups ──────────────────────────────────────────────────────────────────

_dialog_sem = asyncio.Semaphore(3)


async def _dialogs(aid: int, refresh: bool) -> list[dict]:
    key = f"dialogs:{aid}"
    if refresh:
        _cache.pop(key, None)

    async def fetch():
        async with _dialog_sem:
            res = await _call("GET", "/api/chats", params={"account_id": aid})
        if res.get("error"):
            raise RuntimeError(res["error"])
        return res.get("chats", [])

    return await _cached(key, 1800, fetch)


@mcp.tool(annotations=RO)
async def find_groups(
    title_contains: Annotated[str, Field(description="Lọc theo tên nhóm (không phân biệt hoa thường). Rỗng = tất cả")] = "",
    account_id: Annotated[int | None, Field(description="Chỉ quét 1 account. Bỏ trống = quét mọi account đang online")] = None,
    refresh: Annotated[bool, Field(description="Bỏ cache 30 phút")] = False,
) -> dict:
    """Tìm nhóm/kênh mà các account đang tham gia. Mỗi nhóm kèm danh sách account
    là thành viên (để chọn account cào). Kết quả cache 30 phút/account."""
    accs, spam = await _accounts_raw()
    if account_id is not None:
        targets = [a for a in accs if a.get("id") == account_id]
    else:
        targets = [a for a in accs if a.get("is_logged_in") and not a.get("session_error")]
    if not targets:
        return {"groups": [], "note": "Không có account online để quét"}
    q = title_contains.strip().lower()
    groups: dict[int, dict] = {}
    errors = {}

    async def one(a):
        try:
            chats = await _dialogs(a["id"], refresh)
        except Exception as e:
            errors[a["id"]] = str(e)[:120]
            return
        h = L.account_health(a, _spam_for(spam, a["id"]))
        for c in chats:
            if q and q not in (c.get("chat_title") or "").lower():
                continue
            g = groups.setdefault(c["chat_id"], {
                "chat_id": c["chat_id"], "title": c.get("chat_title"), "type": c.get("chat_type"),
                "username": c.get("username"), "members": c.get("participants_count"),
                "accounts_in_group": []})
            g["accounts_in_group"].append({"id": a["id"], "name": a.get("name"), "healthy": h["ok"]})

    await asyncio.gather(*(one(a) for a in targets))
    out = sorted(groups.values(), key=lambda g: -(g["members"] or 0))
    res = {"count": len(out), "groups": out[:100]}
    if errors:
        res["account_errors"] = errors
    return res


# ── Scraping ────────────────────────────────────────────────────────────────

@mcp.tool(annotations=WRITE)
async def scrape_group_members(
    account_id: Annotated[int, Field(description="Account là thành viên của nhóm (lấy từ find_groups)")],
    group_id: Annotated[int, Field(description="chat_id của nhóm")],
    group_title: Annotated[str, Field(description="Tên nhóm (để hiển thị)")] = "",
    filter_active_days: Annotated[int | None, Field(description="Chỉ lấy người online trong N ngày. Bỏ trống = tất cả")] = None,
    method: Annotated[str, Field(description="'members' (danh sách thành viên) hoặc 'history' (người từng nhắn)")] = "members",
    wait_seconds: Annotated[int, Field(ge=0, le=300, description="Chờ tối đa bao lâu để job xong")] = 120,
) -> dict:
    """Cào member của 1 nhóm (chỉ ĐỌC từ Telegram, không nhắn ai). Backend tự bỏ
    admin/owner của nhóm, bot và profile rỗng. Trả scrape_job_id khi xong."""
    if method not in ("members", "history"):
        raise ValueError("method phải là 'members' hoặc 'history'")
    res = await _call("POST", "/api/members/scrape", json={
        "account_id": account_id, "group_id": group_id, "group_title": group_title or None,
        "filter_active_days": filter_active_days, "exclude_bots": True, "scrape_method": method})
    job_id = res["scrape_job_id"]
    deadline = time.time() + wait_seconds
    delay = 3.0
    while time.time() < deadline:
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 15.0)
        jobs = (await _call("GET", "/api/members/scrape-jobs")).get("jobs", [])
        job = next((j for j in jobs if j["scrape_job_id"] == job_id), None)
        if job:
            return {"status": "done", "scrape_job_id": job_id, "member_count": job["member_count"],
                    "group_title": job.get("group_title"),
                    "next": "preview_targets(scrape_job_id=...) để xem trước danh sách"}
    return {"status": "running", "scrape_job_id": job_id,
            "note": "Chưa xong (hoặc nhóm không có member hợp lệ). Gọi list_scrape_jobs sau ít phút."}


@mcp.tool(annotations=RO)
async def list_scrape_jobs(
    title_contains: Annotated[str, Field(description="Lọc theo tên nhóm")] = "",
    limit: Annotated[int, Field(ge=1, le=200)] = 30,
) -> dict:
    """Liệt kê các danh sách member đã cào (scrape job), mới nhất trước."""
    jobs = (await _call("GET", "/api/members/scrape-jobs")).get("jobs", [])
    q = title_contains.strip().lower()
    if q:
        jobs = [j for j in jobs if q in (j.get("group_title") or "").lower() or q in j["scrape_job_id"].lower()]
    return {"count": len(jobs), "jobs": jobs[:limit]}


# ── Target preview / filtered list ──────────────────────────────────────────

FilterKw = Annotated[list[str], Field(description="Bỏ người có username/tên chứa 1 trong các từ (vd ['weex'])")]
FilterIds = Annotated[list[int], Field(description="Bỏ thêm theo user_id")]
FilterNames = Annotated[list[str], Field(description="Bỏ thêm theo username")]


async def _filtered(scrape_job_id, exclude_keywords, exclude_user_ids, exclude_usernames,
                    require_username, only_premium):
    members = (await _call("GET", f"/api/members/scrape-jobs/{scrape_job_id}",
                           params={"limit": 2000})).get("members", [])
    if not members:
        raise RuntimeError("Scrape job không tồn tại hoặc trống")
    accs, _ = await _accounts_raw()
    ids, names = L.internal_identity(accs)
    kept, excluded = L.filter_members(
        members, internal_ids=ids, internal_usernames=names,
        exclude_keywords=exclude_keywords, exclude_user_ids=exclude_user_ids,
        exclude_usernames=exclude_usernames, require_username=require_username,
        only_premium=only_premium)
    return members, kept, excluded


def _summary(members, kept, excluded, show: int) -> dict:
    reasons: dict[str, int] = {}
    for m in excluded:
        reasons[m["reason"]] = reasons.get(m["reason"], 0) + 1
    return {
        "total": len(members), "kept": len(kept), "excluded": len(excluded),
        "excluded_by_reason": reasons,
        "kept_premium": sum(1 for m in kept if m.get("is_premium")),
        "kept_without_username": sum(1 for m in kept if not m.get("username")),
        "kept_sample": [L.compact_member(m) for m in kept[:show]],
        "excluded_list": [L.compact_member(m) for m in excluded[:show]],
        "note": ("Admin/owner nhóm và bot đã bị loại lúc cào. Campaign còn tự bỏ qua người đã nhận DM "
                 "ở campaign khác, blacklist và account nội bộ khi chạy."),
    }


@mcp.tool(annotations=RO)
async def preview_targets(
    scrape_job_id: str,
    exclude_keywords: FilterKw = [],
    exclude_user_ids: FilterIds = [],
    exclude_usernames: FilterNames = [],
    require_username: Annotated[bool, Field(description="Bỏ người không có username (khó gửi từ account khác account cào)")] = False,
    only_premium: bool = False,
    show: Annotated[int, Field(ge=0, le=500)] = 100,
) -> dict:
    """DRY-RUN: xem ai sẽ nhận tin và ai bị loại (kèm lý do), KHÔNG ghi gì.
    Luôn loại account nội bộ của mình và bot."""
    members, kept, excluded = await _filtered(scrape_job_id, exclude_keywords, exclude_user_ids,
                                              exclude_usernames, require_username, only_premium)
    return _summary(members, kept, excluded, show)


@mcp.tool(annotations=WRITE)
async def create_target_list(
    scrape_job_id: str,
    title: Annotated[str, Field(description="Tên danh sách mới, vd 'WEEX SEA meetup – KOL (lọc)'")] = "",
    exclude_keywords: FilterKw = [],
    exclude_user_ids: FilterIds = [],
    exclude_usernames: FilterNames = [],
    require_username: bool = False,
    only_premium: bool = False,
) -> dict:
    """Tạo scrape job MỚI chỉ gồm người qua bộ lọc (giống preview_targets). Job gốc giữ
    nguyên. Dùng scrape_job_id trả về cho create_dm_campaign_draft."""
    members, kept, excluded = await _filtered(scrape_job_id, exclude_keywords, exclude_user_ids,
                                              exclude_usernames, require_username, only_premium)
    if not kept:
        raise RuntimeError("Không còn ai sau khi lọc")
    res = await _call("POST", f"/api/members/scrape-jobs/{scrape_job_id}/subset",
                      json={"keep_user_ids": [m["user_id"] for m in kept], "group_title": title or None})
    s = _summary(members, kept, excluded, 0)
    s.pop("kept_sample"); s.pop("excluded_list")
    return {**res, "filter_summary": s}


# ── Campaigns ───────────────────────────────────────────────────────────────

@mcp.tool(annotations=WRITE)
async def create_dm_campaign_draft(
    name: str,
    scrape_job_id: Annotated[str, Field(description="Danh sách người nhận (nên dùng job từ create_target_list)")],
    sender_account_ids: Annotated[list[int], Field(description="Account gửi; chỉ account health.ok")],
    messages: Annotated[list[str], Field(description="Nội dung tin. Nhiều phần tử = gửi nối tiếp. Hỗ trợ {name}, {first_name}, {group}, spintax {a|b}")],
    delay_min: Annotated[int, Field(ge=60, description="Giây nghỉ tối thiểu giữa 2 DM của 1 account")] = 240,
    delay_max: Annotated[int, Field(ge=60)] = 540,
    daily_limit_premium: Annotated[int, Field(ge=1, le=60)] = 25,
    daily_limit_normal: Annotated[int, Field(ge=1, le=10)] = 8,
    use_ai_remix: Annotated[bool, Field(description="AI viết lại mỗi tin cho khác nhau (cần AI provider hoạt động)")] = True,
    exclude_previous_dms: Annotated[bool, Field(description="Bỏ người đã nhận DM ở campaign khác")] = True,
) -> dict:
    """Tạo DM campaign ở trạng thái NHÁP (draft). KHÔNG gửi gì. Người dùng tự xem lại
    và bấm Start trên dashboard. Từ chối account gửi không khỏe."""
    if delay_max < delay_min:
        raise ValueError("delay_max phải ≥ delay_min")
    msgs = [m.strip() for m in messages if m and m.strip()]
    if not msgs:
        raise ValueError("Cần ít nhất 1 tin nhắn")
    if not sender_account_ids:
        raise ValueError("Cần ít nhất 1 account gửi")
    accs, spam = await _accounts_raw(refresh=True)
    by_id = {a["id"]: a for a in accs}
    bad = {}
    for aid in sender_account_ids:
        a = by_id.get(aid)
        if not a:
            bad[aid] = "không tồn tại"
            continue
        h = L.account_health(a, _spam_for(spam, aid))
        if not h["ok"]:
            bad[aid] = f"{a.get('name')}: {h['label']}"
    if bad:
        raise RuntimeError(f"Account gửi không khỏe, bỏ ra rồi thử lại: {bad}")
    res = await _call("POST", "/api/members/campaigns", json={
        "name": name, "scrape_job_id": scrape_job_id, "sender_account_ids": sender_account_ids,
        "messages": [{"msg_type": "text", "content": m, "media_path": None} for m in msgs],
        "delay_min": delay_min, "delay_max": delay_max,
        "daily_limit_premium": daily_limit_premium, "daily_limit_normal": daily_limit_normal,
        "use_ai_remix": use_ai_remix, "exclude_previous_dms": exclude_previous_dms,
        "auto_resume": False,
    })
    n_prem = sum(1 for aid in sender_account_ids if by_id[aid].get("is_premium"))
    per_day = n_prem * daily_limit_premium + (len(sender_account_ids) - n_prem) * daily_limit_normal
    total = res.get("total_targets") or 0
    return {**res, "state": "draft (chưa gửi)",
            "capacity_per_day": per_day,
            "estimated_days": round(total / per_day, 1) if per_day else None,
            "next": "Người dùng mở tab Members → DM Campaign, kiểm tra rồi tự bấm Start."}


@mcp.tool(annotations=RO)
async def list_campaigns(
    status: Annotated[str, Field(description="Lọc: draft, scheduled, running, paused, paused_auto, completed, error")] = "",
    limit: Annotated[int, Field(ge=1, le=200)] = 30,
) -> dict:
    """Liệt kê DM campaign với tiến độ gọn."""
    cs = (await _call("GET", "/api/members/campaigns")).get("campaigns", [])
    if status:
        cs = [c for c in cs if c.get("status") == status]
    keys = ("id", "name", "status", "scrape_job_id", "sender_account_ids", "total_targets",
            "sent_count", "failed_count", "skipped_count", "use_ai_remix", "updated_at")
    return {"count": len(cs), "campaigns": [{k: c.get(k) for k in keys} for c in cs[:limit]]}


@mcp.tool(annotations=RO)
async def campaign_progress(
    campaign_id: int,
    log_limit: Annotated[int, Field(ge=0, le=500)] = 20,
) -> dict:
    """Tiến độ + log gần nhất của 1 campaign (thành công / lỗi / bỏ qua và lý do)."""
    c = (await _call("GET", f"/api/members/campaigns/{campaign_id}"))["campaign"]
    logs = (await _call("GET", f"/api/members/campaigns/{campaign_id}/logs",
                        params={"limit": max(log_limit, 1)})).get("logs", []) if log_limit else []
    reasons: dict[str, int] = {}
    for lg in logs:
        if lg.get("status") != "success":
            k = f"{lg.get('status')}: {(lg.get('error_message') or '')[:60]}"
            reasons[k] = reasons.get(k, 0) + 1
    keys = ("id", "name", "status", "total_targets", "sent_count", "failed_count", "skipped_count",
            "delay_min", "delay_max", "daily_limit_premium", "daily_limit_normal", "use_ai_remix",
            "sender_account_ids", "messages", "created_at", "updated_at")
    done = (c.get("sent_count") or 0) + (c.get("failed_count") or 0) + (c.get("skipped_count") or 0)
    return {"campaign": {k: c.get(k) for k in keys},
            "progress_pct": round(100 * done / c["total_targets"], 1) if c.get("total_targets") else 0,
            "recent_non_success": reasons,
            "logs": [{k: lg.get(k) for k in ("sent_at", "status", "target_username", "target_user_id",
                                                "account_id", "error_message")
                      if lg.get(k) is not None} for lg in logs[:log_limit]]}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
async def pause_campaign(campaign_id: int) -> dict:
    """Tạm dừng 1 campaign (an toàn, dừng gửi ngay). Muốn chạy lại phải bấm Start trên dashboard."""
    await _call("POST", f"/api/members/campaigns/{campaign_id}/stop")
    c = (await _call("GET", f"/api/members/campaigns/{campaign_id}"))["campaign"]
    return {"campaign_id": campaign_id, "status": c.get("status")}


def main():
    if not _api_key():
        log.warning("Chưa có API key: đặt TGS_API_KEY hoặc DASHBOARD_SECRET_KEY")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
