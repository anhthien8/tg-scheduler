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
    TextWithEntities
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


async def create_client(account_id: int, api_id: int, api_hash: str, session_name: str) -> TelegramClient:
    """Create a Telethon client for an account."""
    session_path = os.path.join(SESSION_DIR, session_name)
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


async def send_text_message(account_id: int, chat_id: int, text: str, parse_mode: str = "html") -> bool:
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    try:
        entity = await client.get_entity(chat_id)
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
        entity = await client.get_entity(chat_id)
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
        entity = await client.get_entity(chat_id)
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
        entity = await client.get_entity(chat_id)
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
        entity = await client.get_entity(chat_id)
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
