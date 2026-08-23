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


def is_bot_account(sender, username: str = None) -> bool:
    """Return True if the Telegram sender entity is a real Telegram Bot or official service account.

    IMPORTANT: Only use definitive signals (sender.bot flag, system IDs).
    Do NOT filter based on username/display-name containing 'bot' — real users
    like @monsterland_bot, @robotics_trader etc. would be wrongly excluded.
    Username-based detection is only used as fallback when no sender object exists.
    """
    if not sender and not username:
        return False

    # 1. Telethon User.bot or is_bot attribute — DEFINITIVE signal from Telegram API
    if sender:
        if getattr(sender, "bot", False) or getattr(sender, "is_bot", False):
            return True
        sender_id = getattr(sender, "id", 0) or 0
        # Known Telegram system/service bot IDs
        if sender_id in (777000, 178220800, 4244000, 4244001, 1088515515) or (0 < sender_id < 1000):
            return True
        # If sender object is available and .bot is False, trust it — this is a real user
        return False

    # 2. Fallback: no sender object, only username available
    #    Use Telegram's official bot naming convention & common bot patterns
    uname = (username or "").strip().lstrip("@").lower()
    if uname.endswith("bot") or uname.endswith("_bot") or uname.startswith("bot_") or "_bot_" in uname:
        return True

    return False


# ── Pre-compiled keyword sets for O(1) membership testing (allocated ONCE at import time) ──
import re as _re

_TIER1_KW = frozenset([
    # English
    "trading", "trader", "traders", "futures", "leverage", "margin", "scalping", "scalper",
    "day trading", "daytrader", "swing trading", "signals", "signal", "vip signals",
    "long", "short", "tp/sl", "take profit", "stop loss", "pnl", "roi", "technical analysis",
    "chart analysis", "orderbook", "copy trading", "copytrade", "funding rate", "quant",
    "algorithmic", "forex", "binary options", "entry point", "entry", "bullish", "bearish",
    "breakout", "price action", "smart money", "smc", "ict", "alpha", "calls", "gem",
    "cscalp", "tradingview", "screener", "order flow", "footprint", "liquidity",
    "prop firm", "prop trading", "propstart", "arbitrage", "grid trading", "open interest",
    # Russian
    "трейдинг", "трейдер", "трейдеры", "трейдеров", "сигналы", "сигнал", "фьючерсы", "плечо",
    "лонг", "шорт", "скальпинг", "скальп", "ордер", "ордера", "точка входа", "стоп-лосс",
    "тейк-профит", "аналитика", "сделки", "сделка", "профит", "мосбиржа", "биржа", "скринер",
    "проп", "проп-трейдинг", "обучение трейдингу", "стакан", "ликвидность", "дневник трейдера",
    "мемы для трейдеров", "депозит",
    # Chinese
    "合约", "合約", "带单", "帶單", "返佣", "返傭", "量化", "现货", "現貨", "杠杆", "槓桿",
    "交易", "行情", "跟单", "跟單", "开仓", "開倉", "平仓", "平倉", "止盈", "止损", "止損",
    "爆仓", "爆倉", "操盘", "操盤", "策略", "指标", "指標", "波段", "短线", "短線", "点位", "點位",
    "财富密码", "現貨密碼", "现货密码", "投研", "盘面", "盤面", "抄底", "逃顶", "逃頂",
    "多单", "空单", "多單", "空單", "波段交易", "合约交易", "合約交易", "搬砖", "套利", "网格",
    "量化机器人", "量化策略", "实盘",
    # Vietnamese
    "giao dịch", "đòn bẩy", "tín hiệu", "kèo", "lệnh", "phân tích kỹ thuật", "chốt lời",
    "cắt lỗ", "bắn kèo", "đánh future", "vào lệnh", "nhận định", "soi kèo", "vào kèo", "sóng",
    "chốt lãi", "bắt đáy", "đu đỉnh", "xu hướng", "lợi nhuận", "lãi", "quản lý vốn",
    # Turkish
    "vadeli", "kaldıraç", "sinyal", "al-sat", "analiz", "kripto sinyal"
])

