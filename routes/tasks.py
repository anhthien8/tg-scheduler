"""Task center (v3.40): one cheap summary of every background job the user started.

GET /api/tasks/summary feeds the sidebar "Việc đang chạy" panel. DB-only (a few small
SELECTs, no Telegram calls) so it works in split web mode too; in combined mode it also
reads the engines' in-memory "really running" flags to spot jobs left stale by a restart.

Each row is normalised to:
  {key, kind, id, name, status, bucket, label, done, total, ts, ts_scheduled, note, page, icon, kind_label}
bucket: run (đang chạy) | plan (đã hẹn / chờ) | warn (cần xem) | done (xong trong 24h)
"""
import asyncio
import logging
import os
import time
from datetime import datetime

import pytz
from fastapi import APIRouter

import database as db
import owed_answers

logger = logging.getLogger("tg-scheduler.tasks")
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

TZ = pytz.timezone(os.getenv("TIMEZONE") or "Asia/Ho_Chi_Minh")
CACHE_TTL = 30          # seconds: every open tab shares one DB pass per 30 s
FRESH_MIN_GAP = 5       # "Làm mới" bypasses the cache at most once per 5 s
DAY = 86400

_cache: dict = {"at": 0.0, "val": None}
_lock = asyncio.Lock()

KINDS = {
    "dm":       ("📨", "Chiến dịch DM", "members"),
    "story":    ("📸", "Story hàng loạt", "stories"),
    "warmup":   ("🔥", "Warmup nhóm", "warmup"),
    "scrape":   ("👥", "Cào thành viên", "members"),
    "batch":    ("📦", "Cào hàng loạt", "members"),
    "crawl":    ("🔍", "Deep Crawl", "members"),
    "invite":   ("➕", "Mời vào nhóm", "members"),
    "zalo":     ("💙", "Chiến dịch Zalo", "zalo"),
    "whatsapp": ("💚", "Chiến dịch WhatsApp", "whatsapp"),
    "schedule": ("📅", "Lịch gửi", "schedules"),
    "watcher":  ("🎯", "Keyword DM", "watchers"),
    "engage":   ("🌐", "Cộng đồng", "engagement"),
    "kolq":     ("💬", "Hàng chờ trả lời", "ai-followup"),
}


def _combined() -> bool:
    return (os.getenv("TG_RUNTIME_MODE") or "combined").lower() == "combined"


async def _rows(sql: str, params=()) -> list[dict]:
    async with db.get_db() as conn:
        cur = await conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in await cur.fetchall()]


def _row(kind, r, status, bucket, label, *, done=None, total=None, ts=None, ts_scheduled=None, note=None):
    icon, kind_label, page = KINDS[kind]
    return {
        "key": f"{kind}:{r['id']}", "kind": kind, "id": r["id"], "name": (r.get("name") or "").strip() or f"#{r['id']}",
        "status": status, "bucket": bucket, "label": label,
        "done": done, "total": total, "ts": ts, "ts_scheduled": ts_scheduled,
        "note": (str(note).strip()[:200] or None) if note else None,
        "page": page, "icon": icon, "kind_label": kind_label,
    }


def _local_ts(text: str | None, tz=TZ) -> int | None:
    """'YYYY-MM-DD HH:MM' wall-clock in tz -> epoch seconds."""
    if not text:
        return None
    try:
        return int(tz.localize(datetime.strptime(str(text)[:16], "%Y-%m-%d %H:%M")).timestamp())
    except Exception:
        return None


# ── Collectors: one per task type. Each returns normalised rows. ─────────────────

