"""
Telegram Client Manager - Multi-account support.
Each account gets its own Telethon client instance.
"""
import os
import re
import random
import logging
import asyncio
from collections import deque
from telethon import TelegramClient, errors
from telethon.tl.types import (
    InputMediaPoll, Poll, PollAnswer,
    Channel, Chat, User,
    TextWithEntities,
    PeerChannel, PeerChat
)
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("tg-scheduler")

SESSION_DIR = os.getenv("SESSION_DIR", os.path.join(os.path.dirname(__file__), "sessions"))
os.makedirs(SESSION_DIR, exist_ok=True)

# Multi-account: account_id -> TelegramClient
_clients: dict[int, TelegramClient] = {}
_code_hashes: dict[int, str] = {}
_auth_cache: dict[int, bool] = {}
_me_cache: dict[int, dict] = {}


def get_client(account_id: int) -> TelegramClient | None:
    return _clients.get(account_id)


async def _get_entity_safe(client: TelegramClient, chat_id: int):
    """
    Robustly resolve a chat entity.
    Plain positive integers are ambiguous in Telethon (treated as PeerUser).
    Try PeerChannel and PeerChat as fallbacks for groups/channels.
    """
    # 1. Try direct lookup (works when entity is already cached)
    try:
        return await client.get_entity(chat_id)
    except Exception:
        pass

    # 2. Try as Channel / Supergroup
    try:
        return await client.get_entity(PeerChannel(chat_id))
    except Exception:
        pass

    # 3. Try as basic Group
    try:
        return await client.get_entity(PeerChat(chat_id))
    except Exception:
        pass

    # 4. Try Bot-API negative ID format (-100XXXXXXXXXX)
    try:
        bot_api_id = int(f"-100{chat_id}")
        return await client.get_entity(bot_api_id)
    except Exception:
        pass

    # 5. If not found in cache, force fetch dialogs to populate Telethon cache, then try again
    try:
        logger.info(f"Chat ID {chat_id} not found in cache. Fetching dialogs to populate Telethon cache...")
        await client.get_dialogs(limit=200)
    except Exception as e:
        logger.warning(f"Failed to fetch dialogs to update cache: {e}")

    # Retry resolving after cache population
    try:
        return await client.get_entity(PeerChannel(chat_id))
    except Exception:
        pass

    try:
        return await client.get_entity(PeerChat(chat_id))
    except Exception:
        pass

    try:
        bot_api_id = int(f"-100{chat_id}")
        return await client.get_entity(bot_api_id)
    except Exception:
        pass

    try:
        return await client.get_entity(chat_id)
    except Exception as final_err:
        raise Exception(
            f"Cannot resolve entity for chat_id={chat_id}. "
            f"Make sure the account has joined the group/channel."
        ) from final_err


def _parse_proxy(proxy_url: str | None):
    """
    Parse proxy URL string to Telethon proxy tuple.
    Supported formats:
      socks5://user:pass@host:port
      socks5://host:port
      socks4://host:port
      http://user:pass@host:port
      http://host:port
      mtproto://host:port/secret   (MTProto proxy)
    Returns Telethon-compatible proxy tuple or None.
    """
    if not proxy_url or not proxy_url.strip():
        return None
    import re
    url = proxy_url.strip()

    # MTProto proxy: mtproto://host:port/secret
    if url.startswith("mtproto://"):
        rest = url[len("mtproto://"):]
        parts = rest.split("/")
        host_port = parts[0]
        secret = parts[1] if len(parts) > 1 else ""
        host, port = host_port.rsplit(":", 1) if ":" in host_port else (host_port, "443")
        return (host, int(port), secret)

    # SOCKS5 / SOCKS4 / HTTP
    try:
        import socks
        m = re.match(
            r'(?P<scheme>socks5|socks4|http)://'
            r'(?:(?P<user>[^:@]+):(?P<passwd>[^@]*)@)?'
            r'(?P<host>[^:]+):(?P<port>\d+)',
            url, re.IGNORECASE
        )
        if not m:
            return None
        scheme = m.group("scheme").lower()
        proxy_type = {
            "socks5": socks.SOCKS5,
            "socks4": socks.SOCKS4,
            "http":   socks.HTTP,
        }[scheme]
        user   = m.group("user")   or None
        passwd = m.group("passwd") or None
        host   = m.group("host")
        port   = int(m.group("port"))
        return (proxy_type, host, port, True, user, passwd)
    except ImportError:
        logger.warning(
            "PySocks not installed. Install with: pip install PySocks\n"
            f"Proxy {proxy_url} will be IGNORED."
        )
        return None
    except Exception:
        return None