_TIER2_KW = frozenset([
    "binance", "bybit", "okx", "weex", "bitget", "mexc", "bingx", "gate.io", "kucoin",
    "coinex", "deribit", "htx", "huobi", "coinbase", "kraken", "cscalp", "tigertrade", "tradingview",
    "vip group", "vip channel", "private channel", "affiliate", "partner", "partnership",
    "kol", "ambassador", "business development", "referral", "ref link", "sponsor",
    "cashback", "fee discount", "đối tác", "hợp tác", "nhóm vip", "kênh kín", "hoa hồng",
    "link ref", "giảm phí", "商务", "商務", "合作", "代理", "vip群", "私享群", "返现",
    "партнер", "сотрудничество", "вип", "рефералка", "скидка", "поддержка", "support"
])

_TIER3_KW = frozenset([
    "crypto", "cryptocurrency", "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "altcoin", "altcoins", "defi", "tokenomics", "market analysis", "whale alert", "web3",
    "invest", "investment", "capital", "fund", "ventures", "holding", "hodl",
    "tiền điện tử", "thị trường", "đầu tư", "phân tích", "tài chính", "vốn",
    "加密", "货币", "貨幣", "加密货币", "加密貨幣", "虚拟货币", "虛擬貨幣", "区块链", "區塊鏈",
    "币圈", "幣圈", "大盘", "大盤", "投资", "投資", "项目", "項目", "资本", "資本",
    "крипта", "криптовалюта", "биткоин", "блокчейн", "инвестиции", "рынок"
])

_PENALTY_KW = frozenset([
    "tap to earn", "notcoin", "hamster kombat", "dogs token", "blum bot", "faucet",
    "free claim", "claim free", "porn", "18+", "casino", "betting", "gambling",
    "lottery", "hack", "leak", "nude", "dating"
])

_NEWS_KW = frozenset(["news", "media", "báo chí", "tin tức", "快讯", "快訊", "新闻", "新聞", "媒体", "媒體", "новости"])

_TITLE_TRADING_RE = _re.compile(
    r"trade|trader|signal|future|quant|scalp|fx|invest|kèo|lệnh|coin|crypto|cscalp|prop|giao dịch"
    r"|合约|合約|量化|投资|投資|скальп|трейд|сигнал|биржа|мосбиржа|moex|крипт"
)


