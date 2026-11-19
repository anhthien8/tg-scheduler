"""
Telegram Client Manager - Multi-account support.
Each account gets its own Telethon client instance.
"""
import asyncio
import logging
import outbound_guard
import os
import time
import random

from dotenv import load_dotenv
from telethon import TelegramClient, errors
from telethon.tl.types import (
    Channel,
    Chat,
    InputMediaPoll,
    PeerChannel,
    PeerChat,
    Poll,
    PollAnswer,
    TextWithEntities,
    User,
)

load_dotenv()
logger = logging.getLogger("tg-scheduler")

SESSION_DIR = os.getenv("SESSION_DIR", os.path.join(os.path.dirname(__file__), "sessions"))
os.makedirs(SESSION_DIR, exist_ok=True)

# Multi-account: account_id -> TelegramClient
_clients: dict[int, TelegramClient] = {}
_code_hashes: dict[int, str] = {}
_auth_cache: dict[int, bool] = {}
_me_cache: dict[int, dict] = {}
_dialogs_cache: dict[int, tuple[float, list]] = {}


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

# Futures/derivatives intent: only these communities are real CEX BD targets.
_FUTURES_KW = frozenset([
    "futures", "future trading", "leverage", "margin", "scalping", "scalper", "scalp",
    "perp", "perpetual", "long/short", "tp/sl", "take profit", "stop loss", "entry",
    "signals", "signal", "copy trading", "copytrade", "day trading", "daytrader",
    "liquidation", "funding rate", "x10", "x20", "x50", "x100",
    "đòn bẩy", "kèo", "bắn kèo", "tín hiệu", "chốt lời", "cắt lỗ", "giao dịch",
    "合约", "合約", "带单", "帶單", "跟单", "开仓", "止盈", "止损", "爆仓",
    "фьючерс", "плечо", "скальп", "сигнал", "трейд",
])