async def _dm() -> list[dict]:
    rows = await _rows("""
        SELECT c.id, c.name, c.status,
               COALESCE(c.sent_count,0)+COALESCE(c.failed_count,0)+COALESCE(c.skipped_count,0) AS done,
               COALESCE(c.total_targets,0) AS total,
               CAST(strftime('%s', c.updated_at) AS INTEGER) AS ts,
               c.scheduled_at, c.target_timezone, COALESCE(c.sent_count,0) AS sent,
               CASE WHEN c.status IN ('paused','paused_auto','error')
                    THEN (SELECT l.error_message FROM dm_campaign_logs l
                          WHERE l.campaign_id = c.id AND l.error_message IS NOT NULL
                          ORDER BY l.id DESC LIMIT 1) END AS note
        FROM dm_campaigns c
        WHERE c.status IN ('running','waiting','scheduled','paused_auto')
           OR (c.status IN ('completed','error','paused') AND c.updated_at >= datetime('now','-1 day'))
        ORDER BY c.updated_at DESC LIMIT 50""")
    out = []
    for r in rows:
        st = r["status"]
        kw = dict(done=r["done"], total=r["total"], ts=r["ts"], note=r["note"])
        if st == "running":
            out.append(_row("dm", r, st, "run", f"Đang gửi · {r['sent']} đã gửi", **kw))
        elif st == "waiting":
            out.append(_row("dm", r, st, "plan", "Chờ lượt (dùng chung nick gửi)", **kw))
        elif st == "scheduled":
            try:
                tz = pytz.timezone(r["target_timezone"] or "Asia/Ho_Chi_Minh")
            except Exception:
                tz = TZ
            out.append(_row("dm", r, st, "plan", "Hẹn giờ", ts_scheduled=_local_ts(r["scheduled_at"], tz), **kw))
        elif st == "paused_auto":
            out.append(_row("dm", r, st, "plan", "Chờ mở khoá nick, tự chạy tiếp", **kw))
        elif st == "error":
            out.append(_row("dm", r, st, "warn", "Lỗi", **kw))
        elif st == "completed":
            out.append(_row("dm", r, st, "done", f"Xong · {r['sent']} đã gửi", **{**kw, "note": None}))
        elif st == "paused":
            # paused = manual stop, or an auto-stop that needs a look (has an error note)
            if r["note"]:
                out.append(_row("dm", r, st, "warn", "Tự dừng", **kw))
            else:
                out.append(_row("dm", r, st, "done", "Đã dừng", **kw))
    return out


async def _story(since: int) -> list[dict]:
    rows = await _rows("""
        SELECT * FROM (
          SELECT j.id, j.name, j.status, CAST(j.scheduled_at AS INTEGER) AS ts_scheduled,
                 (SELECT COUNT(*) FROM story_posts p WHERE p.job_id = j.id AND p.status <> 'pending') AS done,
                 (SELECT COUNT(*) FROM story_posts p WHERE p.job_id = j.id) AS total,
                 (SELECT COUNT(*) FROM story_posts p WHERE p.job_id = j.id AND p.status = 'posted') AS posted,
                 (SELECT COUNT(*) FROM story_posts p WHERE p.job_id = j.id AND p.status IN ('failed','skipped')) AS bad,
                 CAST(MAX(
                   COALESCE((SELECT MAX(COALESCE(p.posted_at, CASE WHEN p.status IN ('failed','skipped') THEN p.post_at END))
                             FROM story_posts p WHERE p.job_id = j.id), 0),
                   COALESCE((SELECT MAX(v.done_at) FROM story_views v WHERE v.job_id = j.id), 0),
                   CAST(strftime('%s', j.created_at) AS INTEGER)) AS INTEGER) AS ts
          FROM story_jobs j
          WHERE j.id > (SELECT COALESCE(MAX(id), 0) - 100 FROM story_jobs))
        WHERE status IN ('scheduled','running') OR ts >= ?
        ORDER BY id DESC""", (since,))
    out = []
    for r in rows:
        st, posted, bad = r["status"], r["posted"], r["bad"]
        kw = dict(done=r["done"], total=r["total"], ts=r["ts"], ts_scheduled=r["ts_scheduled"])
        if st == "scheduled":
            out.append(_row("story", r, st, "plan", "Đã hẹn", **kw))
        elif st == "running":
            out.append(_row("story", r, st, "run", f"Đang đăng · {posted}/{r['total']} nick" + (f", {bad} lỗi" if bad else ""), **kw))
        elif st == "cancelled":
            out.append(_row("story", r, st, "done", "Đã huỷ", **kw))
        elif st == "failed" or (posted == 0 and bad):
            out.append(_row("story", r, "failed", "warn", "Lỗi, không nick nào đăng được", **kw))
        elif st == "partial" or bad:
            out.append(_row("story", r, "partial", "warn", f"Đăng {posted}/{r['total']}, {bad} nick lỗi", **kw))
        else:
            out.append(_row("story", r, st, "done", f"Xong · {posted} nick đã đăng", **kw))
    return out