def score_community_trading(
    title: str = "",
    description: str = "",
    username: str = "",
    contacts: list[str] = None,
    participants_count: int = 0
) -> dict:
    """
    Score a Telegram channel/community for Crypto Exchange Business Development (BD) Outreach.
    Evaluates multi-language keywords (EN, VI, ZH, RU, TR), audience size, community structure,
    and trading intent across title, description, and username.

    Returns:
        {
            "trading_score": int (0-100),
            "category": str ("trading_signals" | "crypto_trading" | "potential_bd" | "news_general" | "low_relevance"),
            "category_label": str,
            "badge_color": str,
            "matched_keywords": list[str],
            "is_trading": bool (True if score >= 60)
        }
    """
    text = f"{title or ''} {description or ''} {username or ''}".lower()
    contacts = contacts or []
    matched_keywords = []

    # Calculate matches using frozenset lookups (O(1) per keyword)
    t1_matches = [k for k in _TIER1_KW if k in text]
    t2_matches = [k for k in _TIER2_KW if k in text]
    t3_matches = [k for k in _TIER3_KW if k in text]
    pen_matches = [k for k in _PENALTY_KW if k in text]

    for k in t1_matches[:4] + t2_matches[:3] + t3_matches[:3]:
        if k not in matched_keywords:
            matched_keywords.append(k)

    # Base points
    score = 0

    # Tier 1 points: 20 per match, up to 50
    score += min(len(t1_matches) * 20, 50)

    # Tier 2 points: 20 per match, up to 40
    score += min(len(t2_matches) * 20, 40)

    # Tier 3 points: 10 per match, up to 30
    score += min(t3_matches.__len__() * 10, 30)

    # Bonus: Has Contact in description or contact list (+15)
    if contacts or "@" in (description or "") or "поддержка" in text or "support" in text:
        score += 15

    # Bonus: Has Discussion Chat / Link in description (+15)
    if "t.me/+" in (description or "") or "t.me/joinchat" in (description or "") or "чат" in text or "chat" in text:
        score += 15

    # Bonus: Direct Title or Username matches trading keywords (+20)
    title_user_text = f"{title or ''} {username or ''}".lower()
    if _TITLE_TRADING_RE.search(title_user_text):
        score += 20

    # Bonus: Audience Size / Subscriber count multiplier (BD Target Value)
    if participants_count >= 20000:
        score += 20
    elif participants_count >= 5000:
        score += 15
    elif participants_count >= 1000:
        score += 10
    elif participants_count >= 300:
        score += 5

    # Penalties: -25 per penalty match
    penalty_score = len(pen_matches) * 25
    score -= penalty_score

    # Precise News & Media adjustment:
    # Only treat as pure news if it contains news terms AND HAS ZERO Tier 1 trading AND ZERO Tier 2 exchange terms
    has_news = any(k in text for k in _NEWS_KW)
    is_pure_news = has_news and len(t1_matches) == 0 and len(t2_matches) == 0
    if is_pure_news:
        if len(t3_matches) >= 2 and score >= 20:
            score = max(score, 45)
        score = min(score, 50)

    # Clamp score to 0..100
    score = max(0, min(100, score))

    # Category determination for BD Outreach
    if score >= 80:
        category = "trading_signals"
        category_label = "🔥 VIP Signals / KOL"
        badge_color = "#10b981"
    elif score >= 60:
        category = "crypto_trading"
        category_label = "📈 Trading & Scalping"
        badge_color = "#3b82f6"
    elif is_pure_news or (score >= 35 and has_news and len(t1_matches) == 0):
        category = "news_general"
        category_label = "📰 Kênh Tin tức"
        badge_color = "#f59e0b"
    elif score >= 45:
        category = "potential_bd"
        category_label = "💎 Tiềm năng BD"
        badge_color = "#8b5cf6"
    elif score >= 30:
        category = "crypto_trading"
        category_label = "📈 Cộng đồng Crypto"
        badge_color = "#6366f1"
    else:
        category = "low_relevance"
        category_label = "⚠️ Ít liên quan"
        badge_color = "#6b7280"

    return {
        "trading_score": score,
        "category": category,
        "category_label": category_label,
        "badge_color": badge_color,
        "matched_keywords": matched_keywords[:6],
        "is_trading": score >= 60
    }


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


async def ensure_connected(client: "TelegramClient", account_id: int, timeout: float = 10.0) -> bool:
    """
    Ensure a Telethon client is connected. If not, attempt to reconnect once.
    Returns True if the client is connected (or successfully reconnected), False otherwise.
    This prevents transient disconnects from permanently marking accounts as offline.
    """
    if client is None:
        return False
    if client.is_connected():
        return True
    logger.info(f"Account {account_id}: client disconnected, attempting auto-reconnect...")
    try:
        await asyncio.wait_for(client.connect(), timeout=timeout)
        if client.is_connected():
            logger.info(f"Account {account_id}: auto-reconnect successful ✓")
            return True
        logger.warning(f"Account {account_id}: connect() called but still not connected")
        return False
    except asyncio.TimeoutError:
        logger.warning(f"Account {account_id}: auto-reconnect timed out after {timeout}s")
        return False
    except Exception as e:
        logger.warning(f"Account {account_id}: auto-reconnect failed: {e}")
        return False