# Bonus hunters / airdrop farmers: not worth BD for a CEX.
_AIRDROP_KW = frozenset([
    "airdrop", "air drop", "airdrops", "bonus hunter", "bonus hunting", "giveaway",
    "free crypto", "free usdt", "free token", "free coin", "free reward", "retroactive",
    "testnet", "faucet", "galxe", "zealy", "taskon", "quest", "tap to earn", "mining bot",
    "deposit bonus", "welcome bonus", "sign up bonus", "signup bonus", "claim reward",
    "săn airdrop", "săn bonus", "săn thưởng", "nhận thưởng", "kèo airdrop", "làm task",
    "空投", "撸毛", "擼毛", "羊毛", "薅羊毛", "аирдроп", "эйрдроп", "раздача", "халява",
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
            "category": str ("trading_signals" | "crypto_trading" | "potential_bd" | "crypto_community" | "news_general" | "airdrop_bonus" | "low_relevance"),
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

    # CEX BD only targets futures/derivatives traders. Airdrop and bonus-hunter
    # channels never qualify; without futures intent a channel can't reach BD tiers.
    has_futures = any(k in text for k in _FUTURES_KW)
    airdrop_hits = [k for k in _AIRDROP_KW if k in text]
    is_airdrop = bool(airdrop_hits) and (not has_futures or len(airdrop_hits) >= 2)
    if is_airdrop:
        score = min(score, 25)
    elif not has_futures:
        score = min(score, 44)

    # Category determination for BD Outreach
    if score >= 80:
        category = "trading_signals"
        category_label = "🔥 VIP Signals / KOL"
        badge_color = "#10b981"
    elif score >= 60:
        category = "crypto_trading"
        category_label = "📈 Trading & Scalping"
        badge_color = "#3b82f6"
    elif is_airdrop:
        category = "airdrop_bonus"
        category_label = "🪂 Airdrop / Bonus"
        badge_color = "#6b7280"
    elif is_pure_news or (score >= 35 and has_news and len(t1_matches) == 0):
        category = "news_general"
        category_label = "📰 Kênh Tin tức"
        badge_color = "#f59e0b"
    elif score >= 45:
        category = "potential_bd"
        category_label = "💎 Tiềm năng BD"
        badge_color = "#8b5cf6"
    elif score >= 30:
        category = "crypto_community"
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


async def ensure_user_entity(account_id: int, user_id: int, username: str | None = None) -> bool:
    """True when `account_id` can address the DM user `user_id` (cheap check before spending an AI call).

    A user id is only usable when its access hash sits in the account's session. After a
    session is re-created that hash is gone and every send fails with "Cannot resolve
    entity". Fallback: resolve the @username once (Telethon then caches it) — accepted
    only if it still belongs to the SAME user id, since usernames change hands.
    """
    client = _clients.get(account_id)
    if not client:
        return False
    try:
        await client.get_input_entity(user_id)
        return True
    except errors.FloodWaitError:
        raise
    except Exception:
        pass
    uname = (username or "").strip().lstrip("@")
    if not uname:
        return False
    try:
        ent = await client.get_entity(uname)
    except errors.FloodWaitError:
        raise
    except Exception:
        return False
    return getattr(ent, "id", None) == user_id


def _marked_id_from_entity(entity) -> int | None:
    """Return the -100... supergroup id for a Telethon Channel/Chat entity."""
    try:
        from telethon.utils import get_peer_id
        pid = get_peer_id(entity)
        # get_peer_id returns the marked id (already -100... for channels).
        return int(pid)
    except Exception:
        cid = getattr(entity, "id", None)
        if cid is None:
            return None
        # Channel/supergroup -> -100<id>; basic group -> -<id>.
        if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
            return int(f"-100{cid}")
        return int(cid)


async def resolve_invite_or_username(link_or_hash: str, username: str | None = None,
                                     account_id: int | None = None) -> dict:
    """Resolve a private-invite hash or public @username to a supergroup id.

    Uses the first available connected account (or `account_id` if given).
    Returns {"group_id": int, "title": str} or raises on failure.
    """
    from telethon import functions
    from telethon.tl.types import Channel, Chat

    client = None
    if account_id is not None:
        client = get_client(account_id)
        if client is None or not client.is_connected():
            client = None
    if client is None:
        for _aid, c in list(_clients.items()):
            if c is not None and c.is_connected():
                client = c
                break
    if client is None:
        raise Exception("Không có tài khoản Telegram nào đang kết nối để resolve link")

    if username:
        entity = await client.get_entity(username)
    else:
        # Inspect the invite to learn the chat id without joining.
        result = await client(functions.messages.CheckChatInviteRequest(link_or_hash))
        entity = getattr(result, "chat", None)
        if entity is None:
            raise Exception("Link mời không hợp lệ hoặc đã hết hạn")

    if not isinstance(entity, (Channel, Chat)):
        raise Exception("Link không trỏ tới nhóm/kênh")

    gid = _marked_id_from_entity(entity)
    if gid is None:
        raise Exception("Không lấy được ID từ entity")
    return {"group_id": int(gid), "title": getattr(entity, "title", "") or ""}


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


# account_id -> human-readable reason the session is unusable (revoked,
# logged out, ...). TCP may still "connect", so the UI can't rely on that.
_session_errors: dict[int, str] = {}

_REVOKED_ERRORS = ("AuthKeyDuplicated", "AuthKeyUnregistered", "SessionRevoked",
                   "SessionExpired", "UserDeactivated", "AuthKeyInvalid")


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
            _session_errors.pop(account_id, None)
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
                # Sync ID và tên thật từ Telegram vào DB để nhận diện nick nội bộ bền vững
                try:
                    import database as _db
                    await _db.update_account_telegram_user_id(account_id, me.id)
                    real_name = " ".join(x for x in [me.first_name, me.last_name or ""] if x).strip()
                    if real_name:
                        acc = await _db.get_account(account_id)
                        if acc and acc.get("name") != real_name:
                            await _db.update_account_name(account_id, real_name)
                            logger.info(f"Account {account_id}: name synced '{acc.get('name')}' → '{real_name}'")
                except Exception as e:
                    logger.debug(f"Account {account_id}: identity sync skipped: {e}")
            except Exception as e:
                logger.warning(f"Account {account_id}: Failed to get self details: {e}")
                logger.info(f"Account {account_id}: connected (authorized)")
            return True
        logger.info(f"Account {account_id}: connected (not authorized)")
        _session_errors[account_id] = "Chưa đăng nhập / session hết hạn"
        return False
    except Exception as e:
        logger.error(f"Account {account_id}: connect failed: {e}")
        name = type(e).__name__
        if any(k in name for k in _REVOKED_ERRORS):
            _auth_cache[account_id] = False
            _session_errors[account_id] = f"Session bị Telegram huỷ ({name.replace('Error', '')})"
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

    Returns True ONLY if the account is now connected via the REQUESTED proxy.
    If the new proxy fails, we best-effort roll back to the previous proxy and
    return False — so a caller that wants to persist the new proxy must treat
    False as "do not commit" (the account is on the old proxy again).
    """
    import database as db_mod

    old_client = _clients.get(account_id)
    old_proxy = None
    if old_client:
        # Remember the old proxy so we can roll back on failure.
        old_proxy = getattr(old_client, "_tg_proxy_url", None)
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

    async def _build_and_start(purl):
        await create_client(
            account_id,
            int(acc["api_id"]),
            acc["api_hash"],
            acc["session_name"],
            proxy_url=purl,
        )
        client = _clients.get(account_id)
        if client is not None:
            # Tag so a future reconnect can roll back to this proxy.
            try:
                client._tg_proxy_url = purl
            except Exception:
                pass
        return await start_client(account_id)

    try:
        ok = await _build_and_start(proxy_url)
        if ok:
            proxy_display = proxy_url.split("@")[-1] if proxy_url and "@" in proxy_url else (proxy_url or "direct")
            logger.info(f"Account {account_id}: ✓ reconnected via {proxy_display}")
            return True

        logger.warning(f"Account {account_id}: reconnected but not authorized")
    except Exception as e:
        logger.error(f"Account {account_id}: reconnect with proxy failed: {e}")

    # New proxy did not work — restore the old connection. Return False either
    # way (the caller must NOT persist the new proxy), but attempt rollback so
    # the account isn't left offline.
    restored = await _rollback_proxy(account_id, old_proxy, acc)
    if not restored:
        logger.error(f"Account {account_id}: account is OFFLINE (rollback failed)")
    return False


async def _rollback_proxy(account_id: int, old_proxy: str | None, acc: dict | None = None) -> bool:
    """Best-effort restore of the previous client when a proxy swap fails.

    Returns True if the account ends up connected again (on the old proxy).
    """
    try:
        if acc is None:
            acc = await _find_account(account_id)
        if acc is None:
            logger.error(f"Account {account_id}: cannot roll back — account not found")
            return False
        await create_client(
            account_id,
            int(acc["api_id"]),
            acc["api_hash"],
            acc["session_name"],
            proxy_url=old_proxy,
        )
        client = _clients.get(account_id)
        if client is not None:
            try:
                client._tg_proxy_url = old_proxy
            except Exception:
                pass
        restored = await start_client(account_id)
        if restored:
            logger.warning(f"Account {account_id}: rolled back to previous proxy after failed swap")
        else:
            logger.error(f"Account {account_id}: rollback also failed — account offline")
        return restored
    except Exception as e:
        logger.error(f"Account {account_id}: proxy rollback failed: {e}")
        return False


async def _find_account(account_id: int) -> dict | None:
    import database as db_mod
    accounts = await db_mod.get_accounts()
    return next((a for a in accounts if a["id"] == account_id), None)


async def _fresh_session_client(account_id: int, old_client: TelegramClient) -> TelegramClient:
    acc = await _find_account(account_id)
    if not acc:
        raise Exception("Account not found")
    proxy_url = getattr(old_client, "_tg_proxy_url", None) or acc.get("proxy_url")
    try:
        await old_client.disconnect()
    except Exception:
        pass
    base = os.path.join(SESSION_DIR, acc["session_name"])
    path = base if base.endswith(".session") else base + ".session"
    if os.path.exists(path):
        os.replace(path, f"{path}.revoked-{int(time.time())}")
    client = await create_client(account_id, int(acc["api_id"]), acc["api_hash"],
                                 acc["session_name"], proxy_url=proxy_url)
    await client.connect()
    _session_errors.pop(account_id, None)
    return client


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
        try:
            result = await client.send_code_request(phone)
        except Exception as e:
            if not any(n in type(e).__name__ for n in _REVOKED_ERRORS):
                raise
            # Session file bị Telegram huỷ → không thể gửi OTP bằng nó nữa.
            # Đổi tên file cũ (giữ lại để tra cứu) và tạo session mới tinh rồi gửi lại.
            logger.warning(f"Account {account_id}: revoked session ({type(e).__name__}), creating fresh session for OTP")
            client = await _fresh_session_client(account_id, client)
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


def _signed_in(account_id: int, user) -> dict:
    """Refresh the auth/me caches after a successful sign-in.

    The client was created (and is_authorized() cached False) before the OTP
    was sent; without this, get_me() kept returning None after login, so the
    account was never auto-named and the list showed the phone instead of
    the Telegram name/username.
    """
    _auth_cache[account_id] = True
    _me_cache[account_id] = {
        "user_id": user.id,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username or "",
        "phone": getattr(user, "phone", "") or "",
    }
    return {"success": True, **{k: v for k, v in _me_cache[account_id].items() if k != "phone"}}


async def sign_in(account_id: int, phone: str, code: str, phone_code_hash: str,
                  password: str | None = None) -> dict:
    """Sign in with OTP code."""
    client = _clients.get(account_id)
    if not client:
        return {"success": False, "error": "Account client not found"}

    try:
        user = await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        logger.info(f"Account {account_id}: signed in as {user.first_name}")
        return _signed_in(account_id, user)
    except errors.SessionPasswordNeededError:
        if password:
            try:
                user = await client.sign_in(password=password)
            except errors.PasswordHashInvalidError:
                return {"success": False, "error": "Mật khẩu 2FA không đúng", "needs_password": True}
            logger.info(f"Account {account_id}: signed in (2FA) as {user.first_name}")
            return _signed_in(account_id, user)
        return {"success": False, "error": "2FA password required", "needs_password": True}
    except errors.PhoneCodeInvalidError:
        return {"success": False, "error": "Mã OTP không đúng"}
    except errors.PhoneCodeExpiredError:
        return {"success": False, "error": "Mã OTP đã hết hạn", "code_expired": True}
    except Exception as e:
        logger.error(f"Account {account_id}: sign in error: {e}")
        return {"success": False, "error": str(e)}


async def logout(account_id: int):
    """Log out an account."""
    _auth_cache.pop(account_id, None)
    _me_cache.pop(account_id, None)
    _dialogs_cache.pop(account_id, None)
    client = _clients.get(account_id)
    if client and client.is_connected():
        try:
            await asyncio.wait_for(client.log_out(), timeout=5.0)
        except Exception:
            pass
        logger.info(f"Account {account_id}: logged out")


async def is_authorized(account_id: int, timeout: float = 3.0) -> bool:
    if account_id in _auth_cache:
        return _auth_cache[account_id]
    client = _clients.get(account_id)
    if not client:
        return False
    if not client.is_connected():
        try:
            await asyncio.wait_for(client.connect(), timeout=timeout)
        except Exception:
            return False
    try:
        is_auth = await asyncio.wait_for(client.is_user_authorized(), timeout=timeout)
        _auth_cache[account_id] = is_auth
        return is_auth
    except Exception:
        return False


async def get_me(account_id: int, timeout: float = 3.0) -> dict | None:
    if account_id in _me_cache:
        return _me_cache[account_id]
    if not await is_authorized(account_id, timeout=timeout):
        return None
    client = _clients.get(account_id)
    if not client:
        return None
    try:
        me = await asyncio.wait_for(client.get_me(), timeout=timeout)
        if me:
            _me_cache[account_id] = {
                "user_id": me.id,
                "first_name": me.first_name,
                "last_name": me.last_name or "",
                "username": me.username or "",
                "phone": me.phone or ""
            }
            try:
                import database as _db
                await _db.update_account_telegram_user_id(account_id, me.id)
            except Exception:
                pass
            return _me_cache[account_id]
    except Exception as e:
        logger.debug(f"Account {account_id} get_me failed or timed out: {e}")
    return None


async def get_dialogs(account_id: int, timeout: float = 20.0, force_refresh: bool = False) -> list:
    import time
    now = time.time()
    if not force_refresh and account_id in _dialogs_cache:
        cached_time, cached_list = _dialogs_cache[account_id]
        if now - cached_time < 300.0:  # 5m TTL cache for ultra-fast instant UI navigation
            return cached_list

    if not await is_authorized(account_id, timeout=min(5.0, timeout)):
        return []
    client = _clients.get(account_id)
    if not client or not client.is_connected():
        return []
    try:
        dialogs = await asyncio.wait_for(client.get_dialogs(limit=200), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Account {account_id} get_dialogs timed out after {timeout}s")
        return _dialogs_cache.get(account_id, (0, []))[1]
    except Exception as e:
        logger.warning(f"Account {account_id} get_dialogs failed: {e}")
        return _dialogs_cache.get(account_id, (0, []))[1]
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
    _dialogs_cache[account_id] = (time.time(), result)
    return result




def _known_chat(chat_id) -> dict | None:
    """Look a group up in ANY account's cached dialog list (no API call).

    The account being checked often has never seen the group (that is why it
    is "missing"), but the observer/another nick usually has — so its title
    and @username are still known locally.
    """
    try:
        cid = abs(int(str(chat_id).replace("-100", "", 1)))
    except (TypeError, ValueError):
        return None
    for _ts, chats in list(_dialogs_cache.values()):
        for c in chats:
            if abs(int(c.get("chat_id") or 0)) == cid:
                return c
    return None


async def check_accounts_in_groups(account_ids: list[int], group_ids: list[int]) -> dict:
    """
    Check which accounts are NOT members of the specified groups.
    Returns: {
        "warnings": [
            {
                "account_id": 2,
                "account_name": "BD Phạm",
                "missing_groups": [{"group_id": 123, "group_title": "WEEX English",
                                    "username": "weex_en", "left": False}]
            }
        ],
        "all_ok": bool
    }
    A group is only reported missing after a direct check: get_dialogs() stops
    at 200 chats, so a busy nick can be a member of a group that is simply not
    in its recent list.
    """
    from telethon.tl.types import PeerChannel

    warnings = []
    for acc_id in account_ids:
        client = _clients.get(acc_id)
        if not client:
            continue
        try:
            dialogs = await get_dialogs(acc_id)
            joined_ids = {abs(d["chat_id"]) for d in dialogs}

            missing = []
            for gid in group_ids:
                clean_gid = abs(int(str(gid).replace("-100", "", 1)))
                if clean_gid in joined_ids:
                    continue
                known = _known_chat(clean_gid) or {}
                title = known.get("chat_title") or ""
                username = known.get("username") or ""
                left = None
                try:
                    ent = await asyncio.wait_for(client.get_entity(PeerChannel(clean_gid)), timeout=8)
                    title = title or getattr(ent, "title", "") or ""
                    username = username or getattr(ent, "username", "") or ""
                    left = bool(getattr(ent, "left", True))
                    if not left:
                        continue  # member, just outside the 200 most recent chats
                except Exception:
                    pass  # this nick has never seen the group
                missing.append({
                    "group_id": gid,
                    "group_title": title or f"Nhóm #{clean_gid}",
                    "username": username,
                    "left": left,
                })

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
        except Exception:
            pass  # skip if can't check (account offline etc)

    return {"warnings": warnings, "all_ok": len(warnings) == 0}

async def leave_channel(account_id: int, chat_id: int) -> dict:
    """Leave (and optionally delete history of) a group, channel, or stop/delete a bot."""
    client = _clients.get(account_id)
    if not client or not client.is_connected():
        return {"success": False, "error": "Tài khoản chưa kết nối — thử lại sau vài giây"}
    try:
        from telethon.tl.functions.channels import LeaveChannelRequest
        from telethon.tl.functions.messages import DeleteHistoryRequest
        from telethon.tl.types import Channel, User

        try:
            entity = await client.get_entity(chat_id)
        except ValueError:
            # Not in the session cache yet — resolve it from the dialog list.
            entity = None
            async for d in client.iter_dialogs(limit=300):
                if d.entity.id == chat_id:
                    entity = d.entity
                    break
            if entity is None:
                return {"success": False, "error": "Không tìm thấy cuộc trò chuyện (có thể đã rời trước đó)"}

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
            try:
                await client(DeleteHistoryRequest(peer=entity, max_id=0, revoke=True))
            except Exception:
                await client(DeleteHistoryRequest(peer=entity, max_id=0, just_clear=False))
        else:
            # Regular group
            from telethon.tl.functions.messages import DeleteChatUserRequest
            me = await client.get_me()
            await client(DeleteChatUserRequest(chat_id=chat_id, user_id=me))

        # Drop the cached dialog so a reload doesn't bring the chat back.
        cached = _dialogs_cache.get(account_id)
        if cached:
            _dialogs_cache[account_id] = (cached[0], [c for c in cached[1] if c["chat_id"] != chat_id])
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def send_text_message(account_id: int, chat_id: int, text: str, parse_mode: str = "html") -> bool:
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    # Last-line guard: internal AI markers ([HANDOVER_REQUIRED], METRICS…) never
    # reach a chat. A message made only of markers is not sent at all.
    text = outbound_guard.guard_outgoing(text, f"acc={account_id} chat={chat_id}")
    if text is None:
        return False
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
    caption = outbound_guard.guard_outgoing(caption, f"acc={account_id} chat={chat_id} photo caption") or ""
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
    caption = outbound_guard.guard_outgoing(caption, f"acc={account_id} chat={chat_id} video caption") or ""
    try:
        entity = await _get_entity_safe(client, chat_id)
        await client.send_file(entity, file_path, caption=caption, parse_mode="html", supports_streaming=True)
        return True
    except errors.FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Account {account_id}: send video error: {e}")
        raise


async def send_media_message(account_id: int, chat_id: int, file_path: str, caption: str = "", *,
                             attributes=None, mime_type: str | None = None,
                             voice_note: bool = False, video_note: bool = False) -> bool:
    """Send any attachment as it was (photo, video, GIF, voice, round video, sticker, audio, file).
    `attributes` / `mime_type` are those of the original document, so it arrives as the same kind."""
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    caption = outbound_guard.guard_outgoing(caption, f"acc={account_id} chat={chat_id} media caption") or ""
    try:
        entity = await _get_entity_safe(client, chat_id)
        await client.send_file(entity, file_path, caption=caption, parse_mode=None, attributes=attributes,
                               mime_type=mime_type, voice_note=voice_note, video_note=video_note,
                               supports_streaming=True)
        return True
    except errors.FloodWaitError:
        raise
    except Exception as e:
        logger.error(f"Account {account_id}: send media error to {chat_id}: {e}")
        raise


async def send_document_message(account_id: int, chat_id: int, file_path: str, caption: str = "") -> bool:
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not connected")
    caption = outbound_guard.guard_outgoing(caption, f"acc={account_id} chat={chat_id} document caption") or ""
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
                    try:
                        entity = await client.get_entity(PeerChat(clean_id))
                    except Exception:
                        # This nick never saw the group. A public group can still
                        # be joined through the @username another nick knows.
                        known = _known_chat(clean_id) or {}
                        if known.get("username"):
                            entity = await client.get_entity(known["username"])
                        else:
                            return {"success": False, "error": "NOT_RESOLVABLE_PRIVATE",
                                    "title": known.get("chat_title") or ""}
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


async def resolve_chat_link(account_id: int, link: str) -> dict | None:
    """Resolve a t.me link / @username / id to {chat_id, chat_title, chat_type}.

    Used by the watcher rule picker so the operator can add groups too large or
    too quiet to appear in get_dialogs(limit=200). Call AFTER join_channel —
    private invite links only resolve for members.
    """
    client = _clients.get(account_id)
    if not client or not client.is_connected():
        return None
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.types import Channel as _Ch
        from telethon.tl.types import Chat as _Chat
        from telethon.tl.types import PeerChannel, PeerChat

        target = link
        if isinstance(target, str) and (target.isdigit() or target.startswith("-100")
                                        or (target.startswith("-") and target[1:].isdigit())):
            try:
                target = int(target)
            except ValueError:
                pass

        entity = None
        try:
            entity = await client.get_entity(target)
        except Exception:
            # Bare numeric ids and -100... ids need an explicit peer wrapper,
            # exactly like join_channel does.
            if isinstance(target, int):
                clean_id = abs(target)
                if str(clean_id).startswith("100"):
                    clean_id = int(str(clean_id)[3:])
                try:
                    entity = await client.get_entity(PeerChannel(clean_id))
                except Exception:
                    entity = await client.get_entity(PeerChat(clean_id))
            else:
                raise

        if isinstance(entity, _Ch):
            chat_type = "channel" if entity.broadcast else "supergroup"
        elif isinstance(entity, _Chat):
            chat_type = "group"
        else:
            return None  # user/bot — not a group

        # participants_count/forum are only on the full object, not the dialog entity.
        is_forum = bool(getattr(entity, "forum", False))
        participants = getattr(entity, "participants_count", None)
        if isinstance(entity, _Ch):
            try:
                full = await client(GetFullChannelRequest(channel=entity))
                is_forum = bool(getattr(full.full_chat, "forum", is_forum))
                participants = getattr(full.full_chat, "participants_count", participants)
            except Exception:
                pass

        return {
            "chat_id": entity.id,
            "chat_title": getattr(entity, "title", str(link)),
            "chat_type": chat_type,
            "username": getattr(entity, "username", "") or "",
            "participants_count": participants,
            "is_forum": is_forum,
        }
    except Exception as e:
        logger.warning("[resolve_chat_link] acc=%s link=%r failed: %s", account_id, link, e)
        return None


async def get_similar_channels_and_contacts(account_id: int, channel_link: str) -> list[dict]:
    """Get recommendations of similar channels and extract admin contact handles from their description."""
    client = _clients.get(account_id)
    if not client:
        raise Exception("Account not found")
        
    import re

    from telethon.tl.functions.channels import GetChannelRecommendationsRequest, GetFullChannelRequest
    
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


# ── Contact verification for Deep Crawl ─────────────────────────────────────
# @mentions in a channel description are often other channels, groups, bots or
# dead usernames. Resolve each once and keep only real people. Results are cached
# for 7 days (performance: no repeated ResolveUsername calls across crawls).
_CONTACT_KIND_TTL = 7 * 86400
_contact_kind_cache: dict[str, tuple[str, float]] = {}


# Channel "about" text by channel id — lets a re-run (e.g. after a server restart)
# skip GetFullChannel for channels it already saw. Seeded from saved results.
_CHANNEL_ABOUT_TTL = 7 * 86400
_channel_about_cache: dict[int, tuple[str, float]] = {}

# Telegram limits seen by Deep Crawl, kept across crawls (in memory) so the next crawl
# does not hit an account that is still limited. account id -> unix time the limit ends.
_crawl_flood_until: dict[int, float] = {}    # GetChannelRecommendations / GetFullChannel
_resolve_flood_until: dict[int, float] = {}  # ResolveUsername (much stricter, up to ~24h)


class DeepCrawlRetryLater(Exception):
    """The source channel cannot be opened until a Telegram limit ends."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


def seed_channel_about_cache(leads) -> int:
    """Fill the description cache from earlier crawl results. Returns entries added."""
    n = 0
    now = time.time()
    for lead in leads or []:
        cid = lead.get("channel_id")
        if cid and cid not in _channel_about_cache and lead.get("description") is not None \
                and not lead.get("no_description"):
            _channel_about_cache[cid] = (lead.get("description") or "", now)
            n += 1
    return n


def export_contact_kind_cache() -> dict:
    now = time.time()
    return {k: v for k, v in _contact_kind_cache.items() if now - v[1] < _CONTACT_KIND_TTL}


def import_contact_kind_cache(data: dict) -> None:
    now = time.time()
    for k, v in (data or {}).items():
        try:
            kind, ts = v[0], float(v[1])
        except Exception:
            continue
        if now - ts < _CONTACT_KIND_TTL and k not in _contact_kind_cache:
            _contact_kind_cache[k] = (kind, ts)


async def classify_contact_username(client, username: str) -> str:
    """Return 'user' | 'bot' | 'channel' | 'group' | 'deleted' | 'scam' | 'invalid'.

    Raises FloodWaitError so the caller can stop resolving for a while."""
    from telethon.tl.functions.contacts import ResolveUsernameRequest
    from telethon.tl.types import PeerUser

    key = username.lstrip("@").lower()
    hit = _contact_kind_cache.get(key)
    if hit and time.time() - hit[1] < _CONTACT_KIND_TTL:
        return hit[0]
    try:
        res = await client(ResolveUsernameRequest(key))
    except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError):
        kind = "invalid"
    else:
        kind = "invalid"
        if isinstance(res.peer, PeerUser):
            u = next((x for x in res.users if x.id == res.peer.user_id), None)
            if u is None:
                kind = "invalid"
            elif getattr(u, "deleted", False):
                kind = "deleted"
            elif is_bot_account(u):
                kind = "bot"
            elif getattr(u, "scam", False) or getattr(u, "fake", False):
                kind = "scam"
            else:
                kind = "user"
        else:
            ch = next(iter(res.chats), None)
            kind = "group" if getattr(ch, "megagroup", False) or getattr(ch, "gigagroup", False) else "channel"
    _contact_kind_cache[key] = (kind, time.time())
    return kind


# Opt-in (Settings key, "1" = on): accounts paused ONLY because of PeerFlood may still be
# used by Deep Crawl. PeerFlood limits messaging strangers; the crawl only reads public
# channel data. Every other pause (manual, FloodWait, banned) still keeps the account out.
PEERFLOOD_CRAWL_SETTING = "deep_crawl_peerflood_paused"


def is_peerflood_pause(acc: dict | None) -> bool:
    """True when the account was paused by the PeerFlood guard (alerts.flood_guard)."""
    return str((acc or {}).get("pause_reason") or "").startswith("PeerFlood")


# Accounts that crawl at the same time in one Deep Crawl. Telegram limits are per account,
# so each one keeps its own pacing; more parallel accounts = faster, same per-account load.
DEEP_CRAWL_PARALLEL = 3


async def deep_crawl_similar_channels(
    account_ids: list[int],
    channel_link: str,
    max_depth: int = 2,
    progress_callback=None,
    stop_flag: dict | None = None,
    min_expand_score: int = 40,
    per_account_budget: int = 300,
    parallel: int = DEEP_CRAWL_PARALLEL,
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
        min_expand_score: only channels with trading_score >= this are crawled
            further (the rest are still saved as leads, their branch is pruned)
        per_account_budget: max Telegram requests per account in one crawl
        parallel: how many accounts crawl their own node at the same time
    """
    import re
    from collections import deque

    from telethon.tl.functions.channels import GetChannelRecommendationsRequest, GetFullChannelRequest

    if not account_ids:
        raise Exception("Không có tài khoản premium nào được chọn")

    # Validate clients. Paused accounts never run ("tạm dừng = dừng hẳn"). Flagged or
    # PeerFlood accounts that are NOT paused may crawl: those limits only block messaging
    # strangers, and the crawl only reads public channel data. Accounts paused only
    # because of PeerFlood join them when the user turned PEERFLOOD_CRAWL_SETTING on.
    import database as _db
    import tme_preview
    from telethon import utils as _tl_utils
    from telethon.tl.types import InputPeerChannel, PeerChannel
    try:
        _accs = {a["id"]: a for a in await _db.get_all_accounts()}
    except Exception:
        _accs = {}
    _names = {aid: (a.get("name") or "") for aid, a in _accs.items()}
    try:
        allow_peerflood = str(await _db.get_setting(PEERFLOOD_CRAWL_SETTING, "0") or "0") == "1"
    except Exception:
        allow_peerflood = False
    valid_clients = []
    skipped_paused = []
    peerflood_readonly = []  # paused for PeerFlood, crawling read-only (opt-in)
    skipped_offline = []
    for aid in account_ids:
        readonly = False
        if await _db.is_account_paused(aid):
            if not (allow_peerflood and is_peerflood_pause(_accs.get(aid))):
                skipped_paused.append(aid)
                continue
            readonly = True
        c = _clients.get(aid)
        if c and c.is_connected():
            valid_clients.append((aid, c))
            if readonly:
                peerflood_readonly.append(aid)
        else:
            skipped_offline.append(aid)
    if skipped_paused:
        logger.info(f"[DeepCrawl] Skipping paused accounts: {skipped_paused}")
    if peerflood_readonly:
        logger.info(f"[DeepCrawl] PeerFlood-paused accounts crawling read-only (opt-in): {peerflood_readonly}")
    if not valid_clients:
        pf_hint = ""
        if not allow_peerflood and any(is_peerflood_pause(_accs.get(a)) for a in skipped_paused):
            pf_hint = ". Bật \"Cho cả tài khoản tạm dừng vì PeerFlood\" để dùng chúng (chỉ đọc)"
        raise Exception("Không có tài khoản nào đang chạy và đã kết nối"
                        + (f" (đang tạm dừng: {', '.join(f'#{a}' for a in skipped_paused)})" if skipped_paused else "")
                        + pf_hint)

    username_regex = re.compile(r'@([a-zA-Z0-9_]{5,32})')

    # BFS state
    visited: set[int] = set()           # channel IDs already processed
    all_leads: list[dict] = []
    # queue items: ((channel_id, access_hash, username), depth, title of that channel)
    queue: deque = deque()

    # Anti-ban counters per account
    REST_EVERY = 20           # requests per account between anti-ban breaks
    HOLDER_SLACK = 30         # prefer the account that already knows a channel unless it is this far ahead
    CONTACT_RESOLVE_CAP = 20  # ResolveUsername calls per account per crawl, for contacts t.me could not check
    MAX_INLINE_WAIT = 120     # never sleep longer than this inside the crawl
    account_request_count: dict[int, int] = {aid: 0 for aid, _ in valid_clients}
    account_idx = -1  # round-robin index
    flood_until = _crawl_flood_until  # aid -> end of a FloodWait on crawl calls
    # ResolveUsername has its own, much stricter limit (FloodWait of up to ~24h). It must
    # not stop the account from crawling, so it is tracked separately.
    resolve_flood_until = _resolve_flood_until
    contact_resolves: dict[int, int] = {}
    rest_until: dict[int, float] = {}  # anti-ban breaks: the other accounts keep crawling
    next_rest_at: dict[int, int] = {aid: REST_EVERY for aid, _ in valid_clients}
    # channel id -> {account id: access_hash}. Hashes are per account: the account that
    # discovered a channel opens it with no username lookup.
    known_hash: dict[int, dict[int, int]] = {}
    REC_GAP = (5.0, 8.0)  # min seconds between two recommendation calls of the same account
    rec_ready_at: dict[int, float] = {}
    busy: set[int] = set()  # accounts crawling a node right now (parallel workers)

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
        "contacts_removed": 0,
        "branches_pruned": 0,
        "nodes_skipped": 0,
        "web_lookups": 0,
        "resolve_calls": 0,
        "accounts_used": len(valid_clients),
        "errors": [],
        "results": all_leads,  # live list → caller can save partial results
    }
    if skipped_paused:
        state["errors"].append(
            f"Bỏ qua {len(skipped_paused)} tài khoản đang tạm dừng: "
            + ", ".join(f"#{a} {_names.get(a, '')}".strip() for a in skipped_paused)
            + f" → cào bằng {len(valid_clients)} tài khoản")
    if peerflood_readonly:
        state["errors"].append(
            f"Dùng {len(peerflood_readonly)} tài khoản đang tạm dừng vì PeerFlood (chỉ đọc): "
            + ", ".join(f"#{a} {_names.get(a, '')}".strip() for a in peerflood_readonly))
    if skipped_offline:
        state["errors"].append("Bỏ qua tài khoản chưa kết nối: " + ", ".join(f"#{a}" for a in skipped_offline))

    def _cnt(aid: int) -> int:
        return account_request_count.get(aid, 0)

    def _is_flooded(aid: int) -> bool:
        return flood_until.get(aid, 0) > time.time()

    def _resolve_limited(aid: int) -> bool:
        return resolve_flood_until.get(aid, 0) > time.time()

    def _mark_flood(aid: int, seconds: int):
        flood_until[aid] = max(flood_until.get(aid, 0), time.time() + seconds)

    def _mark_resolve_flood(aid: int, seconds: int):
        resolve_flood_until[aid] = max(resolve_flood_until.get(aid, 0), time.time() + seconds)
        logger.warning(f"[DeepCrawl] Account #{aid}: username lookups limited for {seconds}s — it keeps crawling")

    def _usable(aid: int) -> bool:
        now = time.time()
        return (flood_until.get(aid, 0) <= now and rest_until.get(aid, 0) <= now
                and _cnt(aid) < per_account_budget)

    def _session_peer(c, key):
        """InputPeerChannel from the account's own session cache (no network call)."""
        try:
            p = c.session.get_input_entity(key)
        except Exception:
            return None
        if asyncio.iscoroutine(p):  # async session backend: skip the cache
            p.close()
            return None
        return p if isinstance(p, InputPeerChannel) and getattr(p, "access_hash", None) else None

    def _remember(aid: int, chats) -> None:
        for ch in chats or []:
            h = getattr(ch, "access_hash", None)
            if h and getattr(ch, "id", None):
                known_hash.setdefault(ch.id, {})[aid] = h

    def _can_open(aid: int, c, ch_id: int, horizon: float = 0) -> bool:
        """The account can open the channel without a limited username lookup: it knows the
        hash, its username lookups are (or within `horizon` seconds will be) free, or its
        session already has the channel."""
        return (aid in known_hash.get(ch_id, {})
                or resolve_flood_until.get(aid, 0) <= time.time() + horizon
                or _session_peer(c, PeerChannel(ch_id)) is not None)

    def _next_job():
        """(queue index, aid, client) for the next node, or None when no free account can
        take any node right now. A node a free account already knows comes first (no
        username lookup), unless that account is far ahead of the others; otherwise the
        oldest node a free account can open. Nodes that no account can open (only limited
        username lookups would) are dropped and counted."""
        nonlocal account_idx
        n = len(valid_clients)
        ready = [(i, aid, c) for i, (aid, c) in enumerate(valid_clients) if _usable(aid) and aid not in busy]
        if not ready:
            return None
        least = min(_cnt(aid) for _, aid, _ in ready)

        def best(cand):
            nonlocal account_idx
            t = min(cand, key=lambda t: (_cnt(t[1]), (t[0] - account_idx - 1) % n))
            account_idx = t[0]
            return t[1], t[2]

        for qi, item in enumerate(queue):
            holders = known_hash.get(item[0][0], {})
            cand = [t for t in ready if t[1] in holders and _cnt(t[1]) <= least + HOLDER_SLACK]
            if cand:
                return (qi, *best(cand))
        qi = 0
        while qi < len(queue):
            ch_id = queue[qi][0][0]
            cand = [t for t in ready if _can_open(t[1], t[2], ch_id)]
            if cand:
                return (qi, *best(cand))
            if not any(_cnt(a) < per_account_budget and _can_open(a, c, ch_id, MAX_INLINE_WAIT)
                       for a, c in valid_clients):
                logger.info(f"[DeepCrawl] Skip '{queue[qi][2]}': only limited username lookups could open it")
                del queue[qi]
                state["nodes_skipped"] += 1
                continue
            qi += 1  # an account that is resting or briefly limited can open it later
        return None

    async def _wait_for_any_account() -> bool:
        """No free account can take a node (FloodWait, anti-ban break or out of budget).
        Wait (capped) if one frees up soon. Returns False when the crawl should stop."""
        with_budget = [aid for aid, _ in valid_clients if _cnt(aid) < per_account_budget]
        if not with_budget:
            state["errors"].append(f"Đã dùng hết hạn mức an toàn ({per_account_budget} request/tài khoản) — "
                                   "dừng để bảo vệ tài khoản. Kết quả đã lưu.")
            return False
        now = time.time()
        busy = [a for a in with_budget if not _usable(a)]
        soonest = min((max(flood_until.get(a, 0), rest_until.get(a, 0)) for a in busy), default=now) - now
        if soonest > MAX_INLINE_WAIT:
            state["errors"].append(f"Mọi tài khoản đang bị Telegram giới hạn (~{int(soonest // 60) + 1} phút) — "
                                   "dừng sớm, kết quả đã lưu. Hãy chạy lại sau.")
            return False
        wait = max(1, int(soonest) + 2)
        if any(flood_until.get(a, 0) > now for a in with_budget):
            state["errors"].append(f"⏳ Mọi tài khoản đang FloodWait — chờ {wait}s")
        state["current_account"] = f"Nghỉ chống ban {wait}s"
        await _update_progress()
        await asyncio.sleep(wait)
        return True

    async def _web_classify(names) -> None:
        """Fill the contact-kind cache from public t.me pages (no account request)."""
        now = time.time()
        todo = []
        for n in names:
            k = n.lstrip("@").lower()
            hit = _contact_kind_cache.get(k)
            if not (hit and now - hit[1] < _CONTACT_KIND_TTL):
                todo.append(k)
        if not todo:
            return
        state["web_lookups"] += len(todo)
        for k, info in (await tme_preview.fetch_many(todo)).items():
            _contact_kind_cache[k] = (info["kind"], time.time())

    async def _search_channel(c, uname: str):
        """contacts.Search fallback for the source channel (separate limit from ResolveUsername)."""
        from telethon.tl.functions.contacts import SearchRequest
        try:
            r = await c(SearchRequest(q=uname, limit=10))
        except Exception as e:
            logger.debug(f"[DeepCrawl] contacts.Search {uname}: {e}")
            return None
        for ch in getattr(r, "chats", []) or []:
            if (getattr(ch, "username", None) or "").lower() == uname.lower() and getattr(ch, "access_hash", None):
                return InputPeerChannel(ch.id, ch.access_hash)
        return None

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

    # Step 1: open the source channel. Each account's session cache first (no network),
    # then a username lookup on accounts whose lookups are not limited, then
    # contacts.Search (a different, separately limited method).
    link = (channel_link or "").strip()
    src_uname, _is_invite = _tl_utils.parse_username(link)
    if _is_invite:
        src_uname = None
    source = None  # (aid, InputPeerChannel)
    resolve_errors = []
    for attempt in range(2):
        for _aid, _client in valid_clients:
            p = _session_peer(_client, link)
            if p:
                source = (_aid, p)
                break
        if source:
            break
        for _aid, _client in valid_clients:
            if _resolve_limited(_aid):
                _left = int(resolve_flood_until[_aid] - time.time())
                resolve_errors.append(f"#{_aid}: giới hạn tra username ~{_left // 60 + 1} phút")
                continue
            try:
                state["resolve_calls"] += 1
                p = await _client.get_input_entity(link)
            except errors.FloodWaitError as e:
                _mark_resolve_flood(_aid, e.seconds)
                resolve_errors.append(f"#{_aid}: giới hạn tra username ~{e.seconds // 60 + 1} phút")
                continue
            except Exception as e:
                resolve_errors.append(f"#{_aid}: {str(e)[:60]}")
                continue
            if isinstance(p, InputPeerChannel):
                source = (_aid, p)
            else:
                resolve_errors.append(f"#{_aid}: không phải kênh")
            break  # resolved: other accounts would get the same answer
        if source:
            break
        if src_uname:
            for _aid, _client in valid_clients:
                p = await _search_channel(_client, src_uname)
                if p:
                    source = (_aid, p)
                    break
        if source:
            break
        waits = [resolve_flood_until.get(a, 0) - time.time() for a, _ in valid_clients]
        if attempt == 0 and waits and all(0 < w <= MAX_INLINE_WAIT for w in waits):
            await asyncio.sleep(min(waits) + 2)  # every account is only briefly limited
            resolve_errors.clear()
            continue
        break
    if not source:
        msg = (f"Không mở được kênh nguồn '{channel_link}': "
               + ("; ".join(dict.fromkeys(resolve_errors)) or "không tìm thấy kênh"))
        waits = [resolve_flood_until.get(a, 0) - time.time() for a, _ in valid_clients]
        if waits and all(w > 0 for w in waits):
            # Every account's username lookups are limited: retry when the first one frees up
            raise DeepCrawlRetryLater(msg, int(min(waits)) + 1)
        raise Exception(msg)
    src_aid, src_peer = source
    known_hash.setdefault(src_peer.channel_id, {})[src_aid] = src_peer.access_hash
    visited.add(src_peer.channel_id)
    src_title = None
    if src_uname:
        info = await tme_preview.fetch(src_uname)
        src_title = (info or {}).get("title")
    src_title = src_title or (f"@{src_uname}" if src_uname else link)
    logger.info(f"[DeepCrawl] Source '{src_title}' opened via account #{src_aid}")

    queue.clear()
    queue.append(((src_peer.channel_id, src_peer.access_hash, src_uname), 0, src_title))

    parallel = max(1, min(int(parallel or 1), len(valid_clients)))
    logger.info(f"[DeepCrawl] Starting BFS from '{channel_link}', max_depth={max_depth}, "
                f"accounts={len(valid_clients)}, parallel={parallel}")

    async def _process_node(channel_ref, depth, node_title, aid, client):
        """Crawl one node with one account. Up to `parallel` of these run at once, each on
        its own account; shared state is only touched between awaits (single event loop)."""
        # Don't go deeper than max_depth
        if depth > max_depth:
            return

        # Depth 0 = source channel, just get its recommendations
        # Depth 1-4 = similar channels found at that depth
        state["current_depth"] = depth
        ch_id_q, _ch_hash_q, ch_uname_q = channel_ref

        # Open the channel: hash this account already knows → its session cache → username lookup
        h = known_hash.get(ch_id_q, {}).get(aid)
        peer = InputPeerChannel(ch_id_q, h) if h else _session_peer(client, PeerChannel(ch_id_q))
        if peer is None and ch_uname_q:
            try:
                state["resolve_calls"] += 1
                account_request_count[aid] = _cnt(aid) + 1
                peer = await client.get_input_entity(ch_uname_q)
            except errors.FloodWaitError as e:
                _mark_resolve_flood(aid, e.seconds)
                queue.appendleft((channel_ref, depth, node_title))  # a holder or another account takes it
                return
            except Exception as e:
                state["errors"].append(f"Không mở được kênh '{node_title}': {str(e)[:60]}")
                return
        if not isinstance(peer, InputPeerChannel) or peer.channel_id != ch_id_q:
            state["errors"].append(f"Bỏ qua kênh '{node_title}': không mở được (username đã đổi?)")
            return
        known_hash.setdefault(ch_id_q, {})[aid] = peer.access_hash

        ch_title = node_title
        state["current_channel"] = ch_title
        logger.info(f"[DeepCrawl] Depth {depth}: '{ch_title}' (id={ch_id_q}) via account #{aid}")
        await _update_progress()

        # Similar channels of this node. Each account keeps its own gap between these calls,
        # whatever the other accounts are doing.
        gap = rec_ready_at.get(aid, 0) - time.time()
        if gap > 0:
            await asyncio.sleep(gap)
        try:
            res = await client(GetChannelRecommendationsRequest(channel=peer))
            account_request_count[aid] = _cnt(aid) + 1
            rec_ready_at[aid] = time.time() + random.uniform(*REC_GAP)
        except errors.FloodWaitError as e:
            _mark_flood(aid, e.seconds)
            logger.warning(f"[DeepCrawl] FloodWait {e.seconds}s on GetRecommendations for account #{aid} "
                           "— another account redoes the node")
            queue.appendleft((channel_ref, depth, node_title))
            return
        except Exception as e:
            state["errors"].append(f"Recommendations error for '{ch_title}': {str(e)[:80]}")
            logger.warning(f"[DeepCrawl] Recommendations error for '{ch_title}': {e}")
            return

        # Anti-ban: delay after recommendation request
        await _safe_delay(3.0, 5.0)

        # Process each recommended channel
        chats = getattr(res, 'chats', []) or []
        _remember(aid, chats)
        logger.info(f"[DeepCrawl] Depth {depth}: '{ch_title}' returned {len(chats)} recommended chats (type={type(res).__name__})")
        state["channels_processed"] += 1
        requeue_node = False  # set when this session gets limited and another account can redo the node

        # Descriptions come from the public t.me page: no account request, no FloodWait.
        # GetFullChannel below is only the fallback (no username / t.me unavailable).
        new_chats = [ch for ch in chats if getattr(ch, 'broadcast', False) and ch.id not in visited]
        now_ts = time.time()
        need_web = [ch for ch in new_chats if getattr(ch, 'username', None)
                    and not (ch.id in _channel_about_cache and now_ts - _channel_about_cache[ch.id][1] < _CHANNEL_ABOUT_TTL)]
        if need_web:
            state["web_lookups"] += len(need_web)
            web = await tme_preview.fetch_many([ch.username for ch in need_web])
            for ch in need_web:
                info = web.get(ch.username.lower())
                if info and info["kind"] == "channel":
                    _channel_about_cache[ch.id] = (info["description"], time.time())
        # Check every @mention of this node in one concurrent batch
        mentions = set()
        for ch in new_chats:
            about = _channel_about_cache.get(ch.id)
            if about:
                mentions.update(username_regex.findall(about[0]))
        await _web_classify(mentions)

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
            contacts_unverified = []
            no_description = False
            try:
                cached = _channel_about_cache.get(ch_id)
                if cached and time.time() - cached[1] < _CHANNEL_ABOUT_TTL:
                    description = cached[0]
                elif _is_flooded(aid) or account_request_count.get(aid, 0) >= per_account_budget:
                    if any(_usable(a) for a, _ in valid_clients):
                        requeue_node = True  # let a healthy account redo the rest of this node
                    else:
                        # Nobody can fetch it: keep the lead (title-based score), skip its description
                        no_description = True
                else:
                    # MUST use the same client that fetched recommendations
                    # because `chat` object is bound to that client's session
                    full_chat = await client(GetFullChannelRequest(channel=chat))
                    description = getattr(full_chat.full_chat, 'about', '') or ""
                    account_request_count[aid] = account_request_count.get(aid, 0) + 1
                    _channel_about_cache[ch_id] = (description, time.time())
                    # Anti-ban: shorter delay for full channel fetch
                    await _safe_delay(1.0, 2.0)

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

                # Keep only real people: drop channels, groups, bots, deleted/scam and dead
                # usernames. Kinds come from public t.me pages; ResolveUsername is only a
                # small, capped fallback for usernames t.me could not answer.
                await _web_classify(contacts)
                verified, unverified = [], []
                for c_str in contacts:
                    _hit = _contact_kind_cache.get(c_str.lstrip("@").lower())
                    if _hit and time.time() - _hit[1] < _CONTACT_KIND_TTL:
                        kind = _hit[0]
                    elif (_resolve_limited(aid) or _is_flooded(aid)
                          or contact_resolves.get(aid, 0) >= CONTACT_RESOLVE_CAP
                          or _cnt(aid) >= per_account_budget):
                        unverified.append(c_str)
                        continue
                    else:
                        contact_resolves[aid] = contact_resolves.get(aid, 0) + 1
                        account_request_count[aid] = _cnt(aid) + 1
                        state["resolve_calls"] += 1
                        await _safe_delay(1.0, 2.0)  # anti-ban: space out ResolveUsername calls
                        try:
                            kind = await classify_contact_username(client, c_str)
                        except errors.FloodWaitError as fe:
                            _mark_resolve_flood(aid, fe.seconds)
                            unverified.append(c_str)
                            continue
                        except Exception as ce:
                            logger.debug(f"[DeepCrawl] contact check {c_str} failed: {ce}")
                            unverified.append(c_str)
                            continue
                    if kind == "user":
                        verified.append(c_str)
                    else:
                        state["contacts_removed"] += 1
                        logger.debug(f"[DeepCrawl] drop contact {c_str}: {kind}")
                contacts = verified + unverified
                contacts_unverified = unverified
            except errors.FloodWaitError as e:
                # Don't sleep for the full (possibly hours-long) wait: mark this account and
                # hand the rest of this node to another account; only sleep for short waits.
                _mark_flood(aid, e.seconds)
                logger.warning(f"[DeepCrawl] FloodWait {e.seconds}s on GetFullChannel for account #{aid}")
                if any(_usable(a) for a, _ in valid_clients):
                    requeue_node = True
                else:
                    no_description = True
                    if e.seconds <= 30:
                        await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                logger.debug(f"[DeepCrawl] GetFullChannel error for {title}: {e}")

            if requeue_node:
                visited.discard(ch_id)  # not processed yet — the retry will pick it up
                break

            score_info = score_community_trading(title, description, username, contacts, participants_count=participants_count)
            lead = {
                "channel_id": ch_id,
                "title": title,
                "username": username,
                "participants_count": participants_count,
                "description": description,
                "contacts": contacts,
                "contacts_unverified": contacts_unverified,
                "no_description": no_description,
                "depth": depth + 1,        # This channel was found at depth+1
                "parent_channel": node_title,
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
                if score_info["trading_score"] >= min_expand_score:
                    ch_access_hash = getattr(chat, 'access_hash', None) or 0
                    queue.append(((ch_id, ch_access_hash, username), depth + 1, title))
                else:
                    # Low-relevance branch: keep the lead, don't crawl its neighbours
                    state["branches_pruned"] += 1

            await _update_progress()

        if requeue_node:
            # Recommendations are bound to the session that fetched them, so another account
            # re-fetches this node; channels already saved stay in `visited` and are skipped.
            state["channels_processed"] -= 1
            queue.appendleft((channel_ref, depth, node_title))

        # Anti-ban break every REST_EVERY requests. Only this account rests; the others
        # keep crawling (the whole crawl used to sleep).
        if _cnt(aid) >= next_rest_at.get(aid, REST_EVERY):
            next_rest_at[aid] = _cnt(aid) + REST_EVERY
            rest = random.uniform(30, 60)
            rest_until[aid] = time.time() + rest
            logger.info(f"[DeepCrawl] Anti-ban break for account #{aid}: {rest:.0f}s")

    async def _run_node(item, aid, client):
        try:
            await _process_node(*item, aid, client)
        except Exception as e:
            logger.exception(f"[DeepCrawl] Node '{item[2]}' failed on account #{aid}")
            state["errors"].append(f"Lỗi khi cào '{item[2]}': {str(e)[:80]}")
        finally:
            busy.discard(aid)

    # Dispatcher: hand nodes to free accounts, up to `parallel` at once.
    tasks: set = set()
    try:
        while True:
            if stop_flag and stop_flag.get("stopped"):
                state["status"] = "stopped"
                break
            while len(tasks) < parallel and queue:
                job = _next_job()
                if job is None:
                    break
                qi, aid, client = job
                item = queue[qi]
                del queue[qi]
                busy.add(aid)
                tasks.add(asyncio.create_task(_run_node(item, aid, client)))
            if busy:
                state["current_account"] = ", ".join(f"Account #{a}" for a in sorted(busy))
            if not tasks:
                if not queue:  # done, or every remaining node was dropped
                    break
                if not await _wait_for_any_account():
                    state["status"] = "stopped"
                    break
                continue
            # Wake when a node finishes, or after 1s to give work to an account that came
            # back from a break (local check, no Telegram call).
            done, _ = await asyncio.wait(tasks, timeout=1, return_when=asyncio.FIRST_COMPLETED)
            tasks -= done
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise
    if tasks:  # stopped: let running nodes finish (they check the stop flag)
        await asyncio.gather(*tasks, return_exceptions=True)
    if stop_flag and stop_flag.get("stopped"):
        await _update_progress()
        logger.info(f"[DeepCrawl] Stopped by user. Found {len(all_leads)} channels.")

    # Done
    if state["nodes_skipped"]:
        state["errors"].append(f"Bỏ qua {state['nodes_skipped']} kênh: chỉ mở được bằng tra username, "
                               "mà tài khoản đang bị Telegram giới hạn tra username")
    if state["status"] != "stopped":
        state["status"] = "completed"
    state["channels_found"] = len(all_leads)
    await _update_progress()

    logger.info(f"[DeepCrawl] Finished. Found {len(all_leads)} unique channels, {state['contacts_found']} contacts "
                f"({state['contacts_removed']} non-user removed, {state['branches_pruned']} branches pruned) "
                f"across {max_depth} layers. Requests per account: {account_request_count}; "
                f"t.me lookups {state['web_lookups']}, username lookups {state['resolve_calls']}")
    return all_leads



async def _check_spam_status_raw(account_id: int) -> dict:
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

        # Send /start command (unblock @SpamBot first if this account blocked it)
        try:
            await client.send_message(spam_bot, "/start")
        except Exception as send_err:
            if "blocked" not in str(send_err).lower():
                raise
            from telethon.tl.functions.contacts import UnblockRequest
            await client(UnblockRequest(id=spam_bot))
            logger.info(f"[SpamCheck] Account {account_id}: unblocked @SpamBot")
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
        # Only SpamBot's explicit "no limits" sentence counts as clean. Loose
        # words like "free"/"good news" also appear in temporary-limit replies
        # ("...will be automatically released / free on 12 Nov"), so limits win.
        _free = ("no limits are currently applied", "free as a bird",
                 "нет ограничений", "не ограничен", "không bị giới hạn", "không có giới hạn")
        _limited = ("limited", "restrict", "released", "spam", "until",
                    "ограничен", "спам", "giới hạn", "bị hạn chế")
        if any(kw in reply_lower for kw in _free):
            status = "free"
        elif any(kw in reply_lower for kw in _limited):
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


_SPAMBOT_UNTIL_RE = _re.compile(r"limited until\s+(\d{1,2}\s+\w{3,9}\s+\d{4}),?\s+(\d{1,2}:\d{2})\s*UTC", _re.I)


def parse_spambot_until(message: str) -> int | None:
    """'…limited until 8 Nov 2026, 08:44 UTC…' → unix seconds (None if no date)."""
    from datetime import datetime, timezone
    m = _SPAMBOT_UNTIL_RE.search(message or "")
    if not m:
        return None
    for fmt in ("%d %b %Y %H:%M", "%d %B %Y %H:%M"):
        try:
            return int(datetime.strptime(f"{m.group(1)} {m.group(2)}", fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def spam_entry_view(entry: dict, now: float | None = None) -> dict:
    """Cached @SpamBot entry + `until` (unix, when the limit lifts) and `expired`.

    expired=True: SpamBot said "limited until X" and X has passed — the entry is
    stale, the account is treated as usable and @SpamBot is asked again on the
    next campaign run (or by the campaign modal).
    """
    out = dict(entry or {})
    if out.get("status") == "limited":
        until = parse_spambot_until(out.get("message", ""))
        out["until"] = until
        out["expired"] = bool(until and until <= (time.time() if now is None else now))
    return out


async def get_cached_spam_status(account_id: int, max_age_hours: float = 24) -> dict | None:
    """Last @SpamBot result if it is recent and conclusive, else None."""
    try:
        import json as _json
        import database as _db
        from datetime import datetime, timezone
        cache = _json.loads(await _db.get_setting("spam_status_cache", "{}") or "{}")
        entry = cache.get(str(account_id)) or {}
        if entry.get("status") not in ("free", "limited") or not entry.get("checked_at"):
            return None
        if entry["status"] == "limited":
            until = parse_spambot_until(entry.get("message", ""))
            if until and until <= time.time():
                return None  # the limit has already expired → ask @SpamBot again
        checked = datetime.fromisoformat(entry["checked_at"])
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - checked).total_seconds() < max_age_hours * 3600:
            return {**entry, "cached": True}
    except Exception as e:
        logger.debug(f"[SpamCheck] Cache read failed for {account_id}: {e}")
    return None


async def check_spam_status(account_id: int, max_age_hours: float = 0, auto_unpause: bool = True) -> dict:
    """Check @SpamBot and remember the result so the Accounts page can show it.

    max_age_hours > 0 reuses a cached result younger than that instead of
    messaging @SpamBot again (campaign starts use 10 min, so every run asks
    afresh but several campaigns started together share one answer).
    """
    if max_age_hours > 0:
        cached = await get_cached_spam_status(account_id, max_age_hours)
        if cached:
            return cached
    result = await _check_spam_status_raw(account_id)
    try:
        import json as _json
        import database as _db
        cache = _json.loads(await _db.get_setting("spam_status_cache", "{}") or "{}")
        cache[str(account_id)] = {k: result.get(k) for k in ("status", "message", "checked_at")}
        await _db.set_setting("spam_status_cache", _json.dumps(cache, ensure_ascii=False))
        # PeerFlood auto-pauses the whole account; once SpamBot says it is clean
        # again, turn it back on so DMs and the AI Agent resume by themselves.
        if auto_unpause and result.get("status") == "free":
            acc = await _db.get_account(account_id)
            if acc and acc.get("is_paused") and str(acc.get("pause_reason") or "").startswith("PeerFlood"):
                await _db.unpause_account(account_id)
                logger.info(f"[SpamCheck] Account {account_id} is free again — auto-unpaused (was PeerFlood)")
    except Exception as e:
        logger.debug(f"[SpamCheck] Could not cache result for {account_id}: {e}")
    return result


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
        await client(InviteToChannelRequest(
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
    for _aid, client in _clients.items():
        if client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
    logger.info(f"Disconnected {len(_clients)} clients")
