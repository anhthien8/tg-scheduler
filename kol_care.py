"""Own the account (WEEX BD training "Relationship-driven deal sourcing", 2026-11-20).

A BD's job starts after onboarding. This module finds the KOLs that need a human
touch today and keeps the record the BD scorecard reads:

* festival greetings in the KOL's own country (Tết, Hari Raya, Diwali, Songkran...),
* a check-in for onboarded KOLs nobody has talked to for 3+ weeks (volume or not) — the
  automatic monthly care message (dm_reply_tracker.process_monthly_kol_care, 30+ days)
  counts as a touch, so the two never land on the same KOL back to back,
* a monthly data review card from the KOL's WEEX volume (trend + next tier).

NOTHING here sends a message. Items are drafted on request and a human edits and
sends them from the dashboard (outbound rule: no automatic follow-up to quiet
leads). Every touch (greeting, check-in, review, meeting, call, event) is logged in
`kol_touchpoints`; skipping an item logs `skip_<kind>` so it does not come back.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta

import aiosqlite

import database as db
from database import get_db

logger = logging.getLogger("tg-scheduler.kol_care")

CHECKIN_DAYS = 21
CARE_STEPS = ("link_confirmed", "first_post", "active", "dormant")          # onboarded: check-ins
GREET_STEPS = ("deal_agreed", "uid_received") + CARE_STEPS                   # agreed or onboarded: greetings
DUE_KINDS = ("greeting", "birthday", "checkin", "review")
TOUCH_KINDS = ("meet", "call", "event") + DUE_KINDS + tuple("skip_" + k for k in DUE_KINDS)

_tables_ready = False


async def ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    async with get_db() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS kol_touchpoints (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                kind        TEXT NOT NULL,
                note        TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
            )""")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_kol_touch_lead ON kol_touchpoints(account_id, user_id, kind)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_kol_touch_time ON kol_touchpoints(created_at)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS kol_volume_monthly (
                uid         TEXT NOT NULL,
                month       TEXT NOT NULL,
                volume      REAL NOT NULL DEFAULT 0,
                updated_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (uid, month)
            )""")
        await conn.commit()
    _tables_ready = True


async def log_touchpoint(account_id: int, user_id: int, kind: str, note: str = "") -> None:
    if kind not in TOUCH_KINDS:
        raise ValueError(f"unknown touchpoint kind: {kind}")
    await ensure_tables()
    async with get_db() as conn:
        await conn.execute("INSERT INTO kol_touchpoints (account_id, user_id, kind, note) VALUES (?, ?, ?, ?)",
                           (account_id, user_id, kind, (note or "")[:500]))
        await conn.commit()


async def save_volumes(month: str, volumes: dict[str, float]) -> int:
    """Store one month of WEEX affiliate volumes ({uid: USDT}); month = 'YYYY-MM'."""
    if not volumes:
        return 0
    await ensure_tables()
    async with get_db() as conn:
        await conn.executemany(
            "INSERT INTO kol_volume_monthly (uid, month, volume, updated_at) VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(uid, month) DO UPDATE SET volume = excluded.volume, updated_at = excluded.updated_at",
            [(str(u), month, float(v or 0)) for u, v in volumes.items()])
        await conn.commit()
    return len(volumes)


def month_range(month: str) -> tuple[str, str]:
    y, m = (int(x) for x in month.split("-"))
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def last_months(n: int, today: date | None = None) -> list[str]:
    """The n full months before this one, oldest first: ['2026-08', '2026-09', '2026-10']."""
    d = (today or date.today()).replace(day=1)
    out = []
    for _ in range(n):
        d = (d - timedelta(days=1)).replace(day=1)
        out.append(d.strftime("%Y-%m"))
    return out[::-1]


async def refresh_volumes(months: int = 3, today: date | None = None) -> dict:
    """Pull the last `months` full months from the WEEX affiliate portal into kol_volume_monthly."""
    import weex_affiliate
    saved = {}
    for month in last_months(months, today):
        start, end = month_range(month)
        saved[month] = await save_volumes(month, await weex_affiliate.monthly_volumes(start, end))
    return saved


# ── Festivals by country ─────────────────────────────────────────────────────
# Fixed-date ones repeat yearly; lunar / Islamic / Hindu dates are listed per year
# (Islamic dates are ±1 day — the greeting window covers it). Add next year's
# dates when the list runs out.
_FIXED = (
    ((1, 1), "New Year", None),
    ((12, 25), "Christmas", {"PH", "SG", "NG", "US", "GB", "AU", "CA", "DE", "FR", "IT", "ES", "BR", "MX", "AR", "ZA", "KE"}),
    ((4, 13), "Songkran", {"TH"}),
    ((8, 17), "Indonesian Independence Day", {"ID"}),
    ((8, 31), "Merdeka Day", {"MY"}),
    ((9, 2), "Vietnam National Day", {"VN"}),
    ((10, 29), "Republic Day", {"TR"}),
)
_LUNAR_CN = {"VN", "CN", "HK", "TW", "SG", "MY", "KR"}
_MUSLIM = {"ID", "MY", "PK", "TR", "AE", "SA", "EG", "BD", "NG", "MA", "DZ"}
_DATED = (
    ("2027-02-06", "Lunar New Year", _LUNAR_CN),
    ("2027-03-10", "Eid al-Fitr", _MUSLIM),
    ("2027-03-22", "Holi", {"IN"}),
    ("2027-05-17", "Eid al-Adha", _MUSLIM),
    ("2027-09-15", "Mid-Autumn Festival", {"VN", "CN", "HK", "TW", "SG", "MY"}),
    ("2027-10-29", "Diwali", {"IN"}),
    ("2028-01-26", "Lunar New Year", _LUNAR_CN),
    ("2028-02-27", "Eid al-Fitr", _MUSLIM),
)
GREET_BEFORE_DAYS = 3   # show the item this many days before the day
GREET_AFTER_DAYS = 1    # …and until this many days after

_COUNTRY_NAMES = {
    "vietnam": "VN", "viet nam": "VN", "việt nam": "VN", "indonesia": "ID", "malaysia": "MY", "thailand": "TH",
    "philippines": "PH", "singapore": "SG", "india": "IN", "pakistan": "PK", "bangladesh": "BD",
    "turkey": "TR", "türkiye": "TR", "turkiye": "TR", "china": "CN", "hong kong": "HK", "taiwan": "TW",
    "korea": "KR", "south korea": "KR", "nigeria": "NG", "uae": "AE", "united arab emirates": "AE", "dubai": "AE",
    "saudi arabia": "SA", "egypt": "EG", "morocco": "MA", "united states": "US", "usa": "US",
    "united kingdom": "GB", "uk": "GB", "australia": "AU", "canada": "CA", "germany": "DE", "france": "FR",
    "brazil": "BR", "mexico": "MX", "argentina": "AR", "south africa": "ZA", "kenya": "KE",
}
_VIET_RE = re.compile(r"[ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđ]", re.IGNORECASE)
_CJK_RE = re.compile(r"[一-鿿]")
_HANGUL_RE = re.compile(r"[가-힯]")
_THAI_RE = re.compile(r"[฀-๿]")


def _country_from_text(text: str) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    if t.upper() in set(_COUNTRY_NAMES.values()):
        return t.upper()
    for name, code in sorted(_COUNTRY_NAMES.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", t):
            return code
    return None


def country_code(profile: dict | None, history: list[dict] | None = None) -> str | None:
    """The KOL's own country first, then the community's, then the language they write in."""
    p = profile or {}
    for key in ("kol_country", "community_country"):
        c = _country_from_text(str(p.get(key) or ""))
        if c:
            return c
    user = " ".join(str(m.get("content") or "") for m in (history or [])[-20:] if m.get("role") == "user")
    if len(_VIET_RE.findall(user)) >= 3:
        return "VN"
    if len(_THAI_RE.findall(user)) >= 3:
        return "TH"
    if len(_HANGUL_RE.findall(user)) >= 3:
        return "KR"
    if len(_CJK_RE.findall(user)) >= 3:
        return "CN"
    return None


def festival_for(country: str | None, today: date) -> dict | None:
    """The festival in the KOL's country whose greeting window covers today, else None."""
    cands = []
    for (m, d), name, where in _FIXED:
        for y in (today.year - 1, today.year, today.year + 1):
            cands.append((date(y, m, d), name, where))
    cands += [(date.fromisoformat(s), name, where) for s, name, where in _DATED]
    for day, name, where in sorted(cands):
        if where is not None and country not in where:
            continue
        if day - timedelta(days=GREET_BEFORE_DAYS) <= today <= day + timedelta(days=GREET_AFTER_DAYS):
            return {"name": name, "date": day.isoformat()}
    return None