async def reconnect_with_proxy(account_id: int, proxy_url: str | None) -> bool:
    """
    Disconnect existing client and reconnect with a new proxy.
    Used when proxy is assigned/changed/removed for an account.
    Returns True if reconnected successfully.
    """
    import database as db_mod

    old_client = _clients.get(account_id)
    if old_client:
        try:
            await old_client.disconnect()
            logger.info(f"Account {account_id}: disconnected for proxy change")
        except Exception as e:
            logger.warning(f"Account {account_id}: disconnect error (continuing): {e}")

    # Get account info from DB
    accounts = await db_mod.get_accounts()
    acc = next((a for a in accounts if a["id"] == account_id), None)
    if not acc:
        logger.error(f"Account {account_id}: not found in DB for reconnect")
        return False

    try:
        # Create new client with proxy
        await create_client(
            account_id,
            int(acc["api_id"]),
            acc["api_hash"],
            acc["session_name"],
            proxy_url=proxy_url,
        )
        # Start and authorize
        ok = await start_client(account_id)
        if ok:
            proxy_display = proxy_url.split("@")[-1] if proxy_url and "@" in proxy_url else (proxy_url or "direct")
            logger.info(f"Account {account_id}: ✓ reconnected via {proxy_display}")
        else:
            logger.warning(f"Account {account_id}: reconnected but not authorized")
        return ok
    except Exception as e:
        logger.error(f"Account {account_id}: reconnect with proxy failed: {e}")
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


