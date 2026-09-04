"""
command_bot.py — Telegram Command Bot: điều khiển TG Scheduler từ nick chính.

Bot cho phép admin (nick chính) gửi tin từ nick phụ, xem trạng thái,
quản lý campaign, và xử lý handover — tất cả ngay trong Telegram.

Setup: lưu bot_token + admin_user_ids trong Settings →启用 từ UI.
ponytail: v1 — text send + inline keyboards. Thêm media, broadcast, /kol card khi cần.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from telethon import TelegramClient, events, Button
from telethon.tl.types import BotCommand, BotCommandScopeDefault
from telethon.tl.functions.bots import SetBotCommandsRequest

import database as db
import telegram_client as tg

logger = logging.getLogger("tg-scheduler.command_bot")

# ── Bot client & state ─────────────────────────────────────────────────────
_bot: TelegramClient | None = None
_admin_ids: set[int] = set()
_STATES: dict[int, dict] = {}       # user_id → state machine
_STATE_TTL = 300                     # 5 minutes

# ── Templates for quick send ────────────────────────────────────────────────
TEMPLATES = {
    "uid_received": "Team đã nhận UID và thông tin của bạn. Mình sẽ kiểm tra và phản hồi sớm nhất có thể 👍",
    "commission_setup": "Commission của bạn đã được cấu hình xong. Bạn có thể bắt đầu chiến dịch ngay!",
    "welcome": "Chào bạn! Cảm ơn bạn đã quan tâm đến chương trình affiliate. Mình sẽ hỗ trợ bạn chi tiết nhất.",
    "follow_up": "Mình muốn follow up xem bạn còn quan tâm đến chương trình không? Nếu có thắc mắc nào, mình sẵn sàng hỗ trợ!",
    "handoff": "Team mình đã review xong. Partnership manager sẽ liên hệ bạn trong thời gian sớm nhất 👍",
}


# ═══════════════════════════════════════════════════════════════════════════
# AUTH CHECK
# ════════════════════════════════════════════════════════════════════════════

def _is_admin(user_id: int) -> bool:
    return user_id in _admin_ids


def _check_admin(event):
    if not _is_admin(event.sender_id):
        return False
    return True


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _get_state(user_id: int) -> dict | None:
    st = _STATES.get(user_id)
    if st and time.time() - st.get("ts", 0) > _STATE_TTL:
        _STATES.pop(user_id, None)
        return None
    return st


def _set_state(user_id: int, state: dict):
    state["ts"] = time.time()
    _STATES[user_id] = state


def _clear_state(user_id: int):
    _STATES.pop(user_id, None)


async def _get_managed_accounts() -> list[dict]:
    """Get all logged-in managed accounts."""
    accounts = await db.get_all_accounts()
    return [a for a in accounts if a.get("is_logged_in") and not a.get("is_paused")]


async def _get_recent_kol_targets(limit: int = 8) -> list[dict]:
    """Get recent KOLs that have chatted with managed accounts."""
    try:
        chats = await db.get_all_followup_chats(limit=30)
        seen = set()
        results = []
        for c in chats:
            uid = c.get("user_id") or c.get("sender_user_id")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            results.append(c)
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        logger.warning(f"Error fetching recent KOL targets: {e}")
        return []


async def _resolve_entity(account_id: int, target: str):
    """Resolve a username or user_id to a Telegram entity via managed account."""
    client = tg.get_client(account_id)
    if not client or not client.is_connected():
        return None, "Account không kết nối"

    try:
        if target.startswith("@"):
            target = target[1:]
        if target.isdigit():
            entity = await client.get_entity(int(target))
        else:
            entity = await client.get_entity(target)
        return entity, None
    except Exception as e:
        return None, str(e)


async def _log_action(admin_user_id: int, action: str, account_id: int = None,
                      target_username: str = None, message_text: str = None,
                      result: str = "success", details: str = None):
    """Log a command action to DB."""
    try:
        await db.log_command_action(
            admin_user_id=admin_user_id,
            action=action,
            account_id=account_id,
            target_username=target_username,
            message_text=message_text,
            result=result,
            details=details,
        )
    except Exception as e:
        logger.warning(f"Failed to log command action: {e}")


# ════════════════════════════════════════════════════════════════════════════
# BOT COMMANDS
# ════════════════════════════════════════════════════════════════════════════

def _register_handlers(client: TelegramClient):
    """Register all command + callback handlers."""

    # ── /start ──────────────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/start$"))
    async def cmd_start(event):
        if not _check_admin(event):
            return
        _clear_state(event.sender_id)
        accounts = await _get_managed_accounts()
        campaigns = await db.get_all_dm_campaigns()
        running = [c for c in campaigns if c.get("status") == "running"]
        paused_auto = [c for c in campaigns if c.get("status") == "paused_auto"]

        text = (
            f"🎛 **TRUNG TÂM ĐIỀU HÀNH**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Account đang sống: **{len(accounts)}**\n"
            f"🔄 Campaign đang chạy: **{len(running)}**\n"
            f"⏸ Chờ auto-resume: **{len(paused_auto)}**\n"
        )
        buttons = [
            [Button.inline("📤 Gửi tin", data="nav:send"),
             Button.inline("📊 Trạng thái", data="nav:status")],
            [Button.inline("📋 Campaign", data="nav:campaigns"),
             Button.inline("👤 Accounts", data="nav:accounts")],
        ]
        await event.respond(text, parse_mode="md",
                            buttons=buttons)

    # ── /status ─────────────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/status$"))
    async def cmd_status(event):
        if not _check_admin(event):
            return
        await _handle_status(event)

    # ── /send [target] ─────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/send(?:\s+(.+))?"))
    async def cmd_send(event):
        if not _check_admin(event):
            return
        target = event.pattern_match.group(1)
        if target:
            target = target.strip()
        await _start_send_flow(event, target)

    # ── /accounts ───────────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/accounts$"))
    async def cmd_accounts(event):
        if not _check_admin(event):
            return
        await _handle_accounts(event)

    # ── /campaigns ──────────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/campaigns$"))
    async def cmd_campaigns(event):
        if not _check_admin(event):
            return
        await _handle_campaigns(event)

    # ── /kol @username ─────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/kol\s+(@?\w+)"))
    async def cmd_kol(event):
        if not _check_admin(event):
            return
        username = event.pattern_match.group(1)
        await _handle_kol_info(event, username)

    # ── /resume <id> ───────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/resume\s+(\d+)"))
    async def cmd_resume(event):
        if not _check_admin(event):
            return
        cid = int(event.pattern_match.group(1))
        await _handle_resume(event, cid)

    # ── /pause <id> ────────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/pause\s+(\d+)"))
    async def cmd_pause(event):
        if not _check_admin(event):
            return
        cid = int(event.pattern_match.group(1))
        await _handle_pause(event, cid)

    # ── /ai-on | /aion @username / /ai-off | /aioff @username ───────────────
    @client.on(events.NewMessage(pattern=r"/ai-?(on|off)\s+(@?\w+)"))
    async def cmd_ai_toggle(event):
        if not _check_admin(event):
            return
        action = event.pattern_match.group(1)
        username = event.pattern_match.group(2)
        await _handle_ai_toggle(event, action, username)

    # ── /check @account ────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r"/check\s+@?(\w+)"))
    async def cmd_check(event):
        if not _check_admin(event):
            return
        name = event.pattern_match.group(1)
        await _handle_check_account(event, name)

    # ── Plain text in send flow ─────────────────────────────────────────────
    @client.on(events.NewMessage(func=lambda e: e.is_private and not e.raw_text.startswith("/")))
    async def on_plain_text(event):
        if not _check_admin(event):
            return
        st = _get_state(event.sender_id)
        if not st:
            return
        step = st.get("step")
        if step == "type_message":
            msg = event.raw_text.strip()
            if not msg:
                await event.respond("⚠️ Tin nhắn không được rỗng.")
                return
            st["message"] = msg
            st["step"] = "confirm"
            _set_state(event.sender_id, st)
            await _show_confirm(event, st)
        elif step == "select_target_new":
            target = event.raw_text.strip()
            if not target.startswith("@"):
                target = "@" + target
            st["target"] = target
            st["step"] = "type_message"
            _set_state(event.sender_id, st)
            acc = next((a for a in await _get_managed_accounts()
                        if a["id"] == st["account_id"]), None)
            acc_name = acc["name"] if acc else f"Acc #{st['account_id']}"
            await event.respond(
                f"📝 Nhập nội dung tin nhắn:\n\n"
                f"📤 Từ: **{acc_name}** → 🎯 **{target}**\n\n"
                f"Gõ tin nhắn cần gửi (gửi 1 tin nhắn duy nhất):",
                parse_mode="md"
            )

    # ── Callback queries (inline buttons) ───────────────────────────────────
    @client.on(events.CallbackQuery)
    async def on_callback(event):
        if not _is_admin(event.sender_id):
            await event.answer("⛔ Không có quyền.", alert=True)
            return

        data = event.data.decode("utf-8") if event.data else ""
        uid = event.sender_id

        try:
            # ── Navigation ──
            if data == "nav:send":
                await event.delete()
                await _start_send_flow(event, None)
                return

            if data == "nav:status":
                await event.delete()
                await _handle_status(event)
                return

            if data == "nav:campaigns":
                await event.delete()
                await _handle_campaigns(event)
                return

            if data == "nav:accounts":
                await event.delete()
                await _handle_accounts(event)
                return

            if data == "nav:home":
                await event.delete()
                # Re-trigger /start
                await cmd_start(event)
                return

            # ── Send flow: select account ──
            if data.startswith("sel_acc:"):
                acc_id = int(data.split(":")[1])
                st = _get_state(uid) or {}
                st["account_id"] = acc_id
                st["step"] = "select_target"
                _set_state(uid, st)
                await _show_target_picker(event, st)
                return

            # ── Send flow: select target ──
            if data == "sel_tgt:new":
                st = _get_state(uid)
                if st:
                    st["step"] = "select_target_new"
                    _set_state(uid, st)
                    acc = next((a for a in await _get_managed_accounts()
                                if a["id"] == st["account_id"]), None)
                    acc_name = acc["name"] if acc else f"Acc #{st['account_id']}"
                    await event.edit(
                        f"📝 Nhập username KOL cần gửi tin:\n\n"
                        f"📤 Từ: **{acc_name}**\n"
                        f"🎯 Gửi đến: (gõ @username hoặc user_id)",
                        parse_mode="md"
                    )
                return

            if data.startswith("sel_tgt:"):
                parts = data.split(":")
                target = parts[1]
                if target == "recent":
                    target = parts[2]  # @username
                st = _get_state(uid)
                if st:
                    st["target"] = target
                    st["step"] = "type_message"
                    _set_state(uid, st)
                    acc = next((a for a in await _get_managed_accounts()
                                if a["id"] == st["account_id"]), None)
                    acc_name = acc["name"] if acc else f"Acc #{st['account_id']}"
                    await event.edit(
                        f"📝 Nhập nội dung tin nhắn:\n\n"
                        f"📤 Từ: **{acc_name}** → 🎯 **{target}**\n\n"
                        f"Gõ tin nhắn cần gửi:",
                        parse_mode="md"
                    )
                return

            # ── Send flow: template ──
            if data.startswith("tpl:"):
                tpl_key = data.split(":", 1)[1]
                st = _get_state(uid)
                if st and tpl_key in TEMPLATES:
                    st["message"] = TEMPLATES[tpl_key]
                    st["step"] = "confirm"
                    _set_state(uid, st)
                    await _show_confirm(event, st)
                elif tpl_key == "custom":
                    st["step"] = "type_message"
                    _set_state(uid, st)
                    await event.edit("📝 Gõ nội dung tin nhắn tùy chỉnh:")
                return

            # ── Send flow: confirm / cancel / edit ──
            if data == "send:confirm":
                st = _get_state(uid)
                if st:
                    await _execute_send(event, st)
                return

            if data == "send:edit":
                st = _get_state(uid)
                if st:
                    st["step"] = "type_message"
                    _set_state(uid, st)
                    await event.edit("📝 Gõ lại nội dung tin nhắn:")
                return

            if data == "send:cancel":
                _clear_state(uid)
                await event.edit("❌ Đã hủy gửi tin.")
                return

            # ── Campaign actions ──
            if data.startswith("camp_resume:"):
                cid = int(data.split(":")[1])
                await _handle_resume(event, cid)
                return

            if data.startswith("camp_pause:"):
                cid = int(data.split(":")[1])
                await _handle_pause(event, cid)
                return

            if data.startswith("camp_detail:"):
                cid = int(data.split(":")[1])
                await _handle_campaign_detail(event, cid)
                return

            # ── Unknown callback ──
            await event.answer()

        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            await event.answer("❌ Có lỗi xảy ra.", alert=True)


# ════════════════════════════════════════════════════════════════════════════
# FLOW IMPLEMENTATIONS
# ════════════════════════════════════════════════════════════════════════════

async def _start_send_flow(event, target: str | None):
    """Step 1: Pick account."""
    uid = event.sender_id if hasattr(event, "sender_id") else event.sender_id
    accounts = await _get_managed_accounts()
    if not accounts:
        if hasattr(event, "respond"):
            await event.respond("⛔ Không có account nào đang kết nối.")
        return

    state = {"step": "select_account", "target": target}
    _set_state(uid, state)

    buttons = []
    row = []
    for a in accounts:
        premium = "⭐" if a.get("is_premium") else ""
        name = a.get("name", f"Acc #{a['id']}")
        me = tg._me_cache.get(a["id"]) or {}
        uname = me.get("username") or ""
        label = f"{premium}{name}" + (f" @{uname}" if uname else "")
        row.append(Button.inline(label, data=f"sel_acc:{a['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([Button.inline("❌ Hủy", data="send:cancel")])

    text = "📤 **Chọn account gửi tin:**\n"
    if target:
        text += f"🎯 Gửi đến: **{target}** (chọn account rồi nhập nội dung)"

    if hasattr(event, "respond"):
        await event.respond(text, parse_mode="md",
                            buttons=buttons)
    elif hasattr(event, "edit"):
        await event.edit(text, parse_mode="md",
                         buttons=buttons)


async def _show_target_picker(event, state: dict):
    """Step 2: Pick target KOL."""
    # If target already set (from /send @username), skip to message
    if state.get("target"):
        state["step"] = "type_message"
        _set_state(event.sender_id, state)
        acc = next((a for a in await _get_managed_accounts()
                    if a["id"] == state["account_id"]), None)
        acc_name = acc["name"] if acc else f"Acc #{state['account_id']}"
        if hasattr(event, "edit"):
            await event.edit(
                f"📝 Nhập nội dung tin nhắn:\n\n"
                f"📤 Từ: **{acc_name}** → 🎯 **{state['target']}**\n\n"
                f"Gõ tin nhắn cần gửi:",
                parse_mode="md"
            )
        else:
            await event.respond(
                f"📝 Nhập nội dung tin nhắn:\n\n"
                f"📤 Từ: **{acc_name}** → 🎯 **{state['target']}**\n\n"
                f"Gõ tin nhắn cần gửi:",
                parse_mode="md"
            )
        return

    recent = await _get_recent_kol_targets()
    buttons = []

    # Recent KOL targets
    if recent:
        for r in recent[:6]:
            username = r.get("username") or r.get("sender_username") or ""
            uid = r.get("user_id") or r.get("sender_user_id") or ""
            name = r.get("name") or r.get("sender_name") or username or f"User #{uid}"
            acc_name = r.get("account_name", "")
            label = f"@{username}" if username else f"ID:{uid}"
            if name and name != username:
                label = f"{name} ({label})"
            if acc_name:
                label += f" · {acc_name}"
            bdata = f"sel_tgt:recent:@{username}" if username else f"sel_tgt:recent:{uid}"
            buttons.append([Button.inline(label, data=bdata)])

    buttons.append([Button.inline("✏️ Nhập username mới", data="sel_tgt:new")])
    buttons.append([Button.inline("❌ Hủy", data="send:cancel")])

    text = "🎯 **Chọn KOL gửi tin đến:**\n"
    if recent:
        text += "\n_Gần đây có chat:_\n"
    else:
        text += "\n_Chưa có lịch sử chat. Nhập username mới._\n"

    if hasattr(event, "edit"):
        await event.edit(text, parse_mode="md",
                         buttons=buttons)
    else:
        await event.respond(text, parse_mode="md",
                            buttons=buttons)


async def _show_confirm(event, state: dict):
    """Step 4: Preview and confirm."""
    uid = event.sender_id
    acc = next((a for a in await _get_managed_accounts()
                if a["id"] == state["account_id"]), None)
    acc_name = acc["name"] if acc else f"Acc #{state['account_id']}"
    target = state.get("target", "?")
    message = state.get("message", "")

    text = (
        f"🔍 **Xác nhận gửi tin nhắn:**\n\n"
        f"📤 Từ: **{acc_name}**\n"
        f"🎯 Đến: **{target}**\n"
        f"💬 Nội dung:\n"
        f"_{message}_\n\n"
        f"Sẽ gửi ngay khi bạn bấm ✅"
    )
    buttons = [
        [Button.inline("✅ Gửi ngay", data="send:confirm"),
         Button.inline("✏️ Sửa lại", data="send:edit")],
        [Button.inline("❌ Hủy", data="send:cancel")],
    ]

    if hasattr(event, "edit"):
        await event.edit(text, parse_mode="md",
                         buttons=buttons)
    else:
        await event.respond(text, parse_mode="md",
                            buttons=buttons)


async def _execute_send(event, state: dict):
    """Actually send the message via managed account."""
    uid = event.sender_id
    account_id = state["account_id"]
    target = state["target"].lstrip("@")
    message = state["message"]

    await event.edit("⏳ **Đang gửi...**", parse_mode="md")

    client = tg.get_client(account_id)
    if not client or not client.is_connected():
        await event.edit("⛔ Account không kết nối. Thử lại sau.")
        _clear_state(uid)
        await _log_action(uid, "send", account_id, target, message,
                          "error", "Account not connected")
        return

    try:
        entity = await client.get_entity(target)
        sent_msg = await client.send_message(entity, message)
        _clear_state(uid)

        acc = next((a for a in await _get_managed_accounts()
                    if a["id"] == account_id), None)
        acc_name = acc["name"] if acc else f"Acc #{account_id}"
        ts = datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")

        await event.edit(
            f"✅ **Đã gửi thành công!**\n\n"
            f"📤 Từ: **{acc_name}**\n"
            f"🎯 Đến: **{target}**\n"
            f"🕐 Thời gian: {ts}\n"
            f"💬 _{message[:80]}{'...' if len(message) > 80 else ''}_\n\n"
            f"[📤 Gửi tin khác](/send) · [🏠 Trung tâm](/start)",
            parse_mode="md"
        )
        await _log_action(uid, "send", account_id, target, message, "success")

    except Exception as e:
        error_msg = str(e)
        if "FloodWaitError" in error_msg:
            error_msg = "⏳ Telegram yêu cầu chờ (FloodWait). Thử lại sau."
        elif "PeerFlood" in error_msg:
            error_msg = "🚨 PeerFlood — account đang bị giới hạn."
        elif "UserPrivacyRestricted" in error_msg:
            error_msg = "🔒 Người nhận chặn tin nhắn từ stranger."
        elif "USER_NOT_FOUND" in error_msg or "UsernameNotOccupied" in error_msg:
            error_msg = "❌ Username không tồn tại."

        _clear_state(uid)
        await event.edit(f"❌ **Gửi thất bại:**\n{error_msg}")
        await _log_action(uid, "send", account_id, target, message,
                          "error", error_msg)
        logger.error(f"Send failed: {e}", exc_info=True)


# ════════════════════════════════════════════════════════════════════════════
# STATUS / INFO COMMANDS
# ════════════════════════════════════════════════════════════════════════════

async def _handle_status(event):
    accounts = await _get_managed_accounts()
    all_accounts = await db.get_all_accounts()
    campaigns = await db.get_all_dm_campaigns()
    running = [c for c in campaigns if c.get("status") == "running"]
    paused_auto = [c for c in campaigns if c.get("status") == "paused_auto"]
    paused = [c for c in campaigns if c.get("status") in ("paused", "error")]
    draft = [c for c in campaigns if c.get("status") == "draft"]

    lines = [
        "📊 **TRẠNG THÁI HỆ THỐNG**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Account: **{len(accounts)}**/{len(all_accounts)} đang sống",
        f"🔄 Campaign đang chạy: **{len(running)}**",
        f"⏸  Chờ auto-resume: **{len(paused_auto)}**",
        f"⏹  Tạm dừng/lỗi: **{len(paused)}**",
        f"📝 Draft: **{len(draft)}**",
        "",
    ]

    # Campaign details
    if running:
        lines.append("━━━ **ĐANG CHẠY** ━━━")
        for c in running[:5]:
            sent = c.get("sent_count", 0)
            total = c.get("total_targets", 0)
            pct = f"{sent}/{total}" if total else "?"
            lines.append(f"  #{c['id']} {c['name']} ({pct})")

    if paused_auto:
        lines.append("\n⏸ **CHỜ MỞ KHÓA**")
        for c in paused_auto[:5]:
            lines.append(f"  #{c['id']} {c['name']}")

    # Account health
    flagged = [a for a in all_accounts if a.get("is_flagged") or a.get("is_paused")]
    if flagged:
        lines.append(f"\n🚨 **Account cần chú ý: {len(flagged)}**")
        for a in flagged[:3]:
            reason = a.get("flag_reason") or a.get("pause_reason") or "?"
            lines.append(f"  {a['name']}: {reason[:40]}")

    buttons = [
        [Button.inline("🔄 Reload", data="nav:status"),
         Button.inline("📋 Campaigns", data="nav:campaigns")],
        [Button.inline("🏠 Trung tâm", data="nav:home")],
    ]

    text = "\n".join(lines)
    if hasattr(event, "edit"):
        await event.edit(text, parse_mode="md",
                         buttons=buttons)
    else:
        await event.respond(text, parse_mode="md",
                            buttons=buttons)


async def _handle_accounts(event):
    all_accounts = await db.get_all_accounts()
    lines = ["👤 **DANH SÁCH ACCOUNTS**", "━━━━━━━━━━━━━━━━━━━━━━━━"]

    for a in all_accounts:
        status = "🟢" if a.get("is_logged_in") else "🔴"
        if a.get("is_paused"):
            status = "⏸"
        if a.get("is_flagged"):
            status = "🚨"
        premium = "⭐" if a.get("is_premium") else ""
        peerflood = ""
        if a.get("peerflood_until", 0) > time.time():
            peerflood = " ⛔Flood"
        lines.append(f"{status} **{a['name']}**{premium}{peerflood}")

    buttons = [
        [Button.inline("🔍 Check SpamBot", data="nav:check_prompt"),
         Button.inline("🔄 Reload", data="nav:accounts")],
        [Button.inline("🏠 Trung tâm", data="nav:home")],
    ]

    await event.respond("\n".join(lines), parse_mode="md",
                        buttons=buttons)


async def _handle_campaigns(event):
    campaigns = await db.get_all_dm_campaigns()
    status_icon = {
        "running": "🟢", "paused_auto": "⏸", "paused": "⏹",
        "error": "❌", "draft": "📝", "completed": "✅", "scheduled": "📅",
    }
    lines = ["📋 **CAMPAIGNS**", "━━━━━━━━━━━━━━━━━━━━━━━━"]

    for c in campaigns[:12]:
        icon = status_icon.get(c.get("status", ""), "?")
        sent = c.get("sent_count", 0)
        total = c.get("total_targets", 0)
        pct = f"{sent}/{total}" if total else "?"
        lines.append(f"{icon} #{c['id']} {c['name']} ({pct})")

    if not campaigns:
        lines.append("_Chưa có campaign nào._")

    buttons = [
        [Button.inline("🔄 Reload", data="nav:campaigns")],
        [Button.inline("🏠 Trung tâm", data="nav:home")],
    ]

    await event.respond("\n".join(lines), parse_mode="md",
                        buttons=buttons)


async def _handle_campaign_detail(event, cid: int):
    try:
        data = await db.get_dm_campaign(cid)
        c = data.get("campaign") or data
        status_icon = {
            "running": "🟢", "paused_auto": "⏸", "paused": "⏹",
            "error": "❌", "draft": "📝", "completed": "✅",
        }
        icon = status_icon.get(c.get("status", ""), "?")
        sent = c.get("sent_count", 0)
        total = c.get("total_targets", 0)
        failed = c.get("failed_count", 0)

        text = (
            f"{icon} **#{c['id']} {c['name']}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Trạng thái: **{c.get('status', '?')}**\n"
            f"📤 Đã gửi: **{sent}**/{total}\n"
            f"❌ Lỗi: **{failed}**\n"
            f"⏱ Delay: {c.get('delay_min', 30)}-{c.get('delay_max', 90)}s\n"
            f"🤖 AI Remix: {'Bật' if c.get('use_ai_remix') else 'Tắt'}\n"
        )

        buttons = []
        if c.get("status") in ("paused", "paused_auto", "draft", "error"):
            buttons.append([Button.inline("▶️ Resume", data=f"camp_resume:{cid}")])
        if c.get("status") == "running":
            buttons.append([Button.inline("⏸ Pause", data=f"camp_pause:{cid}")])
        buttons.append([Button.inline("🏠 Trung tâm", data="nav:home")])

        await event.respond(text, parse_mode="md",
                            buttons=buttons)
    except Exception as e:
        await event.respond(f"❌ Không tìm thấy campaign #{cid}: {e}")


async def _handle_kol_info(event, username: str):
    """Show KOL info from recent chats."""
    try:
        username_clean = username.lstrip("@")
        chats = await db.get_all_followup_chats()
        matches = [c for c in chats
                   if ((c.get("username") or c.get("sender_username") or "").lower() == username_clean.lower())]

        if not matches:
            await event.respond(f"❌ Không tìm thấy thông tin cho @{username_clean}")
            return

        c = matches[0]
        name = c.get("name") or c.get("sender_name") or username_clean
        text = (
            f"👤 **KOL: {name}** (@{username_clean})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💬 Nick phụ chat: **{c.get('account_name', '?')}**\n"
            f"📊 Trạng thái: **{c.get('status', '?')}**\n"
            f"🕐 Tin cuối: {c.get('updated_at', '?')}\n"
        )

        # Campaign info if any
        campaign_id = c.get("campaign_id")
        if campaign_id:
            try:
                camp_data = await db.get_dm_campaign(campaign_id)
                camp = camp_data.get("campaign") or camp_data
                text += f"📋 Campaign: #{campaign_id} {camp.get('name', '?')}\n"
            except Exception:
                pass

        buttons = [
            [Button.inline("📤 Gửi tin", data=f"sel_tgt:recent:@{username_clean}")],
            [Button.inline("🏠 Trung tâm", data="nav:home")],
        ]

        await event.respond(text, parse_mode="md",
                            buttons=buttons)
    except Exception as e:
        await event.respond(f"❌ Lỗi: {e}")


async def _handle_resume(event, cid: int):
    try:
        await db.update_dm_campaign_status(cid, "running")
        # Trigger campaign run
        from routes.members import _run_campaign, _active_campaigns
        import asyncio
        _active_campaigns[cid] = asyncio.create_task(_run_campaign(cid))
        text = f"▶️ Đã resume campaign #{cid}"
        logger.info(f"Command bot: resumed campaign #{cid}")
    except Exception as e:
        text = f"❌ Resume thất bại #{cid}: {e}"

    if hasattr(event, "respond"):
        await event.respond(text)
    elif hasattr(event, "edit"):
        await event.edit(text)


async def _handle_pause(event, cid: int):
    try:
        await db.update_dm_campaign_status(cid, "paused")
        from routes.members import _active_campaigns
        task = _active_campaigns.pop(cid, None)
        if task and isinstance(task, asyncio.Task):
            task.cancel()
        text = f"⏸ Đã pause campaign #{cid}"
        logger.info(f"Command bot: paused campaign #{cid}")
    except Exception as e:
        text = f"❌ Pause thất bại #{cid}: {e}"

    if hasattr(event, "respond"):
        await event.respond(text)
    elif hasattr(event, "edit"):
        await event.edit(text)


async def _handle_ai_toggle(event, action: str, username: str):
    try:
        username_clean = username.lstrip("@")
        new_status = "active" if action == "on" else "paused_admin"
        chats = await db.get_all_followup_chats()
        matches = [c for c in chats
                   if ((c.get("username") or c.get("sender_username") or "").lower() == username_clean.lower())]
        if not matches:
            await event.respond(f"❌ Không tìm thấy @{username_clean}")
            return
        uid = matches[0].get("user_id") or matches[0].get("sender_user_id")
        aid = matches[0].get("account_id")
        if uid and aid:
            await db.update_followup_chat_status(aid, uid, new_status)
            label = "Bật" if action == "on" else "Tắt"
            await event.respond(f"✅ Đã {label} AI cho @{username_clean}")
            await _log_action(event.sender_id, f"ai_{action}", aid,
                              username_clean, result="success")
        else:
            await event.respond(f"❌ Thiếu thông tin cho @{username_clean}")
    except Exception as e:
        await event.respond(f"❌ Lỗi: {e}")


async def _handle_check_account(event, name: str):
    """Check SpamBot status for an account."""
    all_accounts = await db.get_all_accounts()
    acc = next((a for a in all_accounts
                if name.lower() in (a.get("name", "").lower(), a.get("phone", ""))), None)
    if not acc:
        await event.respond(f"❌ Không tìm thấy account '{name}'")
        return

    client = tg.get_client(acc["id"])
    if not client or not client.is_connected():
        await event.respond(f"🔴 {acc['name']}: không kết nối")
        return

    try:
        from telegram_client import check_spam_status
        result = await check_spam_status(acc["id"])
        status = result.get("status", "unknown")
        message = result.get("message", "")
        icon = {"free": "🟢", "limited": "🟡", "unknown": "⚪"}.get(status, "⚪")
        await event.respond(
            f"{icon} **{acc['name']}**\n"
            f"Trạng thái: **{status}**\n"
            f"Chi tiết: {message}"
        )
    except Exception as e:
        await event.respond(f"❌ Check thất bại {acc['name']}: {e}")


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC API: Send handover alert to bot admin
# ════════════════════════════════════════════════════════════════════════════

async def send_handover_alert(account_name: str, sender_username: str,
                              sender_name: str, reason: str,
                              campaign_name: str = None, ai_message: str = None):
    """Push a handover notification to the admin via the command bot.
    Called from dm_reply_tracker._notify_main_account_handover.
    """
    if not _bot or not _bot.is_connected():
        return False

    for admin_id in _admin_ids:
        try:
            text = (
                f"🤝 **HANDOVER KOL**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 KOL: **{sender_name}** (@{sender_username})\n"
                f"💬 Nick phụ: **{account_name}**\n"
                f"📋 Campaign: {campaign_name or '?'}\n"
                f"📝 Lý do: {reason}\n"
            )
            if ai_message:
                short_msg = ai_message[:200] + ("..." if len(ai_message) > 200 else "")
                text += f"\n🤖 AI nhận định:\n_{short_msg}_\n"

            buttons = [
                [Button.inline("📤 Gửi tin", data=f"sel_tgt:recent:@{sender_username}")],
                [Button.inline("👤 Xem KOL", data=f"nav:home")],
            ]

            await _bot.send_message(
                admin_id, text, parse_mode="md",
                buttons=buttons
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to send handover alert to {admin_id}: {e}")
    return False


# ════════════════════════════════════════════════════════════════════════════
# START / STOP
# ════════════════════════════════════════════════════════════════════════════

async def start_command_bot():
    """Start the command bot if configured and enabled."""
    global _bot, _admin_ids

    try:
        enabled = await db.get_setting("command_bot_enabled", "0")
        if enabled != "1":
            logger.info("[CommandBot] Disabled (set command_bot_enabled=1 in settings)")
            return False

        token = await db.get_setting("command_bot_token", "")
        if not token:
            logger.warning("[CommandBot] No bot token configured")
            return False

        admin_ids_str = await db.get_setting("command_bot_admin_ids", "")
        if not admin_ids_str:
            logger.warning("[CommandBot] No admin user IDs configured")
            return False
        _admin_ids = {int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()}

        # Get API credentials from any logged-in account
        accounts = await db.get_all_accounts()
        logged_in = [a for a in accounts if a.get("is_logged_in")]
        if not logged_in:
            logger.warning("[CommandBot] No logged-in accounts for API credentials")
            return False

        api_id = int(logged_in[0]["api_id"])
        api_hash = logged_in[0]["api_hash"]

        # Create and start bot client
        import os
        session_dir = os.path.join(os.path.dirname(__file__), "sessions")
        bot_session = os.path.join(session_dir, "command_bot")

        # Nếu token đã đổi so với lần trước → xóa session cũ để Telethon
        # login lại đúng bot mới (session cũ giữ auth của bot cũ, token mới bị bỏ qua)
        token_marker = bot_session + ".token_id"
        token_id = token.split(":")[0]  # bot id, không phải secret
        prev_token_id = None
        try:
            if os.path.exists(token_marker):
                with open(token_marker, "r", encoding="utf-8") as f:
                    prev_token_id = f.read().strip()
        except Exception:
            pass
        if prev_token_id and prev_token_id != token_id:
            for ext in (".session", ".session-journal"):
                p = bot_session + ext
                for _ in range(5):
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                            logger.info(f"[CommandBot] Token changed — removed stale session {p}")
                        break
                    except Exception as e:
                        logger.warning(f"[CommandBot] Could not remove stale session (retrying): {e}")
                        await asyncio.sleep(0.3)
        try:
            os.makedirs(session_dir, exist_ok=True)
            with open(token_marker, "w", encoding="utf-8") as f:
                f.write(token_id)
        except Exception:
            pass

        _bot = TelegramClient(bot_session, api_id, api_hash)
        await _bot.start(bot_token=token)

        # Register handlers
        _register_handlers(_bot)

        # Set bot commands menu
        try:
            await _bot(SetBotCommandsRequest(
                scope=BotCommandScopeDefault(),
                lang_code="",
                commands=[
                    BotCommand("start", "🎛 Mở trung tâm điều khiển"),
                    BotCommand("status", "📊 Xem trạng thái hệ thống"),
                    BotCommand("send", "📤 Gửi tin nhắn từ nick phụ"),
                    BotCommand("accounts", "👤 Danh sách accounts"),
                    BotCommand("campaigns", "📋 Danh sách campaigns"),
                    BotCommand("kol", "🔍 Xem thông tin KOL (@username)"),
                    BotCommand("resume", "▶️ Resume campaign (ID)"),
                    BotCommand("pause", "⏸ Pause campaign (ID)"),
                    BotCommand("aion", "🤖 Bật AI cho KOL (@username)"),
                    BotCommand("aioff", "🤖 Tắt AI cho KOL (@username)"),
                    BotCommand("check", "🔍 Check SpamBot account (@name)"),
                ]
            ))
        except Exception as e:
            logger.warning(f"Failed to set bot commands: {e}")

        me = await _bot.get_me()
        logger.info(f"[CommandBot] Started as @{me.username} (admins: {_admin_ids})")
        return True

    except Exception as e:
        logger.error(f"[CommandBot] Failed to start: {e}", exc_info=True)
        return False


def is_running() -> bool:
    """True if the bot client is live and connected."""
    return _bot is not None and _bot.is_connected()


async def stop_command_bot():
    """Gracefully stop the command bot."""
    global _bot
    if _bot and _bot.is_connected():
        await _bot.disconnect()
        logger.info("[CommandBot] Stopped")
    _bot = None
    _admin_ids.clear()
    _STATES.clear()
