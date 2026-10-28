"""
alerts.py — Cảnh báo hệ thống qua Telegram Saved Messages + backup DB.

- send_alert(kind, text): gửi cảnh báo qua account đầu tiên đang kết nối,
  dedup theo kind trong 1 giờ để tránh spam. Không bao giờ ném exception.
- flood_guard(account_id): PeerFlood -> auto-pause account + cảnh báo.
- run_backup(): SQLite online backup (an toàn khi DB đang chạy), giữ 7 bản.

Tắt cảnh báo: settings key alerts_enabled = "0".
ponytail: chưa có auto-resume sau 24h pause — bật lại thủ công trong UI;
thêm cron resume khi cần vận hành không người trực.
"""
import logging
import os
import sqlite3
import time
from datetime import datetime

import database as db

logger = logging.getLogger("tg-scheduler.alerts")

_alert_log: dict[str, float] = {}  # kind -> last sent timestamp
ALERT_COOLDOWN_SECS = 3600

BACKUP_DIR = os.path.join(os.path.dirname(db.DB_PATH), "backups")
BACKUP_KEEP = 7


async def _team_chat(client):
    """Nhóm nhận báo cáo lead (setting lead_alert_chat, mặc định "Weex OS").

    Tìm theo tên trong dialog của account; gửi không reply_to → vào topic General
    nếu là forum. None nếu account không ở trong nhóm.
    """
    title = (await db.get_setting("lead_alert_chat", "Weex OS") or "").strip().lower()
    if not title:
        return None
    async for d in client.iter_dialogs(limit=300):
        if (d.is_group or d.is_channel) and title in (d.name or "").lower():
            return d.entity
    return None


async def send_alert(kind: str, text: str, to_team: bool = False):
    """Gửi cảnh báo về Saved Messages (hoặc nhóm team nếu to_team). Dedup theo kind trong 1 giờ."""
    try:
        if await db.get_setting("alerts_enabled", "1") != "1":
            return
        now = time.time()
        if now - _alert_log.get(kind, 0) < ALERT_COOLDOWN_SECS:
            logger.debug(f"[Alert] Dedup skipped: {kind}")
            return

        import telegram_client as tg
        accounts = await db.get_all_accounts()
        for a in accounts:
            if not a.get("is_logged_in"):
                continue
            client = tg.get_client(a["id"])
            if not client or not client.is_connected():
                continue
            target = "me"
            if to_team:
                target = await _team_chat(client)
                if target is None:
                    continue  # account này không ở trong nhóm, thử account khác
            ts = datetime.now().strftime("%d/%m %H:%M")
            await client.send_message(target, f"🚨 [TG Scheduler] {text}\n({ts})")
            _alert_log[kind] = now
            logger.info(f"[Alert] Sent '{kind}': {text}")
            return
        logger.warning(f"[Alert] Không có account nào kết nối, bỏ qua: {text}")
    except Exception as e:
        logger.error(f"[Alert] Lỗi gửi cảnh báo: {e}")


async def flood_guard(account_id: int) -> bool:
    """PeerFlood -> tắt account ngay để bảo vệ khỏi ban + cảnh báo.

    Returns True nếu account vừa bị pause. Bật lại thủ công trong UI.
    """
    try:
        accounts = await db.get_all_accounts()
        acc = next((a for a in accounts if a["id"] == account_id), None)
        name = (acc.get("name") or f"#{account_id}") if acc else f"#{account_id}"
        if acc and acc.get("is_paused"):
            return False  # đã tắt rồi, khỏi pause lại
        await db.pause_account(
            account_id,
            "PeerFlood — tự động tạm dừng để bảo vệ tài khoản"
        )
        logger.warning(f"[FloodGuard] Account {account_id} AUTO-PAUSED: PeerFlood")

        # Ban-triggered proxy rotation: a fresh IP often clears a temporary
        # limit tied to the previous IP. Opt-out via proxy_rotate_on_ban=0.
        rotation_note = ""
        try:
            if await db.get_setting("proxy_rotate_on_ban", "1") == "1":
                import proxy_manager as pm
                cur_proxy = acc.get("proxy_url") if acc else None
                res = await pm.rotate_proxy_for_account(account_id, cur_proxy)
                if res.get("rotated"):
                    rotation_note = "\n🔄 Đã tự đổi proxy sang IP mới để gỡ giới hạn."
                elif res.get("reason"):
                    rotation_note = f"\n(Chưa đổi proxy: {res['reason']})"
        except Exception as e:
            logger.warning(f"[FloodGuard] Proxy rotation skipped for {account_id}: {e}")

        await send_alert(
            f"peerflood_{account_id}",
            f"⛔ Tài khoản {name} (ID {account_id}) bị PeerFlood — "
            f"ĐÃ TỰ TẮT để tránh bị ban. Kiểm tra kỹ trước khi bật lại.{rotation_note}"
        )
        return True
    except Exception as e:
        logger.error(f"[FloodGuard] Lỗi: {e}")
        return False