async def _warmup(since: int) -> list[dict]:
    rows = await _rows("""
        SELECT * FROM (
          SELECT w.id, COALESCE(g.name, 'Nhóm #' || w.group_id) AS name, w.status,
                 COALESCE(w.posts_today,0) AS done, COALESCE(w.daily_post_limit,0) AS total,
                 COALESCE((SELECT CAST(strftime('%s', MAX(l.posted_at)) AS INTEGER) FROM warmup_logs l WHERE l.job_id = w.id),
                          CAST(strftime('%s', w.created_at) AS INTEGER)) AS ts
          FROM warmup_jobs w LEFT JOIN warmup_groups g ON g.id = w.group_id)
        WHERE status IN ('running','error') OR ts >= ?
        ORDER BY id DESC LIMIT 50""", (since,))
    we = None
    if _combined():
        try:
            import warmup_engine as we
        except Exception:
            we = None
    out = []
    for r in rows:
        st = r["status"]
        kw = dict(done=r["done"], total=r["total"], ts=r["ts"])
        if st == "running":
            if we is not None and not we.is_job_running(r["id"]):
                if r["ts"] and r["ts"] >= since:
                    out.append(_row("warmup", r, "stopped", "done", "Đã dừng", **kw))
                continue
            nxt = None
            if we is not None:
                try:
                    nxt = we.job_state(r["id"]).get("next_at")
                except Exception:
                    nxt = None
            out.append(_row("warmup", r, st, "run", f"Đang chạy · {r['done']}/{r['total']} bài hôm nay",
                            ts_scheduled=int(nxt) if nxt else None, **kw))
        elif st == "error":
            out.append(_row("warmup", r, st, "warn", "Lỗi", **kw))
        else:
            out.append(_row("warmup", r, st, "done", "Đã dừng", **kw))
    return out


async def _scrape() -> list[dict]:
    rows = await _rows("""
        SELECT scrape_job_id AS id, MAX(group_title) AS name, COUNT(*) AS n,
               CAST(strftime('%s', MAX(scraped_at)) AS INTEGER) AS ts
        FROM scraped_members
        WHERE scraped_at >= datetime('now','-1 day')
          AND scrape_job_id GLOB 'scrape_*'
          AND scrape_job_id NOT GLOB '*_f[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'
        GROUP BY scrape_job_id ORDER BY ts DESC LIMIT 20""")
    return [_row("scrape", r, "done", "done", f"Cào xong · {r['n']} thành viên", done=r["n"], total=r["n"], ts=r["ts"])
            for r in rows]


async def _batch() -> list[dict]:
    rows = await _rows("""
        SELECT batch_job_id AS id,
               COALESCE(MAX(channel_title), MIN(channel_username))
                 || CASE WHEN COUNT(*) > 1 THEN ' +' || (COUNT(*) - 1) || ' kênh' ELSE '' END AS name,
               CASE WHEN SUM(status IN ('pending','running')) > 0
                         AND MAX(COALESCE(finished_at, started_at)) < strftime('%Y-%m-%dT%H:%M:%S','now','-2 hours') THEN 'interrupted'
                    WHEN SUM(status IN ('pending','running')) > 0 THEN 'running'
                    WHEN SUM(status = 'error') = COUNT(*) THEN 'error'
                    ELSE 'done' END AS status,
               SUM(status IN ('done','error')) AS done, COUNT(*) AS total,
               CAST(strftime('%s', MAX(COALESCE(finished_at, started_at))) AS INTEGER) AS ts,
               SUM(status = 'error') AS errors, SUM(COALESCE(member_count,0)) AS members
        FROM batch_scrape_channels
        GROUP BY batch_job_id
        HAVING SUM(status IN ('pending','running')) > 0
            OR MAX(finished_at) >= strftime('%Y-%m-%dT%H:%M:%S','now','-1 day')
        ORDER BY ts DESC LIMIT 20""")
    out = []
    for r in rows:
        st = r["status"]
        kw = dict(done=r["done"], total=r["total"], ts=r["ts"])
        if st == "running":
            out.append(_row("batch", r, st, "run", f"Đang cào · {r['done']}/{r['total']} kênh", **kw))
        elif st == "interrupted":
            out.append(_row("batch", r, st, "warn", "Bị gián đoạn (server khởi động lại)", **kw))
        elif st == "error":
            out.append(_row("batch", r, st, "warn", "Lỗi tất cả kênh", **kw))
        else:
            lbl = f"Cào xong · {r['members']} thành viên" + (f", {r['errors']} kênh lỗi" if r["errors"] else "")
            out.append(_row("batch", r, st, "done", lbl, **kw))
    return out


