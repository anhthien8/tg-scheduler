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
from datetime import datetime, timezone
from typing import Any

from telethon import events
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

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

# Background periodic worker task for 60m auto-resume and drip follow-ups
_periodic_worker_task: asyncio.Task | None = None


async def _remove_ai_send_after_delay(account_id: int, user_id: int):
    await asyncio.sleep(30)  # Covers full AI cycle: API call + typing delay
    _pending_ai_sends.discard((account_id, user_id))


# ── Main account ID for handover notifications ────────────────────────────────
MAIN_ACCOUNT_ID = 3  # @weexwill / "Will Weex"


async def _notify_main_account_handover(
    account_id: int,
    sender_id: int,
    sender_username: str | None,
    reason: str,
):
    """Send handover notification to main @weexwill account (Saved Messages)."""
    try:
        acc_info = await db.get_account(account_id)
        acc_name = (acc_info or {}).get("name", f"Account #{account_id}")
        user_tag = f"@{sender_username}" if sender_username else f"user_id={sender_id}"
        direct_link = f"https://t.me/{sender_username}" if sender_username else f"tg://user?id={sender_id}"

        alert_text = (
            f"🚨 HANDOVER ALERT\n\n"
            f"👤 Lead: {user_tag}\n"
            f"🔗 Direct Chat: {direct_link}\n"
            f"📱 Account: {acc_name} (#{account_id})\n"
            f"📋 Reason: {reason}\n\n"
            f"Please check the AI Followup dashboard or tap the link to message them directly."
        )

        # Send to main account's Saved Messages
        main_client = tg.get_client(MAIN_ACCOUNT_ID)
        if main_client and main_client.is_connected():
            me = await main_client.get_me()
            if me:
                await main_client.send_message(me.id, alert_text)
                logger.info("[Handover] 📬 Notification sent to main account #%d for %s (acc=%d, reason=%s)",
                            MAIN_ACCOUNT_ID, user_tag, account_id, reason)
                return

        # Fallback: if main account not available, try sending from the same account
        if account_id != MAIN_ACCOUNT_ID:
            client = tg.get_client(account_id)
            if client and client.is_connected():
                me = await client.get_me()
                if me:
                    await client.send_message(me.id, alert_text)
                    logger.info("[Handover] 📬 Notification sent to self (acc=%d) for %s (reason=%s)",
                                account_id, user_tag, reason)
                    return

        logger.warning("[Handover] Could not send notification — no client available")
    except Exception as e:
        logger.debug("[Handover] Notification error: %s", e)


async def _async_update_kol_profile(account_id: int, user_id: int, history: list[dict], provider: str, api_keys: list[str], kwargs: dict):
    """Background task to extract and update KOL profile facts in DB."""
    try:
        extracted = await ai_rmx.extract_kol_profile(history, provider, api_keys, **kwargs)
        if extracted:
            await db.upsert_kol_profile(account_id, user_id, extracted)
            logger.info("🧠 [KOL Memory] Updated profile for user %d (acc=%d): %s", user_id, account_id, extracted)
    except Exception as e:
        logger.debug("[KOL Memory] Error updating profile: %s", e)


async def _async_distill_human_rule(account_id: int, user_id: int, message_text: str):
    """Background task: Analyze human admin manual reply and distill learned rules."""
    try:
        chat = await db.get_followup_chat(account_id, user_id)
        if not chat:
            return

        history = json.loads(chat.get("history_json", "[]"))
        if not history:
            return

        user_questions = [m["content"] for m in history if m.get("role") == "user"]
        if not user_questions:
            return
        last_user_q = user_questions[-1]

        agent_config = None
        target_cmp_id = chat.get("campaign_id")
        if target_cmp_id:
            cmp = await db.get_dm_campaign(target_cmp_id)
            if cmp and cmp.get("ai_agent_id"):
                agent_config = await db.get_ai_agent(cmp["ai_agent_id"])

        if not agent_config:
            acc_obj = await db.get_account(account_id)
            if acc_obj and acc_obj.get("ai_agent_id"):
                agent_config = await db.get_ai_agent(acc_obj["ai_agent_id"])

        if not agent_config:
            active_agents = await db.get_all_ai_agents(active_only=True)
            if active_agents:
                agent_config = active_agents[0]

        ai_provider = agent_config.get("provider", "gemini") if agent_config else "gemini"
        ai_keys = agent_config.get("api_keys_json", []) if agent_config else []
        if isinstance(ai_keys, str):
            try:
                ai_keys = json.loads(ai_keys)
            except Exception:
                ai_keys = []

        if not ai_keys:
            raw = await db.get_setting(f"ai_keys_{ai_provider}", "[]")
            ai_keys = json.loads(raw) if raw else []

        if not ai_keys:
            return

        kwargs = {}
        if ai_provider in ("openai_compatible", "chatgpt_oauth") and agent_config:
            if agent_config.get("base_url"): kwargs["base_url"] = agent_config["base_url"]
            if agent_config.get("model"): kwargs["model"] = agent_config["model"]

        rule = await ai_rmx.distill_human_takeover_rule(
            user_question=last_user_q,
            human_answer=message_text,
            chat_history=history[-6:],
            provider=ai_provider,
            api_keys=ai_keys,
            **kwargs
        )

        if rule and rule.get("pattern") and rule.get("answer"):
            agent_id = agent_config.get("id") if agent_config else None
            await db.create_learned_knowledge(
                ai_agent_id=agent_id,
                user_id=user_id,
                question_pattern=rule["pattern"],
                learned_answer=rule["answer"],
                context_summary=f"Discovered from human takeover on acc #{account_id}"
            )
            logger.info("🎓 [Self-Learning] Extracted new rule for agent %s: '%s' -> '%s'",
                        agent_id, rule["pattern"], rule["answer"])
    except Exception as e:
        logger.debug("[Self-Learning] Distillation failed: %s", e)


