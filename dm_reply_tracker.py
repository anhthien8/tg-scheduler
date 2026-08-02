"""
dm_reply_tracker.py
───────────────────
Listens for incoming private messages on ALL logged-in accounts.
When a private message arrives from a user who was previously DM'd
by any keyword watcher (status='success'), it is recorded as a
"hot lead reply" in the dm_replies table.

Messages from users who were never DM'd are also stored so no
conversation is lost (watcher_id = NULL in that case).

Dedup key: (account_id, sender_user_id, message_id)  — stored in-memory
to prevent double-insertion within a session.
"""

from __future__ import annotations

import asyncio
import logging
import random
import json
from typing import Any

from telethon import events

import database as db
import telegram_client as tg
import ai_remix as ai_rmx

logger = logging.getLogger("tg-scheduler.inbox")

# ── In-memory state ────────────────────────────────────────────────────────────
# handler_removers: account_id → (client, handler_fn)
_handler_removers: dict[int, tuple[Any, Any]] = {}

# Dedup set: (account_id, sender_user_id, message_id)
_seen: set[tuple[int, int, int]] = set()
_MAX_SEEN = 5000  # cap to prevent unbounded growth


def _trim_seen() -> None:
    global _seen
    if len(_seen) > _MAX_SEEN:
        to_remove = list(_seen)[: _MAX_SEEN // 2]
        for item in to_remove:
            _seen.discard(item)


# ── Core handler factory ───────────────────────────────────────────────────────

def _make_handler(account_id: int):
    """Return a Telethon event handler for private incoming messages on account_id."""

    async def _handler(event: events.NewMessage.Event) -> None:
        # Only care about private (1-to-1) chats
        if not event.is_private:
            return

        sender = await event.get_sender()
        if sender is None:
            return

        # Ignore messages from ourselves (Saved Messages)
        client = tg.get_client(account_id)
        if client is None:
            return
        try:
            me = await client.get_me()
            if me and sender.id == me.id:
                return
        except Exception:
            pass

        sender_id = sender.id
        msg_id = event.id

        # Dedup
        key = (account_id, sender_id, msg_id)
        if key in _seen:
            return
        _seen.add(key)
        _trim_seen()

        # Build sender name
        first = getattr(sender, "first_name", "") or ""
        last  = getattr(sender, "last_name",  "") or ""
        sender_name     = (first + " " + last).strip() or None
        sender_username = getattr(sender, "username", None)
        message_text    = event.raw_text or ""

        # Check if this sender was previously DM'd by any watcher
        watcher_id = await db.find_watcher_id_for_user(sender_id)

        logger.info(
            "[Inbox] acc=%d ← @%s (id=%d) | watcher=%s | %s",
            account_id,
            sender_username or "?",
            sender_id,
            watcher_id or "none",
            repr(message_text[:60]),
        )

        await db.add_dm_reply({
            "account_id":      account_id,
            "sender_user_id":  sender_id,
            "sender_username": sender_username,
            "sender_name":     sender_name,
            "message_text":    message_text,
            "watcher_id":      watcher_id,
        })

        # ── Smart Template Rotation: record reply for variant performance ──
        try:
            import template_rotation as tmpl_rot
            # Check campaign logs first
            campaign_log = await db.find_campaign_log_for_user(sender_id)
            if campaign_log and campaign_log.get("template_variant_id"):
                await tmpl_rot.record_reply(
                    template_id=campaign_log["template_variant_id"],
                    variant_index=campaign_log.get("template_variant_index", 0),
                    campaign_id=campaign_log.get("campaign_id"),
                )
            else:
                # Check watcher logs
                watcher_log = await db.find_watcher_log_for_user(sender_id)
                if watcher_log and watcher_log.get("template_variant_index") is not None:
                    # For watcher DMs, we need the watcher's template_id
                    # which is the watcher_id itself (each watcher has its own messages)
                    w_id = watcher_log.get("watcher_id")
                    if w_id:
                        await tmpl_rot.record_reply(
                            template_id=w_id,
                            variant_index=watcher_log["template_variant_index"],
                            watcher_id=w_id,
                        )
        except Exception as _tr_err:
            logger.debug("[Inbox] Template rotation reply tracking error: %s", _tr_err)

        # ── AI Follow-Up Sales Agent Engine ──
        try:
            enabled_str = await db.get_setting("ai_followup_enabled", "true")
            if enabled_str.lower() in ("true", "1"):
                # Get or create chat session
                chat = await db.get_or_create_followup_chat(
                    account_id=account_id,
                    user_id=sender_id,
                    username=sender_username,
                    name=sender_name,
                    watcher_id=watcher_id
                )

                current_status = chat.get("status", "active")
                if current_status in ("paused_admin", "onboarded", "needs_human"):
                    logger.info("[AIFollowUp] User %d chat status is '%s' — skipping AI reply", sender_id, current_status)
                else:
                    # Append incoming user message
                    chat = await db.append_followup_chat_message(account_id, sender_id, "user", message_text)

                    # Check handover keywords
                    handover_raw = await db.get_setting("ai_followup_handover_keywords", '["gặp admin", "tư vấn viên", "số điện thoại"]')
                    try:
                        handover_kws = json.loads(handover_raw) if handover_raw else []
                    except Exception:
                        handover_kws = []

                    msg_lower = message_text.lower().strip()
                    needs_handover = any(kw.lower().strip() in msg_lower for kw in handover_kws if kw.strip())

                    max_replies_str = await db.get_setting("ai_followup_max_replies", "5")
                    max_replies = int(max_replies_str) if max_replies_str.isdigit() else 5

                    if needs_handover:
                        logger.info("[AIFollowUp] Handover keyword matched for user %d — setting status to 'needs_human'", sender_id)
                        await db.update_followup_chat_status(account_id, sender_id, "needs_human")
                    elif chat.get("reply_count", 0) >= max_replies:
                        logger.info("[AIFollowUp] Max replies (%d) reached for user %d — setting status to 'needs_human'", max_replies, sender_id)
                        await db.update_followup_chat_status(account_id, sender_id, "needs_human")
                    else:
                        # Prepare system prompt + KB
                        sys_prompt = await db.get_setting("ai_followup_system_prompt", "")
                        kb = await db.get_setting("ai_followup_knowledge_base", "")
                        combined_prompt = sys_prompt
                        if kb and kb.strip():
                            combined_prompt += "\n\n--- KNOWLEDGE BASE ---\n" + kb.strip()

                        async def _load_provider_keys(prov):
                            if not prov:
                                return []
                            try:
                                raw = await db.get_setting(f"ai_keys_{prov}", "[]")
                                return json.loads(raw) if raw else []
                            except Exception:
                                return []

                        ai_provider = await db.get_setting("ai_provider", "gemini")
                        ai_keys = await _load_provider_keys(ai_provider)

                        # Auto-fallback to any provider with keys if selected provider has no keys
                        if not ai_keys:
                            all_providers = ["gemini", "groq", "openai", "deepseek", "openai_compatible"]
                            for alt_prov in all_providers:
                                alt_keys = await _load_provider_keys(alt_prov)
                                if alt_keys:
                                    logger.warning(
                                        "[AIFollowUp] Provider '%s' missing keys, falling back to '%s' (%d keys)",
                                        ai_provider, alt_prov, len(alt_keys)
                                    )
                                    ai_provider = alt_prov
                                    ai_keys = alt_keys
                                    break

                        if not ai_keys:
                            logger.warning(
                                "[AIFollowUp] ⚠️ Không thể tạo phản hồi AI cho user %d: Chưa có API Key nào được cài đặt!",
                                sender_id
                            )

                        if ai_keys:
                            history = chat.get("history", [])
                            try:
                                ai_reply = await ai_rmx.generate_chat_response(history, combined_prompt, ai_provider, ai_keys)
                            except Exception as ex_gen:
                                logger.error("[AIFollowUp] AI generate_chat_response error for user %d: %s", sender_id, ex_gen)
                                ai_reply = None

                            if ai_reply:
                                # Check for tags
                                new_status = "active"
                                if "[HANDOVER_REQUIRED]" in ai_reply:
                                    new_status = "needs_human"
                                    ai_reply = ai_reply.replace("[HANDOVER_REQUIRED]", "").strip()
                                elif "[ONBOARDED]" in ai_reply:
                                    new_status = "onboarded"
                                    ai_reply = ai_reply.replace("[ONBOARDED]", "").strip()

                                if ai_reply:
                                    # Human-like delay + typing indicator
                                    delay = random.uniform(6.0, 15.0)
                                    logger.info("[AIFollowUp] Replying to user %d (acc=%d) in %.1fs...", sender_id, account_id, delay)
                                    try:
                                        await client.action(sender_id, "typing")
                                    except Exception:
                                        pass

                                    await asyncio.sleep(delay)

                                    try:
                                        await client.send_message(sender_id, ai_reply)
                                        await db.append_followup_chat_message(
                                            account_id, sender_id, "assistant", ai_reply, inc_reply_count=True
                                        )
                                        if new_status != "active":
                                            await db.update_followup_chat_status(account_id, sender_id, new_status)
                                        logger.info("[AIFollowUp] AI reply sent to user %d", sender_id)
                                        return  # Managed by AI Sales Agent, skip legacy rules
                                    except Exception as ex_send:
                                        logger.error("[AIFollowUp] Failed to send AI reply to user %d: %s", sender_id, ex_send)

        except Exception as ex_ai:
            logger.error("[AIFollowUp] Error in AI follow-up engine: %s", ex_ai, exc_info=True)

        # ── Legacy Auto-Reply Chatbot Logic (Fallback) ──
        rules = await db.get_active_auto_reply_rules()
        if not rules:
            return

        for rule in rules:
            # Check if rule applies to this account
            if rule.get("account_ids") and account_id not in rule["account_ids"]:
                continue

            # Check trigger type
            matched = False
            trigger_type = rule.get("trigger_type", "keyword")
            if trigger_type == "any":
                matched = True
            elif trigger_type == "keyword":
                msg_norm = message_text.lower().strip()
                for kw in rule.get("trigger_keywords", []):
                    if kw.lower().strip() in msg_norm:
                        matched = True
                        break

            if not matched:
                continue

            # Check reply limit for this user
            sent_count = await db.count_user_auto_replies(rule["id"], sender_id)
            if sent_count >= rule.get("max_replies_per_user", 3):
                logger.debug(
                    "[AutoReply] Limit reached (%d/%d) for user %d on rule '%s'",
                    sent_count, rule.get("max_replies_per_user", 3), sender_id, rule["name"]
                )
                continue

            # Prepare reply message
            reply_text = None
            if rule.get("use_ai"):
                ai_provider = await db.get_setting("ai_provider", None)
                if ai_provider in ("gemini", "deepseek", "openai", "groq"):
                    try:
                        raw = await db.get_setting("ai_keys_" + ai_provider, "[]")
                        ai_keys = json.loads(raw) if raw else []
                        if ai_keys:
                            sys_prompt = rule.get("ai_system_prompt") or "You are a helpful assistant."
                            prompt = f"Instructions:\n{sys_prompt}\n\nIncoming message from user:\n{message_text}\n\nResponse:"
                            reply_text = await ai_rmx.generate_response(prompt, ai_provider, ai_keys)
                    except Exception as e:
                        logger.warning("[AutoReply] AI generation failed: %s", e)

            # Fallback to templates if AI disabled or failed
            if not reply_text:
                replies = rule.get("reply_messages", [])
                if replies:
                    reply_text = replies[0].get("content")

            if not reply_text:
                continue

            # Send reply with natural random delay
            delay = random.uniform(2.0, 5.0)
            logger.info("[AutoReply] Match found for rule '%s'. Replying in %.1fs...", rule["name"], delay)
            await asyncio.sleep(delay)

            try:
                await client.send_message(sender_id, reply_text)
                await db.add_auto_reply_log({
                    "rule_id": rule["id"],
                    "account_id": account_id,
                    "user_id": sender_id,
                    "username": sender_username,
                    "trigger_text": message_text,
                    "reply_text": reply_text,
                    "status": "success"
                })
                logger.info("[AutoReply] Sent reply to user %d via acc %d", sender_id, account_id)
            except Exception as ex:
                logger.error("[AutoReply] Failed to send reply to user %d: %s", sender_id, ex)
                await db.add_auto_reply_log({
                    "rule_id": rule["id"],
                    "account_id": account_id,
                    "user_id": sender_id,
                    "username": sender_username,
                    "trigger_text": message_text,
                    "reply_text": reply_text,
                    "status": f"failed: {ex}"
                })

    return _handler


# ── Public API ─────────────────────────────────────────────────────────────────

def _register_account(account_id: int) -> None:
    """Register a NewMessage handler on the given account's client."""
    client = tg.get_client(account_id)
    if not client or not client.is_connected():
        logger.debug("[Inbox] acc=%d: client not connected, skip register", account_id)
        return

    # Remove stale handler for this account if any
    _unregister_account(account_id)

    handler_fn = _make_handler(account_id)
    client.add_event_handler(handler_fn, events.NewMessage(incoming=True))
    _handler_removers[account_id] = (client, handler_fn)
    logger.info("[Inbox] acc=%d: reply handler registered", account_id)


def _unregister_account(account_id: int) -> None:
    """Remove the reply handler for a given account."""
    entry = _handler_removers.pop(account_id, None)
    if entry:
        client, handler_fn = entry
        try:
            client.remove_event_handler(handler_fn, events.NewMessage(incoming=True))
        except Exception:
            pass


async def start_reply_tracker() -> None:
    """
    Register inbox handlers on all currently connected accounts.
    Called once at server startup (after all clients are connected).
    """
    accounts = await db.get_all_accounts()
    registered = 0
    for acc in accounts:
        if acc.get("is_logged_in"):
            _register_account(acc["id"])
            registered += 1
    logger.info("[Inbox] Reply tracker started — %d account(s) monitored", registered)


async def stop_reply_tracker() -> None:
    """Remove all reply handlers (called at shutdown)."""
    for acc_id in list(_handler_removers.keys()):
        _unregister_account(acc_id)
    logger.info("[Inbox] Reply tracker stopped")


def register_account(account_id: int) -> None:
    """
    Public helper: call this after a new account logs in so its
    inbox is immediately monitored without restarting the server.
    """
    _register_account(account_id)


def unregister_account(account_id: int) -> None:
    """Public helper: call when an account logs out."""
    _unregister_account(account_id)