async def _crawl() -> list[dict]:
    rows = await _rows("""
        SELECT json_extract(h.value,'$.id') AS id, json_extract(h.value,'$.channel_link') AS name,
               json_extract(h.value,'$.status') AS status,
               COALESCE(json_extract(h.value,'$.channels_found'),0) AS done,
               COALESCE(json_extract(h.value,'$.contacts_found'),0) AS contacts,
               CAST(strftime('%s', COALESCE(json_extract(h.value,'$.ended_at'), NULLIF(json_extract(h.value,'$.started_at'),'')), 'utc') AS INTEGER) AS ts,
               NULL AS ts_scheduled, json_extract(h.value,'$.note') AS note
        FROM settings s, json_each(s.value) h
        WHERE s.key = 'deep_crawl_history' AND json_valid(s.value)
          AND (json_extract(h.value,'$.status') = 'running'
               OR json_extract(h.value,'$.ended_at') >= strftime('%Y-%m-%dT%H:%M:%S','now','localtime','-1 day'))
        UNION ALL
        SELECT 'q' || q.key, json_extract(q.value,'$.channel_link'),
               CASE WHEN json_extract(q.value,'$.retry_after') IS NOT NULL THEN 'flood_wait' ELSE 'queued' END,
               0, 0, CAST(strftime('%s', json_extract(q.value,'$.added_at'), 'utc') AS INTEGER),
               CAST(json_extract(q.value,'$.retry_after') AS INTEGER), NULL
        FROM settings s2, json_each(s2.value) q
        WHERE s2.key = 'deep_crawl_queue' AND json_valid(s2.value)""")
    live_id = None
    if _combined():
        try:
            import routes.members as m
            t = m._deep_crawl_task
            if t is not None and not t.done():
                live_id = m._deep_crawl_history_id
        except Exception:
            live_id = None
    out = []
    for r in rows:
        st = r["status"]
        kw = dict(done=r["done"], total=None, ts=r["ts"], ts_scheduled=r["ts_scheduled"], note=r["note"])
        if st == "running":
            if _combined() and r["id"] != live_id:
                out.append(_row("crawl", r, "interrupted", "warn", "Bị gián đoạn, có thể chạy lại", **kw))
            else:
                out.append(_row("crawl", r, st, "run", f"Đang quét · {r['done']} kênh, {r['contacts']} contact", **kw))
        elif st == "queued":
            out.append(_row("crawl", r, st, "plan", "Trong hàng đợi", **kw))
        elif st == "flood_wait" and str(r["id"]).startswith("q"):
            out.append(_row("crawl", r, st, "plan", "Chờ hết FloodWait, tự thử lại", **kw))
        elif st == "completed":
            out.append(_row("crawl", r, st, "done", f"Xong · {r['done']} kênh, {r['contacts']} contact", **{**kw, "note": None}))
        elif st == "stopped":
            out.append(_row("crawl", r, st, "done", "Đã dừng", **kw))
        elif st in ("error", "interrupted"):
            out.append(_row("crawl", r, st, "warn", "Lỗi" if st == "error" else "Bị gián đoạn, có thể chạy lại", **kw))
    return out


async def _invite() -> list[dict]:
    rows = await _rows("""
        SELECT c.id, COALESCE(NULLIF(c.name,''), c.target_chat_title, c.target_chat) AS name, c.status,
               COALESCE(c.invited_count,0)+COALESCE(c.failed_count,0)+COALESCE(c.skipped_count,0) AS done,
               COALESCE(c.total_targets,0) AS total, COALESCE(c.invited_count,0) AS invited,
               CAST(strftime('%s', c.updated_at) AS INTEGER) AS ts
        FROM invite_campaigns c
        WHERE c.status = 'running'
           OR (c.status IN ('completed','paused','error') AND c.updated_at >= datetime('now','-1 day'))
        ORDER BY c.updated_at DESC LIMIT 20""")
    active = None
    if _combined():
        try:
            import invite_engine
            active = invite_engine._active_invite_campaigns
        except Exception:
            active = None
    out = []
    for r in rows:
        st = r["status"]
        kw = dict(done=r["done"], total=r["total"], ts=r["ts"])
        if st == "running":
            if active is not None and not active.get(r["id"]):
                out.append(_row("invite", r, "stale", "warn", "Đã dừng (server khởi động lại)", **kw))
            else:
                out.append(_row("invite", r, st, "run", f"Đang mời · {r['invited']} đã vào", **kw))
        elif st == "error":
            out.append(_row("invite", r, st, "warn", "Lỗi", **kw))
        else:
            out.append(_row("invite", r, st, "done", f"Xong · {r['invited']} đã vào" if st == "completed" else "Tạm dừng", **kw))
    return out