_BDAY_RE = re.compile(r"(?:(\d{4})-(\d{1,2})-(\d{1,2}))|(?:(\d{1,2})\s*[/.\-]\s*(\d{1,2}))")


def parse_birthday(value) -> tuple[int, int] | None:
    """(day, month) from 'DD/MM', 'DD-MM', 'DD.MM' or ISO 'YYYY-MM-DD'; None when unreadable."""
    m = _BDAY_RE.search(str(value or ""))
    if not m:
        return None
    if m.group(1):
        day, month = int(m.group(3)), int(m.group(2))
    else:
        day, month = int(m.group(4)), int(m.group(5))
    try:
        date(2024, month, day)  # leap year: 29/02 is valid
    except ValueError:
        return None
    return day, month


def birthday_for(value, today: date) -> dict | None:
    """The KOL's birthday when its greeting window covers today (29/02 → 28/02 in other years)."""
    dm = parse_birthday(value)
    if not dm:
        return None
    day, month = dm
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            d = date(y, 2, 28)
        if d - timedelta(days=GREET_BEFORE_DAYS) <= today <= d + timedelta(days=GREET_AFTER_DAYS):
            return {"name": "Birthday", "date": d.isoformat()}
    return None


# ── Monthly review numbers ───────────────────────────────────────────────────

