"""Story scheduler (v3.39): bulk-post Telegram Stories from many nicks, then let our
own internal nicks view each other's stories.

Flow
----
1. A job = media + caption + nicks + start time + stagger. Creating it writes one
   `story_posts` row per nick (post_at = start + i * stagger + small jitter).
2. One light APScheduler job (`story_tick`, every 60s) picks due posts and due
   views with two indexed queries; nothing else runs while there is no work.
3. After a nick posts, every other internal nick (the job's nicks, or all healthy
   logged-in nicks) gets a `story_views` row at a random delay (view_delay_min..max
   minutes). Views only read the story: no message is ever sent.

Safety
------
- At most POSTS_PER_TICK posts and VIEWS_PER_TICK views per minute, with a random
  pause between actions, and a viewer never views twice within VIEWER_MIN_GAP.
- Daily caps per nick (stories posted, stories viewed).
- FloodWait on any nick -> that nick is paused (db.pause_account) and its pending
  posts/views are skipped. Nothing is retried automatically.
- Paused / flagged / PeerFlood / logged-out nicks are skipped, never forced.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time

import database as db
from database import get_db

logger = logging.getLogger("tg-scheduler.stories")

MEDIA_DIR = os.getenv("MEDIA_DIR", os.path.join(os.path.dirname(__file__), "media"))

TICK_SECONDS = 60
POSTS_PER_TICK = 3
VIEWS_PER_TICK = 6
VIEWER_MIN_GAP = 45            # seconds between two views by the same nick
LATE_POST_GRACE = 2 * 3600     # a post more than 2h overdue (server was off) is skipped
STORY_LIFETIME = 24 * 3600     # views after the story expired are skipped
DAILY_POSTS_FREE = 3           # Telegram's own limit for non-Premium is low; stay under it
DAILY_POSTS_PREMIUM = 20
DAILY_VIEWS_PER_NICK = 60

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}

_tick_lock = asyncio.Lock()
_last_view_at: dict[int, float] = {}                   # viewer account id -> last view time
_peer_cache: dict[tuple[int, int], object] = {}        # (viewer, poster) -> InputPeer


# ── Pure helpers (unit tested) ────────────────────────────────────────────────

def media_type_for(filename: str) -> str | None:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return None


def plan_post_times(start_ts: float, account_ids: list[int], stagger_min: int,
                    rng: random.Random | None = None) -> list[tuple[int, float]]:
    """Nick i posts at start + i*stagger (+ up to 20% of the stagger as jitter).

    The first nick posts exactly at the start time; order follows the selection.
    """
    rng = rng or random
    stagger = max(0, int(stagger_min or 0)) * 60
    out = []
    for i, aid in enumerate(account_ids):
        jitter = rng.uniform(0, stagger * 0.2) if (i and stagger) else 0.0
        out.append((int(aid), float(start_ts) + i * stagger + jitter))
    return out


def plan_view_times(posted_ts: float, viewer_ids: list[int], poster_id: int,
                    delay_min: int, delay_max: int,
                    rng: random.Random | None = None) -> list[tuple[int, float]]:
    """Each other nick views once, at a random minute in [delay_min, delay_max]."""
    rng = rng or random
    lo = max(1, int(delay_min or 1))
    hi = max(lo, int(delay_max or lo))
    out = []
    for v in viewer_ids:
        if int(v) == int(poster_id):
            continue
        out.append((int(v), float(posted_ts) + rng.uniform(lo * 60, hi * 60)))
    return out


def unsafe_reason(acc: dict | None, now: float | None = None) -> str | None:
    """Why this nick must not post/view right now (None = OK)."""
    if not acc:
        return "không tìm thấy nick"
    if not acc.get("is_logged_in"):
        return "chưa đăng nhập"
    if acc.get("is_paused"):
        return "đang tạm dừng"
    if acc.get("is_flagged"):
        return "bị flag"
    try:
        if float(acc.get("peerflood_until") or 0) > (now or time.time()):
            return "đang PeerFlood"
    except (TypeError, ValueError):
        pass
    return None


# ── AI caption ("1 click to post") ───────────────────────────────────────────

CAPTION_MAX = 200          # non-Premium story caption limit, safe for every nick
_CAPTION_LANGS = {"vi": "Vietnamese", "en": "English", "id": "Indonesian", "ru": "Russian",
                  "zh": "Simplified Chinese", "es": "Spanish", "pt": "Portuguese"}


def _caption_prompt(idea: str, lang: str, n: int) -> str:
    language = _CAPTION_LANGS.get(lang, "Vietnamese")
    base = (f"Rewrite this caption idea into fresh wording, keep its meaning and any link or @handle exactly: {idea}"
            if idea.strip() else "Write a casual, positive everyday caption that fits any photo or short video.")
    return (
        f"You write Telegram Story captions. {base}\n"
        f"Write {n} different caption(s) in {language}. Each one: 1 or 2 short lines, at most 150 characters, "
        "sounds like a real person, at most 2 emoji, at most 1 hashtag, no quotes around it, "
        "no numbering, no square brackets, no explanations.\n"
        f"Return ONLY a JSON array of {n} strings."
    )


def _clean_caption(text: str) -> str:
    from outbound_guard import clean_ai
    t, _ = clean_ai(str(text or ""))
    t = re.sub(r"\[[^\]\n]{0,80}\]", "", t)              # any leftover [TAG]
    t = t.strip().strip("\"'“”‘’`").strip()
    t = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", t)         # stray numbering
    if len(t) > CAPTION_MAX:
        cut = t[:CAPTION_MAX]
        t = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,.;:-")
    return t.strip()


def _parse_captions(raw: str, n: int) -> list[str]:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    items = None
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            items = json.loads(m.group(0))
        except Exception:
            items = None
    if not isinstance(items, list):
        items = [ln for ln in raw.splitlines() if ln.strip()]
    out = []
    for it in items:
        c = _clean_caption(it if isinstance(it, str) else str(it))
        if c and c not in out:
            out.append(c)
    return out[:n]


async def _ai_provider() -> tuple[str, list[str], dict]:
    import ai_remix
    first = await db.get_setting("ai_provider", "gemini") or "gemini"
    order = [first] + [p for p in await ai_remix.get_fallback_order() if p != first]
    for prov in order:
        keys, kw = await ai_remix.get_provider_config(prov)
        if keys:
            return prov, keys, kw
    return "", [], {}


async def ai_captions(idea: str = "", lang: str = "vi", n: int = 1) -> list[str]:
    """``n`` short story captions from the configured AI provider. Raises RuntimeError
    when no provider is set up or the AI returned nothing usable. Fills up to ``n``
    by asking again once, then repeats the list (never returns fewer than 1)."""
    import ai_remix
    n = max(1, min(int(n), 50))
    prov, keys, kw = await _ai_provider()
    if not prov:
        raise RuntimeError("Chưa cấu hình AI (Cài đặt → AI)")
    out: list[str] = []
    for _ in range(2):
        need = n - len(out)
        raw = await ai_remix.generate_response(_caption_prompt(idea, lang, need), prov, keys, **kw)
        for c in _parse_captions(raw or "", need):
            if c not in out:
                out.append(c)
        if len(out) >= n:
            break
    if not out:
        raise RuntimeError("AI không trả về caption, thử lại sau")
    while len(out) < n:
        out.append(out[len(out) % max(1, len(out))])
    return out[:n]


# ── Job CRUD ─────────────────────────────────────────────────────────────────

async def create_job(*, name: str, account_ids: list[int], media_file: str, caption: str,
                     scheduled_at: float, stagger_min: int = 5, privacy: str = "all",
                     cross_view: bool = True, viewer_scope: str = "job",
                     view_delay_min: int = 3, view_delay_max: int = 45,
                     captions: dict[int, str] | None = None) -> int:
    """``captions`` = optional per-nick caption (AI variants); missing nicks use ``caption``."""
    mtype = media_type_for(media_file)
    if not mtype:
        raise ValueError("File phải là ảnh (jpg/png/webp) hoặc video (mp4/mov/webm/mkv)")
    ids = list(dict.fromkeys(int(a) for a in account_ids))
    if not ids:
        raise ValueError("Chọn ít nhất 1 nick")
    async with get_db() as conn:
        cur = await conn.execute(
            """INSERT INTO story_jobs (name, account_ids, media_file, media_type, caption, scheduled_at,
                   stagger_min, privacy, cross_view, viewer_scope, view_delay_min, view_delay_max)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name or "", json.dumps(ids), os.path.basename(media_file), mtype, caption or "",
             float(scheduled_at), int(stagger_min), privacy if privacy in ("all", "contacts") else "all",
             1 if cross_view else 0, viewer_scope if viewer_scope in ("job", "all") else "job",
             int(view_delay_min), int(view_delay_max)),
        )
        job_id = cur.lastrowid
        for aid, ts in plan_post_times(scheduled_at, ids, stagger_min):
            await conn.execute(
                "INSERT INTO story_posts (job_id, account_id, post_at, caption) VALUES (?,?,?,?)",
                (job_id, aid, ts, (captions or {}).get(aid)),
            )
        await conn.commit()
    return job_id