async def _zalo() -> list[dict]:
    rows = await _rows("""
        SELECT c.id, c.name, c.status, c.last_error AS note, COALESCE(c.daily_limit,0) AS total,
               (SELECT COUNT(*) FROM zalo_campaign_logs l
                 WHERE l.campaign_id = c.id AND l.action = 'friend_request' AND l.ok = 1
                   AND l.created_at >= date('now','localtime')) AS done,
               CAST(strftime('%s', (SELECT MAX(l.created_at) FROM zalo_campaign_logs l WHERE l.campaign_id = c.id), 'utc') AS INTEGER) AS ts
        FROM zalo_campaigns c
        WHERE c.status = 'running'
           OR (c.status IN ('done','paused') AND EXISTS (
                 SELECT 1 FROM zalo_campaign_logs l
                  WHERE l.campaign_id = c.id AND l.created_at >= datetime('now','localtime','-1 day')))
        ORDER BY c.id DESC LIMIT 20""")
    out = []
    for r in rows:
        st = r["status"]
        kw = dict(done=r["done"], total=r["total"], ts=r["ts"], note=r["note"])
        if st == "running":
            out.append(_row("zalo", r, st, "run", f"Đang chạy · {r['done']}/{r['total']} lời mời hôm nay", **kw))
        else:
            out.append(_row("zalo", r, st, "done", "Hết danh sách" if st == "done" else "Tạm dừng", **kw))
    return out


async def _whatsapp() -> list[dict]:
    rows = await _rows("""
        SELECT c.id, c.name, c.status, c.last_error AS note, COALESCE(c.daily_limit,0) AS total,
               (SELECT COUNT(*) FROM wa_campaign_logs l
                 WHERE l.campaign_id = c.id AND l.action = 'open' AND l.ok = 1
                   AND l.created_at >= date('now','localtime')) AS done,
               CAST(strftime('%s', (SELECT MAX(l.created_at) FROM wa_campaign_logs l WHERE l.campaign_id = c.id), 'utc') AS INTEGER) AS ts
        FROM wa_campaigns c
        WHERE c.status = 'running'
           OR (c.status IN ('done','paused') AND EXISTS (
                 SELECT 1 FROM wa_campaign_logs l
                  WHERE l.campaign_id = c.id AND l.created_at >= datetime('now','localtime','-1 day')))
        ORDER BY c.id DESC LIMIT 20""")
    out = []
    for r in rows:
        st = r["status"]
        kw = dict(done=r["done"], total=r["total"], ts=r["ts"], note=r["note"])
        if st == "running":
            out.append(_row("whatsapp", r, st, "run", f"Đang chạy · {r['done']}/{r['total']} tin mở đầu hôm nay", **kw))
        else:
            out.append(_row("whatsapp", r, st, "done", "Hết danh sách" if st == "done" else "Tạm dừng", **kw))
    return out


async def _schedule(since: int) -> list[dict]:
    # Only one-shot schedules: recurring ones are standing rules, always "on", not tasks.
    rows = await _rows("""
        SELECT * FROM (
          SELECT s.id, s.name, s.is_active, COALESCE(s.current_sends,0) AS sends,
                 s.once_date, s.time_of_day,
                 MAX(CAST(strftime('%s', s.updated_at) AS INTEGER),
                     COALESCE((SELECT CAST(strftime('%s', MAX(l.sent_at)) AS INTEGER)
                                 FROM send_logs l WHERE l.schedule_id = s.id), 0)) AS ts
          FROM schedules s WHERE s.schedule_type = 'once')
        WHERE (is_active = 1 AND sends = 0) OR ts >= ?
        ORDER BY id DESC LIMIT 30""", (since,))
    now = time.time()
    out = []
    for r in rows:
        at = _local_ts(f"{r['once_date']} {r['time_of_day']}") if r["once_date"] and r["time_of_day"] else None
        kw = dict(done=None, total=None, ts=r["ts"], ts_scheduled=at)
        if r["sends"] > 0:
            out.append(_row("schedule", r, "done", "done", "Đã gửi", **kw))
        elif not r["is_active"]:
            continue  # turned off by the user, not a task anymore
        elif at and at < now - 300:
            out.append(_row("schedule", r, "expired", "warn", "Quá giờ mà chưa gửi", **kw))
        else:
            out.append(_row("schedule", r, "scheduled", "plan", "Hẹn giờ gửi", **kw))
    return out