async def check_lead_sla():
    """Tier A/B leads stuck in needs_human > 2h → ping. Dedup per lead per 6h."""
    try:
        if await db.get_setting("alerts_enabled", "1") != "1":
            return
        async with db.get_db() as conn:
            conn.row_factory = __import__("aiosqlite").Row
            cursor = await conn.execute("""
                SELECT account_id, user_id, username, name, lead_tier, intent_score,
                       human_takeover_at, summary
                FROM ai_followup_chats
                WHERE status = 'needs_human'
                  AND lead_tier IN ('Tier A', 'Tier B')
                  AND human_takeover_at IS NOT NULL
            """)
            rows = [dict(r) for r in await cursor.fetchall()]
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        stale = []
        for r in rows:
            try:
                tk = datetime.fromisoformat(r["human_takeover_at"]).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if (now - tk).total_seconds() > 7200:  # > 2h
                stale.append(r)
        if not stale:
            return
        stale.sort(key=lambda r: -(r.get("intent_score") or 0))
        lines = []
        for r in stale[:5]:
            tag = f"@{r['username']}" if r.get("username") else f"#{r['user_id']}"
            name = r.get("name") or tag
            lines.append(f"• {name} ({tag}) — {r['lead_tier']} {r.get('intent_score') or 0}%")
        await send_alert(
            "lead_sla",
            f"⏰ {len(stale)} lead Tier A/B đang chờ người thật >2h:\n" + "\n".join(lines)
            + "\n\nVào AI Follow-Up xử lý kẻo nguội lead!",
            to_team=True,
        )
    except Exception as e:
        logger.error(f"[SLA] Lỗi: {e}")


async def check_reply_sla():
    """Speed-to-reply alert: hot leads waiting on US for too long.

    Finds Tier A/B leads whose last message is from the USER (we owe a reply)
    and have been waiting longer than `reply_sla_minutes` (default 30). Fires a
    dedup'd alert so operators jump in before the lead goes cold. Disable via
    setting `reply_sla_enabled=0`.
    """
    try:
        if await db.get_setting("alerts_enabled", "1") != "1":
            return
        if await db.get_setting("reply_sla_enabled", "1") != "1":
            return
        try:
            threshold_min = int(await db.get_setting("reply_sla_minutes", "30"))
        except (ValueError, TypeError):
            threshold_min = 30
        if threshold_min <= 0:
            return

        import aiosqlite
        async with db.get_db() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("""
                SELECT account_id, user_id, username, name, lead_tier, intent_score,
                       updated_at, history_json
                FROM ai_followup_chats
                WHERE status = 'active'
                  AND (lead_tier IN ('Tier A', 'Tier B') OR intent_score >= 50)
            """)
            rows = [dict(r) for r in await cursor.fetchall()]

        import json as _json
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        waiting = []
        for r in rows:
            # Only when the LAST message is from the user (we owe a reply).
            try:
                hist = _json.loads(r.get("history_json") or "[]")
            except Exception:
                hist = []
            if not hist or hist[-1].get("role") != "user":
                continue
            try:
                ts = datetime.fromisoformat(r["updated_at"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            waited_min = (now - ts).total_seconds() / 60
            if waited_min > threshold_min:
                waiting.append((waited_min, r))

        if not waiting:
            return
        waiting.sort(key=lambda x: -x[0])
        lines = []
        for waited_min, r in waiting[:5]:
            tag = f"@{r['username']}" if r.get("username") else f"#{r['user_id']}"
            name = r.get("name") or tag
            lines.append(f"• {name} ({tag}) — chờ {int(waited_min)} phút, {r['lead_tier']} {r.get('intent_score') or 0}%")

        # Per-lead dedup: the default 1h send_alert cooldown is LONGER than the
        # 30-min SLA, so a single shared kind would suppress alerts for OTHER
        # leads. Key the cooldown on the specific set of waiting leads, so the
        # same set won't re-alert within an hour but a new lead still will.
        lead_keys = sorted(f"{r['account_id']}:{r['user_id']}" for _, r in waiting[:5])
        kind = "reply_sla:" + ",".join(lead_keys)

        await send_alert(
            kind,
            f"🔥 {len(waiting)} lead nóng đang chờ phản hồi >{threshold_min} phút:\n"
            + "\n".join(lines) + "\n\nTrả lời NGAY kẻo nguội lead!",
            to_team=True,
        )
    except Exception as e:
        logger.error(f"[ReplySLA] Lỗi: {e}")


def run_backup() -> str | None:
    """SQLite online backup (đọc nhất quán dù DB đang chạy WAL). Giữ 7 bản mới nhất."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        dest = os.path.join(BACKUP_DIR, f"scheduler_{ts}.db")
        src = sqlite3.connect(db.DB_PATH)
        try:
            dst = sqlite3.connect(dest)
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        # Xóa bản cũ, giữ BACKUP_KEEP bản mới nhất
        backups = sorted(
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith("scheduler_") and f.endswith(".db")
        )
        for old in backups[:-BACKUP_KEEP]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except OSError:
                pass
        size_mb = os.path.getsize(dest) / 1024 / 1024
        logger.info(f"[Backup] OK: {os.path.basename(dest)} ({size_mb:.1f} MB)")
        return dest
    except Exception as e:
        logger.error(f"[Backup] Lỗi: {e}")
        return None