async def list_jobs(limit: int = 50) -> list[dict]:
    async with get_db() as conn:
        conn.row_factory = __import__("aiosqlite").Row
        jobs = [dict(r) for r in await (await conn.execute(
            "SELECT * FROM story_jobs ORDER BY id DESC LIMIT ?", (int(limit),))).fetchall()]
        if not jobs:
            return []
        ids = [j["id"] for j in jobs]
        q = ",".join("?" * len(ids))
        posts = await (await conn.execute(
            f"SELECT job_id, status, COUNT(*) FROM story_posts WHERE job_id IN ({q}) GROUP BY job_id, status",
            ids)).fetchall()
        views = await (await conn.execute(
            f"SELECT job_id, status, COUNT(*) FROM story_views WHERE job_id IN ({q}) GROUP BY job_id, status",
            ids)).fetchall()
    pc: dict[int, dict] = {}
    vc: dict[int, dict] = {}
    for jid, st, n in posts:
        pc.setdefault(jid, {})[st] = n
    for jid, st, n in views:
        vc.setdefault(jid, {})[st] = n
    for j in jobs:
        j["account_ids"] = json.loads(j.get("account_ids") or "[]")
        j["posts"] = pc.get(j["id"], {})
        j["views"] = vc.get(j["id"], {})
    return jobs