async def create_client(
    account_id: int,
    api_id: int,
    api_hash: str,
    session_name: str,
    proxy_url: str | None = None,
) -> TelegramClient:
    """Create a Telethon client for an account."""
    session_path = os.path.join(SESSION_DIR, session_name)

    proxy = _parse_proxy(proxy_url)

    # MTProto proxy uses a different connection class
    if proxy_url and proxy_url.strip().startswith("mtproto://"):
        from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
        client = TelegramClient(
            session_path, api_id, api_hash,
            connection=ConnectionTcpMTProxyRandomizedIntermediate,
            proxy=proxy,
        )
        logger.info(f"Account {account_id}: using MTProto proxy {proxy_url}")
    elif proxy:
        client = TelegramClient(session_path, api_id, api_hash, proxy=proxy)
        logger.info(f"Account {account_id}: using proxy {proxy_url}")
    else:
        client = TelegramClient(session_path, api_id, api_hash)

    _clients[account_id] = client
    return client


async def start_client(account_id: int) -> bool:
    """Connect and check authorization for an account."""
    client = _clients.get(account_id)
    if not client:
        return False
    try:
        await client.connect()
        is_auth = await client.is_user_authorized()
        _auth_cache[account_id] = is_auth
        if is_auth:
            try:
                # Force updates stream and cache initialization
                await client.get_dialogs(limit=5)
            except Exception as e:
                logger.warning(f"Account {account_id}: Failed to get dialogs on startup: {e}")
            try:
                me = await client.get_me()
                _me_cache[account_id] = {
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name or "",
                    "username": me.username or "",
                    "phone": me.phone or ""
                }
                logger.info(f"Account {account_id}: connected as @{me.username} (id={me.id})")
            except Exception as e:
                logger.warning(f"Account {account_id}: Failed to get self details: {e}")
                logger.info(f"Account {account_id}: connected (authorized)")
            return True
        logger.info(f"Account {account_id}: connected (not authorized)")
        return False
    except Exception as e:
        logger.error(f"Account {account_id}: connect failed: {e}")
        return False


async def send_code(account_id: int, phone: str) -> str:
    """Send login code. Returns phone_code_hash."""
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account client not found")

    try:
        if not client.is_connected():
            await client.connect()
    except Exception as e:
        logger.error(f"Account {account_id}: reconnect error: {e}")
        raise

    try:
        result = await client.send_code_request(phone)
        _code_hashes[account_id] = result.phone_code_hash
        logger.info(f"Account {account_id}: code sent to {phone}")
        return result.phone_code_hash
    except errors.ApiIdInvalidError:
        raise Exception("API_ID hoặc API_HASH không hợp lệ")
    except errors.PhoneNumberInvalidError:
        raise Exception("Số điện thoại không hợp lệ (+84...)")
    except errors.FloodWaitError as e:
        raise Exception(f"Vui lòng đợi {e.seconds} giây")
    except Exception as e:
        logger.error(f"Account {account_id}: send code error: {e}")
        raise


async def sign_in(account_id: int, phone: str, code: str, phone_code_hash: str,
                  password: str | None = None) -> dict:
    """Sign in with OTP code."""
    client = _clients.get(account_id)
    if not client:
        return {"success": False, "error": "Account client not found"}

    try:
        user = await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        logger.info(f"Account {account_id}: signed in as {user.first_name}")
        return {
            "success": True,
            "user_id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name or "",
            "username": user.username or ""
        }
    except errors.SessionPasswordNeededError:
        if password:
            user = await client.sign_in(password=password)
            return {
                "success": True,
                "user_id": user.id,
                "first_name": user.first_name,
                "last_name": user.last_name or "",
                "username": user.username or ""
            }
        return {"success": False, "error": "2FA password required", "needs_password": True}
    except errors.PhoneCodeInvalidError:
        return {"success": False, "error": "Mã OTP không đúng"}
    except Exception as e:
        logger.error(f"Account {account_id}: sign in error: {e}")
        return {"success": False, "error": str(e)}