def review_numbers(volumes: list[tuple[str, float]], ladder: dict) -> dict | None:
    """Trend card from [(month, volume_usd)] oldest first: last month, change, next tier distance."""
    vols = [(m, float(v or 0)) for m, v in volumes]
    if not vols:
        return None
    month, last = vols[-1]
    prev = vols[-2][1] if len(vols) > 1 else None
    tiers = sorted(ladder.get("tiers") or [], key=lambda t: float(t["min_volume_m"]))
    cur = None
    nxt = None
    for t in tiers:
        if last >= float(t["min_volume_m"]) * 1_000_000:
            cur = t
        elif nxt is None:
            nxt = t
    return {
        "month": month, "months": [{"month": m, "volume": v} for m, v in vols],
        "volume": last,
        "change_pct": (round((last - prev) / prev * 100) if prev else None),
        "tier": cur["tier"] if cur else None,
        "next_tier": nxt["tier"] if nxt else None,
        "next_tier_pct": nxt["revshare_pct"] if nxt else None,
        "to_next_tier": (round(float(nxt["min_volume_m"]) * 1_000_000 - last) if nxt else None),
    }


def _m(v: float) -> str:
    return f"{v / 1_000_000:.1f}M" if v >= 100_000 else f"{v / 1_000:.0f}k"


def review_summary(r: dict) -> str:
    parts = [f"{r['month']}: {_m(r['volume'])}"]
    if r.get("change_pct") is not None:
        parts.append(f"{r['change_pct']:+d}% vs previous month")
    if r.get("to_next_tier"):
        parts.append(f"{_m(r['to_next_tier'])} away from Tier {r['next_tier']} ({r['next_tier_pct']}%)")
    return ", ".join(parts)


# ── Due list ─────────────────────────────────────────────────────────────────

def _days_since(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "").split("+")[0])
    except ValueError:
        return None
    return (now - dt).total_seconds() / 86400