async def job_detail(job_id: int) -> dict | None:
    async with get_db() as conn:
        conn.row_factory = __import__("aiosqlite").Row
        job = await (await conn.execute("SELECT * FROM story_jobs WHERE id=?", (job_id,))).fetchone()
        if not job:
            return None
        posts = [dict(r) for r in await (await conn.execute(
            """SELECT p.*, a.name AS account_name,
                      (SELECT COUNT(*) FROM story_views v WHERE v.post_id=p.id AND v.status='done') AS views_done,
                      (SELECT COUNT(*) FROM story_views v WHERE v.post_id=p.id AND v.status='pending') AS views_pending
               FROM story_posts p LEFT JOIN accounts a ON a.id=p.account_id
               WHERE p.job_id=? ORDER BY p.post_at""", (job_id,))).fetchall()]
    out = dict(job)
    out["account_ids"] = json.loads(out.get("account_ids") or "[]")
    out["post_rows"] = posts
    return out


async def cancel_job(job_id: int) -> bool:
    async with get_db() as conn:
        cur = await conn.execute(
            "UPDATE story_jobs SET status='cancelled' WHERE id=? AND status IN ('scheduled','running')", (job_id,))
        await conn.execute(
            "UPDATE story_posts SET status='cancelled' WHERE job_id=? AND status='pending'", (job_id,))
        await conn.execute(
            "UPDATE story_views SET status='cancelled' WHERE job_id=? AND status='pending'", (job_id,))
        await conn.commit()
        return cur.rowcount > 0


async def delete_job(job_id: int) -> bool:
    async with get_db() as conn:
        await conn.execute("DELETE FROM story_views WHERE job_id=?", (job_id,))
        await conn.execute("DELETE FROM story_posts WHERE job_id=?", (job_id,))
        cur = await conn.execute("DELETE FROM story_jobs WHERE id=?", (job_id,))
        await conn.commit()
        return cur.rowcount > 0


# ── Internals ────────────────────────────────────────────────────────────────

async def _accounts_by_id() -> dict[int, dict]:
    return {int(a["id"]): a for a in await db.get_all_accounts()}


async def _count_today(sql: str, account_id: int) -> int:
    since = time.time() - 86400
    async with get_db() as conn:
        row = await (await conn.execute(sql, (account_id, since))).fetchone()
    return int(row[0] or 0)


async def _set_post(post_id: int, status: str, error: str | None = None,
                    story_id: int | None = None, posted_at: float | None = None):
    async with get_db() as conn:
        await conn.execute(
            "UPDATE story_posts SET status=?, error=?, story_id=COALESCE(?, story_id), posted_at=COALESCE(?, posted_at) WHERE id=?",
            (status, (error or None) and str(error)[:300], story_id, posted_at, post_id))
        await conn.commit()


