"""Story photo library + autopilot.

1. Sync: photos from one account's Saved Messages are downloaded into MEDIA_DIR.
2. Classify: a vision AI puts each photo in one fixed category and rejects anything
   risky for the accounts (weapons, cash, gambling, faces, documents...). A photo the
   AI could not read stays 'error' and is never posted.
3. Autopilot: once a day it plans `per_day` stories per chosen nick from the chosen
   categories: least-used photos first, no photo twice on the same day, a nick never
   gets a photo it already posted. Each post is a normal story_engine job with an AI
   caption, so posting, caps, FloodWait handling and cross-views stay in one place.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import time
from datetime import datetime

import database as db
import story_engine as se

logger = logging.getLogger("tg-scheduler.story-library")

CATEGORIES = {
    "du_lich": "🏞 Du lịch & phong cảnh",
    "am_thuc": "☕ Ẩm thực & cà phê",
    "cong_viec": "💼 Công việc & văn phòng",
    "su_kien": "🎤 Sự kiện & hội thảo",
    "crypto": "📈 Crypto & biểu đồ",
    "doi_song": "🌿 Đời sống thường ngày",
    "the_thao": "🏃 Thể thao & sức khoẻ",
    "khac": "🗂 Khác",
}
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_PER_DAY = se.DAILY_POSTS_FREE

_CLASSIFY_PROMPT = (
    "You review a photo that will be posted as a Telegram Story by several marketing accounts.\n"
    "1) Pick exactly one category key: " + ", ".join(CATEGORIES) + ".\n"
    "2) Decide if it is SAFE to post. It is NOT safe if it shows any of: weapons or ammunition; "
    "cash, stacks of money or bank transfers; gambling, casino tables, chips or cards; drugs, vapes or "
    "alcohol as the main subject; nudity or sexual content; violence, blood or injury; children as the "
    "subject; a clearly recognisable human face; ID cards, passports, bank cards, QR codes, phone or "
    "account numbers; screenshots of chats or personal data; luxury goods or cars shown off as wealth; "
    "politics or religion; a watermark or logo of another channel or brand; anything illegal or shocking.\n"
    "3) Describe the photo in one short Vietnamese sentence.\n"
    'Return ONLY JSON: {"category": "<key>", "safe": true|false, '
    '"reason": "<if not safe: short Vietnamese reason, else empty>", "desc": "<Vietnamese sentence>"}'
)

_state = {"state": "idle", "done": 0, "total": 0, "error": None}
_task: asyncio.Task | None = None


# ── Schema ────────────────────────────────────────────────────────────────────

async def ensure_schema():
    async with db.get_db() as conn:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS story_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_account_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                file TEXT NOT NULL,                 -- basename inside MEDIA_DIR
                status TEXT DEFAULT 'pending',      -- pending | ok | rejected | error
                category TEXT,
                description TEXT,
                reason TEXT,
                used_count INTEGER DEFAULT 0,
                last_used_at REAL,
                created_at REAL,
                UNIQUE(source_account_id, msg_id)
            );
            CREATE INDEX IF NOT EXISTS idx_story_media_status ON story_media(status, category);
            CREATE TABLE IF NOT EXISTS story_autopilot (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 0,
                categories TEXT DEFAULT '[]',
                account_ids TEXT DEFAULT '[]',
                per_day INTEGER DEFAULT 1,
                start_hour INTEGER DEFAULT 8,
                end_hour INTEGER DEFAULT 22,
                lang TEXT DEFAULT 'vi',
                cross_view INTEGER DEFAULT 1,
                last_planned TEXT
            );
            INSERT OR IGNORE INTO story_autopilot (id) VALUES (1);
        """)
        await conn.commit()


async def _rows(sql: str, params=()) -> list[dict]:
    async with db.get_db() as conn:
        cur = await conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in await cur.fetchall()]


async def _exec(sql: str, params=()):
    async with db.get_db() as conn:
        await conn.execute(sql, params)
        await conn.commit()


