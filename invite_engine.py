"""
Invite Engine - Campaign executor for inviting members to channels/groups.
Mirrors the DM campaign execution pattern in routes/members.py.
"""
import asyncio
import random
import logging
from datetime import datetime, timezone

import database as db
import telegram_client as tg
from telethon import errors
from telethon.tl.types import PeerUser

logger = logging.getLogger("tg-scheduler.invite")

# Module-level state for active campaigns
_active_invite_campaigns: dict[int, bool] = {}


def stop_invite_campaign(campaign_id: int):
    """Signal an invite campaign to stop."""
    _active_invite_campaigns[campaign_id] = False


async def run_invite_campaign(campaign_id: int):
    """
    Main execution loop for an invite campaign.
    Supports two modes: 'direct' (InviteToChannelRequest) and 'dm_link' (send DM with invite link).
    """
    _active_invite_campaigns[campaign_id] = True

    try:
        campaign = await db.get_invite_campaign(campaign_id)
        if not campaign:
            logger.error(f"[Invite] Campaign {campaign_id} not found")
            return

        # Load config
        target_chat = campaign["target_chat"]
        invite_mode = campaign.get("invite_mode", "direct")
        invite_link = campaign.get("invite_link", "")
        dm_message_template = campaign.get("dm_message", "")
        sender_ids = campaign.get("sender_account_ids", [])
        daily_limit = campaign.get("daily_limit", 50)
        delay_min = campaign.get("delay_min", 45)
        delay_max = campaign.get("delay_max", 120)
        target_title = campaign.get("target_chat_title", target_chat)

        if not sender_ids:
            logger.error(f"[Invite] Campaign {campaign_id}: no sender accounts")
            await db.update_invite_campaign_status(campaign_id, "error")
            return

        # Load members
        members = await db.get_scraped_members(campaign["scrape_job_id"], limit=10000)
        if not members:
            logger.warning(f"[Invite] Campaign {campaign_id}: no members found")
            await db.update_invite_campaign_status(campaign_id, "completed",
                                                     invited=0, failed=0, skipped=0)
            return

        # Get already-processed user IDs
        sent_user_ids = await db.get_invite_campaign_sent_user_ids(campaign_id)

        # Update total targets
        total = len(members)
        await db.update_invite_campaign_status(campaign_id, "running")

        # Counters
        invited = campaign.get("invited_count", 0) or 0
        failed = campaign.get("failed_count", 0) or 0
        skipped = campaign.get("skipped_count", 0) or 0
        daily_sent = 0
        consecutive_errors = 0
        account_idx = 0
        flooded_accounts = set()

        logger.info(f"[Invite] Campaign {campaign_id} started: mode={invite_mode}, "
                     f"targets={total}, already_done={len(sent_user_ids)}, "
                     f"senders={len(sender_ids)}, daily_limit={daily_limit}")

        for member in members:
            # Check stop signal
            if not _active_invite_campaigns.get(campaign_id, False):
                logger.info(f"[Invite] Campaign {campaign_id} stopped by user")
                await db.update_invite_campaign_status(campaign_id, "paused",
                                                         invited=invited, failed=failed, skipped=skipped)
                return

            user_id = member.get("user_id")
            username = member.get("username", "")
            first_name = member.get("first_name", "")

            # Skip already processed
            if user_id in sent_user_ids:
                continue

            # Check daily limit
            if daily_sent >= daily_limit:
                logger.info(f"[Invite] Campaign {campaign_id}: daily limit reached ({daily_sent}/{daily_limit})")
                await db.update_invite_campaign_status(campaign_id, "paused",
                                                         invited=invited, failed=failed, skipped=skipped)
                return

            # Skip bots
            if member.get("is_bot"):
                skipped += 1
                await db.add_invite_campaign_log(
                    campaign_id, 0, user_id, username, "skipped", "Bot account"
                )
                continue

            # Account rotation
            available_senders = [sid for sid in sender_ids if sid not in flooded_accounts]
            if not available_senders:
                logger.warning(f"[Invite] Campaign {campaign_id}: all accounts flooded, pausing")
                await db.update_invite_campaign_status(campaign_id, "paused",
                                                         invited=invited, failed=failed, skipped=skipped)
                return

            acc_id = available_senders[account_idx % len(available_senders)]
            account_idx += 1

            try:
                if invite_mode == "direct":
                    # Direct invite via API
                    try:
                        if username:
                            user_target = username
                        else:
                            user_target = PeerUser(user_id)

                        result = await tg.invite_to_channel(acc_id, target_chat, user_target)

                        if result.get("success"):
                            invited += 1
                            daily_sent += 1
                            consecutive_errors = 0
                            await db.add_invite_campaign_log(
                                campaign_id, acc_id, user_id, username, "invited"
                            )
                            logger.info(f"[Invite] Campaign {campaign_id}: invited {username or user_id} "
                                         f"via account {acc_id} ({invited}/{total})")
                        else:
                            error_msg = result.get("error", "Unknown error")
                            if "already" in error_msg.lower() or "participant" in error_msg.lower():
                                skipped += 1
                                await db.add_invite_campaign_log(
                                    campaign_id, acc_id, user_id, username, "already_member", error_msg
                                )
                            else:
                                failed += 1
                                consecutive_errors += 1
                                await db.add_invite_campaign_log(
                                    campaign_id, acc_id, user_id, username, "failed", error_msg
                                )

                    except errors.UserAlreadyParticipantError:
                        skipped += 1
                        await db.add_invite_campaign_log(
                            campaign_id, acc_id, user_id, username, "already_member"
                        )

                elif invite_mode == "dm_link":
                    # Send DM with invite link
                    msg_text = dm_message_template
                    msg_text = msg_text.replace("{name}", first_name or username or "bạn")
                    msg_text = msg_text.replace("{group_name}", target_title or target_chat)
                    msg_text = msg_text.replace("{invite_link}", invite_link or "")

                    try:
                        if username:
                            user_target = username
                        else:
                            user_target = PeerUser(user_id)

                        await tg.send_message(acc_id, user_target, msg_text)
                        invited += 1
                        daily_sent += 1
                        consecutive_errors = 0
                        await db.add_invite_campaign_log(
                            campaign_id, acc_id, user_id, username, "invited"
                        )
                        logger.info(f"[Invite] Campaign {campaign_id}: DM invite link to {username or user_id} "
                                     f"via account {acc_id} ({invited}/{total})")

                    except errors.UserPrivacyRestrictedError:
                        skipped += 1
                        await db.add_invite_campaign_log(
                            campaign_id, acc_id, user_id, username, "skipped", "Privacy restricted"
                        )

            except errors.FloodWaitError as e:
                wait_time = e.seconds + random.randint(10, 30)
                logger.warning(f"[Invite] FloodWait {e.seconds}s on account {acc_id}, waiting {wait_time}s")
                flooded_accounts.add(acc_id)
                await asyncio.sleep(min(wait_time, 300))
                flooded_accounts.discard(acc_id)
                continue

            except errors.PeerFloodError:
                logger.warning(f"[Invite] PeerFlood on account {acc_id}, excluding for this run")
                flooded_accounts.add(acc_id)
                consecutive_errors += 1
                await asyncio.sleep(random.uniform(120, 300))
                continue

            except errors.UserPrivacyRestrictedError:
                skipped += 1
                await db.add_invite_campaign_log(
                    campaign_id, acc_id, user_id, username, "skipped", "Privacy restricted"
                )
                continue

            except errors.ChatAdminRequiredError:
                logger.error(f"[Invite] Campaign {campaign_id}: account {acc_id} needs admin rights!")
                failed += 1
                await db.add_invite_campaign_log(
                    campaign_id, acc_id, user_id, username, "failed", "Admin rights required"
                )
                flooded_accounts.add(acc_id)
                continue

            except Exception as e:
                failed += 1
                consecutive_errors += 1
                error_msg = str(e)[:200]
                await db.add_invite_campaign_log(
                    campaign_id, acc_id, user_id, username, "failed", error_msg
                )
                logger.error(f"[Invite] Campaign {campaign_id}: error inviting {username or user_id}: {error_msg}")

            # Update progress every 5 operations
            ops = invited + failed + skipped
            if ops % 5 == 0 and ops > 0:
                await db.update_invite_campaign_status(campaign_id, "running",
                                                         invited=invited, failed=failed, skipped=skipped)

            # Auto-pause on too many consecutive errors
            if consecutive_errors >= 10:
                logger.warning(f"[Invite] Campaign {campaign_id}: 10 consecutive errors, auto-pausing")
                await db.update_invite_campaign_status(campaign_id, "paused",
                                                         invited=invited, failed=failed, skipped=skipped)
                return

            # Random delay with exponential backoff
            backoff = min(2 ** max(consecutive_errors - 3, 0), 16) if consecutive_errors > 3 else 1
            delay = random.uniform(delay_min, delay_max) * backoff
            delay = min(delay, 300)
            await asyncio.sleep(delay)

        # All members processed
        logger.info(f"[Invite] Campaign {campaign_id} completed: invited={invited}, failed={failed}, skipped={skipped}")
        await db.update_invite_campaign_status(campaign_id, "completed",
                                                 invited=invited, failed=failed, skipped=skipped)

    except Exception as e:
        logger.error(f"[Invite] Campaign {campaign_id} crashed: {e}", exc_info=True)
        await db.update_invite_campaign_status(campaign_id, "error")
    finally:
        _active_invite_campaigns.pop(campaign_id, None)