async def _set_view(view_id: int, status: str, error: str | None = None):
    async with get_db() as conn:
        await conn.execute(
            "UPDATE story_views SET status=?, error=?, done_at=? WHERE id=?",
            (status, (error or None) and str(error)[:300], time.time() if status == "done" else None, view_id))
        await conn.commit()


async def _stop_nick(account_id: int, reason: str):
    """FloodWait: pause the nick for good (manual resume only) and drop its pending work."""
    try:
        await db.pause_account(account_id, reason)
    except Exception as e:
        logger.warning("[Story] pause nick %s failed: %s", account_id, e)
    async with get_db() as conn:
        await conn.execute(
            "UPDATE story_posts SET status='skipped', error=? WHERE account_id=? AND status='pending'",
            ("nick đã tạm dừng vì FloodWait", account_id))
        await conn.execute(
            "UPDATE story_views SET status='skipped', error=? WHERE viewer_account_id=? AND status='pending'",
            ("nick đã tạm dừng vì FloodWait", account_id))
        await conn.commit()
    logger.warning("[Story] nick %s PAUSED: %s", account_id, reason)
    try:
        import alerts
        await alerts.send_alert(f"story_floodwait_{account_id}",
                                f"📸 Story: nick #{account_id} bị {reason} → đã tạm dừng, không tự chạy lại.")
    except Exception:
        pass


def _flood_seconds(exc: Exception) -> int | None:
    """FloodWait / slow-mode style errors -> seconds, else None."""
    secs = getattr(exc, "seconds", None)
    name = type(exc).__name__
    if secs is not None and ("Flood" in name or "Wait" in name):
        return int(secs)
    if "FLOOD" in str(exc).upper():
        return int(secs or 0)
    return None


def _video_attributes(path: str):
    from telethon.tl.types import DocumentAttributeVideo
    duration, w, h = 0.0, 720, 1280
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height:format=duration", "-of", "json", path],
                capture_output=True, text=True, timeout=20).stdout
            data = json.loads(out or "{}")
            st = (data.get("streams") or [{}])[0]
            w = int(st.get("width") or w)
            h = int(st.get("height") or h)
            duration = float((data.get("format") or {}).get("duration") or 0)
        except Exception as e:
            logger.debug("[Story] ffprobe failed: %s", e)
    return [DocumentAttributeVideo(duration=duration, w=w, h=h, supports_streaming=True)]


async def _send_story(client, path: str, media_type: str, caption: str, privacy: str) -> int:
    """Upload + stories.SendStory as the client's own user. Returns the story id."""
    from telethon.tl.functions.stories import SendStoryRequest
    from telethon.tl.types import (InputMediaUploadedDocument, InputMediaUploadedPhoto,
                                   InputPeerSelf, InputPrivacyValueAllowAll,
                                   InputPrivacyValueAllowContacts)
    uploaded = await client.upload_file(path)
    if media_type == "video":
        mime = "video/mp4" if path.lower().endswith(".mp4") else "video/quicktime" if path.lower().endswith(".mov") else "video/webm" if path.lower().endswith(".webm") else "video/x-matroska"
        media = InputMediaUploadedDocument(file=uploaded, mime_type=mime,
                                           attributes=await asyncio.to_thread(_video_attributes, path))
    else:
        media = InputMediaUploadedPhoto(file=uploaded)
    rules = [InputPrivacyValueAllowContacts()] if privacy == "contacts" else [InputPrivacyValueAllowAll()]
    res = await client(SendStoryRequest(peer=InputPeerSelf(), media=media, privacy_rules=rules,
                                        caption=caption or None, random_id=random.getrandbits(63)))
    # Updates -> UpdateStory(story=StoryItem(id=...)) or UpdateStoryID(id=...)
    for upd in getattr(res, "updates", []) or []:
        sid = getattr(getattr(upd, "story", None), "id", None) or (
            getattr(upd, "id", None) if type(upd).__name__ == "UpdateStoryID" else None)
        if sid:
            return int(sid)
    return 0


async def _resolve_poster(viewer_client, viewer_id: int, poster_id: int, poster_acc: dict):
    key = (viewer_id, poster_id)
    if key in _peer_cache:
        return _peer_cache[key]
    import telegram_client as tg
    me = None
    try:
        me = await tg.get_me(poster_id)
    except Exception:
        pass
    uid = (me or {}).get("user_id") or poster_acc.get("telegram_user_id")
    uname = (me or {}).get("username") or ""
    peer = None
    if uid:
        try:
            peer = await viewer_client.get_input_entity(int(uid))   # session cache: no API call
        except Exception:
            peer = None
    if peer is None and uname:
        peer = await viewer_client.get_input_entity(uname)          # one ResolveUsername, cached below
    if peer is None:
        raise LookupError("không tìm được nick đăng (nick đăng chưa có username)")
    _peer_cache[key] = peer
    return peer