async def _volumes_by_uid() -> dict[str, list[tuple[str, float]]]:
    await ensure_tables()
    async with get_db() as conn:
        cur = await conn.execute("SELECT uid, month, volume FROM kol_volume_monthly ORDER BY month")
        rows = await cur.fetchall()
    out: dict[str, list[tuple[str, float]]] = {}
    for uid, month, vol in rows:
        out.setdefault(str(uid), []).append((month, vol))
    return out


async def due_items(today: date | None = None) -> list[dict]:
    """KOLs who need a human touch today (greeting / check-in / monthly review), newest step first."""
    import kol_offer
    await ensure_tables()
    today = today or date.today()
    now = datetime.utcnow()
    month_start = today.replace(day=1).isoformat()
    last_month = last_months(1, today)[0]
    async with get_db() as conn:
        conn.row_factory = aiosqlite.Row
        marks = ",".join("?" for _ in GREET_STEPS)
        cur = await conn.execute(f"""
            SELECT c.account_id, c.user_id, c.username, c.name, c.kol_step, c.updated_at, c.last_care_at, c.history_json,
                   p.profile_json, p.weex_uid
            FROM ai_followup_chats c
            LEFT JOIN kol_profiles p ON p.account_id = c.account_id AND p.user_id = c.user_id
            WHERE COALESCE(c.status, '') != 'bot_ignored' AND c.kol_step IN ({marks})
              AND c.user_id NOT IN (SELECT user_id FROM dm_blacklist WHERE user_id IS NOT NULL)
        """, GREET_STEPS)
        chats = [dict(r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT account_id, user_id, kind, MAX(created_at) AS last FROM kol_touchpoints GROUP BY 1, 2, 3")
        touches: dict[tuple[int, int], dict[str, str]] = {}
        for r in await cur.fetchall():
            touches.setdefault((r["account_id"], r["user_id"]), {})[r["kind"]] = r["last"]
    vols = await _volumes_by_uid()
    ladder = await kol_offer.get_ladder()
    items = []
    for c in chats:
        key = (c["account_id"], c["user_id"])
        t = touches.get(key, {})
        try:
            prof = json.loads(c.get("profile_json") or "{}") or {}
        except Exception:
            prof = {}
        base = {"account_id": c["account_id"], "user_id": c["user_id"], "username": c.get("username") or "",
                "name": c.get("name") or "", "step": c.get("kol_step") or "",
                "archetype": prof.get("archetype") or ""}
        last_any = max((v for v in t.values() if v), default=None)

        try:
            history = json.loads(c.get("history_json") or "[]")
        except Exception:
            history = []
        country = country_code(prof, history)
        fest = festival_for(country, today)
        if fest:
            window_start = (date.fromisoformat(fest["date"]) - timedelta(days=10)).isoformat()
            done = max(t.get("greeting") or "", t.get("skip_greeting") or "")
            if done < window_start:
                items.append({**base, "kind": "greeting", "country": country, "festival": fest,
                              "reason": f"{fest['name']} ({fest['date']})"})

        bday = birthday_for(prof.get("birthday"), today)
        if bday:
            window_start = (date.fromisoformat(bday["date"]) - timedelta(days=10)).isoformat()
            done = max(t.get("birthday") or "", t.get("skip_birthday") or "")
            if done < window_start:
                items.append({**base, "kind": "birthday", "festival": bday,
                              "reason": f"Sinh nhật {bday['date'][8:10]}/{bday['date'][5:7]}"})

        if c.get("kol_step") in CARE_STEPS:
            # The automatic monthly care check-in (dm_reply_tracker.process_monthly_kol_care) counts as a touch.
            quiet = _days_since(max(c.get("updated_at") or "", c.get("last_care_at") or "") or None, now)
            touched = _days_since(last_any, now)
            if quiet is not None and quiet >= CHECKIN_DAYS and (touched is None or touched >= CHECKIN_DAYS):
                items.append({**base, "kind": "checkin", "reason": f"{int(quiet)} ngày chưa nói chuyện"})

        uid = str(c.get("weex_uid") or prof.get("weex_uid") or "")
        if uid and uid in vols and vols[uid][-1][0] == last_month:
            done = max(t.get("review") or "", t.get("skip_review") or "")
            r = review_numbers(vols[uid][-3:], ladder)
            if r and done < month_start:
                items.append({**base, "kind": "review", "review": r, "reason": review_summary(r)})
    order = {"birthday": 0, "greeting": 1, "review": 2, "checkin": 3}
    items.sort(key=lambda i: (order[i["kind"]], i["name"].lower()))
    return items


# ── Drafts ───────────────────────────────────────────────────────────────────

_TASK = {
    "greeting": ("Write a short personal {festival} greeting to this KOL. Warm and specific to them (use what you "
                 "know from the chat), 1-2 short sentences. NO business, no offer, no link, no question about volume."),
    "birthday": ("It is this KOL's birthday. Write a short, warm happy-birthday message, specific to them (use what "
                 "you know from the chat), 1-2 short sentences. NO business, no offer, no link, no question about volume."),
    "checkin": ("We have not talked to this KOL for a few weeks. Write a light check-in, 1-2 short sentences: ask how "
                "the market is treating them or how their community is doing, referring to something from the chat. "
                "No pitch, no pressure, no figures, no link."),
    "review": ("Write a short monthly review message sharing their WEEX numbers like a teammate: {summary}. Then ask "
               "ONE question about how to grow it together (e.g. new traders this month, an idea for a campaign). "
               "2-3 short sentences. Use only these numbers, rounded; say 'roughly'. No new offer."),
}


def draft_prompt(kind: str, name: str, history: list[dict], item: dict) -> str:
    convo = "\n".join(f"{'KOL' if m.get('role') == 'user' else 'US'}: {str(m.get('content') or '')[:300]}"
                      for m in (history or [])[-12:])
    task = _TASK[kind].format(festival=(item.get("festival") or {}).get("name", ""),
                              summary=review_summary(item["review"]) if item.get("review") else "")
    return (
        "You are a BD person at WEEX who personally looks after this KOL — a friend who walks with them through the "
        "market, not a salesperson. Write ONE Telegram message.\n"
        f"KOL name: {name or 'unknown'}\n"
        f"Recent chat:\n{convo or '(no history)'}\n\n"
        f"TASK: {task}\n"
        "Write in the SAME language the KOL writes in the chat. Sound like a real person typing on a phone: casual, "
        "no lists, no bracket tags, no signature, at most one emoji. Output ONLY the message text."
    )


async def generate_draft(account_id: int, user_id: int, kind: str) -> str:
    import ai_remix as ai_rmx
    import dm_reply_tracker as drt
    import outbound_guard
    if kind not in DUE_KINDS:
        raise ValueError("unknown draft kind")
    item = next((i for i in await due_items() if i["account_id"] == account_id
                 and i["user_id"] == user_id and i["kind"] == kind), None)
    chat = await db.get_followup_chat(account_id, user_id)
    if not chat:
        raise ValueError("chat not found")
    item = item or {"kind": kind}
    if kind == "review" and not item.get("review"):
        raise ValueError("no volume data for this KOL yet")
    agent = await drt._proactive_agent(account_id, chat)
    provider, keys, kwargs = await drt._resolve_ai_provider(agent)
    if not keys:
        raise ValueError("no AI provider configured")
    text = await ai_rmx.generate_response(draft_prompt(kind, chat.get("name") or "", chat.get("history") or [], item),
                                          provider, keys, **kwargs)
    text, _ = outbound_guard.clean_ai((text or "").strip().strip('"'))
    return ai_rmx.humanize_text(text or "")


# ── BD scorecard (slide 17: track weekly) ────────────────────────────────────

async def scorecard(today: date | None = None) -> dict:
    await ensure_tables()
    today = today or date.today()
    async with get_db() as conn:
        async def one(sql, params=()):
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
            return row[0] if row and row[0] is not None else 0

        replied_7d = await one("SELECT COUNT(*) FROM ai_followup_chats WHERE last_user_message_at >= datetime('now', '-7 days') "
                               "AND COALESCE(status, '') != 'bot_ignored'")
        replied_ab_7d = await one("SELECT COUNT(*) FROM ai_followup_chats WHERE last_user_message_at >= datetime('now', '-7 days') "
                                  "AND COALESCE(status, '') != 'bot_ignored' AND lead_tier IN ('Tier A', 'Tier B')")
        progressed_7d = await one("SELECT COUNT(DISTINCT account_id || ':' || user_id) FROM kol_step_events "
                                  "WHERE created_at >= datetime('now', '-7 days') AND to_step IN "
                                  "('deal_agreed', 'uid_received', 'link_confirmed', 'first_post', 'active')")
        active = await one("SELECT COUNT(*) FROM ai_followup_chats WHERE kol_step = 'active'")
        # Onboarded 90+ days ago (first link_confirmed) and still posting / active today.
        cohort = await one("""SELECT COUNT(*) FROM (SELECT account_id, user_id, MIN(created_at) AS t FROM kol_step_events
                              WHERE to_step = 'link_confirmed' GROUP BY 1, 2) WHERE t <= datetime('now', '-90 days')""")
        cohort_alive = await one("""SELECT COUNT(*) FROM (SELECT account_id, user_id, MIN(created_at) AS t FROM kol_step_events
                                    WHERE to_step = 'link_confirmed' GROUP BY 1, 2) e
                                    JOIN ai_followup_chats c ON c.account_id = e.account_id AND c.user_id = e.user_id
                                    WHERE e.t <= datetime('now', '-90 days') AND c.kol_step IN ('first_post', 'active')""")
        cur = await conn.execute("SELECT kind, COUNT(*) FROM kol_touchpoints WHERE created_at >= datetime('now', '-30 days') "
                                 "GROUP BY kind")
        touches = {k: n for k, n in await cur.fetchall()}
        referrals_30d = await one("SELECT COUNT(*) FROM referral_attributions WHERE attributed_at >= datetime('now', '-30 days')")
        cur = await conn.execute("SELECT COALESCE(NULLIF(archetype, ''), 'unknown'), COUNT(*) FROM kol_profiles GROUP BY 1")
        by_type = {k: n for k, n in await cur.fetchall()}
        m_prev2, m_prev = last_months(2, today)
        cur = await conn.execute("SELECT month, COUNT(*), SUM(volume) FROM kol_volume_monthly WHERE month IN (?, ?) "
                                 "AND volume > 0 GROUP BY month", (m_prev2, m_prev))
        vol = {m: {"kols": n, "volume": v} for m, n, v in await cur.fetchall()}
    per_kol = {m: (vol[m]["volume"] / vol[m]["kols"] if m in vol and vol[m]["kols"] else 0) for m in (m_prev2, m_prev)}
    return {
        "replies_7d": replied_7d, "replies_ab_7d": replied_ab_7d,
        "reply_quality_pct": round(replied_ab_7d / replied_7d * 100) if replied_7d else None,
        "progressed_7d": progressed_7d,
        "active_kols": active,
        "onboarded_90d": cohort, "still_active_90d": cohort_alive,
        "retention_90d_pct": round(cohort_alive / cohort * 100) if cohort else None,
        "volume": {"months": [m_prev2, m_prev], "by_month": vol, "per_kol": per_kol,
                   "per_kol_change_pct": (round((per_kol[m_prev] - per_kol[m_prev2]) / per_kol[m_prev2] * 100)
                                          if per_kol[m_prev2] else None)},
        "referrals_30d": referrals_30d,
        "meetings_30d": touches.get("meet", 0) + touches.get("event", 0),
        "calls_30d": touches.get("call", 0),
        "care_30d": {k: touches.get(k, 0) for k in DUE_KINDS},
        "by_archetype": by_type,
    }