async def logout(account_id: int):
    """Log out an account."""
    _auth_cache.pop(account_id, None)
    _me_cache.pop(account_id, None)
    client = _clients.get(account_id)
    if client and client.is_connected():
        await client.log_out()
        logger.info(f"Account {account_id}: logged out")


async def is_authorized(account_id: int) -> bool:
    if account_id in _auth_cache:
        return _auth_cache[account_id]
    client = _clients.get(account_id)
    if not client:
        return False
    if not client.is_connected():
        try:
            await client.connect()
        except Exception:
            return False
    is_auth = await client.is_user_authorized()
    _auth_cache[account_id] = is_auth
    return is_auth


async def get_me(account_id: int) -> dict | None:
    if account_id in _me_cache:
        return _me_cache[account_id]
    if not await is_authorized(account_id):
        return None
    client = _clients[account_id]
    me = await client.get_me()
    if me:
        _me_cache[account_id] = {
            "user_id": me.id,
            "first_name": me.first_name,
            "last_name": me.last_name or "",
            "username": me.username or "",
            "phone": me.phone or ""
        }
        return _me_cache[account_id]
    return None


async def get_dialogs(account_id: int) -> list:
    if not await is_authorized(account_id):
        return []
    client = _clients[account_id]
    dialogs = await client.get_dialogs(limit=200)
    result = []
    for d in dialogs:
        entity = d.entity
        if isinstance(entity, Channel):
            chat_type = "channel" if entity.broadcast else "supergroup"
            result.append({
                "chat_id": entity.id,
                "chat_title": entity.title,
                "chat_type": chat_type,
                "username": entity.username or "",
                "participants_count": getattr(entity, "participants_count", None)
            })
        elif isinstance(entity, Chat):
            result.append({
                "chat_id": entity.id,
                "chat_title": entity.title,
                "chat_type": "group",
                "username": "",
                "participants_count": entity.participants_count
            })
    return result




async def check_accounts_in_groups(account_ids: list[int], group_ids: list[int]) -> dict:
    """
    Check which accounts are NOT members of the specified groups.
    Returns: {
        "warnings": [
            {
                "account_id": 2,
                "account_name": "BD Phạm",
                "missing_groups": [{"group_id": 123, "group_title": "WEEX English"}]
            }
        ],
        "all_ok": bool
    }
    """
    warnings = []
    for acc_id in account_ids:
        client = _clients.get(acc_id)
        if not client:
            continue
        try:
            dialogs = await get_dialogs(acc_id)
            joined_ids = {abs(d["chat_id"]) for d in dialogs}

            # Build a map of id -> title from dialogs
            id_to_title = {abs(d["chat_id"]): d.get("chat_title", "") for d in dialogs}

            missing = []
            for gid in group_ids:
                clean_gid = abs(int(str(gid).replace("-100", "")))
                if clean_gid not in joined_ids:
                    title = id_to_title.get(clean_gid, f"Group ID {gid}")
                    missing.append({"group_id": gid, "group_title": title})

            if missing:
                # Get account name
                acc_name = f"Account {acc_id}"
                try:
                    me = await get_me(acc_id)
                    if me:
                        acc_name = " ".join(filter(None, [me.get("first_name",""), me.get("last_name","")])).strip() or me.get("username") or acc_name
                except Exception:
                    pass
                warnings.append({
                    "account_id": acc_id,
                    "account_name": acc_name,
                    "missing_groups": missing
                })
        except Exception as e:
            pass  # skip if can't check (account offline etc)

    return {"warnings": warnings, "all_ok": len(warnings) == 0}

async def leave_channel(account_id: int, chat_id: int) -> dict:
    """Leave (and optionally delete history of) a group or channel."""
    client = _clients.get(account_id)
    if not client:
        return {"success": False, "error": "Account not found"}
    try:
        from telethon.tl.functions.channels import LeaveChannelRequest
        from telethon.tl.functions.messages import DeleteHistoryRequest
        from telethon.tl.types import Channel, Chat

        entity = await client.get_entity(chat_id)

        if isinstance(entity, Channel):
            await client(LeaveChannelRequest(entity))
        else:
            # Regular group
            from telethon.tl.functions.messages import DeleteChatUserRequest
            me = await client.get_me()
            await client(DeleteChatUserRequest(chat_id=chat_id, user_id=me))

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def send_text_message(account_id: int, chat_id: int, text: str, parse_mode: str = "html") -> bool:
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    try:
        entity = await _get_entity_safe(client, chat_id)
        await client.send_message(entity, text, parse_mode=parse_mode)
        return True
    except errors.FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Account {account_id}: send text error to {chat_id}: {e}")
        raise