async def _view_story(viewer_client, peer, story_id: int):
    from telethon.tl.functions.stories import IncrementStoryViewsRequest, ReadStoriesRequest
    await viewer_client(ReadStoriesRequest(peer=peer, max_id=int(story_id)))
    try:
        await viewer_client(IncrementStoryViewsRequest(peer=peer, id=[int(story_id)]))
    except Exception as e:
        if _flood_seconds(e) is not None:
            raise
        logger.debug("[Story] IncrementStoryViews ignored: %s", e)


async def _viewer_pool(job: dict, accounts: dict[int, dict]) -> list[int]:
    """Healthy viewers only: a nick paused/flagged now never gets view rows queued."""
    if job.get("viewer_scope") == "all":
        pool = list(accounts)
    else:
        pool = [int(a) for a in json.loads(job.get("account_ids") or "[]")]
    return [aid for aid in pool if not unsafe_reason(accounts.get(aid))]


async def _refresh_job_status(job_id: int):
    async with get_db() as conn:
        job = await (await conn.execute("SELECT status FROM story_jobs WHERE id=?", (job_id,))).fetchone()
        if not job or job[0] == "cancelled":
            return
        p = await (await conn.execute(
            "SELECT COUNT(*) FROM story_posts WHERE job_id=? AND status='pending'", (job_id,))).fetchone()
        v = await (await conn.execute(
            "SELECT COUNT(*) FROM story_views WHERE job_id=? AND status='pending'", (job_id,))).fetchone()
        status = "running" if (p[0] or v[0]) else "done"
        await conn.execute("UPDATE story_jobs SET status=? WHERE id=?", (status, job_id))
        await conn.commit()


# ── Post / view one item ─────────────────────────────────────────────────────

async def _do_post(row: dict, accounts: dict[int, dict]):
    import telegram_client as tg
    now = time.time()
    aid = int(row["account_id"])
    if now - float(row["post_at"]) > LATE_POST_GRACE:
        return await _set_post(row["id"], "skipped", "quá giờ đăng hơn 2 tiếng (server tắt?)")
    acc = accounts.get(aid)
    why = unsafe_reason(acc, now) or (getattr(tg, "_session_errors", {}).get(aid) and "lỗi session")
    if why:
        return await _set_post(row["id"], "skipped", why)
    cap = DAILY_POSTS_PREMIUM if acc.get("is_premium") else DAILY_POSTS_FREE
    if await _count_today("SELECT COUNT(*) FROM story_posts WHERE account_id=? AND status='posted' AND posted_at>?", aid) >= cap:
        return await _set_post(row["id"], "skipped", f"đã đăng {cap} story trong 24h")
    client = tg.get_client(aid)
    if not client:
        return await _set_post(row["id"], "skipped", "nick chưa kết nối")
    path = os.path.join(MEDIA_DIR, os.path.basename(row["media_file"]))
    if not os.path.isfile(path):
        return await _set_post(row["id"], "failed", "không thấy file media")
    try:
        story_id = await _send_story(client, path, row["media_type"], row["caption"], row["privacy"])
    except Exception as e:
        secs = _flood_seconds(e)
        if secs is not None:
            await _set_post(row["id"], "failed", f"FloodWait {secs}s")
            return await _stop_nick(aid, f"FloodWait {secs}s khi đăng story")
        return await _set_post(row["id"], "failed", f"{type(e).__name__}: {e}")
    posted = time.time()
    await _set_post(row["id"], "posted", None, story_id=story_id, posted_at=posted)
    logger.info("[Story] nick %s posted story %s (job %s)", aid, story_id, row["job_id"])
    if row["cross_view"] and story_id:
        viewers = await _viewer_pool(row, accounts)
        plan = plan_view_times(posted, viewers, aid, row["view_delay_min"], row["view_delay_max"])
        async with get_db() as conn:
            for vid, ts in plan:
                await conn.execute(
                    "INSERT OR IGNORE INTO story_views (post_id, job_id, viewer_account_id, view_at) VALUES (?,?,?,?)",
                    (row["id"], row["job_id"], vid, ts))
            await conn.commit()


