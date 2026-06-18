"""
Telegram Client Manager - Multi-account support.
Each account gets its own Telethon client instance.
"""
import os
import logging
import asyncio
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

SESSION_DIR = os.path.join(os.path.dirname(__file__), "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

# Multi-account: account_id -> TelegramClient
_clients: dict[int, TelegramClient] = {}
_code_hashes: dict[int, str] = {}


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
        if await client.is_user_authorized():
            try:
                # Force updates stream and cache initialization
                await client.get_dialogs(limit=5)
            except Exception as e:
                logger.warning(f"Account {account_id}: Failed to get dialogs on startup: {e}")
            try:
                me = await client.get_me()
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
    client = _clients.get(account_id)
    if client and client.is_connected():
        await client.log_out()
        logger.info(f"Account {account_id}: logged out")


async def is_authorized(account_id: int) -> bool:
    client = _clients.get(account_id)
    if not client:
        return False
    if not client.is_connected():
        try:
            await client.connect()
        except Exception:
            return False
    return await client.is_user_authorized()


async def get_me(account_id: int) -> dict | None:
    if not await is_authorized(account_id):
        return None
    client = _clients[account_id]
    me = await client.get_me()
    return {
        "user_id": me.id,
        "first_name": me.first_name,
        "last_name": me.last_name or "",
        "username": me.username or "",
        "phone": me.phone or ""
    }


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


async def disconnect_all():
    """Disconnect all clients."""
    for aid, client in _clients.items():
        if client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
    logger.info(f"Disconnected {len(_clients)} clients")