# ── Sync + classify ───────────────────────────────────────────────────────────

def status() -> dict:
    return dict(_state)


def start_sync(account_id: int, limit: int = 300) -> bool:
    """Start importing + classifying in the background. False if one is already running."""
    global _task
    if _task and not _task.done():
        return False
    _task = asyncio.create_task(_sync(account_id, limit), name="story-library-sync")
    return True


def start_classify() -> bool:
    global _task
    if _task and not _task.done():
        return False
    _task = asyncio.create_task(_run(classify_pending()), name="story-library-classify")
    return True


async def _run(coro):
    try:
        await coro
    except Exception as e:
        logger.exception("[StoryLib] failed")
        _state["error"] = str(e)
    finally:
        _state["state"] = "idle"


async def _sync(account_id: int, limit: int):
    _state.update(state="importing", done=0, total=0, error=None)
    try:
        await import_saved(account_id, limit)
        await classify_pending()
    except Exception as e:
        logger.exception("[StoryLib] sync failed")
        _state["error"] = str(e)
    finally:
        _state["state"] = "idle"


async def import_saved(account_id: int, limit: int = 300) -> int:
    """Download new photos from the account's Saved Messages. Returns how many were added."""
    import telegram_client as tg
    from telethon.tl.types import InputMessagesFilterPhotos
    await ensure_schema()
    client = tg.get_client(int(account_id))
    if not client:
        raise RuntimeError("Nick chưa kết nối Telegram")
    known = {r["msg_id"] for r in await _rows(
        "SELECT msg_id FROM story_media WHERE source_account_id = ?", (int(account_id),))}
    os.makedirs(se.MEDIA_DIR, exist_ok=True)
    added = 0
    async for msg in client.iter_messages("me", limit=max(1, min(int(limit), 1000)),
                                          filter=InputMessagesFilterPhotos):
        if msg.id in known:
            continue
        name = f"saved_{int(account_id)}_{msg.id}.jpg"
        await client.download_media(msg, file=os.path.join(se.MEDIA_DIR, name))
        await _exec("INSERT OR IGNORE INTO story_media (source_account_id, msg_id, file, created_at) "
                    "VALUES (?, ?, ?, ?)", (int(account_id), msg.id, name, time.time()))
        added += 1
        _state["total"] = added
    logger.info("[StoryLib] imported %d photo(s) from Saved Messages of nick %s", added, account_id)
    return added


