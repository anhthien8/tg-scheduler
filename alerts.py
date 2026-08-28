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


async def send_alert(kind: str, text: str):
    """Gửi cảnh báo về Saved Messages. Dedup theo kind trong 1 giờ."""
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
            ts = datetime.now().strftime("%d/%m %H:%M")
            await client.send_message("me", f"🚨 [TG Scheduler] {text}\n({ts})")
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
        await send_alert(
            f"peerflood_{account_id}",
            f"⛔ Tài khoản {name} (ID {account_id}) bị PeerFlood — "
            f"ĐÃ TỰ TẮT để tránh bị ban. Kiểm tra kỹ trước khi bật lại."
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
            + "\n\nVào AI Follow-Up xử lý kẻo nguội lead!"
        )
    except Exception as e:
        logger.error(f"[SLA] Lỗi: {e}")


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
