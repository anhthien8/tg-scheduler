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
import re
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

# Pending AI sends set: (account_id, user_id)
_pending_ai_sends: set[tuple[int, int]] = set()


async def _remove_ai_send_after_delay(account_id: int, user_id: int):
    await asyncio.sleep(5)
    _pending_ai_sends.discard((account_id, user_id))


def sanitize_telegram_html(text: str) -> str:
    if not text:
        return ""
    # 0. Convert literal '\n' string representation to real line breaks
    s = text.replace('\\n', '\n')

    # 1. Convert html block elements & lists to clean newlines/bullets
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</?p\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</?div\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<li\s*/?>', '\n• ', s, flags=re.IGNORECASE)
    s = re.sub(r'</?ul\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</?ol\s*/?>', '\n', s, flags=re.IGNORECASE)

    # 2. Convert markdown bold/italic syntax
    s = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'<i>\1</i>', s)

    # 3. Strip any unsupported HTML tags (keep Telegram supported: b, i, u, s, strong, em, ins, del, code, pre, a)
    allowed_pattern = r'</?(?:b|i|u|s|strong|em|ins|del|strike|code|pre|a(?:\s+href="[^"]*")?)\s*/?>'

    def _clean_tag(m):
        tag = m.group(0)
        if re.match(allowed_pattern, tag, re.IGNORECASE):
            return tag
        return ""

    s = re.sub(r'<[^>]+>', _clean_tag, s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


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

        # If account is OFF (paused by user or auto-paused), skip all auto-replies and AI processing
        if await db.is_account_paused(account_id):
            logger.debug("[Inbox] Account #%d is OFF/Paused — skipping message processing", account_id)
            return

        sender = await event.get_sender()
        if sender is None:
            return

        # Check if message is from ourselves (outgoing from human owner/admin)
        client = tg.get_client(account_id)
        if client is None:
            return
        try:
            me = await client.get_me()
            if me and sender.id == me.id:
                # ── HUMAN ADMIN OUTGOING MESSAGE INTERCEPTION ──
                # If human admin typed a message manually on Telegram to a user:
                # Auto-pause AI Agent for this user so AI never intervenes in human conversations
                peer = await event.get_input_chat()
                peer_user_id = getattr(peer, "user_id", None) or getattr(event, "chat_id", None)
                if peer_user_id and peer_user_id != me.id:
                    if (account_id, peer_user_id) in _pending_ai_sends:
                        # This outgoing send was initiated by AI script, skip auto-pause
                        _pending_ai_sends.discard((account_id, peer_user_id))
                        return
                    logger.info("[AIFollowUp] 🛑 Human admin manually typed message to user %d (acc=%d) — auto-pausing AI Agent (status='needs_human')", peer_user_id, account_id)
                    await db.update_followup_chat_status(account_id, peer_user_id, "needs_human")
                return
        except Exception as _me_err:
            logger.debug("[Inbox] me check error: %s", _me_err)

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
                agent_config = None
                target_campaign_id = None

                # 1. Identify if this user belongs to an active RUNNING campaign with AI Agent
                running_log = await db.find_running_campaign_log_for_user(sender_id)
                if running_log:
                    target_campaign_id = running_log.get("campaign_id")
                    cmp = await db.get_dm_campaign(target_campaign_id) if target_campaign_id else None
                    if cmp and cmp.get("status") == "running" and cmp.get("ai_agent_id"):
                        c_agent = await db.get_ai_agent(cmp["ai_agent_id"])
                        if c_agent and c_agent.get("is_active", 1):
                            agent_config = c_agent
                            logger.info("[AIFollowUp] 🤖 Campaign #%s AI Agent '%s' selected for user %d", target_campaign_id, agent_config["name"], sender_id)

                # 2. Fallback: Check if Telegram ACCOUNT itself has an assigned AI Agent (for organic AFK DMs)
                if not agent_config:
                    account_obj = await db.get_account(account_id)
                    if account_obj and account_obj.get("ai_agent_id"):
                        acc_agent = await db.get_ai_agent(account_obj["ai_agent_id"])
                        if acc_agent and acc_agent.get("is_active", 1):
                            agent_config = acc_agent
                            logger.info("[AIFollowUp] 🤖 Account-level AI Agent '%s' selected for organic DM on acc=%d from user %d", agent_config["name"], account_id, sender_id)

                if not agent_config:
                    logger.debug("[AIFollowUp] No active AI Agent assigned for campaign or account — skipping AI reply for user %d", sender_id)
                else:
                    # 3. AI Agent verified! Get or create chat session
                    chat = await db.get_or_create_followup_chat(
                        account_id=account_id,
                        user_id=sender_id,
                        username=sender_username,
                        name=sender_name,
                        campaign_id=target_campaign_id,
                        watcher_id=watcher_id
                    )

                    current_status = chat.get("status", "active")
                    if current_status in ("paused_admin", "onboarded", "needs_human"):
                        logger.info("[AIFollowUp] User %d chat status is '%s' — skipping AI reply", sender_id, current_status)
                    else:
                        # Append incoming user message
                        chat = await db.append_followup_chat_message(account_id, sender_id, "user", message_text)

                        # Agent-specific handover keywords and max replies
                        agent_handover_kws = agent_config.get("handover_keywords", [])
                        if isinstance(agent_handover_kws, str):
                            try:
                                agent_handover_kws = json.loads(agent_handover_kws)
                            except Exception:
                                agent_handover_kws = []

                        msg_lower = message_text.lower().strip()
                        needs_handover = any(kw.lower().strip() in msg_lower for kw in agent_handover_kws if kw and kw.strip())

                        max_replies_val = agent_config.get("max_replies", 10)

                        if needs_handover:
                            logger.warning("[AIFollowUp] 🚨 [ADMIN ALERT] Handover keyword matched for user %d (acc=%d) — setting status to 'needs_human'", sender_id, account_id)
                            await db.update_followup_chat_status(account_id, sender_id, "needs_human")
                        elif chat.get("reply_count", 0) >= max_replies_val:
                            logger.warning("[AIFollowUp] 🚨 [ADMIN ALERT] Max replies (%d) reached for user %d (acc=%d) — setting status to 'needs_human'", max_replies_val, sender_id, account_id)
                            await db.update_followup_chat_status(account_id, sender_id, "needs_human")
                        else:
                            kwargs = {}
                            ai_provider = None
                            ai_keys = []

                            # 1. Prefer Agent-specific provider & keys
                            if agent_config:
                                agent_keys = agent_config.get("api_keys_json", [])
                                if isinstance(agent_keys, str):
                                    try:
                                        agent_keys = json.loads(agent_keys)
                                    except Exception:
                                        agent_keys = []
                                if agent_keys:
                                    ai_provider = agent_config.get("provider", "gemini")
                                    ai_keys = agent_keys

                            # 2. Fallback to global settings if agent has no keys
                            if not ai_keys:
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

                                if not ai_keys:
                                    all_providers = ["gemini", "groq", "openai", "deepseek", "openai_compatible"]
                                    for alt_prov in all_providers:
                                        alt_keys = await _load_provider_keys(alt_prov)
                                        if alt_keys:
                                            ai_provider = alt_prov
                                            ai_keys = alt_keys
                                            break

                            # 3. Always populate base_url & model for openai_compatible
                            if ai_provider == "openai_compatible":
                                b_url = agent_config.get("base_url", "") if agent_config else ""
                                mod = agent_config.get("model", "") if agent_config else ""
                                if not b_url or not b_url.strip():
                                    b_url = await db.get_setting("ai_oai_compat_base_url", "")
                                if not mod or not mod.strip():
                                    mod = await db.get_setting("ai_oai_compat_model", "")
                                if b_url and b_url.strip():
                                    kwargs["base_url"] = b_url.strip()
                                if mod and mod.strip():
                                    kwargs["model"] = mod.strip()

                            sys_prompt = agent_config.get("system_prompt", "")
                            kb = agent_config.get("knowledge_base", "")

                            format_rules = (
                                "\n\n--- CRITICAL RESPONSE INSTRUCTIONS ---\n"
                                "1. DYNAMIC LANGUAGE MATCHING MANDATE: Automatically detect the language used by the user in their message (e.g., Chinese/中文, English, Vietnamese, Russian, Spanish, etc.) and ALWAYS reply in that EXACT SAME LANGUAGE! If the user speaks Chinese (中文), reply in fluent Chinese (中文). If English, reply in English. Match their language naturally.\n"
                                "2. TELEGRAM FORMATTING: Do NOT output literal '\\n' text characters. Use actual line breaks. Do NOT use raw markdown like **bold**. Use standard HTML <b>bold</b> or <i>italic</i> for formatting, or write clean plain text.\n"
                                "3. KNOWLEDGE BASE ACCURACY: If the user asks about policies, rates, commissions, benefits, or exchange details, ALWAYS extract and cite specific, exact numbers and facts directly from the KNOWLEDGE BASE section below."
                            )
                            combined_prompt = sys_prompt + format_rules
                            if kb and kb.strip():
                                combined_prompt += "\n\n--- KNOWLEDGE BASE ---\n" + kb.strip()

                            if not ai_keys:
                                logger.warning("[AIFollowUp] ⚠️ Cannot generate AI reply for user %d: No API Keys configured!", sender_id)
                            else:
                                full_history = chat.get("history", [])
                                history = full_history[-10:] if len(full_history) > 10 else full_history
                                logger.info(
                                    "[AIFollowUp] 🤖 Generating AI reply using Agent '%s' (user %d, history_len=%d)...",
                                    agent_config['name'], sender_id, len(history)
                                )
                                try:
                                    ai_reply = await ai_rmx.generate_chat_response(history, combined_prompt, ai_provider, ai_keys, **kwargs)
                                except Exception as ex_gen:
                                    logger.error("[AIFollowUp] AI generate_chat_response error for user %d: %s", sender_id, ex_gen)
                                    ai_reply = None

                                if ai_reply:
                                    new_status = "active"
                                    if "[HANDOVER_REQUIRED]" in ai_reply:
                                        new_status = "needs_human"
                                        ai_reply = ai_reply.replace("[HANDOVER_REQUIRED]", "").strip()
                                    elif "[ONBOARDED]" in ai_reply:
                                        new_status = "onboarded"
                                        ai_reply = ai_reply.replace("[ONBOARDED]", "").strip()

                                    # Sanitize and convert AI output to Telegram-compatible HTML
                                    ai_reply = sanitize_telegram_html(ai_reply)

                                    if ai_reply:
                                        delay = random.uniform(6.0, 15.0)
                                        logger.info("[AIFollowUp] Simulating human typing for %.1fs before sending AI reply to user %d...", delay, sender_id)
                                        await asyncio.sleep(delay)
                                        _pending_ai_sends.add((account_id, sender_id))
                                        await tg.send_text_message(account_id, sender_id, ai_reply)
                                        asyncio.create_task(_remove_ai_send_after_delay(account_id, sender_id))
                                        await db.append_followup_chat_message(account_id, sender_id, "assistant", ai_reply, inc_reply_count=True)

                                    if new_status != "active":
                                        await db.update_followup_chat_status(account_id, sender_id, new_status)
                                    logger.info("[AIFollowUp] AI reply sent to user %d", sender_id)
                                    return  # Managed by AI Sales Agent, skip legacy rules

        except Exception as ex_ai:
            logger.error("[AIFollowUp] Error in AI follow-up engine: %s", ex_ai, exc_info=True)

        # ── Legacy Auto-Reply Chatbot Logic (Fallback) ──
        existing_chat = await db.get_followup_chat(account_id, sender_id)
        if existing_chat and existing_chat.get("status") in ("paused_admin", "onboarded", "needs_human"):
            logger.info("[AutoReply] User %d chat status is '%s' — skipping legacy auto-reply", sender_id, existing_chat.get("status"))
            return

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
    client.add_event_handler(handler_fn, events.NewMessage())
    _handler_removers[account_id] = (client, handler_fn)
    logger.info("[Inbox] acc=%d: reply handler registered (incoming + outgoing human interception)", account_id)


def _unregister_account(account_id: int) -> None:
    """Remove the reply handler for a given account."""
    entry = _handler_removers.pop(account_id, None)
    if entry:
        client, handler_fn = entry
        try:
            client.remove_event_handler(handler_fn, events.NewMessage())
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