def parse_verdict(raw: str) -> dict | None:
    """AI answer → {status, category, reason, description}; None if unreadable."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("safe"), bool):
        return None
    cat = d.get("category") if d.get("category") in CATEGORIES else "khac"
    return {"status": "ok" if d["safe"] else "rejected", "category": cat,
            "reason": "" if d["safe"] else str(d.get("reason") or "Có yếu tố rủi ro")[:200],
            "description": str(d.get("desc") or "")[:300]}


async def _vision(prompt: str, data_url: str) -> str:
    """Ask the first configured provider that can actually see images (others would guess)."""
    import ai_remix
    first = await db.get_setting("ai_provider", "gemini") or "gemini"
    order = [first] + [p for p in await ai_remix.get_fallback_order() if p != first]
    last: Exception | None = None
    for prov in order:
        if prov not in ai_remix._VISION_PROVIDERS:
            continue
        keys, kw = await ai_remix.get_provider_config(prov)
        for key in keys:
            try:
                res = await ai_remix._call_chat_provider(
                    prov, key, [{"role": "user", "content": prompt}], images=[data_url], **kw)
                if res:
                    return res
            except Exception as e:
                last = e
    if last:
        raise RuntimeError(f"AI đọc ảnh lỗi: {last}")
    raise RuntimeError("Cần AI đọc được ảnh (OpenAI hoặc OpenAI-compatible) — Cài đặt → AI")


async def classify_one(row: dict) -> dict:
    path = os.path.join(se.MEDIA_DIR, os.path.basename(row["file"]))
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_IMAGE_BYTES:
        verdict = {"status": "rejected", "category": None, "reason": "Không đọc được file ảnh", "description": ""}
    else:
        with open(path, "rb") as f:
            url = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
        verdict = parse_verdict(await _vision(_CLASSIFY_PROMPT, url)) or {
            "status": "error", "category": None, "reason": "AI trả lời không đúng định dạng", "description": ""}
    await _exec("UPDATE story_media SET status = ?, category = ?, reason = ?, description = ? WHERE id = ?",
                (verdict["status"], verdict["category"], verdict["reason"], verdict["description"], row["id"]))
    return verdict


async def classify_pending():
    await ensure_schema()
    rows = await _rows("SELECT * FROM story_media WHERE status IN ('pending', 'error') ORDER BY id")
    _state.update(state="classifying", done=0, total=len(rows), error=None)
    for row in rows:
        await classify_one(row)  # a provider failure stops the run (raises) instead of rejecting everything
        _state["done"] += 1
        await asyncio.sleep(0.5)


# ── Library listing ───────────────────────────────────────────────────────────

async def library() -> dict:
    await ensure_schema()
    items = await _rows("SELECT id, file, status, category, description, reason, used_count "
                        "FROM story_media ORDER BY id DESC")
    counts = {k: 0 for k in CATEGORIES}
    for it in items:
        if it["status"] == "ok":
            counts[it["category"] or "khac"] += 1
    return {"items": items, "categories": CATEGORIES, "counts": counts, "job": status()}


def media_path(media_id_file: str) -> str | None:
    path = os.path.join(se.MEDIA_DIR, os.path.basename(media_id_file or ""))
    return path if os.path.isfile(path) else None


# ── Autopilot ─────────────────────────────────────────────────────────────────

async def get_autopilot() -> dict:
    await ensure_schema()
    cfg = (await _rows("SELECT * FROM story_autopilot WHERE id = 1"))[0]
    cfg["categories"] = json.loads(cfg["categories"] or "[]")
    cfg["account_ids"] = json.loads(cfg["account_ids"] or "[]")
    return cfg


async def save_autopilot(*, enabled: bool, categories: list[str], account_ids: list[int], per_day: int,
                         start_hour: int, end_hour: int, lang: str = "vi", cross_view: bool = True):
    cats = [c for c in categories if c in CATEGORIES]
    if enabled and not cats:
        raise ValueError("Chọn ít nhất 1 danh mục ảnh")
    if enabled and not account_ids:
        raise ValueError("Chọn ít nhất 1 nick")
    if not 0 <= start_hour < end_hour <= 24:
        raise ValueError("Khung giờ không hợp lệ")
    await ensure_schema()
    await _exec("UPDATE story_autopilot SET enabled=?, categories=?, account_ids=?, per_day=?, start_hour=?, "
                "end_hour=?, lang=?, cross_view=? WHERE id = 1",
                (int(enabled), json.dumps(cats), json.dumps([int(a) for a in account_ids]),
                 max(1, min(int(per_day), MAX_PER_DAY)), int(start_hour), int(end_hour), lang or "vi",
                 int(cross_view)))


AUTO_PREFIX = "🤖 Tự động"


async def cancel_pending_auto() -> int:
    """Autopilot switched off → its stories that are not posted yet are cancelled."""
    jobs = await _rows("SELECT id FROM story_jobs WHERE name LIKE ? AND status = 'scheduled'", (AUTO_PREFIX + "%",))
    await _exec("UPDATE story_autopilot SET last_planned = NULL WHERE id = 1")  # re-enable plans again today
    return sum([await se.cancel_job(j["id"]) for j in jobs])


def pick_media(pool: list[dict], nick_used: set[str], taken: set[str], n: int,
               rng: random.Random | None = None) -> list[dict]:
    """Photos for one nick today: never one taken by another nick today or already
    posted by this nick; least-used first, random among equals."""
    rng = rng or random
    fresh = [m for m in pool if m["file"] not in taken and m["file"] not in nick_used]
    fresh.sort(key=lambda m: (m["used_count"] or 0, rng.random()))
    return fresh[:n]


def plan_slots(now: float, start_hour: int, end_hour: int, n: int,
               rng: random.Random | None = None) -> list[float]:
    """n random times today inside [max(now+5min, start), end), one per equal slice."""
    rng = rng or random
    day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    lo, hi = max(now + 300, day + start_hour * 3600), day + end_hour * 3600
    if hi - lo < 600 or n < 1:
        return []
    step = (hi - lo) / n
    return [lo + i * step + rng.uniform(0, step * 0.8) for i in range(n)]


async def _caption(desc: str, lang: str) -> str:
    import ai_remix
    prov, keys, kw = await se._ai_provider()
    if not prov or not desc:
        return ""
    language = se._CAPTION_LANGS.get(lang, "Vietnamese")
    prompt = (f"Write 1 Telegram Story caption in {language} for a photo showing: {desc}\n"
              "It must sound like the account owner casually sharing their day: 1 short line, at most 120 "
              "characters, at most 1 emoji, no hashtag, no selling, no quotes, no explanations.\n"
              "Return ONLY a JSON array with 1 string.")
    try:
        caps = se._parse_captions(await ai_remix.generate_response(prompt, prov, keys, **kw) or "", 1)
    except Exception as e:
        logger.debug("[StoryLib] caption failed: %s", e)
        return ""
    return caps[0] if caps else ""


async def plan_today(now: float | None = None) -> int:
    """Create today's autopilot story jobs once. Returns how many posts were planned."""
    now = now or time.time()
    cfg = await get_autopilot()
    today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    if not cfg["enabled"] or cfg["last_planned"] == today:
        return 0
    slots_n = max(1, min(int(cfg["per_day"]), MAX_PER_DAY))
    if not plan_slots(now, cfg["start_hour"], cfg["end_hour"], 1):
        return 0  # today's window is over; try again tomorrow
    marks = ",".join("?" * len(cfg["categories"])) or "''"
    pool = await _rows(f"SELECT id, file, category, description, used_count FROM story_media "
                       f"WHERE status = 'ok' AND category IN ({marks})", cfg["categories"])
    taken: set[str] = set()
    planned = 0
    for aid in cfg["account_ids"]:
        used = {r["media_file"] for r in await _rows(
            "SELECT j.media_file FROM story_posts p JOIN story_jobs j ON j.id = p.job_id "
            "WHERE p.account_id = ? AND p.status IN ('pending', 'posted')", (int(aid),))}
        times = plan_slots(now, cfg["start_hour"], cfg["end_hour"], slots_n)
        for media, ts in zip(pick_media(pool, used, taken, len(times)), times):
            taken.add(media["file"])
            cap = await _caption(media["description"] or "", cfg["lang"])
            await se.create_job(name=f"{AUTO_PREFIX} · {CATEGORIES.get(media['category'], '')}",
                                account_ids=[int(aid)], media_file=media["file"], caption=cap,
                                scheduled_at=ts, stagger_min=0, cross_view=bool(cfg["cross_view"]),
                                viewer_scope="all")
            await _exec("UPDATE story_media SET used_count = used_count + 1, last_used_at = ? WHERE id = ?",
                        (now, media["id"]))
            planned += 1
    await _exec("UPDATE story_autopilot SET last_planned = ? WHERE id = 1", (today,))
    logger.info("[StoryLib] autopilot planned %d story post(s) for %s", planned, today)
    return planned


async def autopilot_tick():
    try:
        await plan_today()
    except Exception:
        logger.exception("[StoryLib] autopilot planning failed")


def register_job(scheduler) -> None:
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler.add_job(autopilot_tick, trigger=IntervalTrigger(minutes=30), id="story_autopilot",
                      name="Story autopilot planner", replace_existing=True, max_instances=1, coalesce=True)