async def _do_view(row: dict, accounts: dict[int, dict]) -> bool:
    """Returns False when the view was deferred (viewer needs a pause)."""
    import telegram_client as tg
    now = time.time()
    vid = int(row["viewer_account_id"])
    if now - _last_view_at.get(vid, 0) < VIEWER_MIN_GAP:
        return False
    if now - float(row["posted_at"] or 0) > STORY_LIFETIME - 300:
        await _set_view(row["id"], "skipped", "story đã hết hạn")
        return True
    why = unsafe_reason(accounts.get(vid), now) or unsafe_reason(accounts.get(int(row["account_id"])), now)
    if why:
        await _set_view(row["id"], "skipped", why)
        return True
    if await _count_today("SELECT COUNT(*) FROM story_views WHERE viewer_account_id=? AND status='done' AND done_at>?", vid) >= DAILY_VIEWS_PER_NICK:
        await _set_view(row["id"], "skipped", f"nick đã xem {DAILY_VIEWS_PER_NICK} story trong 24h")
        return True
    client = tg.get_client(vid)
    if not client:
        await _set_view(row["id"], "skipped", "nick chưa kết nối")
        return True
    _last_view_at[vid] = now
    try:
        peer = await _resolve_poster(client, vid, int(row["account_id"]), accounts.get(int(row["account_id"])) or {})
        await _view_story(client, peer, int(row["story_id"]))
    except Exception as e:
        secs = _flood_seconds(e)
        if secs is not None:
            await _set_view(row["id"], "failed", f"FloodWait {secs}s")
            await _stop_nick(vid, f"FloodWait {secs}s khi xem story")
            return True
        await _set_view(row["id"], "failed", f"{type(e).__name__}: {e}")
        return True
    await _set_view(row["id"], "done")
    return True


# ── Tick (APScheduler job) ───────────────────────────────────────────────────

async def _pause(lo: float, hi: float):
    """Random human-like gap between two Telegram actions (patched out in tests)."""
    await asyncio.sleep(random.uniform(lo, hi))


async def tick():
    """Run due posts and views. Two indexed queries when idle; never overlaps."""
    if _tick_lock.locked():
        return
    async with _tick_lock:
        now = time.time()
        async with get_db() as conn:
            conn.row_factory = __import__("aiosqlite").Row
            posts = [dict(r) for r in await (await conn.execute(
                """SELECT p.id, p.job_id, p.account_id, p.post_at, j.media_file, j.media_type,
                          COALESCE(p.caption, j.caption) AS caption,
                          j.privacy, j.cross_view, j.viewer_scope, j.account_ids, j.view_delay_min, j.view_delay_max
                   FROM story_posts p JOIN story_jobs j ON j.id=p.job_id
                   WHERE p.status='pending' AND p.post_at<=? ORDER BY p.post_at LIMIT ?""",
                (now, POSTS_PER_TICK))).fetchall()]
            views = [dict(r) for r in await (await conn.execute(
                """SELECT v.id, v.job_id, v.viewer_account_id, p.account_id, p.story_id, p.posted_at
                   FROM story_views v JOIN story_posts p ON p.id=v.post_id
                   WHERE v.status='pending' AND v.view_at<=? ORDER BY v.view_at LIMIT ?""",
                (now, VIEWS_PER_TICK * 3))).fetchall()]
        if not posts and not views:
            return
        accounts = await _accounts_by_id()
        touched: set[int] = set()
        for i, row in enumerate(posts):
            if i:
                await _pause(4, 10)
            try:
                await _do_post(row, accounts)
            except Exception as e:
                logger.exception("[Story] post %s crashed", row["id"])
                await _set_post(row["id"], "failed", f"{type(e).__name__}: {e}")
            touched.add(row["job_id"])
            accounts = await _accounts_by_id()  # a FloodWait may have just paused a nick
        done = 0
        for row in views:
            if done >= VIEWS_PER_TICK:
                break
            try:
                if not await _do_view(row, accounts):
                    continue
            except Exception as e:
                logger.exception("[Story] view %s crashed", row["id"])
                await _set_view(row["id"], "failed", f"{type(e).__name__}: {e}")
            done += 1
            touched.add(row["job_id"])
            await _pause(2, 6)
        for jid in touched:
            await _refresh_job_status(jid)


def register_job(scheduler) -> None:
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler.add_job(
        tick,
        trigger=IntervalTrigger(seconds=TICK_SECONDS),
        id="story_tick",
        name="Story scheduler tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