def is_bot_account(username: str | None, name: str | None, message_text: str | None) -> bool:
    """Check if the sender is likely a bot/channel/system notification."""
    if username and username.lower().endswith("bot"):
        return True
    if name and "bot" in name.lower():
        return True
    if message_text:
        bot_signals = [
            "t.me/", "/start", "tap to start", "bot launched", "channel",
            "subscribe", "official bot", "giveaway", "airdrop bot",
            "automated message", "broadcast", "earn money", "casino"
        ]
        text_lower = message_text.lower()
        if any(sig in text_lower for sig in bot_signals) and len(message_text) > 150:
            return True
    return False


def sanitize_telegram_html(text: str) -> str:
    """Sanitize and convert raw AI output into clean Telegram-compatible HTML."""
    if not text:
        return ""

    s = text.strip()

    def _decode_unicode_escapes(t: str) -> str:
        def _simple_escape(m):
            try:
                return chr(int(m.group(1), 16))
            except (ValueError, OverflowError):
                return m.group(0)
        return re.sub(r'\\u([0-9a-fA-F]{4})', _simple_escape, t)

    s = _decode_unicode_escapes(s)

    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</?p\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</?div\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<li\s*/?>', '\n• ', s, flags=re.IGNORECASE)
    s = re.sub(r'</?ul\s*/?>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</?ol\s*/?>', '\n', s, flags=re.IGNORECASE)

    s = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'<i>\1</i>', s)

    allowed_pattern = r'</?(?:b|i|u|s|strong|em|ins|del|strike|code|pre|a(?:\s+href="[^"]*")?)\s*/?>'

    def _clean_tag(m):
        tag = m.group(0)
        if re.match(allowed_pattern, tag, re.IGNORECASE):
            return tag
        return ""

    s = re.sub(r'<[^>]+>', _clean_tag, s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


async def generate_and_send_ai_reply_for_chat(
    account_id: int,
    user_id: int,
    sender_username: str | None = None,
    sender_name: str | None = None,
    target_campaign_id: int | None = None,
    watcher_id: int | None = None,
    event: Any | None = None,
) -> bool:
    """Generate and send AI response for a chat session. Returns True if reply was sent."""
    # 0. Check blacklist
    if await db.is_user_blacklisted(user_id=user_id, username=sender_username or ""):
        logger.info("[AIFollowUp] 🚫 User %s (id=%d) is blacklisted — skipping AI reply", sender_username or "?", user_id)
        return False

    # 0b. Check if human admin is actively chatting (human_takeover_at < 60m)
    chat_check = await db.get_followup_chat(account_id, user_id)
    if chat_check:
        takeover_str = chat_check.get("human_takeover_at")
        if takeover_str:
            try:
                # SQLite datetime('now') is UTC without tzinfo — always compare in UTC
                dt_tk = datetime.fromisoformat(takeover_str).replace(tzinfo=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                elapsed = (now_utc - dt_tk).total_seconds()
                if elapsed < 3600:
                    logger.info("[AIFollowUp] 🛑 Human admin active (takeover %ds ago) — skipping AI reply for user %d",
                                int(elapsed), user_id)
                    return False
            except Exception:
                pass

    # 1. Multi-fire guard
    if (account_id, user_id) in _pending_ai_sends:
        logger.info("[AIFollowUp] ⚡ Skipping duplicate — AI reply already in progress for user @%s (id=%d, acc=%d)",
                    sender_username or "?", user_id, account_id)
        return False
    _pending_ai_sends.add((account_id, user_id))  # Lock immediately to prevent race condition

    # 2. Get chat session
    chat = await db.get_or_create_followup_chat(
        account_id=account_id,
        user_id=user_id,
        username=sender_username,
        name=sender_name,
        campaign_id=target_campaign_id,
        watcher_id=watcher_id
    )
    if not chat:
        return False

    full_history = chat.get("history", [])
    if not full_history:
        return False

    # Ensure last message is from user (do not reply to yourself)
    if full_history[-1].get("role") != "user":
        logger.debug("[AIFollowUp] Last message is already from assistant for user %d — skipping", user_id)
        return False

    # 3. Resolve AI Agent config
    agent_config = None
    if target_campaign_id:
        cmp = await db.get_dm_campaign(target_campaign_id)
        if cmp and cmp.get("ai_agent_id"):
            c_agent = await db.get_ai_agent(cmp["ai_agent_id"])
            if c_agent and c_agent.get("is_active", 1):
                agent_config = c_agent

    if not agent_config:
        account_obj = await db.get_account(account_id)
        acc_agent_id = account_obj.get("ai_agent_id") if account_obj else None

        # If account explicitly has NO agent assigned (Tắt AI), do NOT fallback
        # Also refuse if campaign was checked but had no agent either
        if not acc_agent_id:
            logger.info("[AIFollowUp] Account #%d has AI disabled — refusing to send AI reply for user %d",
                        account_id, user_id)
            return False

        if acc_agent_id:
            acc_agent = await db.get_ai_agent(acc_agent_id)
            if acc_agent and acc_agent.get("is_active", 1):
                agent_config = acc_agent

    # Fallback to any active agent ONLY if account/campaign had an agent but config was missing
    if not agent_config:
        active_agents = await db.get_all_ai_agents(active_only=True)
        if active_agents:
            agent_config = active_agents[0]
        else:
            sys_prompt = await db.get_setting("ai_followup_system_prompt", None) or await db.get_setting("ai_custom_prompt", None)
            kb = await db.get_setting("ai_followup_knowledge_base", "")
            max_rep_str = await db.get_setting("ai_followup_max_replies", "20")
            max_rep = int(max_rep_str) if max_rep_str and max_rep_str.isdigit() else 20
            handover_raw = await db.get_setting("ai_followup_handover_keywords", '["gặp admin", "tư vấn viên", "số điện thoại"]')
            agent_config = {
                "name": "Global AI Sales Agent",
                "system_prompt": sys_prompt or "Bạn là chuyên gia tư vấn bán hàng & onboard thân thiện.",
                "knowledge_base": kb or "",
                "max_replies": max_rep,
                "handover_keywords": handover_raw,
                "provider": await db.get_setting("ai_provider", "gemini"),
                "api_keys_json": []
            }

    # 4. Resolve API Provider & Keys
    kwargs = {}
    ai_provider = None
    ai_keys = []
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

    if not ai_keys:
        async def _load_provider_keys(prov):
            if not prov: return []
            try:
                raw = await db.get_setting(f"ai_keys_{prov}", "[]")
                return json.loads(raw) if raw else []
            except Exception: return []

        ai_provider = await db.get_setting("ai_provider", "gemini")
        ai_keys = await _load_provider_keys(ai_provider)
        if not ai_keys:
            all_providers = ["chatgpt_oauth", "gemini", "groq", "openai", "deepseek", "openai_compatible"]
            for alt_prov in all_providers:
                alt_keys = await _load_provider_keys(alt_prov)
                if alt_keys:
                    ai_provider = alt_prov
                    ai_keys = alt_keys
                    break

    if ai_provider in ("openai_compatible", "chatgpt_oauth"):
        prefix = "ai_oai_compat_" if ai_provider == "openai_compatible" else "ai_chatgpt_oauth_"
        b_url = (agent_config.get("base_url") if agent_config else "") or (await db.get_setting(f"{prefix}base_url", ""))
        mod = (agent_config.get("model") if agent_config else "") or (await db.get_setting(f"{prefix}model", ""))
        if b_url and b_url.strip(): kwargs["base_url"] = b_url.strip()
        if mod and mod.strip(): kwargs["model"] = mod.strip()

    if not ai_keys:
        logger.warning("[AIFollowUp] ⚠️ Cannot generate AI reply for user %d: No API Keys configured!", user_id)
        return False

    sys_prompt = agent_config.get("system_prompt", "")
    kb = agent_config.get("knowledge_base", "")

    format_rules = (
        "\n\n--- CRITICAL RESPONSE INSTRUCTIONS ---\n"
        "1. DYNAMIC LANGUAGE MATCHING MANDATE: Automatically detect the language used by the user in their message (e.g., Chinese/中文, English, Vietnamese, Russian, Spanish, etc.) and ALWAYS reply in that EXACT SAME LANGUAGE!\n"
        "2. TELEGRAM FORMATTING: Do NOT output literal '\\n' text characters. Use actual line breaks. Do NOT use raw markdown like **bold**. Use standard HTML <b>bold</b> or <i>italic</i> for formatting.\n"
        "3. KNOWLEDGE BASE & COMMISSION QUOTING MANDATE:\n"
        "   - VIETNAMESE 🇻🇳, KOREAN 🇰🇷, CHINESE 🇨🇳 (or Verified Volume > 20M): Quote 70% - 80% commission.\n"
        "   - GLOBAL / ENGLISH / HIGH-RISK / NIGERIA 🇳🇬 / INDIA 🇮🇳 / OTHER (< 20M volume): STRICTLY ONLY quote 50% - 60% base commission. NEVER mention 70% or 80%! Highlight daily USDT withdrawals, No KYC, No Tax, and ask for their community link & monthly volume.\n"
        "4. STRICT CONTEXTUAL CONTINUATION, NATURAL HUMAN TONE & ANTI-NAGGING:\n"
        "   - NATURAL HUMAN TONE: Gần gũi, cởi mở, thân thiện như anh em trong ngành crypto (bro, anh em, bạn bè tùy đối tượng). Không dùng văn phong máy móc, trang trọng hay quan cách.\n"
        "   - ANTI-NAGGING & NO FORCED CTA: TUYỆT ĐỐI KHÔNG chèo kéo, ép buộc hoặc giục giã KOL hành động (NGHIÊM CẤM các câu hỏi ép như 'Ready to post?', 'Bạn đã sẵn sàng đăng chưa?', 'Bao giờ bạn lên bài?').\n"
        "   - CONCISE DIRECT ANSWERS: Nếu đối tác hỏi câu hỏi đơn giản (như KYC, nạp rút, link ref), HÃY TRẢ LỜI NGẮN GỌN, TỰ NHIÊN VÀ DỪNG LẠI. Không cố tình nhồi nhét câu hỏi cuối tin nếu không cần thiết!\n"
        "   - ANTI-CHECKLIST / ANTI-WIZARD: TUYỆT ĐỐI KHÔNG dùng văn phong biểu mẫu hay liệt kê bước tiếp theo dạng robot (như 'UID received. Setting up...', 'Next step: 1..., 2...'). Hãy nói chuyện như một người bạn BD tự nhiên (ví dụ: 'Got your UID bro! Will bind commission for you now. Feel free to check out the campaign when you have time 👍').\n"
        "   - DEMANDING / DIFFICULT CASES ESCALATION: Nếu KOL đòi hỏi quá khó khăn (deal vượt khung, đòi ngân sách/upfront lớn, tài trợ phức tạp), hãy nói khéo léo để đá về phía ban giám đốc sàn phê duyệt riêng (e.g. 'Case này đặc biệt và vượt thẩm quyền thông thường của mình rồi, để mình chuyển tiếp proposal chi tiết của bạn lên Ban Giám Đốc / Partnership Committee của sàn WEEX để duyệt riêng nhé!') và kích hoạt [HANDOVER_REQUIRED].\n"
        "   - NO HALLUCINATION & NO SPAM: NEVER hallucinate, never output random unrelated greetings, and never repeat points already made.\n"
        "5. LEAD EVALUATION METRICS: At the VERY END of your response, append a hidden metadata JSON tag on its own line: [METRICS: {\"intent_score\": <0-100>, \"lead_tier\": \"<Tier A|Tier B|Tier C>\", \"summary\": \"<1-sentence lead need summary>\"}].\n"
        "   - Tier A (Intent 80-100): High volume trader/KOL (>10k subs or >$5M vol), ready for meeting, negotiating terms.\n"
        "   - Tier B (Intent 40-79): Interested in exchange benefits, asking detailed questions.\n"
        "   - Tier C (Intent 0-39): Casual question, low interest, or greeting."
    )
    combined_prompt = sys_prompt + format_rules
    if kb and kb.strip():
        combined_prompt += "\n\n--- KNOWLEDGE BASE ---\n" + kb.strip()

    kol_prof = await db.get_kol_profile(account_id, user_id)
    if kol_prof:
        prof_lines = [f" - {k}: {v}" for k, v in kol_prof.items() if v]
        if prof_lines:
            combined_prompt += "\n\n--- REMEMBERED KOL PROFILE (FACTS PREVIOUSLY STATED BY THIS USER) ---\n"
            combined_prompt += "\n".join(prof_lines)
            combined_prompt += "\nDO NOT ask the user for any of these facts again!"

    if agent_config and agent_config.get("id"):
        learned_rules = await db.get_learned_knowledge_for_agent(agent_config["id"], status="approved")
        if learned_rules:
            combined_prompt += "\n\n--- LEARNED RULES (DISCOVERED FROM HUMAN ADMIN INTERVENTIONS) ---\n"
            for r in learned_rules:
                combined_prompt += f"• When user asks about: {r['question_pattern']} -> Follow this answer/policy: {r['learned_answer']}\n"

    history = full_history[-40:] if len(full_history) > 40 else full_history
    logger.info("[AIFollowUp] 🤖 Generating AI reply using Agent '%s' for user @%s (%d, history_len=%d)...",
                agent_config.get('name', '?'), sender_username or '?', user_id, len(history))

    # _pending_ai_sends already locked at guard check (line 291)
    try:
        ai_reply = await ai_rmx.generate_chat_response(history, combined_prompt, ai_provider, ai_keys, **kwargs)

        if not ai_reply:
            return False

        new_status = "active"
        if "[HANDOVER_REQUIRED]" in ai_reply:
            new_status = "needs_human"
            ai_reply = ai_reply.replace("[HANDOVER_REQUIRED]", "").strip()
            asyncio.create_task(_notify_main_account_handover(
                account_id, user_id, sender_username,
                reason="AI triggered [HANDOVER_REQUIRED]"
            ))
        elif "[ONBOARDED]" in ai_reply:
            new_status = "onboarded"
            ai_reply = ai_reply.replace("[ONBOARDED]", "").strip()

        intent_score = 30
        lead_tier = "Tier C"
        summary_text = ""
        if re.search(r'METRICS\s*:', ai_reply, re.IGNORECASE):
            try:
                metrics_match = re.search(r'\[METRICS:\s*(\{.*\})\]', ai_reply, re.DOTALL | re.IGNORECASE)
                if not metrics_match:
                    metrics_match = re.search(r'METRICS:\s*(\{.*\})', ai_reply, re.DOTALL | re.IGNORECASE)
                if metrics_match:
                    metrics_json = json.loads(metrics_match.group(1))
                    intent_score = int(metrics_json.get("intent_score", 30))
                    lead_tier = str(metrics_json.get("lead_tier", "Tier C"))
                    summary_text = str(metrics_json.get("summary", ""))
            except Exception as ex_m:
                logger.debug("[AIFollowUp] Error parsing METRICS tag: %s", ex_m)

            ai_reply = re.sub(r'\[?METRICS\s*:.*$', '', ai_reply, flags=re.DOTALL | re.IGNORECASE).strip()

        await db.update_followup_lead_metrics(account_id, user_id, intent_score, lead_tier, summary_text)

        ai_reply = sanitize_telegram_html(ai_reply)
        if not ai_reply:
            return False

        delay = min(22.0, max(8.0, len(ai_reply) * 0.04 + random.uniform(5.0, 10.0)))
        logger.info("[AIFollowUp] Simulating human reading & typing for %.1fs before sending AI reply to user %d...", delay, user_id)

        client = tg.get_client(account_id)
        if client and event and getattr(event, "message", None) and getattr(event.message, "id", None):
            try:
                reaction_chance = float(await db.get_setting("ai_reaction_chance", "0.35"))
                if random.random() < reaction_chance:
                    react_emoji = random.choice(["❤️", "👍", "🔥", "👌", "💯", "⚡", "🤝"])
                    chat_peer = await event.get_input_chat()
                    await client(SendReactionRequest(
                        peer=chat_peer,
                        msg_id=event.message.id,
                        reaction=[ReactionEmoji(emoticon=react_emoji)],
                    ))
            except Exception:
                pass

        if client:
            try:
                # First wait a few seconds as human reading time
                read_time = min(delay * 0.4, 6.0)
                if read_time > 0:
                    await asyncio.sleep(read_time)
                # Then show typing action for the remaining duration
                type_time = max(1.0, delay - read_time)
                async with client.action(user_id, 'typing'):
                    await asyncio.sleep(type_time)
            except Exception:
                if delay > 0:
                    await asyncio.sleep(delay)
        elif delay > 0:
            await asyncio.sleep(delay)

        await tg.send_text_message(account_id, user_id, ai_reply)
        asyncio.create_task(_remove_ai_send_after_delay(account_id, user_id))
        await db.append_followup_chat_message(account_id, user_id, "assistant", ai_reply, inc_reply_count=True)
        asyncio.create_task(_async_update_kol_profile(account_id, user_id, full_history, ai_provider, ai_keys, kwargs))

        if new_status != "active":
            await db.update_followup_chat_status(account_id, user_id, new_status)
        logger.info("[AIFollowUp] ✅ AI reply sent to user @%s (%d) (Tier: %s, Score: %d)", sender_username or '?', user_id, lead_tier, intent_score)
        return True
    except Exception as ex_send:
        logger.error("[AIFollowUp] Error in AI reply pipeline for user %d: %s", user_id, ex_send)
        return False
    finally:
        # Guarantee cleanup — prevent permanent lock on any error path
        # Only discard if _remove_ai_send_after_delay was NOT already scheduled (i.e. send failed)
        if (account_id, user_id) in _pending_ai_sends:
            _pending_ai_sends.discard((account_id, user_id))


def _make_handler(account_id: int):
    """Factory creating the event handler for a specific account."""

    async def _handler(event: events.NewMessage.Event):
        # Only private 1-on-1 messages
        if not event.is_private:
            return

        # ── Outgoing human admin manual interception ──
        if event.out:
            try:
                dest_id = event.chat_id
                if not dest_id:
                    return

                msg_content = event.raw_text or event.message.message or ""
                if not msg_content.strip():
                    return

                # If AI is currently sending, this outgoing msg IS the AI message — skip
                if (account_id, dest_id) in _pending_ai_sends:
                    return

                # Human admin is manually sending → ALWAYS lock out AI
                existing_f_chat = await db.get_followup_chat(account_id, dest_id)
                if existing_f_chat:
                    logger.info(
                        "🚨 [AIFollowUp] Human Admin manual message detected to user %d (acc=%d)! Pausing AI Agent.",
                        dest_id, account_id
                    )
                    await db.set_human_takeover(account_id, dest_id)
                    await db.append_followup_chat_message(account_id, dest_id, "assistant", f"[Human Admin]: {msg_content}")
                    asyncio.create_task(_async_distill_human_rule(account_id, dest_id, msg_content))
            except Exception as ex_out:
                logger.debug("[AIFollowUp] Error in outgoing human interception: %s", ex_out)
            return

        # Dedup check
        msg_id = event.message.id
        sender_id = event.sender_id

        dedup_key = (account_id, sender_id, msg_id)
        if dedup_key in _seen:
            return
        if len(_seen) >= _MAX_SEEN:
            _seen.clear()
        _seen.add(dedup_key)

        sender = await event.get_sender()
        sender_username = getattr(sender, "username", None) or ""
        first_name = getattr(sender, "first_name", "") or ""
        last_name = getattr(sender, "last_name", "") or ""
        sender_name = f"{first_name} {last_name}".strip() or sender_username or str(sender_id)

        message_text = event.raw_text or event.message.message or ""

        # Bot check
        if is_bot_account(sender_username, sender_name, message_text):
            logger.debug("[Inbox] Ignored bot message from @%s (%s)", sender_username, sender_name)
            await db.add_dm_reply({
                "watcher_id": None,
                "account_id": account_id,
                "sender_user_id": sender_id,
                "sender_username": sender_username,
                "sender_name": sender_name,
                "message_text": message_text,
                "platform": "telegram",
            })
            chat = await db.get_or_create_followup_chat(
                account_id=account_id,
                user_id=sender_id,
                username=sender_username,
                name=sender_name
            )
            if chat:
                await db.update_followup_chat_status(account_id, sender_id, "bot_ignored")
            return

        # Record hot lead reply
        watcher_log = await db.find_watcher_log_for_user(sender_id)
        watcher_id = watcher_log["watcher_id"] if watcher_log else None

        await db.add_dm_reply({
            "watcher_id": watcher_id,
            "account_id": account_id,
            "sender_user_id": sender_id,
            "sender_username": sender_username,
            "sender_name": sender_name,
            "message_text": message_text,
            "platform": "telegram",
        })

        # Blacklist check
        if await db.is_user_blacklisted(user_id=sender_id, username=sender_username):
            logger.info("[Inbox] 🚫 Skipped AI response — user @%s (id=%d) is in blacklist",
                        sender_username or "?", sender_id)
            return

        # Template rotation recording
        try:
            import template_rotation as tmpl_rot
            campaign_log = await db.find_campaign_log_for_user(sender_id)
            if campaign_log and campaign_log.get("template_variant_id"):
                await tmpl_rot.record_reply(
                    template_id=campaign_log["template_variant_id"],
                    variant_index=campaign_log.get("template_variant_index", 0),
                    campaign_id=campaign_log.get("campaign_id"),
                )
            elif watcher_log and watcher_log.get("template_variant_index") is not None:
                w_id = watcher_log.get("watcher_id")
                if w_id:
                    await tmpl_rot.record_reply(
                        template_id=w_id,
                        variant_index=watcher_log["template_variant_index"],
                        watcher_id=w_id,
                    )
        except Exception as _tr_err:
            logger.debug("[Inbox] Template rotation reply tracking error: %s", _tr_err)

        # AI Follow-up Engine
        try:
            enabled_str = await db.get_setting("ai_followup_enabled", "true")
            if enabled_str.lower() in ("true", "1"):
                # Identify campaign
                target_campaign_id = None
                running_log = await db.find_running_campaign_log_for_user(sender_id) or await db.find_campaign_log_for_user(sender_id)
                if running_log:
                    target_campaign_id = running_log.get("campaign_id")

                chat = await db.get_or_create_followup_chat(
                    account_id=account_id,
                    user_id=sender_id,
                    username=sender_username,
                    name=sender_name,
                    campaign_id=target_campaign_id,
                    watcher_id=watcher_id
                )

                current_status = chat.get("status", "active")

                # 60m Timeout check on incoming message — use human_takeover_at
                if current_status in ("needs_human", "paused_admin"):
                    # Prefer human_takeover_at; fallback to updated_at for legacy rows
                    takeover_at_str = chat.get("human_takeover_at") or chat.get("updated_at")
                    should_resume = False
                    if takeover_at_str:
                        try:
                            # SQLite datetime('now') is UTC without tzinfo — always compare in UTC
                            dt_takeover = datetime.fromisoformat(takeover_at_str).replace(tzinfo=timezone.utc)
                            now_utc = datetime.now(timezone.utc)
                            if (now_utc - dt_takeover).total_seconds() >= 3600:
                                should_resume = True
                        except Exception:
                            pass
                    if should_resume:
                        logger.info("[AIFollowUp] ⏰ 60m elapsed since human takeover for user %d — auto-resuming!", sender_id)
                        await db.update_followup_chat_status(account_id, sender_id, "active")
                        current_status = "active"

                # Always append incoming user message
                chat = await db.append_followup_chat_message(account_id, sender_id, "user", message_text)

                if current_status in ("paused_admin", "onboarded", "needs_human", "bot_ignored"):
                    logger.info("[AIFollowUp] User %d status '%s' (<60m since human takeover) — recorded msg, skipping AI reply",
                                sender_id, current_status)
                    return

                # Handover keywords check
                account_obj = await db.get_account(account_id)
                agent_id = account_obj.get("ai_agent_id") if account_obj else None

                # ── If this account has AI turned off ("Tắt"), skip AI reply entirely ──
                if not agent_id:
                    logger.info("[AIFollowUp] Account #%d has AI Agent disabled (Tắt) — skipping AI reply for user %d",
                                account_id, sender_id)
                    return

                agent_config = await db.get_ai_agent(agent_id) if agent_id else None

                agent_handover_kws = agent_config.get("handover_keywords", []) if agent_config else []
                if isinstance(agent_handover_kws, str):
                    try: agent_handover_kws = json.loads(agent_handover_kws)
                    except Exception: agent_handover_kws = []

                msg_lower = message_text.lower().strip()
                needs_handover = any(kw.lower().strip() in msg_lower for kw in agent_handover_kws if kw and kw.strip())
                max_replies_val = agent_config.get("max_replies", 20) if agent_config else 20

                if needs_handover:
                    logger.warning("[AIFollowUp] 🚨 Handover keyword matched for user %d (acc=%d)", sender_id, account_id)
                    await db.update_followup_chat_status(account_id, sender_id, "needs_human")
                    asyncio.create_task(_notify_main_account_handover(
                        account_id, sender_id, sender_username,
                        reason=f"Handover keyword detected: '{message_text[:80]}'"
                    ))
                    return
                elif chat.get("reply_count", 0) >= max_replies_val:
                    logger.warning("[AIFollowUp] 🚨 Max replies (%d) reached for user %d (acc=%d)", max_replies_val, sender_id, account_id)
                    await db.update_followup_chat_status(account_id, sender_id, "needs_human")
                    asyncio.create_task(_notify_main_account_handover(
                        account_id, sender_id, sender_username,
                        reason=f"Max replies ({max_replies_val}) reached"
                    ))
                    return

                # Trigger AI Reply!
                await generate_and_send_ai_reply_for_chat(
                    account_id=account_id,
                    user_id=sender_id,
                    sender_username=sender_username,
                    sender_name=sender_name,
                    target_campaign_id=target_campaign_id,
                    watcher_id=watcher_id,
                    event=event,
                )
        except Exception as ex_ai:
            logger.error("[AIFollowUp] Error in AI follow-up engine: %s", ex_ai, exc_info=True)

    return _handler


# ── Public API ─────────────────────────────────────────────────────────────────

def _register_account(account_id: int) -> None:
    client = tg.get_client(account_id)
    if not client or not client.is_connected():
        logger.debug("[Inbox] acc=%d: client not connected, skip register", account_id)
        return
    _unregister_account(account_id)
    handler_fn = _make_handler(account_id)
    client.add_event_handler(handler_fn, events.NewMessage())
    _handler_removers[account_id] = (client, handler_fn)
    logger.info("[Inbox] acc=%d: reply handler registered", account_id)


def _unregister_account(account_id: int) -> None:
    entry = _handler_removers.pop(account_id, None)
    if entry:
        client, handler_fn = entry
        try:
            client.remove_event_handler(handler_fn, events.NewMessage())
        except Exception:
            pass


async def _followup_periodic_loop():
    """Continuous background worker loop to auto-resume timed-out takeovers and process drips."""
    logger.info("[AIFollowUp] 🔄 Continuous AI follow-up background worker active.")
    while True:
        try:
            await asyncio.sleep(300)  # Check every 5 minutes
            await process_drip_followups()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[AIFollowUp] Error in periodic follow-up loop: %s", e)
            await asyncio.sleep(10)


async def start_reply_tracker() -> None:
    """Register inbox handlers on all connected accounts & start background worker."""
    global _periodic_worker_task
    accounts = await db.get_all_accounts()
    registered = 0
    for acc in accounts:
        if acc.get("is_logged_in"):
            _register_account(acc["id"])
            registered += 1
    logger.info("[Inbox] Reply tracker started — %d account(s) monitored", registered)

    if _periodic_worker_task is None or _periodic_worker_task.done():
        _periodic_worker_task = asyncio.create_task(_followup_periodic_loop())


async def stop_reply_tracker() -> None:
    """Remove all handlers & stop periodic worker."""
    global _periodic_worker_task
    if _periodic_worker_task and not _periodic_worker_task.done():
        _periodic_worker_task.cancel()
        _periodic_worker_task = None
    for acc_id in list(_handler_removers.keys()):
        _unregister_account(acc_id)
    logger.info("[Inbox] Reply tracker stopped")


def register_account(account_id: int) -> None:
    _register_account(account_id)


def unregister_account(account_id: int) -> None:
    _unregister_account(account_id)


async def process_drip_followups() -> dict:
    """
    1. Scan human takeover chats (>60m since update) where user is waiting for a reply -> Auto-resume & Send AI Reply!
    2. Scan stuck active chats (>2m since user message with 0 replies generated) -> Send AI Reply!
    3. Scan inactive followup chats (>48h / >5d) -> Process Drip Follow-ups!
    """
    import aiosqlite
    sent_count = 0
    errors = []

    # ── 1. Auto-resume chats paused for human takeover if >60m elapsed ──
    try:
        async with db.get_db() as db_conn:
            db_conn.row_factory = aiosqlite.Row
            cur_to = await db_conn.execute("""
                SELECT * FROM ai_followup_chats
                WHERE status IN ('needs_human', 'paused_admin')
                  AND (human_takeover_at IS NULL OR datetime(human_takeover_at) <= datetime('now', '-60 minutes'))
                  AND datetime(updated_at) <= datetime('now', '-60 minutes')
                LIMIT 20
            """)
            to_chats = [dict(r) for r in await cur_to.fetchall()]

        for t in to_chats:
            hist = json.loads(t.get("history_json", "[]"))
            if hist and hist[-1].get("role") == "user" and (hist[-1].get("content") or "").strip():
                logger.info("[AIFollowUp] ⏰ 60m timeout: Human admin silent after user msg for user @%s (%d) — auto-resuming & generating AI reply!",
                            t.get("username", "?"), t["user_id"])
                await db.update_followup_chat_status(t["account_id"], t["user_id"], "active")
                client = tg.get_client(t["account_id"])
                if client and client.is_connected():
                    asyncio.create_task(generate_and_send_ai_reply_for_chat(
                        account_id=t["account_id"],
                        user_id=t["user_id"],
                        sender_username=t.get("username"),
                        sender_name=t.get("name"),
                        target_campaign_id=t.get("campaign_id"),
                        watcher_id=t.get("watcher_id"),
                    ))
                    sent_count += 1
            elif hist and hist[-1].get("role") == "assistant":
                # Admin or assistant was the last to speak -> simply flip back to active
                await db.update_followup_chat_status(t["account_id"], t["user_id"], "active")
    except Exception as e_to:
        logger.error("[AIFollowUp] Error processing 60m auto-resume: %s", e_to)

    # ── 2. Recover stuck active chats (>2m since user message) ──
    try:
        async with db.get_db() as db_conn:
            db_conn.row_factory = aiosqlite.Row
            cur_stuck = await db_conn.execute("""
                SELECT * FROM ai_followup_chats
                WHERE status = 'active'
                  AND datetime(updated_at) <= datetime('now', '-2 minutes')
                  AND datetime(updated_at) >= datetime('now', '-48 hours')
                  AND (human_takeover_at IS NULL OR datetime(human_takeover_at) <= datetime('now', '-60 minutes'))
                LIMIT 20
            """)
            stuck_chats = [dict(r) for r in await cur_stuck.fetchall()]

        for sc in stuck_chats:
            hist = json.loads(sc.get("history_json", "[]"))
            if hist and hist[-1].get("role") == "user" and (hist[-1].get("content") or "").strip():
                if (sc["account_id"], sc["user_id"]) not in _pending_ai_sends:
                    client = tg.get_client(sc["account_id"])
                    if client and client.is_connected():
                        logger.info("[AIFollowUp] 🔄 Recovering pending AI reply for user @%s (%d)...",
                                    sc.get("username", "?"), sc["user_id"])
                        asyncio.create_task(generate_and_send_ai_reply_for_chat(
                            account_id=sc["account_id"],
                            user_id=sc["user_id"],
                            sender_username=sc.get("username"),
                            sender_name=sc.get("name"),
                            target_campaign_id=sc.get("campaign_id"),
                            watcher_id=sc.get("watcher_id"),
                        ))
                        sent_count += 1
    except Exception as e_stuck:
        logger.error("[AIFollowUp] Error recovering stuck chats: %s", e_stuck)

    # ── 3. Process Drip Follow-ups ──
    try:
        async with db.get_db() as db_conn:
            db_conn.row_factory = aiosqlite.Row
            cursor = await db_conn.execute("""
                SELECT * FROM ai_followup_chats
                WHERE status = 'active'
                  AND (lead_tier IN ('Tier A', 'Tier B') OR intent_score >= 50)
                  AND last_drip_stage < 2
                  AND datetime(updated_at) <= datetime('now', '-48 hours')
                ORDER BY updated_at ASC
                LIMIT 20
            """)
            drip_chats = [dict(r) for r in await cursor.fetchall()]

        for chat in drip_chats:
            account_id = chat["account_id"]
            user_id = chat["user_id"]
            stage = chat.get("last_drip_stage", 0) + 1

            if await db.is_user_blacklisted(user_id=user_id, username=chat.get("username", "")):
                continue

            client = tg.get_client(account_id)
            if not client or not client.is_connected():
                continue

            account_obj = await db.get_account(account_id)
            agent_id = account_obj.get("ai_agent_id") if account_obj else None
            agent_config = await db.get_ai_agent(agent_id) if agent_id else None

            if stage == 1:
                drip_instruction = (
                    "The user has not replied in 2 days. Send a friendly, non-pushy follow-up (1-2 short sentences) "
                    "mentioning our latest crypto trading competition with $50,000 prize pool and VIP Maker fee discounts."
                )
            else:
                drip_instruction = (
                    "The user has been silent for 5 days. Send a courteous, brief final check-in asking if they need "
                    "any customized rate proposal or have any questions before we close this conversation."
                )

            sys_p = agent_config.get("system_prompt", "") if agent_config else ""
            combined = sys_p + f"\n\n[DRIP FOLLOWUP STAGE {stage} INSTRUCTION]: " + drip_instruction

            try:
                history = json.loads(chat.get("history_json", "[]"))
                ai_provider = agent_config.get("provider", "gemini") if agent_config else "gemini"
                ai_keys = agent_config.get("api_keys_json", []) if agent_config else []
                if isinstance(ai_keys, str):
                    ai_keys = json.loads(ai_keys)
                if not ai_keys:
                    continue

                kwargs = {}
                if ai_provider in ("openai_compatible", "chatgpt_oauth") and agent_config:
                    if agent_config.get("base_url"): kwargs["base_url"] = agent_config["base_url"]
                    if agent_config.get("model"): kwargs["model"] = agent_config["model"]

                msg = await ai_rmx.generate_chat_response(history[-5:], combined, ai_provider, ai_keys, **kwargs)
                if msg:
                    msg = sanitize_telegram_html(msg)
                    await tg.send_text_message(account_id, user_id, msg)
                    await db.append_followup_chat_message(account_id, user_id, "assistant", msg, inc_reply_count=True)
                    async with db.get_db() as db_conn2:
                        await db_conn2.execute(
                            "UPDATE ai_followup_chats SET last_drip_stage = ?, updated_at = datetime('now') WHERE account_id = ? AND user_id = ?",
                            (stage, account_id, user_id)
                        )
                        await db_conn2.commit()
                    sent_count += 1
            except Exception as e:
                logger.error("[DripEngine] Failed drip for user %d: %s", user_id, e)
                errors.append(str(e))
    except Exception as e_drip:
        logger.error("[DripEngine] Error in drip batch: %s", e_drip)

    return {"sent": sent_count, "errors": errors}