async def send_photo_message(account_id: int, chat_id: int, file_path: str, caption: str = "") -> bool:
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    try:
        entity = await _get_entity_safe(client, chat_id)
        await client.send_file(entity, file_path, caption=caption, parse_mode="html")
        return True
    except errors.FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Account {account_id}: send photo error: {e}")
        raise


async def send_video_message(account_id: int, chat_id: int, file_path: str, caption: str = "") -> bool:
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    try:
        entity = await _get_entity_safe(client, chat_id)
        await client.send_file(entity, file_path, caption=caption, parse_mode="html", supports_streaming=True)
        return True
    except errors.FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Account {account_id}: send video error: {e}")
        raise


async def send_document_message(account_id: int, chat_id: int, file_path: str, caption: str = "") -> bool:
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    try:
        entity = await _get_entity_safe(client, chat_id)
        await client.send_file(entity, file_path, caption=caption, parse_mode="html", force_document=True)
        return True
    except errors.FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Account {account_id}: send document error: {e}")
        raise


async def send_poll_message(account_id: int, chat_id: int, question: str, options: list[str],
                            multiple_choice: bool = False) -> bool:
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    try:
        entity = await _get_entity_safe(client, chat_id)
        poll_answers = [
            PollAnswer(text=TextWithEntities(text=opt, entities=[]), option=str(i).encode())
            for i, opt in enumerate(options)
        ]
        poll = Poll(
            id=0,
            question=TextWithEntities(text=question, entities=[]),
            answers=poll_answers,
            multiple_choice=multiple_choice
        )
        media = InputMediaPoll(poll=poll)
        await client.send_message(entity, file=media)
        return True
    except errors.FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Account {account_id}: send poll error: {e}")
        raise
async def join_channel(account_id: int, channel_link: str) -> dict:
    """Join a channel/group using its link or username."""
    client = _clients.get(account_id)
    if not client:
        return {"success": False, "error": "Account not found"}
    try:
        from telethon.tl.functions.channels import JoinChannelRequest
        entity = await client.get_entity(channel_link)
        await client(JoinChannelRequest(entity))
        return {"success": True, "title": getattr(entity, "title", channel_link)}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_similar_channels_and_contacts(account_id: int, channel_link: str) -> list[dict]:
    """Get recommendations of similar channels and extract admin contact handles from their description."""
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not found")
        
    from telethon.tl.functions.channels import GetChannelRecommendationsRequest, GetFullChannelRequest
    import re
    
    # 1. Resolve target channel
    try:
        channel = await client.get_entity(channel_link)
    except Exception as e:
        raise Exception(f"Không thể tìm thấy kênh '{channel_link}': {str(e)}")
        
    # 2. Get recommendations
    try:
        res = await client(GetChannelRecommendationsRequest(channel=channel))
    except Exception as e:
        raise Exception(f"Không thể lấy kênh tương tự: {str(e)}")
        
    leads = []
    username_regex = re.compile(r'@([a-zA-Z0-9_]{5,32})')
    
    for chat in getattr(res, 'chats', []):
        if not getattr(chat, 'broadcast', False):
            continue
            
        username = getattr(chat, 'username', None)
        title = getattr(chat, 'title', '')
        participants_count = getattr(chat, 'participants_count', 0) or getattr(chat, 'member_count', 0)
        
        description = ""
        contacts = []
        try:
            full_chat = await client(GetFullChannelRequest(channel=chat))
            description = getattr(full_chat.full_chat, 'about', '') or ""
            
            found = username_regex.findall(description)
            for u in found:
                u_lower = u.lower()
                if username and u_lower == username.lower():
                    continue
                contact_str = f"@{u}"
                if contact_str not in contacts:
                    contacts.append(contact_str)
        except Exception as e:
            logger.warning(f"Failed to get full channel details for {title}: {e}")
            
        leads.append({
            "channel_id": chat.id,
            "title": title,
            "username": username,
            "participants_count": participants_count,
            "description": description,
            "contacts": contacts
        })
        
    return leads