async def _alerts() -> list[dict]:
    """Standing rules only matter here when the engine stopped them on its own."""
    out = []
    rows = await _rows("""
        SELECT w.id, w.name, w.paused_reason AS note, CAST(strftime('%s', w.updated_at) AS INTEGER) AS ts
        FROM keyword_watchers w
        WHERE COALESCE(w.platform,'telegram') = 'telegram' AND w.is_active = 0
          AND COALESCE(w.paused_reason,'') <> '' AND w.updated_at >= datetime('now','-1 day')
        ORDER BY w.updated_at DESC LIMIT 20""")
    out += [_row("watcher", r, "auto_paused", "warn", "Tự dừng sau nhiều lỗi liên tiếp", ts=r["ts"], note=r["note"]) for r in rows]
    rows = await _rows("""
        SELECT c.id, c.name, c.pause_reason AS note, CAST(strftime('%s', c.updated_at) AS INTEGER) AS ts
        FROM engagement_campaigns c WHERE c.status = 'auto_paused'
        ORDER BY c.updated_at DESC LIMIT 20""")
    out += [_row("engage", r, "auto_paused", "warn", "Tự dừng (nick bị mute/ban)", ts=r["ts"], note=r["note"]) for r in rows]
    return out


async def _reply_queue() -> list[dict]:
    """KOLs waiting on a person (AI handed over, a promise, chat tay): one row, newest first."""
    rows = await _rows("""
        SELECT c.username, c.name, c.status, c.owed_since,
               CASE WHEN json_valid(c.history_json) THEN json_extract(c.history_json, '$[#-1].role') END AS last_role,
               CASE WHEN json_valid(c.history_json)
                    THEN substr(json_extract(c.history_json, '$[#-1].content'), 1, 40) END AS last_text,
               CAST(strftime('%s', COALESCE(c.last_user_message_at, c.updated_at)) AS INTEGER) AS ts
        FROM ai_followup_chats c
        WHERE c.status <> 'bot_ignored'
          AND (c.owed_since IS NOT NULL OR c.status IN ('needs_human', 'paused_admin'))
          AND COALESCE(NULLIF(c.kol_step, ''), 'prospect') NOT IN ('declined', 'dormant', 'media')
          AND c.user_id NOT IN (SELECT telegram_user_id FROM accounts WHERE telegram_user_id IS NOT NULL)""")
    waiting = sorted((r for r in rows if owed_answers.lead_waiting_on_us(r)), key=lambda r: r["ts"] or 0, reverse=True)
    if not waiting:
        return []
    who = [f"@{r['username']}" if r["username"] else (r["name"] or "?") for r in waiting[:5]]
    newest = waiting[0]["ts"] or 0
    head = {"id": "queue", "name": f"{len(waiting)} KOL chờ người trả lời"}
    # status = newest wait: a KOL newly handed over re-raises the toast and the red dot.
    return [_row("kolq", head, f"w{newest}", "warn", f"mới nhất {who[0]}", ts=newest,
                 note="Bấm để mở hàng chờ: " + ", ".join(who) + (" …" if len(waiting) > 5 else ""))]


async def _collect() -> dict:
    since = int(time.time()) - DAY
    jobs = [("kolq", _reply_queue()), ("dm", _dm()), ("story", _story(since)), ("warmup", _warmup(since)),
            ("scrape", _scrape()), ("batch", _batch()), ("crawl", _crawl()), ("invite", _invite()),
            ("zalo", _zalo()), ("whatsapp", _whatsapp()), ("schedule", _schedule(since)), ("alerts", _alerts())]
    tasks, errors = [], []
    for name, coro in jobs:     # sequential: the DB pool is small and every query is tiny
        try:
            tasks += await coro
        except Exception as e:  # missing table (fresh install / lazily created) or bad row: skip that type only
            errors.append(name)
            logger.debug("[tasks] %s skipped: %s", name, e)
    counts = {b: sum(1 for t in tasks if t["bucket"] == b) for b in ("run", "plan", "warn", "done")}
    return {"tasks": tasks, "counts": counts, "generated_at": int(time.time()), "skipped": errors}


@router.get("/summary")
async def summary(fresh: int = 0):
    now = time.time()
    age = now - _cache["at"]
    if _cache["val"] is not None and (age < CACHE_TTL and not (fresh and age >= FRESH_MIN_GAP)):
        return _cache["val"]
    async with _lock:
        if _cache["val"] is not None and time.time() - _cache["at"] < (FRESH_MIN_GAP if fresh else CACHE_TTL):
            return _cache["val"]
        _cache["val"] = await _collect()
        _cache["at"] = time.time()
        return _cache["val"]
