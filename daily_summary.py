"""
Daily Summary — Sends a daily activity report to the user's Telegram Saved Messages.
Aggregates DM stats, reply counts, account health, and watcher activity.
"""
import logging
from datetime import datetime

import database as db
import telegram_client as tg

logger = logging.getLogger("tg-scheduler.daily-summary")


async def send_daily_summary():
    """
    Gather today's stats and send a summary to the primary account's Saved Messages.
    Called by APScheduler as a daily cron job.
    """
    try:
        # Check if enabled
        enabled = await db.get_setting("daily_summary_enabled", "0")
        if enabled != "1":
            return

        # Get the account to send from
        account_id_str = await db.get_setting("daily_summary_account_id", "")
        if not account_id_str:
            # Use first logged-in account
            accounts = await db.get_all_accounts()
            logged_in = [a for a in accounts if a.get("is_logged_in")]
            if not logged_in:
                logger.warning("[DailySummary] No logged-in account available")
                return
            account_id = logged_in[0]["id"]
        else:
            account_id = int(account_id_str)

        # Check client is connected
        client = tg.get_client(account_id)
        if not client or not client.is_connected():
            logger.warning(f"[DailySummary] Account {account_id} not connected, skipping")
            return

        # Gather stats
        overview = await db.get_analytics_overview()
        daily = await db.get_analytics_daily_stats(days=1)
        health = await db.get_analytics_account_health()
        watcher_stats = await db.get_watcher_log_stats()
        unread = await db.count_unread_replies()

        # Today's numbers (last entry in the list is today)
        today_data = daily[-1] if daily else {}
        today_sent = today_data.get("sent", 0)
        today_failed = today_data.get("failed", 0)
        today_replies = today_data.get("replies", 0)

        # Account health summary
        flagged_count = sum(1 for a in health if a.get("is_flagged"))
        total_accounts = len(health)
        healthy_count = total_accounts - flagged_count

        # Build message (plain text, no emoji)
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        lines = [
            f"--- BAO CAO NGAY {now} ---",
            "",
            f"DM gui hom nay: {today_sent}",
            f"DM loi: {today_failed}",
            f"Reply nhan duoc: {today_replies}",
            f"Tin nhan chua doc: {unread}",
            "",
            f"Tong DM da gui: {overview.get('total_dm_sent', 0)}",
            f"Tong reply: {overview.get('total_replies', 0)}",
            f"Ti le phan hoi: {overview.get('response_rate', 0):.1f}%",
            "",
            f"Tai khoan: {healthy_count} hoat dong / {flagged_count} canh bao",
            f"Watcher dang chay: {watcher_stats.get('active_watchers', 0)}",
            f"DM hom nay (watcher): {watcher_stats.get('today', 0)}",
        ]

        # Add warnings if any
        warnings = []
        for a in health:
            if a.get("is_flagged"):
                warnings.append(
                    f"  - {a.get('account_name', 'N/A')} (ID {a['account_id']}): "
                    f"CANH BAO - {a.get('flag_reason', 'N/A')}"
                )
            elif a.get("health_score", 100) < 50:
                warnings.append(
                    f"  - {a.get('account_name', 'N/A')} (ID {a['account_id']}): "
                    f"Suc khoe {a.get('health_score', 0)}%"
                )

        if warnings:
            lines.append("")
            lines.append("CANH BAO TAI KHOAN:")
            lines.extend(warnings)

        lines.append("")
        lines.append("--- TG Scheduler ---")

        summary_text = "\n".join(lines)

        # Send to Saved Messages
        await client.send_message("me", summary_text)
        logger.info(f"[DailySummary] Sent daily summary via account {account_id}")

    except Exception as e:
        logger.error(f"[DailySummary] Failed to send: {e}", exc_info=True)
