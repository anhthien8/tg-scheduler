"""Suggest DM-campaign content + AI settings for a scraped list.

The campaign modal calls this when a list is picked, so the user only reviews
and saves. Inputs are all local data, no AI call:
  - the list itself (kol_channels.country for Deep-Crawl saves, name scripts
    for member scrapes) -> audience (KOL vs members) and language mix;
  - past campaigns' real results (ai_followup_chats reply_count>0 / sent_count)
    -> reuse the best-performing message in the same language;
  - agents / AI keys -> Remix + Agent dropdown.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from channel_country import _CYR, _SCRIPTS, _VI, guess_country
from database import get_db, get_settings_bulk

MIN_SENT = 15          # past campaign needs this many sends to be trusted
REMIX_MIN_TARGETS = 20  # below this, identical text is not a spam risk
_KOL_JOB = re.compile(r"_Contacts_|_VIP_|^deep_|kol", re.I)
_NAME_VARS = re.compile(r"[ \t]*(\{|\[)(first_name|name|full_name|last_name)(\}|\])")
_BRANDS = ("weex", "blofin", "bitget", "bitbase", "pu prime", "bybit", "okx", "mexc", "bingx")

COUNTRY_LANG = {
    "EN": "en", "IN": "en", "PK": "en", "NG": "en", "PH": "en", "CN": "zh", "AR": "ar",
    "RU": "ru", "UA": "uk", "VN": "vi", "ID": "id", "MY": "ms", "TR": "tr", "IR": "fa",
    "KR": "ko", "JP": "ja", "TH": "th", "ES": "es", "BR": "pt", "DE": "de", "FR": "fr",
    "IT": "it",
}
LANG_LABEL = {
    "en": "EN", "zh": "CN", "ar": "AR", "ru": "RU", "uk": "UA", "vi": "VN", "id": "ID",
    "ms": "MY", "tr": "TR", "fa": "IR", "ko": "KR", "ja": "JP", "th": "TH", "es": "ES",
    "pt": "BR", "de": "DE", "fr": "FR", "it": "IT", "hi": "IN",
}
_SCRIPT_LANG = {"KR": "ko", "JP": "ja", "CN": "zh", "TH": "th", "IR": "fa", "AR": "ar", "IN": "hi"}

_FALLBACK = {
    ("en", "kol"): (
        "{Hey|Hi|Hello} 👋\n\nI'm with the <BRAND> partnerships team. {Came across|Saw} your "
        "trading community and think there's a good fit for a revenue-share partnership: "
        "competitive commission, fast payouts, and a dedicated BD manager for you.\n\n"
        "No need to leave your current exchange, <BRAND> can run alongside it.\n\n"
        "Open to a quick look at the terms?"
    ),
    ("vi", "kol"): (
        "Chào bạn 👋\n\nMình phụ trách Partnership của <BRAND>. Thấy cộng đồng trading của bạn "
        "khá chất nên muốn trao đổi nhanh về chương trình hợp tác chia sẻ doanh thu: hoa hồng "
        "cạnh tranh, thanh toán nhanh, có BD hỗ trợ riêng.\n\n"
        "Không cần bỏ sàn hiện tại, <BRAND> chạy song song được.\n\n"
        "Bạn có muốn xem qua điều khoản không?"
    ),
    ("en", "members"): (
        "{Hey|Hi} {first_name} 👋 {Saw|Noticed} you in {group}. Are you trading futures "
        "right now? <BRAND> is onboarding active traders with low fees and VIP support. "
        "Want me to send you the details?"
    ),
    ("vi", "members"): (
        "Chào {first_name} 👋 Mình thấy bạn trong nhóm {group}. Bạn có đang trade futures không? "
        "<BRAND> đang hỗ trợ trader mới phí thấp và chăm sóc VIP. Mình gửi bạn thông tin nhé?"
    ),
}


def text_lang(text: str) -> str:
    """Language of a message we wrote (vi / en / script languages)."""
    t = text or ""
    if len(_VI.findall(t)) >= 3:
        return "vi"
    for code, rx in _SCRIPTS:
        if len(rx.findall(t)) >= 3:
            return _SCRIPT_LANG.get(code, "en")
    if len(_CYR.findall(t)) >= 6:
        return "ru"
    return "en"


def _name_lang(name: str) -> str | None:
    """Script-based guess for a person's display name; Latin names -> None."""
    for code, rx in _SCRIPTS:
        if rx.search(name):
            return _SCRIPT_LANG.get(code)
    if len(_CYR.findall(name)) >= 2:
        return "ru"
    if len(_VI.findall(name)) >= 2:
        return "vi"
    return None


def strip_name_vars(text: str) -> str:
    """'Hey {first_name}, ...' -> 'Hey, ...' for lists without real names."""
    out = _NAME_VARS.sub("", text or "")
    return re.sub(r"[ \t]+([,!.?])", r"\1", out)