async def get_dialogs(account_id: int, timeout: float = 20.0) -> list:
    if not await is_authorized(account_id):
        return []
    client = _clients.get(account_id)
    if not client or not client.is_connected():
        return []
    try:
        dialogs = await asyncio.wait_for(client.get_dialogs(limit=200), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Account {account_id} get_dialogs timed out after {timeout}s")
        return []
    except Exception as e:
        logger.warning(f"Account {account_id} get_dialogs failed: {e}")
        return []
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
        elif isinstance(entity, User):
            if entity.bot:
                name = " ".join(filter(None, [entity.first_name, entity.last_name])).strip()
                result.append({
                    "chat_id": entity.id,
                    "chat_title": name or entity.username or f"Bot {entity.id}",
                    "chat_type": "bot",
                    "username": entity.username or "",
                    "participants_count": None
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
    """Leave (and optionally delete history of) a group, channel, or stop/delete a bot."""
    client = _clients.get(account_id)
    if not client:
        return {"success": False, "error": "Account not found"}
    try:
        from telethon.tl.functions.channels import LeaveChannelRequest
        from telethon.tl.functions.messages import DeleteHistoryRequest
        from telethon.tl.types import Channel, Chat, User

        entity = await client.get_entity(chat_id)

        if isinstance(entity, Channel):
            await client(LeaveChannelRequest(entity))
        elif isinstance(entity, User) and entity.bot:
            # Stop bot (block)
            from telethon.tl.functions.contacts import BlockRequest
            try:
                await client(BlockRequest(id=entity))
            except Exception as e:
                logger.warning(f"Failed to block bot {chat_id}: {e}")
            
            # Delete bot history
            await client(DeleteHistoryRequest(peer=entity, max_id=0, revoke=True))
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
async def join_channel(account_id: int, channel_link: str | int) -> dict:
    """Join a channel/group using its link, username, or numeric chat_id."""
    client = _clients.get(account_id)
    if not client or not client.is_connected():
        return {"success": False, "error": "Account not found or not connected"}
    try:
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.types import PeerChannel, PeerChat
        
        target = channel_link
        if isinstance(target, str) and (target.isdigit() or target.startswith("-100") or (target.startswith("-") and target[1:].isdigit())):
            try:
                target = int(target)
            except ValueError:
                pass
                
        entity = None
        try:
            entity = await client.get_entity(target)
        except Exception:
            if isinstance(target, int):
                clean_id = abs(target)
                if str(clean_id).startswith("100"):
                    clean_id = int(str(clean_id)[3:])
                try:
                    entity = await client.get_entity(PeerChannel(clean_id))
                except Exception:
                    entity = await client.get_entity(PeerChat(clean_id))
            else:
                raise Exception(f"Cannot resolve entity: {channel_link}")

        await client(JoinChannelRequest(entity))
        title = getattr(entity, "title", str(channel_link))
        return {"success": True, "title": title}
    except Exception as e:
        err_msg = str(e)
        if "already a participant" in err_msg.lower() or "user_already_participant" in err_msg.lower():
            return {"success": True, "title": "Đã tham gia nhóm trước đó"}
        return {"success": False, "error": err_msg}


async def auto_join_accounts_to_groups(account_ids: list[int], group_ids: list[int]) -> dict:
    """
    Auto-join all specified accounts into the specified groups.
    """
    results = []
    for acc_id in account_ids:
        client = _clients.get(acc_id)
        if not client or not client.is_connected():
            continue
        for gid in group_ids:
            res = await join_channel(acc_id, gid)
            results.append({
                "account_id": acc_id,
                "group_id": gid,
                "success": res.get("success", False),
                "title": res.get("title", str(gid)),
                "error": res.get("error", "")
            })
    return {"results": results}


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
                if is_bot_account(None, u):
                    continue
                contact_str = f"@{u}"
                if contact_str not in contacts:
                    contacts.append(contact_str)
        except Exception as e:
            logger.warning(f"Failed to get full channel details for {title}: {e}")

        score_info = score_community_trading(title, description, username, contacts, participants_count=participants_count)
        leads.append({
            "channel_id": chat.id,
            "title": title,
            "username": username,
            "participants_count": participants_count,
            "description": description,
            "contacts": contacts,
            "trading_score": score_info["trading_score"],
            "category": score_info["category"],
            "category_label": score_info["category_label"],
            "badge_color": score_info["badge_color"],
            "matched_keywords": score_info["matched_keywords"],
            "is_trading": score_info["is_trading"],
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

    # Step 1: Resolve the source channel — try all accounts until one works
    # If ALL accounts are FloodWait, wait for the shortest one then retry (up to 3 times)
    source_entity = None
    resolve_errors = []
    MAX_RESOLVE_RETRIES = 3
    for resolve_attempt in range(MAX_RESOLVE_RETRIES):
        resolve_errors = []
        flood_waits = []  # (seconds, aid, client) for each FloodWait account
        for _aid, _client in valid_clients:
            try:
                source_entity = await _client.get_entity(channel_link)
                source_id = getattr(source_entity, 'id', None)
                if source_id:
                    visited.add(source_id)
                logger.info(f"[DeepCrawl] Source resolved via account #{_aid}")
                break
            except errors.FloodWaitError as e:
                resolve_errors.append(f"Account #{_aid}: FloodWait {e.seconds}s")
                flood_waits.append((e.seconds, _aid, _client))
                logger.warning(f"[DeepCrawl] Account #{_aid} FloodWait on resolve ({e.seconds}s), trying next...")
                continue
            except Exception as e:
                resolve_errors.append(f"Account #{_aid}: {str(e)[:60]}")
                continue
        if source_entity:
            break
        # All accounts failed — if ALL failures are FloodWait, auto-wait the shortest one then retry
        if flood_waits and len(flood_waits) == len([r for r in resolve_errors if "FloodWait" in r]):
            min_wait = min(s for s, _, _ in flood_waits)
            wait_time = min(min_wait + 5, 120)  # cap at 2 minutes
            if resolve_attempt < MAX_RESOLVE_RETRIES - 1:
                logger.warning(f"[DeepCrawl] All accounts FloodWait on resolve. "
                               f"Waiting {wait_time}s then retrying (attempt {resolve_attempt+1}/{MAX_RESOLVE_RETRIES})...")
                state["errors"] = [f"⏳ FloodWait — tự động thử lại sau {wait_time}s "
                                   f"(lần {resolve_attempt+1}/{MAX_RESOLVE_RETRIES})"]
                if progress_callback:
                    try:
                        await progress_callback(state)
                    except Exception:
                        pass
                await asyncio.sleep(wait_time)
                continue
        break  # Non-FloodWait errors → don't retry
    if not source_entity:
        all_errors = "; ".join(resolve_errors)
        raise Exception(f"Không thể resolve kênh nguồn '{channel_link}' trên tất cả tài khoản: {all_errors}")

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

        # Resolve channel entity — with multi-account fallback on FloodWait
        entity = None
        async def _try_resolve_with(c):
            """Try to resolve channel_ref using client c."""
            if isinstance(channel_ref, str):
                return await c.get_entity(channel_ref)
            elif isinstance(channel_ref, tuple):
                ch_id_q, ch_hash_q, ch_uname_q = channel_ref
                if ch_uname_q:
                    return await c.get_entity(ch_uname_q)
                elif ch_hash_q:
                    from telethon.tl.types import InputPeerChannel
                    peer = InputPeerChannel(channel_id=ch_id_q, access_hash=ch_hash_q)
                    return await c.get_entity(peer)
                else:
                    return None
            else:
                _uname = getattr(channel_ref, 'username', None)
                _ahash = getattr(channel_ref, 'access_hash', None)
                _chid = getattr(channel_ref, 'id', None)
                if _uname:
                    return await c.get_entity(_uname)
                elif _chid and _ahash:
                    from telethon.tl.types import InputPeerChannel
                    peer = InputPeerChannel(channel_id=_chid, access_hash=_ahash)
                    return await c.get_entity(peer)
                return None

        # Try primary account first, then fallback to others on FloodWait
        try:
            entity = await _try_resolve_with(client)
        except errors.FloodWaitError:
            logger.warning(f"[DeepCrawl] Account #{aid} FloodWait on resolve, trying others...")
            for _faid, _fclient in valid_clients:
                if _faid == aid:
                    continue
                try:
                    entity = await _try_resolve_with(_fclient)
                    if entity:
                        aid, client = _faid, _fclient  # Switch to this account for recommendations too
                        state["current_account"] = f"Account #{aid}"
                        break
                except errors.FloodWaitError:
                    continue
                except Exception:
                    continue
        except Exception as e:
            state["errors"].append(f"Resolve error at depth {depth}: {str(e)[:80]}")
            continue

        if not entity:
            state["errors"].append(f"Skip depth {depth}: all accounts flood-limited for resolve")
            continue

        ch_title = getattr(entity, 'title', str(channel_ref))
        state["current_channel"] = ch_title
        logger.info(f"[DeepCrawl] Depth {depth}: resolved '{ch_title}' (id={getattr(entity, 'id', '?')}) via account #{aid}")
        await _update_progress()

        # Get recommendations for this channel — with multi-account fallback
        res = None
        try:
            res = await client(GetChannelRecommendationsRequest(channel=entity))
            account_request_count[aid] = account_request_count.get(aid, 0) + 1
        except errors.FloodWaitError as e:
            logger.warning(f"[DeepCrawl] FloodWait on GetRecommendations for account #{aid} ({e.seconds}s), trying others...")
            # Try other accounts
            for _faid, _fclient in valid_clients:
                if _faid == aid:
                    continue
                try:
                    # Need to re-resolve entity for the fallback client
                    _uname = getattr(entity, 'username', None)
                    _ahash = getattr(entity, 'access_hash', None)
                    _chid = getattr(entity, 'id', None)
                    if _uname:
                        _entity = await _fclient.get_entity(_uname)
                    elif _chid and _ahash:
                        from telethon.tl.types import InputPeerChannel
                        _peer = InputPeerChannel(channel_id=_chid, access_hash=_ahash)
                        _entity = await _fclient.get_entity(_peer)
                    else:
                        continue
                    res = await _fclient(GetChannelRecommendationsRequest(channel=_entity))
                    account_request_count[_faid] = account_request_count.get(_faid, 0) + 1
                    logger.info(f"[DeepCrawl] GetRecommendations succeeded via fallback account #{_faid}")
                    break
                except errors.FloodWaitError:
                    continue
                except Exception:
                    continue
            if res is None:
                # All accounts flood-limited — wait on the original and re-queue
                wait_time = min(e.seconds + 10, 120)  # Cap wait at 2 min
                logger.warning(f"[DeepCrawl] All accounts flood-limited for recommendations, waiting {wait_time}s")
                state["errors"].append(f"FloodWait all accounts: pause {wait_time}s")
                await _update_progress()
                await asyncio.sleep(wait_time)
                queue.appendleft((channel_ref, depth, parent_title))
                continue
        except Exception as e:
            state["errors"].append(f"Recommendations error for '{ch_title}': {str(e)[:80]}")
            logger.warning(f"[DeepCrawl] Recommendations error for '{ch_title}': {e}")
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
        logger.info(f"[DeepCrawl] Depth {depth}: '{ch_title}' returned {len(chats)} recommended chats (type={type(res).__name__})")
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
                # MUST use the same client that fetched recommendations
                # because `chat` object is bound to that client's session
                full_chat = await client(GetFullChannelRequest(channel=chat))
                description = getattr(full_chat.full_chat, 'about', '') or ""
                account_request_count[aid] = account_request_count.get(aid, 0) + 1

                found = username_regex.findall(description)
                for u in found:
                    u_lower = u.lower()
                    if username and u_lower == username.lower():
                        continue
                    if is_bot_account(None, u):
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

            score_info = score_community_trading(title, description, username, contacts, participants_count=participants_count)
            lead = {
                "channel_id": ch_id,
                "title": title,
                "username": username,
                "participants_count": participants_count,
                "description": description,
                "contacts": contacts,
                "depth": depth + 1,        # This channel was found at depth+1
                "parent_channel": parent_title if depth > 0 else ch_title,
                "trading_score": score_info["trading_score"],
                "category": score_info["category"],
                "category_label": score_info["category_label"],
                "badge_color": score_info["badge_color"],
                "matched_keywords": score_info["matched_keywords"],
                "is_trading": score_info["is_trading"],
            }
            all_leads.append(lead)
            state["channels_found"] = len(all_leads)
            state["contacts_found"] += len(contacts)

            # If we haven't reached max depth, queue this channel for next layer
            # Store as (id, access_hash, username) tuple to avoid cross-account entity issues
            if depth + 1 < max_depth:
                ch_access_hash = getattr(chat, 'access_hash', None) or 0
                queue.append(((ch_id, ch_access_hash, username), depth + 1, title))

            await _update_progress()

    # Done
    if state["status"] != "stopped":
        state["status"] = "completed"
    state["channels_found"] = len(all_leads)
    await _update_progress()

    logger.info(f"[DeepCrawl] Finished. Found {len(all_leads)} unique channels, {state['contacts_found']} contacts across {max_depth} layers.")
    return all_leads



async def check_spam_status(account_id: int) -> dict:
    """Check account spam status by messaging @SpamBot.

    Returns dict with keys:
        status: "free" | "limited" | "unknown"
        message: Raw SpamBot response text
        checked_at: ISO timestamp
    """
    from datetime import datetime, timezone
    client = get_client(account_id)
    if not client or not client.is_connected():
        return {
            "status": "unknown",
            "message": "Tài khoản chưa kết nối",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        # Resolve @SpamBot entity
        spam_bot = await client.get_entity("SpamBot")

        # Send /start command
        await client.send_message(spam_bot, "/start")

        # Wait for response (SpamBot replies within seconds)
        await asyncio.sleep(3)

        # Read last message from SpamBot
        messages = await client.get_messages(spam_bot, limit=1)
        if not messages:
            return {
                "status": "unknown",
                "message": "Không nhận được phản hồi từ SpamBot",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

        reply_text = messages[0].text or ""
        reply_lower = reply_text.lower()

        # Parse SpamBot response
        # SpamBot typically says:
        #   "Good news, no limits..."  → free
        #   "Your account is limited..." → limited
        #   "Unfortunately, ..." → limited
        if any(kw in reply_lower for kw in [
            "no limits", "good news", "free",
            "не ограничен", "нет ограничений",
            "không bị giới hạn",
        ]):
            status = "free"
        elif any(kw in reply_lower for kw in [
            "limited", "restrict", "spam",
            "ограничен", "спам",
            "giới hạn", "bị hạn chế",
        ]):
            status = "limited"
        else:
            status = "unknown"

        logger.info(f"[SpamCheck] Account {account_id}: {status} — {reply_text[:80]}")
        return {
            "status": status,
            "message": reply_text[:500],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.warning(f"[SpamCheck] Account {account_id} error: {e}")
        return {
            "status": "unknown",
            "message": f"Lỗi kiểm tra: {str(e)[:200]}",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


async def invite_to_channel(account_id: int, channel, users) -> dict:
    """Invite users to a channel/group. Uses InviteToChannelRequest."""
    client = _clients.get(account_id)
    if not client:
        return {"success": False, "error": "Account not found"}
    try:
        from telethon.tl.functions.channels import InviteToChannelRequest
        channel_entity = await client.get_entity(channel)
        if not isinstance(users, list):
            users = [users]
        user_entities = []
        for u in users:
            try:
                ue = await client.get_entity(u)
                user_entities.append(ue)
            except Exception as e:
                logger.warning(f"Cannot resolve user {u}: {e}")
        if not user_entities:
            return {"success": False, "error": "No valid users to invite"}
        result = await client(InviteToChannelRequest(
            channel=channel_entity,
            users=user_entities
        ))
        return {"success": True, "invited": len(user_entities)}
    except errors.FloodWaitError:
        raise
    except errors.PeerFloodError:
        raise
    except errors.UserPrivacyRestrictedError:
        raise
    except errors.ChatAdminRequiredError:
        raise
    except Exception as e:
        logger.error(f"Account {account_id}: invite error: {e}")
        return {"success": False, "error": str(e)}


async def export_invite_link(account_id: int, channel) -> dict:
    """Export an invite link for a channel/group."""
    client = _clients.get(account_id)
    if not client:
        return {"success": False, "error": "Account not found"}
    try:
        from telethon.tl.functions.messages import ExportChatInviteRequest
        entity = await client.get_entity(channel)
        result = await client(ExportChatInviteRequest(peer=entity))
        return {"success": True, "link": result.link}
    except Exception as e:
        logger.error(f"Account {account_id}: export invite link error: {e}")
        return {"success": False, "error": str(e)}


async def get_channel_info(account_id: int, channel) -> dict:
    """Get basic info about a channel/group."""
    client = _clients.get(account_id)
    if not client:
        return {"success": False, "error": "Account not found"}
    try:
        entity = await client.get_entity(channel)
        info = {
            "success": True,
            "id": entity.id,
            "title": getattr(entity, "title", str(entity.id)),
            "username": getattr(entity, "username", None),
            "is_channel": isinstance(entity, Channel) and entity.broadcast,
            "is_group": isinstance(entity, Channel) and entity.megagroup,
            "participants_count": getattr(entity, "participants_count", None),
        }
        return info
    except Exception as e:
        return {"success": False, "error": str(e)}


async def disconnect_all():
    """Disconnect all clients."""
    for aid, client in _clients.items():
        if client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
    logger.info(f"Disconnected {len(_clients)} clients")
