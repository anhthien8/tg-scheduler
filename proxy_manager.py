"""
Proxy Pool Manager — Webshare.io integration + manual proxy list import.
Handles fetching, parsing, testing, and auto-assigning proxies to Telegram accounts.
"""
import asyncio
import logging
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger("tg-scheduler.proxy")

# ── Webshare API ──────────────────────────────────────────────────────────

WEBSHARE_API_BASE = "https://proxy.webshare.io/api/v2"

# Test target: Telegram's API endpoint (lightweight, always up)
PROXY_TEST_URL = "https://api.telegram.org/"
PROXY_TEST_TIMEOUT = 10  # seconds


async def fetch_webshare_proxies(api_key: str, proxy_type: str = "http") -> dict:
    """
    Fetch proxy list from Webshare.io API.
    Returns { "success": bool, "proxies": list[str], "count": int, "error": str|None }
    Each proxy is formatted as: http://user:pass@ip:port
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            all_proxies = []
            page = 1
            while True:
                resp = await client.get(
                    f"{WEBSHARE_API_BASE}/proxy/list/",
                    params={"mode": "direct", "page": page, "page_size": 100},
                    headers={"Authorization": f"Token {api_key}"},
                )
                if resp.status_code == 401:
                    return {"success": False, "proxies": [], "count": 0,
                            "error": "API key không hợp lệ (401 Unauthorized)"}
                if resp.status_code != 200:
                    return {"success": False, "proxies": [], "count": 0,
                            "error": f"Webshare API trả về HTTP {resp.status_code}"}

                data = resp.json()
                results = data.get("results", [])
                if not results:
                    break

                for p in results:
                    addr = p.get("proxy_address", "")
                    port = p.get("port", "")
                    user = p.get("username", "")
                    pwd  = p.get("password", "")
                    if addr and port:
                        scheme = proxy_type  # http by default
                        if user and pwd:
                            url = f"{scheme}://{user}:{pwd}@{addr}:{port}"
                        else:
                            url = f"{scheme}://{addr}:{port}"
                        all_proxies.append(url)

                # Check if there's a next page
                if not data.get("next"):
                    break
                page += 1

            logger.info(f"[Proxy] Fetched {len(all_proxies)} proxies from Webshare")
            return {"success": True, "proxies": all_proxies, "count": len(all_proxies), "error": None}

    except httpx.TimeoutException:
        return {"success": False, "proxies": [], "count": 0,
                "error": "Timeout khi kết nối Webshare API"}
    except Exception as e:
        logger.error(f"[Proxy] Webshare fetch error: {e}")
        return {"success": False, "proxies": [], "count": 0, "error": str(e)}


# ── Proxy Health Test ─────────────────────────────────────────────────────

async def test_single_proxy(proxy_url: str) -> dict:
    """
    Test a single proxy by making an HTTP request through it.
    Returns { "proxy": str, "ok": bool, "latency_ms": int, "error": str|None }
    """
    start = time.monotonic()
    try:
        # Determine proxy scheme for httpx
        proxy_for_httpx = proxy_url
        # httpx expects http:// or socks5:// proxies
        async with httpx.AsyncClient(
            proxy=proxy_for_httpx,
            timeout=PROXY_TEST_TIMEOUT,
            verify=False,
        ) as client:
            resp = await client.get(PROXY_TEST_URL)
            latency = int((time.monotonic() - start) * 1000)
            ok = resp.status_code < 500
            return {"proxy": proxy_url, "ok": ok, "latency_ms": latency, "error": None}
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        err_msg = str(e)[:80]
        return {"proxy": proxy_url, "ok": False, "latency_ms": latency, "error": err_msg}


async def test_proxies_batch(proxy_urls: list[str], concurrency: int = 10) -> dict:
    """
    Test a batch of proxies concurrently.
    Returns {
        "total": int,
        "passed": int,
        "failed": int,
        "results": list[{proxy, ok, latency_ms, error}],
        "valid_proxies": list[str]   # Only proxies that passed
    }
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _test_with_semaphore(proxy: str):
        async with semaphore:
            return await test_single_proxy(proxy)

    logger.info(f"[Proxy] Testing {len(proxy_urls)} proxies (concurrency={concurrency})...")
    tasks = [_test_with_semaphore(p) for p in proxy_urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    test_results = []
    valid = []
    for r in results:
        if isinstance(r, Exception):
            test_results.append({"proxy": "?", "ok": False, "latency_ms": 0, "error": str(r)[:80]})
        else:
            test_results.append(r)
            if r["ok"]:
                valid.append(r["proxy"])

    passed = len(valid)
    failed = len(proxy_urls) - passed
    logger.info(f"[Proxy] Test complete: {passed} passed, {failed} failed out of {len(proxy_urls)}")

    return {
        "total": len(proxy_urls),
        "passed": passed,
        "failed": failed,
        "results": test_results,
        "valid_proxies": valid,
    }


# ── Proxy List Parser ─────────────────────────────────────────────────────

def parse_proxy_list(raw_text: str, default_scheme: str = "http") -> list[str]:
    """
    Parse a raw proxy list text into standardized proxy URLs.
    Supports formats:
      - ip:port:user:pass
      - ip:port@user:pass
      - user:pass@ip:port
      - socks5://user:pass@ip:port
      - http://user:pass@ip:port
      - ip:port  (no auth)
    Returns list of formatted proxy URLs like: http://user:pass@ip:port
    """
    proxies = []
    lines = raw_text.strip().splitlines()

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        proxy_url = _parse_single_proxy(line, default_scheme)
        if proxy_url:
            proxies.append(proxy_url)

    logger.info(f"[Proxy] Parsed {len(proxies)} proxies from text input")
    return proxies


def _parse_single_proxy(line: str, default_scheme: str = "http") -> Optional[str]:
    """Parse a single proxy line into a standardized URL."""

    # Already a URL: socks5://... or http://...
    url_match = re.match(
        r'^(socks5|socks4|http|https)://(?:([^:@]+):([^@]*)@)?([^:]+):(\d+)$',
        line, re.IGNORECASE
    )
    if url_match:
        scheme = url_match.group(1).lower()
        user   = url_match.group(2) or ""
        pwd    = url_match.group(3) or ""
        host   = url_match.group(4)
        port   = url_match.group(5)
        if user and pwd:
            return f"{scheme}://{user}:{pwd}@{host}:{port}"
        return f"{scheme}://{host}:{port}"

    # Format: ip:port:user:pass
    four_part = re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', line)
    if four_part:
        host, port, user, pwd = four_part.groups()
        return f"{default_scheme}://{user}:{pwd}@{host}:{port}"

    # Format: user:pass@ip:port
    at_format = re.match(r'^([^:@]+):([^@]+)@([^:]+):(\d+)$', line)
    if at_format:
        user, pwd, host, port = at_format.groups()
        return f"{default_scheme}://{user}:{pwd}@{host}:{port}"

    # Format: ip:port (no auth)
    simple = re.match(r'^([^:]+):(\d+)$', line)
    if simple:
        host, port = simple.groups()
        return f"{default_scheme}://{host}:{port}"

    logger.warning(f"[Proxy] Could not parse: {line[:50]}")
    return None


# ── Auto-assign ───────────────────────────────────────────────────────────

async def auto_assign_proxies(proxy_urls: list[str], accounts: list[dict]) -> dict:
    """
    Auto-assign proxies to accounts round-robin.
    Returns { "assignments": [{account_id, account_name, proxy_url}], "unassigned_accounts": int }
    """
    assignments = []
    if not proxy_urls:
        return {"assignments": [], "unassigned_accounts": len(accounts)}

    for i, acc in enumerate(accounts):
        proxy = proxy_urls[i % len(proxy_urls)]
        assignments.append({
            "account_id": acc["id"],
            "account_name": acc.get("name", acc.get("phone", str(acc["id"]))),
            "proxy_url": proxy,
        })

    unassigned = max(0, len(accounts) - len(proxy_urls)) if len(proxy_urls) < len(accounts) else 0
    logger.info(
        f"[Proxy] Auto-assigned {len(assignments)} accounts, "
        f"{len(proxy_urls)} proxies available, {unassigned} accounts sharing proxies"
    )
    return {"assignments": assignments, "unassigned_accounts": unassigned}
