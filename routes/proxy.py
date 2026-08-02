"""
Proxy management API routes.
Handles Webshare.io integration, proxy list import, testing, and account assignment.
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

import database as db
import proxy_manager as pm
import telegram_client as tg

logger = logging.getLogger("tg-scheduler.proxy")
router = APIRouter(prefix="/api/proxy", tags=["proxy"])


# ── Request Models ────────────────────────────────────────────────────────

class WebshareRequest(BaseModel):
    api_key: str
    proxy_type: str = "http"  # http or socks5
    auto_assign: bool = True

class ImportRequest(BaseModel):
    raw_text: str
    default_scheme: str = "http"
    auto_assign: bool = True

class AssignRequest(BaseModel):
    proxy_url: str


# ── GET /api/proxy/status ─────────────────────────────────────────────────

@router.get("/status")
async def get_proxy_status():
    """Get current proxy pool status + account-proxy mapping."""
    accounts = await db.get_accounts()
    proxy_pool_raw = await db.get_setting("proxy_pool", "[]")
    webshare_key = await db.get_setting("webshare_api_key", "")

    try:
        proxy_pool = json.loads(proxy_pool_raw)
    except Exception:
        proxy_pool = []

    mapping = []
    for acc in accounts:
        proxy = acc.get("proxy_url") or None
        mapping.append({
            "account_id": acc["id"],
            "account_name": acc.get("name", acc.get("phone", "")),
            "is_logged_in": acc.get("is_logged_in", False),
            "proxy_url": proxy,
            "has_proxy": bool(proxy),
        })

    return {
        "pool_size": len(proxy_pool),
        "proxy_pool": proxy_pool,
        "webshare_configured": bool(webshare_key),
        "accounts": mapping,
        "accounts_with_proxy": sum(1 for m in mapping if m["has_proxy"]),
        "accounts_without_proxy": sum(1 for m in mapping if not m["has_proxy"]),
    }


# ── POST /api/proxy/webshare ─────────────────────────────────────────────

@router.post("/webshare")
async def fetch_from_webshare(req: WebshareRequest):
    """Fetch proxies from Webshare API, test them, and optionally auto-assign."""
    # Save API key
    await db.set_setting("webshare_api_key", req.api_key)

    # Fetch from Webshare
    result = await pm.fetch_webshare_proxies(req.api_key, req.proxy_type)
    if not result["success"]:
        return {"success": False, "error": result["error"]}

    raw_proxies = result["proxies"]
    if not raw_proxies:
        return {"success": False, "error": "Webshare trả về 0 proxy. Kiểm tra subscription."}

    # Test all proxies
    test_result = await pm.test_proxies_batch(raw_proxies)
    valid_proxies = test_result["valid_proxies"]

    if not valid_proxies:
        return {
            "success": False,
            "error": f"Tất cả {test_result['total']} proxy đều failed. Không có proxy nào hoạt động.",
            "test_results": test_result["results"],
        }

    # Save valid proxies to pool
    await db.set_setting("proxy_pool", json.dumps(valid_proxies))

    response = {
        "success": True,
        "fetched": len(raw_proxies),
        "tested": test_result["total"],
        "passed": test_result["passed"],
        "failed": test_result["failed"],
        "test_results": test_result["results"],
    }

    # Auto-assign if requested
    if req.auto_assign:
        assign_result = await _do_auto_assign(valid_proxies)
        response["assignments"] = assign_result

    return response


# ── POST /api/proxy/import ────────────────────────────────────────────────

@router.post("/import")
async def import_proxy_list(req: ImportRequest):
    """Parse raw proxy list, test them, and optionally auto-assign."""
    parsed = pm.parse_proxy_list(req.raw_text, req.default_scheme)
    if not parsed:
        return {"success": False, "error": "Không parse được proxy nào. Kiểm tra format."}

    # Test all proxies
    test_result = await pm.test_proxies_batch(parsed)
    valid_proxies = test_result["valid_proxies"]

    if not valid_proxies:
        return {
            "success": False,
            "error": f"Tất cả {test_result['total']} proxy đều failed.",
            "test_results": test_result["results"],
        }

    # Save valid proxies to pool
    existing_raw = await db.get_setting("proxy_pool", "[]")
    try:
        existing = json.loads(existing_raw)
    except Exception:
        existing = []

    # Merge (no duplicates)
    merged = list(dict.fromkeys(existing + valid_proxies))
    await db.set_setting("proxy_pool", json.dumps(merged))

    response = {
        "success": True,
        "parsed": len(parsed),
        "tested": test_result["total"],
        "passed": test_result["passed"],
        "failed": test_result["failed"],
        "pool_total": len(merged),
        "test_results": test_result["results"],
    }

    # Auto-assign if requested
    if req.auto_assign:
        assign_result = await _do_auto_assign(merged)
        response["assignments"] = assign_result

    return response


# ── POST /api/proxy/assign/{account_id} ──────────────────────────────────

@router.post("/assign/{account_id}")
async def assign_proxy(account_id: int, req: AssignRequest):
    """Assign a specific proxy to an account + reconnect."""
    # Test the proxy first
    test = await pm.test_single_proxy(req.proxy_url)
    if not test["ok"]:
        return {"success": False, "error": f"Proxy failed test: {test['error']}"}

    await db.update_account_proxy(account_id, req.proxy_url)
    reconnected = await _reconnect_account(account_id, req.proxy_url)

    return {
        "success": True,
        "account_id": account_id,
        "proxy_url": req.proxy_url,
        "latency_ms": test["latency_ms"],
        "reconnected": reconnected,
    }


# ── POST /api/proxy/remove/{account_id} ──────────────────────────────────

@router.post("/remove/{account_id}")
async def remove_proxy(account_id: int):
    """Remove proxy from an account + reconnect without proxy."""
    await db.update_account_proxy(account_id, None)
    reconnected = await _reconnect_account(account_id, None)
    return {"success": True, "account_id": account_id, "reconnected": reconnected}


# ── POST /api/proxy/reassign ─────────────────────────────────────────────

@router.post("/reassign")
async def reassign_all():
    """Re-assign proxies from pool to all accounts (round-robin)."""
    pool_raw = await db.get_setting("proxy_pool", "[]")
    try:
        proxy_pool = json.loads(pool_raw)
    except Exception:
        proxy_pool = []

    if not proxy_pool:
        return {"success": False, "error": "Proxy pool trống. Fetch/import proxy trước."}

    result = await _do_auto_assign(proxy_pool)
    return {"success": True, **result}


# ── POST /api/proxy/clear-pool ────────────────────────────────────────────

@router.post("/clear-pool")
async def clear_pool():
    """Clear the proxy pool and remove all account proxies."""
    await db.set_setting("proxy_pool", "[]")
    accounts = await db.get_accounts()
    for acc in accounts:
        if acc.get("proxy_url"):
            await db.update_account_proxy(acc["id"], None)
    return {"success": True, "message": "Đã xóa tất cả proxy"}


# ── Internal helpers ──────────────────────────────────────────────────────

async def _do_auto_assign(proxy_urls: list[str]) -> dict:
    """Auto-assign proxies to all accounts and reconnect them."""
    accounts = await db.get_accounts()
    assign_result = await pm.auto_assign_proxies(proxy_urls, accounts)

    reconnected = 0
    failed_reconnect = 0
    for a in assign_result["assignments"]:
        await db.update_account_proxy(a["account_id"], a["proxy_url"])
        ok = await _reconnect_account(a["account_id"], a["proxy_url"])
        if ok:
            reconnected += 1
        else:
            failed_reconnect += 1

    return {
        "assigned": len(assign_result["assignments"]),
        "reconnected": reconnected,
        "failed_reconnect": failed_reconnect,
        "assignments": assign_result["assignments"],
    }


async def _reconnect_account(account_id: int, proxy_url: Optional[str]) -> bool:
    """Reconnect an account with a new proxy."""
    try:
        result = await tg.reconnect_with_proxy(account_id, proxy_url)
        return result
    except Exception as e:
        logger.warning(f"[Proxy] Reconnect account {account_id} failed: {e}")
        return False