async def deep_crawl_similar_channels(
    account_ids: list[int],
    channel_link: str,
    max_depth: int = 2,
    progress_callback=None,
    stop_flag: dict | None = None,
) -> list[dict]:
    """
    BFS deep crawl of similar channels up to max_depth layers.
    Uses multi-account rotation and anti-ban safety measures.

    Args:
        account_ids: List of premium account IDs to rotate through
        channel_link: Source channel link/username
        max_depth: How many layers deep to crawl (1-4)
        progress_callback: async callable(state_dict) for realtime updates
        stop_flag: dict with key "stopped" (bool) to allow graceful abort
    """
    import re
    from collections import deque
    from telethon.tl.functions.channels import GetChannelRecommendationsRequest, GetFullChannelRequest

    if not account_ids:
        raise Exception("Không có tài khoản premium nào được chọn")

    # Validate clients
    valid_clients = []
    for aid in account_ids:
        c = _clients.get(aid)
        if c and c.is_connected():
            valid_clients.append((aid, c))
    if not valid_clients:
        raise Exception("Không có tài khoản nào đang kết nối")

    username_regex = re.compile(r'@([a-zA-Z0-9_]{5,32})')

    # BFS state
    visited: set[int] = set()           # channel IDs already processed
    all_leads: list[dict] = []
    # queue items: (channel_entity_or_link, depth, parent_title)
    queue: deque = deque()
    queue.append((channel_link, 0, "—"))  # depth 0 = source channel itself

    # Anti-ban counters per account
    account_request_count: dict[int, int] = {aid: 0 for aid, _ in valid_clients}
    account_idx = 0  # round-robin index

    # Progress state
    state = {
        "status": "running",
        "current_depth": 0,
        "max_depth": max_depth,
        "channels_found": 0,
        "channels_processed": 0,
        "contacts_found": 0,
        "queue_remaining": 0,
        "current_channel": "",
        "current_account": "",
        "errors": [],
    }

    def _next_client():
        """Round-robin to next available client."""
        nonlocal account_idx
        for _ in range(len(valid_clients)):
            account_idx = (account_idx + 1) % len(valid_clients)
            aid, client = valid_clients[account_idx]
            # Skip if account has hit daily soft limit (100 requests)
            if account_request_count.get(aid, 0) >= 100:
                continue
            return aid, client
        # All accounts exhausted — use least-used one anyway
        aid, client = valid_clients[account_idx]
        return aid, client

    async def _safe_delay(base_min: float, base_max: float):
        """Random delay for anti-ban."""
        delay = random.uniform(base_min, base_max)
        await asyncio.sleep(delay)

    async def _update_progress():
        state["queue_remaining"] = len(queue)
        if progress_callback:
            try:
                await progress_callback(state)
            except Exception:
                pass

    # Step 1: Resolve the source channel to get its ID into visited
    first_aid, first_client = valid_clients[0]
    try:
        source_entity = await first_client.get_entity(channel_link)
        source_id = getattr(source_entity, 'id', None)
        if source_id:
            visited.add(source_id)
    except Exception as e:
        raise Exception(f"Không thể tìm thấy kênh nguồn '{channel_link}': {str(e)}")

    # Replace the initial queue item with the resolved entity
    queue.clear()
    queue.append((source_entity, 0, "— Kênh gốc —"))

    logger.info(f"[DeepCrawl] Starting BFS from '{channel_link}', max_depth={max_depth}, accounts={len(valid_clients)}")

    while queue:
        # Check stop flag
        if stop_flag and stop_flag.get("stopped"):
            state["status"] = "stopped"
            await _update_progress()
            logger.info(f"[DeepCrawl] Stopped by user. Found {len(all_leads)} channels.")
            break

        channel_ref, depth, parent_title = queue.popleft()

        # Don't go deeper than max_depth
        if depth > max_depth:
            continue

        # Depth 0 = source channel, just get its recommendations
        # Depth 1-4 = similar channels found at that depth
        state["current_depth"] = depth

        # Pick next account (round-robin)
        aid, client = _next_client()
        state["current_account"] = f"Account #{aid}"

        # Resolve channel entity
        try:
            if isinstance(channel_ref, str):
                entity = await client.get_entity(channel_ref)
            else:
                entity = channel_ref
        except Exception as e:
            state["errors"].append(f"Resolve error at depth {depth}: {str(e)[:80]}")
            continue

        ch_title = getattr(entity, 'title', str(channel_ref))
        state["current_channel"] = ch_title
        await _update_progress()

        # Get recommendations for this channel
        try:
            res = await client(GetChannelRecommendationsRequest(channel=entity))
            account_request_count[aid] = account_request_count.get(aid, 0) + 1
        except errors.FloodWaitError as e:
            wait_time = e.seconds + 10
            logger.warning(f"[DeepCrawl] FloodWait on account {aid}, waiting {wait_time}s")
            state["errors"].append(f"FloodWait account #{aid}: pause {wait_time}s")
            await _update_progress()
            await asyncio.sleep(wait_time)
            # Re-queue this channel and continue
            queue.appendleft((channel_ref, depth, parent_title))
            continue
        except Exception as e:
            state["errors"].append(f"Recommendations error for '{ch_title}': {str(e)[:80]}")
            continue

        # Anti-ban: delay after recommendation request
        await _safe_delay(3.0, 5.0)

        # Session cooldown: every 20 requests per account, take a longer break
        if account_request_count.get(aid, 0) % 20 == 0 and account_request_count.get(aid, 0) > 0:
            cooldown = random.uniform(30, 60)
            logger.info(f"[DeepCrawl] Session cooldown for account {aid}: {cooldown:.0f}s")
            state["errors"].append(f"Cooldown account #{aid}: {cooldown:.0f}s")
            await _update_progress()
            await asyncio.sleep(cooldown)

        # Process each recommended channel
        chats = getattr(res, 'chats', [])
        state["channels_processed"] += 1

        for chat in chats:
            if stop_flag and stop_flag.get("stopped"):
                break

            if not getattr(chat, 'broadcast', False):
                continue

            ch_id = chat.id
            if ch_id in visited:
                continue
            visited.add(ch_id)

            username = getattr(chat, 'username', None)
            title = getattr(chat, 'title', '')
            participants_count = getattr(chat, 'participants_count', 0) or getattr(chat, 'member_count', 0)

            # Get full channel info for description & contacts
            description = ""
            contacts = []
            try:
                # Pick account for full channel fetch (can use same or rotate)
                full_aid, full_client = _next_client()
                full_chat = await full_client(GetFullChannelRequest(channel=chat))
                description = getattr(full_chat.full_chat, 'about', '') or ""
                account_request_count[full_aid] = account_request_count.get(full_aid, 0) + 1

                found = username_regex.findall(description)
                for u in found:
                    u_lower = u.lower()
                    if username and u_lower == username.lower():
                        continue
                    contact_str = f"@{u}"
                    if contact_str not in contacts:
                        contacts.append(contact_str)

                # Anti-ban: shorter delay for full channel fetch
                await _safe_delay(1.0, 2.0)
            except errors.FloodWaitError as e:
                wait_time = e.seconds + 10
                logger.warning(f"[DeepCrawl] FloodWait on GetFullChannel, waiting {wait_time}s")
                await asyncio.sleep(wait_time)
            except Exception as e:
                logger.debug(f"[DeepCrawl] GetFullChannel error for {title}: {e}")

            lead = {
                "channel_id": ch_id,
                "title": title,
                "username": username,
                "participants_count": participants_count,
                "description": description,
                "contacts": contacts,
                "depth": depth + 1,        # This channel was found at depth+1
                "parent_channel": parent_title if depth > 0 else ch_title,
            }
            all_leads.append(lead)
            state["channels_found"] = len(all_leads)
            state["contacts_found"] += len(contacts)

            # If we haven't reached max depth, queue this channel for next layer
            if depth + 1 < max_depth:
                queue.append((chat, depth + 1, title))

            await _update_progress()

    # Done
    if state["status"] != "stopped":
        state["status"] = "completed"
    state["channels_found"] = len(all_leads)
    await _update_progress()

    logger.info(f"[DeepCrawl] Finished. Found {len(all_leads)} unique channels, {state['contacts_found']} contacts across {max_depth} layers.")
    return all_leads



async def disconnect_all():
    """Disconnect all clients."""
    for aid, client in _clients.items():
        if client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
    logger.info(f"Disconnected {len(_clients)} clients")