def mentioned_brand(text: str) -> str | None:
    low = (text or "").lower()
    return next((b for b in _BRANDS if b in low), None)


def smoothed_rate(replied: int, sent: int) -> float:
    return (replied + 1) / (sent + 10)


async def _kol_jobs(conn) -> set[str]:
    cur = await conn.execute("SELECT DISTINCT saved_job FROM kol_channels WHERE saved_job IS NOT NULL AND saved_job != ''")
    return {r[0] for r in await cur.fetchall()}


def _audience(job_id: str, kol_jobs: set[str]) -> str:
    return "kol" if (job_id in kol_jobs or _KOL_JOB.search(job_id or "")) else "members"


async def profile_list(job_id: str) -> dict:
    async with get_db() as conn:
        kol_jobs = await _kol_jobs(conn)
        cur = await conn.execute(
            "SELECT COUNT(*), MAX(group_title) FROM scraped_members WHERE scrape_job_id = ?", (job_id,))
        total, group_title = await cur.fetchone()
        cur = await conn.execute(
            "SELECT first_name, last_name FROM scraped_members WHERE scrape_job_id = ? LIMIT 3000", (job_id,))
        names = await cur.fetchall()
        cur = await conn.execute(
            "SELECT COALESCE(country,''), COUNT(*) FROM kol_channels WHERE saved_job = ? GROUP BY 1", (job_id,))
        countries = await cur.fetchall()

    dist: Counter = Counter()
    for code, n in countries:
        lang = COUNTRY_LANG.get((code or "").upper())
        if lang:
            dist[lang] += n
    real = 0
    for first, last in names:
        nm = f"{first or ''} {last or ''}".strip()
        if not nm or nm.startswith("@"):
            continue
        real += 1
        if not countries:
            lang = _name_lang(nm)
            if lang:
                dist[lang] += 1
    glang = COUNTRY_LANG.get(guess_country(group_title or ""))
    if glang and glang != "en":
        dist[glang] += max(3, sum(dist.values()) // 3)

    known = sum(dist.values())
    vi_share = dist["vi"] / known if known else 0.0
    base = "vi" if known >= 3 and vi_share >= 0.5 else "en"
    base_share = (dist[base] / known) if known else 0.0
    audience = "kol" if countries else _audience(job_id, kol_jobs)
    return {
        "job_id": job_id, "group_title": group_title, "total": total or 0,
        "audience": audience, "has_real_names": bool(names) and real / len(names) >= 0.5,
        "languages": dict(dist.most_common()), "known": known,
        "language": base, "base_share": base_share,
    }


async def _past_campaigns() -> list[dict]:
    async with get_db() as conn:
        kol_jobs = await _kol_jobs(conn)
        cur = await conn.execute(
            "SELECT campaign_id, SUM(reply_count > 0) FROM ai_followup_chats "
            "WHERE campaign_id IS NOT NULL GROUP BY campaign_id")
        replied = {r[0]: int(r[1] or 0) for r in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT id, name, scrape_job_id, messages, sent_count, use_ai_remix, ai_agent_id "
            "FROM dm_campaigns WHERE sent_count >= ?", (MIN_SENT,))
        rows = await cur.fetchall()
    out = []
    for cid, name, job, msgs, sent, remix, agent in rows:
        try:
            messages = [m for m in json.loads(msgs or "[]") if isinstance(m, dict)]
        except (TypeError, ValueError):
            continue
        text = "\n".join(m.get("content") or "" for m in messages)
        if len(text.strip()) <= 30:
            continue
        out.append({
            "id": cid, "name": name, "messages": messages, "text": text,
            "sent": sent, "replied": replied.get(cid, 0), "use_ai_remix": bool(remix),
            "ai_agent_id": agent, "lang": text_lang(text), "audience": _audience(job, kol_jobs),
            "brand": mentioned_brand(text),
        })
    return out


def pick_best(campaigns: list[dict], language: str, audience: str, brand: str | None) -> dict | None:
    best, best_score = None, 0.0
    for c in campaigns:
        if c["lang"] != language:
            continue
        if brand and c["brand"] and c["brand"] != brand:
            continue  # another exchange's pitch
        score = smoothed_rate(c["replied"], c["sent"])
        if c["audience"] == audience:
            score *= 1.15
        if brand and c["brand"] == brand:
            score *= 1.1
        if score > best_score:
            best, best_score = c, score
    return best


def _has_keys(raw) -> bool:
    try:
        return bool(json.loads(raw or "[]"))
    except (TypeError, ValueError):
        return False


async def _agents() -> list[dict]:
    async with get_db() as conn:
        cur = await conn.execute("SELECT id, name, is_active, api_keys_json FROM ai_agents")
        agents = [{"id": r[0], "name": r[1], "active": bool(r[2]), "has_keys": _has_keys(r[3])}
                  for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT ai_agent_id, COUNT(*) FROM dm_campaigns WHERE ai_agent_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
        usage = [r[0] for r in await cur.fetchall()]
    order = {aid: i for i, aid in enumerate(usage)}
    return sorted(agents, key=lambda a: order.get(a["id"], 999))


def pick_agent(agents: list[dict], source_agent_id, brand: str | None) -> tuple[dict | None, str]:
    active = [a for a in agents if a["active"]]
    by_id = {a["id"]: a for a in active}
    if source_agent_id in by_id:
        return by_id[source_agent_id], "dùng lại Agent của chiến dịch mẫu"
    if brand:
        hit = next((a for a in active if brand in (a["name"] or "").lower()), None)
        if hit:
            return hit, f"khớp brand {brand.upper()}"
    if active:
        return active[0], "Agent được dùng nhiều nhất"
    return None, ""


async def _ai_configured(agent: dict | None) -> bool:
    if agent and agent.get("has_keys"):
        return True
    provs = ["chatgpt_oauth", "gemini", "groq", "openai", "deepseek", "openai_compatible"]
    cfg = await get_settings_bulk({"ai_provider": None, **{f"ai_keys_{p}": "[]" for p in provs}})
    if cfg.get("ai_provider") in ("openai_compatible", "chatgpt_oauth"):
        return True
    for p in provs:
        try:
            if json.loads(cfg.get(f"ai_keys_{p}") or "[]"):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _lang_summary(languages: dict, known: int) -> str:
    if not known:
        return "chưa rõ ngôn ngữ"
    parts = [f"{round(n * 100 / known)}% {LANG_LABEL.get(l, l.upper())}" for l, n in list(languages.items())[:4]]
    return ", ".join(parts)


async def suggest(job_id: str, brand: str | None = None) -> dict:
    prof = await profile_list(job_id)
    brand = (brand or "").strip().lower() or None
    lang, audience = prof["language"], prof["audience"]
    reasons: list[str] = []

    who = "KOL/contact" if audience == "kol" else "member"
    reasons.append(f"Tệp {prof['total']} {who} — {_lang_summary(prof['languages'], prof['known'])} "
                   f"→ viết bằng {'tiếng Việt' if lang == 'vi' else 'tiếng Anh'}")

    best = pick_best(await _past_campaigns(), lang, audience, brand)
    if best:
        messages = [{"content": m.get("content") or ""} for m in best["messages"] if (m.get("content") or "").strip()]
        rate = round(best["replied"] * 100 / best["sent"]) if best["sent"] else 0
        reasons.append(f"Nội dung lấy từ chiến dịch #{best['id']} «{best['name']}»: {best['replied']}/{best['sent']} "
                       f"người trả lời ({rate}%) — tốt nhất trong các chiến dịch cùng ngôn ngữ")
        brand = brand or best["brand"]
    else:
        tpl = _FALLBACK[(lang, audience)].replace("<BRAND>", (brand or "WEEX").upper())
        messages = [{"content": tpl}]
        reasons.append("Chưa có chiến dịch cùng ngôn ngữ đủ dữ liệu (≥15 tin đã gửi) → dùng mẫu chuẩn, nên sửa lại cho hợp")

    if not prof["has_real_names"]:
        stripped = [{"content": strip_name_vars(m["content"])} for m in messages]
        if stripped != messages:
            reasons.append("Tệp chỉ có @username, không có tên thật → bỏ biến {first_name}/{name} để không chào «Hey @abc»")
        messages = stripped

    agent, why_agent = pick_agent(await _agents(), best["ai_agent_id"] if best else None, brand)
    if agent:
        reasons.append(f"Agent: {agent['name']} ({why_agent})")

    remix = prof["total"] >= REMIX_MIN_TARGETS
    if remix and not await _ai_configured(agent):
        remix = False
        reasons.append("Chưa cấu hình AI key → tắt Remix (vào Cài đặt AI để bật)")
    elif remix:
        reasons.append(f"Remix bật: {prof['total']} người nhận → AI viết lại mỗi tin khác nhau, giảm bị đánh dấu spam")
    else:
        reasons.append(f"Remix tắt: tệp nhỏ ({prof['total']} người)")

    mixed = prof["known"] >= 10 and prof["base_share"] < 0.9
    translate = remix and (mixed or prof["known"] < 10)
    if translate:
        reasons.append("Bật Dịch: tệp nhiều ngôn ngữ → AI gửi bằng tiếng mẹ đẻ của từng người" if mixed
                       else "Bật Dịch: chưa đủ dữ liệu ngôn ngữ → để AI tự nhận tiếng mẹ đẻ của từng người")

    return {
        "messages": messages,
        "use_ai_remix": remix,
        "ai_agent_id": agent["id"] if agent else None,
        "auto_translate_native": translate,
        "language": lang,
        "audience": audience,
        "brand": brand,
        "source_campaign_id": best["id"] if best else None,
        "reasons": reasons,
    }
