"""Auto tier-up for WEEX sub-affiliates (KOLs) via the affiliates.weex.com portal API.

Monthly (day 1): read last month's trade volume per sub-affiliate, map it to the
KOL ladder (kol_offer), and queue an upgrade when the ladder rate is ABOVE the
current futures cashback ratio. Never downgrades (ladder rule: quarterly review,
done by hand). Every upgrade waits for an admin's tap in the command bot before
the rate is written to WEEX — it moves real money.

The portal API is internal/unofficial: auth is the portal's `accessToken`
(localStorage after login) pasted into setting `weex_aff_token`, sent as Bearer.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

import database as db

logger = logging.getLogger("tg-scheduler.weex_affiliate")

BASE = "https://affiliates.weex.com/agency/v1/agency/interface"
TOKEN_KEY = "weex_aff_token"
ENABLED_KEY = "weex_tier_auto_enabled"

# Portal form fields (camelCase) ← agent/list record fields (snake_case). Rates are ratios (0.8 = 80%).
_RATE_FIELDS = {
    "spotRebateRate": "spot_rebate_rate",
    "spotTraceUserRebateRate": "spot_trace_user_rebate_rate",
    "spotApiRebateRate": "spot_api_rebate_rate",
    "spotApiTraceUserRebateRate": "spot_api_trace_user_rebate_rate",
    "rebateRate": "rebate_rate",  # futures cashback ratio — the one the ladder drives
    "traceUserRebateRate": "trace_user_rebate_rate",
    "apiRebateRate": "api_rebate_rate",
    "apiTraceUserRebateRate": "api_trace_user_rebate_rate",
}


class WeexAuthError(RuntimeError):
    pass


async def _request(method: str, path: str, *, params=None, json=None) -> dict:
    token = (await db.get_setting(TOKEN_KEY, "") or "").strip().strip('"')
    if not token:
        raise WeexAuthError("Chưa có token WEEX affiliate (setting weex_aff_token)")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(method, BASE + path, params=params, json=json,
                            headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    body = r.json()
    if body.get("code") in (401, 437):
        raise WeexAuthError("Token WEEX affiliate hết hạn / sai — đăng nhập portal và dán token mới")
    return body


async def list_agents(agent_uid: str = "") -> list[dict]:
    out, page = [], 1
    while True:
        body = await _request("GET", "/agent/list", params={
            "page": page, "limit": 50, "agentUserId": agent_uid, "showType": 0})
        data = body.get("data") or {}
        recs = data.get("records") or []
        out += recs
        if not recs or len(out) >= int(data.get("total") or 0):
            return out
        page += 1


async def monthly_volumes(start: str, end: str) -> dict[str, float]:
    """{affiliate_uid: futures trade volume USDT} for [start, end] (YYYY-MM-DD)."""
    out, page = {}, 1
    while True:
        body = await _request("GET", "/rankings/list", params={
            "metric": 1, "role": 0, "startTime": start, "endTime": end,
            "type": 0, "page": page, "limit": 10})  # portal rejects limit > 10
        rows = body.get("data") or []
        for r in rows:
            out[str(r.get("userId"))] = float(r.get("totalAmount") or 0)
        if not rows or len(out) >= int(body.get("count") or 0):
            return out
        page += 1


def tier_for_volume(volume_usd: float, ladder: dict) -> dict:
    tiers = sorted(ladder["tiers"], key=lambda t: t["min_volume_m"])
    best = tiers[0]
    for t in tiers:
        if volume_usd >= t["min_volume_m"] * 1_000_000:
            best = t
    return best


def plan_upgrades(agents: list[dict], volumes: dict[str, float], ladder: dict) -> list[dict]:
    plans = []
    for a in agents:
        uid = str(a.get("agent_user_id"))
        vol = volumes.get(uid, 0.0)
        tier = tier_for_volume(vol, ladder)
        new_rate = round(tier["revshare_pct"] / 100, 3)
        old_rate = float(a.get("rebate_rate") or 0)
        if new_rate > old_rate + 1e-9:  # upgrades only
            plans.append({"agent_uid": uid, "agent_name": a.get("agent_name") or "",
                          "volume_usd": vol, "old_rate": old_rate, "new_rate": new_rate,
                          "tier": tier["tier"]})
    return plans


def update_payload(rec: dict, new_rate: float) -> dict:
    """Body for agent/update, same shape the portal form sends (captured 2026-11-17); futures rate replaced."""
    body = {camel: float(rec.get(snake) or 0) for camel, snake in _RATE_FIELDS.items()}
    body.update({
        "rebateRate": new_rate,
        "agentUserId": int(rec["agent_user_id"]),
        "account": rec.get("account") or "",
        "agentName": (rec.get("agent_name") or "").strip(),
        "channelCode": rec.get("channel_code") or "",
        "relBdAccountId": str(rec.get("rel_bd_account_id") or 0),
        "agentType": rec.get("agent_type") or 1,
        "agentLevel": rec.get("agent_level") or 0,
        "rebateNotice": bool(rec.get("rebate_notice")),
        "id": rec["id"],
    })
    return body


async def apply_upgrade(agent_uid: str, expected_old: float, new_rate: float) -> None:
    """Write the new futures rate. Re-reads the record first; refuses if it changed meanwhile."""
    recs = [r for r in await list_agents(agent_uid) if str(r.get("agent_user_id")) == agent_uid]
    if not recs:
        raise RuntimeError(f"Không tìm thấy sub-affiliate UID {agent_uid}")
    rec = recs[0]
    cur = float(rec.get("rebate_rate") or 0)
    if abs(cur - expected_old) > 1e-9:
        raise RuntimeError(f"Tỷ lệ hiện tại đã đổi ({cur} ≠ {expected_old}), bỏ qua")
    body = await _request("POST", "/agent/update", json=update_payload(rec, new_rate))
    if not body.get("success"):
        raise RuntimeError(body.get("message") or body.get("msg") or "WEEX từ chối cập nhật")


def previous_month(today: date | None = None) -> tuple[str, str, str]:
    """(month 'YYYY-MM', first day, last day) of the month before `today`."""
    last = (today or date.today()).replace(day=1) - timedelta(days=1)
    return last.strftime("%Y-%m"), last.replace(day=1).isoformat(), last.isoformat()


# ── DB ──────────────────────────────────────────────────────────────────────

async def save_plans(month: str, plans: list[dict]) -> list[dict]:
    """Insert new pending rows (skips agents already queued this month). Returns inserted rows."""
    inserted = []
    async with db.get_db() as conn:
        for p in plans:
            cur = await conn.execute(
                "INSERT OR IGNORE INTO weex_tier_upgrades "
                "(month, agent_uid, agent_name, volume_usd, old_rate, new_rate, tier) VALUES (?,?,?,?,?,?,?)",
                (month, p["agent_uid"], p["agent_name"], p["volume_usd"], p["old_rate"], p["new_rate"], p["tier"]))
            if cur.rowcount:
                inserted.append({**p, "id": cur.lastrowid, "month": month})
        await conn.commit()
    return inserted


async def get_upgrade(upgrade_id: int) -> dict | None:
    async with db.get_db() as conn:
        cur = await conn.execute("SELECT * FROM weex_tier_upgrades WHERE id=?", (upgrade_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return dict(zip([d[0] for d in cur.description], row))


async def pending_ids(month: str) -> list[int]:
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT id FROM weex_tier_upgrades WHERE month=? AND status='pending' ORDER BY id", (month,))
        return [r[0] for r in await cur.fetchall()]


async def _set_status(upgrade_id: int, status: str, error: str = "") -> None:
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE weex_tier_upgrades SET status=?, error=?, decided_at=datetime('now') WHERE id=?",
            (status, error, upgrade_id))
        await conn.commit()


async def approve(upgrade_id: int) -> str:
    """Apply one pending upgrade. Returns a one-line result for the admin."""
    u = await get_upgrade(upgrade_id)
    if not u or u["status"] != "pending":
        return f"#{upgrade_id}: không còn chờ duyệt"
    label = f"{u['agent_name']} ({u['agent_uid']}) {u['old_rate']:.0%} → {u['new_rate']:.0%}"
    try:
        await apply_upgrade(u["agent_uid"], u["old_rate"], u["new_rate"])
    except Exception as e:
        await _set_status(upgrade_id, "failed", str(e)[:300])
        logger.warning("[WeexTier] apply #%s failed: %s", upgrade_id, e)
        return f"❌ {label}: {e}"
    await _set_status(upgrade_id, "applied")
    logger.info("[WeexTier] applied #%s %s", upgrade_id, label)
    return f"✅ {label}"


async def reject(upgrade_id: int) -> str:
    u = await get_upgrade(upgrade_id)
    if not u or u["status"] != "pending":
        return f"#{upgrade_id}: không còn chờ duyệt"
    await _set_status(upgrade_id, "rejected")
    return f"🚫 Bỏ qua {u['agent_name']} ({u['agent_uid']})"


# ── Onboarding on UID ───────────────────────────────────────────────────────

BIG_KOL_MIN_VOLUME_M = 20  # ≥ this stated monthly volume → straight onto our CSS line
CHANNEL_KEY = "weex_aff_channel_code"


def add_payload(uid: str, name: str, rate: float, channel: str) -> dict:
    """Body for agent/add: same shape as the edit form, no id, futures rate only."""
    body = {camel: 0.0 for camel in _RATE_FIELDS}
    body.update({"rebateRate": rate, "agentUserId": int(uid), "account": name, "agentName": name,
                 "channelCode": channel, "relBdAccountId": "0", "agentType": 1, "agentLevel": 1,
                 "rebateNotice": False})
    return body


def _portal_name(username: str | None, uid: str) -> str:
    import re
    base = re.sub(r"[^A-Za-z0-9]", "", username or "")[:20]
    return base or f"kol{uid}"


async def create_or_raise(uid: str, username: str | None, rate: float) -> str:
    """Put UID on our line at `rate`: add if new, raise if already ours at a lower rate. Returns a result line."""
    existing = [r for r in await list_agents(uid) if str(r.get("agent_user_id")) == uid]
    if existing:
        cur = float(existing[0].get("rebate_rate") or 0)
        if cur >= rate - 1e-9:
            return f"UID {uid} đã ở line CSS với {cur:.0%}, giữ nguyên"
        await apply_upgrade(uid, cur, rate)
        return f"UID {uid} nâng {cur:.0%} → {rate:.0%}"
    channel = await db.get_setting(CHANNEL_KEY, "CSS2026") or "CSS2026"
    body = await _request("POST", "/agent/add", json=add_payload(uid, _portal_name(username, uid), rate, channel))
    if not body.get("success"):
        raise RuntimeError(body.get("message") or body.get("msg") or "WEEX từ chối tạo sub-affiliate")
    data = body.get("data") or {}
    # Portal login for the KOL: admins only, never pasted into the KOL chat automatically.
    return (f"UID {uid} đã tạo sub-affiliate {rate:.0%}"
            + (f"\nTài khoản portal: `{data.get('account')}` / `{data.get('pwd')}`" if data.get("pwd") else ""))


async def onboard_uid(account_id: int, user_id: int, uid: str) -> str | None:
    """On a confirmed WEEX UID: big KOL → auto add to CSS line at Tier 1; small KOL → card for the team.

    Off unless weex_tier_auto_enabled=1. Never raises (runs as a background task).
    """
    try:
        if await db.get_setting(ENABLED_KEY, "0") != "1":
            return None
        if (await db.get_chat_exchange(account_id, user_id)).upper() != "WEEX":
            return None
        import command_bot
        import kol_offer
        prof = await db.get_kol_profile(account_id, user_id) or {}
        vol_m = kol_offer.parse_volume_m(prof.get("monthly_volume"))
        who = f"@{prof['username']}" if prof.get("username") else f"user {user_id}"
        vol_txt = f"{vol_m:g}M" if vol_m is not None else "chưa khai"
        if vol_m is not None and vol_m >= BIG_KOL_MIN_VOLUME_M:
            ladder = await kol_offer.get_ladder()
            rate = round(sorted(ladder["tiers"], key=lambda t: t["tier"])[0]["revshare_pct"] / 100, 3)
            try:
                res = await create_or_raise(uid, prof.get("username"), rate)
                text = f"🟢 **KOL lớn lên line CSS** — {who}, volume khai {vol_txt}\n{res}"
            except Exception as e:
                res = None
                text = f"❌ **Tạo sub-affiliate lỗi** — {who} UID `{uid}`, volume khai {vol_txt}\n{e}"
        else:
            res = "team"
            text = (f"🆕 **KOL nhỏ gửi UID** — {who}\nUID: `{uid}`\nVolume khai: {vol_txt}\n"
                    "→ Submit lên WEEX xin KOL + camp.")
        await command_bot.send_admin_text(text)
        return res
    except WeexAuthError as e:
        import alerts
        await alerts.send_alert("weex_aff_token", f"🔑 {e} (onboard UID {uid} chưa xử lý)")
    except Exception as e:
        logger.error("[WeexOnboard] UID %s failed: %s", uid, e)
    return None


# ── Monthly job ─────────────────────────────────────────────────────────────

def review_text(month: str, rows: list[dict]) -> str:
    lines = [f"📈 **Nâng bậc hoa hồng WEEX — volume tháng {month}**", ""]
    for r in rows:
        lines.append(f"#{r['id']} **{r['agent_name']}** `{r['agent_uid']}` — "
                     f"{r['volume_usd'] / 1e6:.2f}M → Tier {r['tier']}: "
                     f"{r['old_rate']:.0%} → **{r['new_rate']:.0%}**")
    lines += ["", "Volume = futures volume trên portal. Bấm duyệt để ghi tỷ lệ mới lên WEEX."]
    return "\n".join(lines)


async def run_monthly_review(today: date | None = None) -> list[dict]:
    if await db.get_setting(ENABLED_KEY, "0") != "1":
        return []
    import alerts
    import kol_offer
    month, start, end = previous_month(today)
    try:
        agents = await list_agents()
        volumes = await monthly_volumes(start, end)
    except WeexAuthError as e:
        await alerts.send_alert("weex_aff_token", f"🔑 {e}")
        return []
    except Exception as e:
        logger.error("[WeexTier] review %s failed: %s", month, e)
        await alerts.send_alert("weex_tier_review", f"Nâng bậc WEEX tháng {month} lỗi: {e}")
        return []
    rows = await save_plans(month, plan_upgrades(agents, volumes, await kol_offer.get_ladder()))
    if rows:
        import command_bot
        if not await command_bot.send_weex_tier_review(review_text(month, rows), month, [r["id"] for r in rows]):
            await alerts.send_alert("weex_tier_review", f"Có {len(rows)} KOL chờ nâng bậc WEEX tháng {month} "
                                                       "nhưng command bot không gửi được.")
    logger.info("[WeexTier] %s: %d agents, %d upgrades queued", month, len(agents), len(rows))
    return rows
