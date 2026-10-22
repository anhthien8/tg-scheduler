"""
kol_offer.py
────────────
Pre-approved KOL starter package: the single commission ladder every KOL gets.

This module is the ONLY source of commission / bonus figures the AI may quote.
It replaces the per-language percentages that used to live inside the prompt
rules and the sample agent knowledge base (spec: docs/spec-kol-starter-package.md).

* The ladder is stored as structured JSON in the `kol_offer_ladder` setting
  (never free text) with `version` + `effective_from`, so a KOL can be tied to
  the exact version they agreed to (`kol_profiles.offer_version`).
* Every save bumps the version and writes a row to `kol_offer_ladder_history`.
* `render_offer_ladder_prompt()` turns the JSON into the prompt block injected
  into every AI turn (table + rules + AI boundaries).
* `find_off_ladder_figures()` is the code-side guard: any % or $ figure in an
  AI reply that is not in the ladder is caught *outside* the prompt and the
  reply is replaced by a safe fallback + handover, exactly like the existing
  `contains_ai_disclosure()` guard in dm_reply_tracker.
"""

import copy
import json
import logging
import re
from datetime import date

import database as db

logger = logging.getLogger("kol_offer")

LADDER_SETTING_KEY = "kol_offer_ladder"

# Founder-approved ladder (2026-10-21). Applies to every KOL, every region.
DEFAULT_LADDER: dict = {
    "version": 1,
    "effective_from": "2026-10-21",
    "headline": "up to 80% revshare",
    "tiers": [
        {"tier": 1, "min_volume_m": 0, "revshare_pct": 55, "bonus_per_1m_taker_usd": 100,
         "label": "Starting tier (5% above a standard affiliate)"},
        {"tier": 2, "min_volume_m": 10, "revshare_pct": 60, "bonus_per_1m_taker_usd": 100, "label": ""},
        {"tier": 3, "min_volume_m": 20, "revshare_pct": 65, "bonus_per_1m_taker_usd": 100, "label": ""},
        {"tier": 4, "min_volume_m": 50, "revshare_pct": 70, "bonus_per_1m_taker_usd": 50, "label": ""},
        {"tier": 5, "min_volume_m": 100, "revshare_pct": 80, "bonus_per_1m_taker_usd": 10, "label": ""},
    ],
    "rules": {
        "upgrade": "Tiers are reviewed per calendar month. Reaching a threshold this month unlocks the new tier from NEXT month. The first month is always the starting tier.",
        "downgrade": "Downgrades happen only at the quarterly review, if volume stays below the tier threshold.",
        "threshold_basis": "Tier thresholds use TOTAL monthly volume (maker + taker).",
        "bonus_basis": "The per-1M bonus is computed on TAKER volume only.",
        "commission_payout": "Commission (revshare) is paid DAILY.",
        "bonus_payout": "The volume bonus is paid on the 10th of the following month.",
        "exclusions": "Self-trading and wash volume are excluded from valid volume.",
        "sub_affiliate": "Volume from the KOL's sub-affiliates COUNTS toward their tier and bonus.",
        "bonus_dip_explanation": "At tiers 4-5 the bonus is lower because WEEX prioritises a higher, sustainable revshare over one-off bonuses.",
        "why_us": [
            "WEEX never trades against its users",
            "KYT / compliance in place — a safe home for the KOL's community",
            "real, running campaigns the KOL can plug into immediately",
            "rewards for organic communities, not just raw volume",
        ],
        "banned_phrases": ["B-book", "b book", "bbook"],
        # Figures that may legitimately appear in replies although they are not
        # ladder figures (e.g. '5%' = 'above a standard affiliate', '100% organic').
        "extra_allowed_percents": [5, 100],
        "extra_allowed_dollars": [],
    },
    "ai_may_close": [
        "the ladder itself, how tiers are calculated, payout schedule, exclusions",
        "why the bonus is lower at tiers 4-5",
        "choosing ONE currently valid campaign for the KOL",
        "receiving the KOL's WEEX UID and confirming it has been noted",
    ],
    "must_handover": [
        "upfront payment, trading funds, CPA or fixed fee requests",
        "any %, bonus or threshold outside this ladder, or an exception request",
        "contracts / paperwork, or payment in another token",
        "payout disputes or volume that does not match",
    ],
}

_TIER_KEYS = ("tier", "min_volume_m", "revshare_pct", "bonus_per_1m_taker_usd")


# ── Validation ──────────────────────────────────────────────────────────────

def validate_ladder(ladder) -> list[str]:
    """Return a list of human-readable problems; empty list == valid."""
    errors: list[str] = []
    if not isinstance(ladder, dict):
        return ["Thang offer phải là object JSON"]
    tiers = ladder.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        return ["Thiếu danh sách bậc (tiers)"]
    prev_vol = -1
    prev_rev = -1
    for i, t in enumerate(tiers, start=1):
        if not isinstance(t, dict):
            errors.append(f"Bậc {i}: không phải object")
            continue
        for k in _TIER_KEYS:
            if k not in t:
                errors.append(f"Bậc {i}: thiếu trường '{k}'")
        try:
            vol = float(t.get("min_volume_m", -1))
            rev = float(t.get("revshare_pct", -1))
            bonus = float(t.get("bonus_per_1m_taker_usd", -1))
        except (TypeError, ValueError):
            errors.append(f"Bậc {i}: mốc/revshare/bonus phải là số")
            continue
        if vol < 0:
            errors.append(f"Bậc {i}: mốc volume phải ≥ 0")
        if not (0 < rev <= 100):
            errors.append(f"Bậc {i}: revshare phải trong (0, 100]")
        if bonus < 0:
            errors.append(f"Bậc {i}: bonus phải ≥ 0")
        if vol <= prev_vol:
            errors.append(f"Bậc {i}: mốc volume phải tăng dần")
        if rev <= prev_rev:
            errors.append(f"Bậc {i}: revshare phải tăng dần")
        prev_vol, prev_rev = vol, rev
    if tiers and isinstance(tiers[0], dict):
        try:
            if float(tiers[0].get("min_volume_m", 1)) != 0:
                errors.append("Bậc 1 phải có mốc volume = 0 (bậc khởi điểm)")
        except (TypeError, ValueError):
            pass
    rules = ladder.get("rules", {})
    if rules is not None and not isinstance(rules, dict):
        errors.append("rules phải là object")
    eff = ladder.get("effective_from")
    if eff:
        try:
            date.fromisoformat(str(eff))
        except ValueError:
            errors.append("effective_from phải có dạng YYYY-MM-DD")
    return errors


def _normalize_ladder(ladder: dict) -> dict:
    """Coerce numbers, fill missing optional keys from the default."""
    out = copy.deepcopy(DEFAULT_LADDER)
    out.update({k: v for k, v in ladder.items() if k != "rules"})
    rules = copy.deepcopy(DEFAULT_LADDER["rules"])
    rules.update(ladder.get("rules") or {})
    out["rules"] = rules
    tiers = []
    for i, t in enumerate(ladder.get("tiers", []), start=1):
        tiers.append({
            "tier": int(t.get("tier", i)),
            "min_volume_m": float(t["min_volume_m"]),
            "revshare_pct": float(t["revshare_pct"]),
            "bonus_per_1m_taker_usd": float(t["bonus_per_1m_taker_usd"]),
            "label": str(t.get("label", "") or ""),
        })
    out["tiers"] = tiers
    return out


def _fmt_num(x) -> str:
    """55.0 -> '55', 12.5 -> '12.5'."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    return str(int(f)) if f == int(f) else f"{f:g}"


# ── Tier prediction ─────────────────────────────────────────────────────────

def parse_volume_m(value) -> float | None:
    """Parse a stated monthly volume into millions (USD).

    Accepts 15, '15', '15M', '15m', '15 million', '15,000,000', '$15M', '1.5B'.
    Returns None when nothing numeric is found.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        # Bare large numbers are raw USD, small ones are already millions.
        return v / 1_000_000 if v >= 100_000 else v
    s = str(value).strip().lower().replace(",", "").replace("$", "").replace("usdt", "").replace("usd", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(billion|million|thousand|bn|b|mn|m|k)?", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").strip()
    if unit in ("b", "bn", "billion"):
        return num * 1000
    if unit in ("m", "mn", "million"):
        return num
    if unit in ("k", "thousand"):
        return num / 1000
    return num / 1_000_000 if num >= 100_000 else num


def tier_for_volume(ladder: dict, volume_m: float | None) -> dict | None:
    """Highest tier whose threshold the volume meets (None when volume unknown)."""
    if volume_m is None:
        return None
    best = None
    for t in ladder.get("tiers", []):
        try:
            if float(volume_m) >= float(t["min_volume_m"]):
                best = t
        except (TypeError, ValueError, KeyError):
            continue
    return best


def predicted_tier(ladder: dict, volume_claimed) -> dict | None:
    """Tier the KOL would reach *from next month* given the volume they claimed."""
    vol = parse_volume_m(volume_claimed)
    t = tier_for_volume(ladder, vol)
    if t is None:
        return None
    return {**t, "volume_m": vol}


# ── Prompt rendering ────────────────────────────────────────────────────────

def _tier_rows(ladder: dict) -> list[str]:
    rows = []
    for t in ladder["tiers"]:
        vol = float(t["min_volume_m"])
        cond = (t.get("label") or "").strip() or (f">= {_fmt_num(vol)}M total volume / month" if vol > 0 else "starting tier")
        rows.append(
            f"| {t['tier']} | {cond} | {_fmt_num(t['revshare_pct'])}% | ${_fmt_num(t['bonus_per_1m_taker_usd'])} |"
        )
    return rows


def render_offer_ladder_prompt(ladder: dict | None = None, volume_claimed=None) -> str:
    """Build the prompt block the AI receives every turn.

    `volume_claimed` (profile field) enables the 'predicted tier' sentence; when
    absent the AI is told NOT to predict a tier.
    """
    ladder = _normalize_ladder(ladder or DEFAULT_LADDER)
    r = ladder["rules"]
    lines = [
        f"--- OFFICIAL WEEX KOL COMMISSION LADDER (v{ladder['version']}, effective {ladder['effective_from']}) ---",
        f"Headline you may use: \"{ladder.get('headline') or 'up to 80% revshare'}\". When the KOL asks for details, use EXACTLY this ladder. "
        "It is the SAME for every KOL, every region and every language — there is no other offer.",
        "| Tier | Condition (monthly) | RevShare | Bonus per 1M TAKER volume |",
        "|---|---|---|---|",
        *_tier_rows(ladder),
        "RULES:",
        f"- Upgrade: {r['upgrade']}",
        f"- Downgrade: {r['downgrade']}",
        f"- Threshold: {r['threshold_basis']} Bonus: {r['bonus_basis']}",
        f"- Payout: {r['commission_payout']} {r['bonus_payout']}",
        f"- Valid volume: {r['exclusions']} {r['sub_affiliate']}",
        f"- If asked why the bonus drops at the top tiers: {r['bonus_dip_explanation']}",
        "- When the KOL compares us with other exchanges, use ONLY these reasons: " + "; ".join(r.get("why_us") or []) + ".",
        "  NEVER name another exchange. NEVER use the phrase \"B-book\" or any variant.",
        "YOU MAY SETTLE ON YOUR OWN: " + "; ".join(ladder.get("ai_may_close") or []) + ".",
        "YOU MUST END YOUR REPLY WITH [HANDOVER_REQUIRED] FOR: " + "; ".join(ladder.get("must_handover") or []) + ".",
        "NEVER quote any % or $ figure that is not in this ladder (no internal estimates, no 'total payout' maths, no ranges like 50-60%).",
    ]
    pred = predicted_tier(ladder, volume_claimed)
    if pred is not None:
        lines.append(
            f"TIER PREDICTION ALLOWED: the KOL stated about {_fmt_num(pred['volume_m'])}M monthly volume → "
            f"if they keep that volume, from next month they qualify for Tier {pred['tier']} "
            f"({_fmt_num(pred['revshare_pct'])}% revshare + ${_fmt_num(pred['bonus_per_1m_taker_usd'])} per 1M taker). "
            "You may tell them this; remind them the first month starts at the starting tier."
        )
    else:
        lines.append(
            "TIER PREDICTION: the KOL has NOT stated their monthly volume — do NOT predict a tier for them. "
            "Ask naturally for their approximate monthly volume instead."
        )
    return "\n".join(lines)


# ── Code-side guard ─────────────────────────────────────────────────────────

_PCT_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[-–~]|to)\s*(\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)
_DOLLAR_RE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmM]\b)?")
_USD_SUFFIX_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?\s*(?:USDT|USD|dollars?|bucks)\b", re.IGNORECASE)

_MULT = {"k": 1_000, "m": 1_000_000}


def _to_number(num_s: str, unit: str | None) -> float:
    n = float(num_s.replace(",", ""))
    if unit:
        n *= _MULT.get(unit.lower().strip(), 1)
    return n


def allowed_figures(ladder: dict | None = None) -> tuple[set[float], set[float]]:
    """(allowed percents, allowed dollar amounts) derived from the ladder."""
    ladder = _normalize_ladder(ladder or DEFAULT_LADDER)
    pcts: set[float] = set()
    dollars: set[float] = set()
    for t in ladder["tiers"]:
        pcts.add(float(t["revshare_pct"]))
        dollars.add(float(t["bonus_per_1m_taker_usd"]))
        vol = float(t["min_volume_m"])
        if vol > 0:
            # '10M' / '$10M' volume thresholds are legitimate dollar figures.
            dollars.add(vol * 1_000_000)
    dollars.add(1_000_000)  # 'per 1M taker volume'
    r = ladder["rules"]
    for p in r.get("extra_allowed_percents") or []:
        try:
            pcts.add(float(p))
        except (TypeError, ValueError):
            pass
    for d in r.get("extra_allowed_dollars") or []:
        try:
            dollars.add(float(d))
        except (TypeError, ValueError):
            pass
    return pcts, dollars


def find_off_ladder_figures(text: str, ladder: dict | None = None) -> list[str]:
    """Return every %/$ figure (and banned phrase) in `text` that the ladder does not allow.

    An empty list means the reply is safe to send.
    """
    if not text:
        return []
    ladder = _normalize_ladder(ladder or DEFAULT_LADDER)
    pcts_ok, dollars_ok = allowed_figures(ladder)
    bad: list[str] = []

    def _pct_bad(v: float) -> bool:
        return not any(abs(v - ok) < 1e-6 for ok in pcts_ok)

    def _usd_bad(v: float) -> bool:
        return not any(abs(v - ok) < 1e-6 for ok in dollars_ok)

    for m in _PCT_RANGE_RE.finditer(text):
        for g in (m.group(1), m.group(2)):
            if _pct_bad(float(g)):
                bad.append(f"{g}%")
    for m in _PCT_RE.finditer(text):
        if _pct_bad(float(m.group(1))):
            bad.append(m.group(0).strip())
    for m in _DOLLAR_RE.finditer(text):
        if _usd_bad(_to_number(m.group(1), m.group(2))):
            bad.append(m.group(0).strip())
    for m in _USD_SUFFIX_RE.finditer(text):
        if _usd_bad(_to_number(m.group(1), m.group(2))):
            bad.append(m.group(0).strip())
    low = text.lower()
    for phrase in ladder["rules"].get("banned_phrases") or []:
        if phrase and phrase.lower() in low:
            bad.append(phrase)
    # de-dup, keep order
    seen = set()
    out = []
    for b in bad:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def get_safe_offer_fallback_reply(lang: str = "en") -> str:
    """Reply sent instead of an AI message that quoted off-ladder figures."""
    if (lang or "").lower().startswith("vi"):
        return (
            "Để mình check lại con số chính xác với team rồi báo bạn ngay nhé, "
            "không muốn nói sai cho bạn 🙏"
        )
    return (
        "Let me double-check the exact numbers with the team and get right back to you — "
        "don't want to misquote anything for you 🙏"
    )


# ── Storage ─────────────────────────────────────────────────────────────────

async def get_ladder() -> dict:
    """Current ladder (setting) or the default when unset/invalid."""
    raw = await db.get_setting(LADDER_SETTING_KEY)
    if raw:
        try:
            ladder = json.loads(raw)
            if not validate_ladder(ladder):
                return _normalize_ladder(ladder)
            logger.warning("kol_offer_ladder setting invalid, using default: %s", validate_ladder(ladder))
        except Exception as exc:
            logger.warning("kol_offer_ladder setting unreadable (%s), using default", exc)
    return copy.deepcopy(DEFAULT_LADDER)


async def is_ladder_customized() -> bool:
    return bool(await db.get_setting(LADDER_SETTING_KEY))


async def save_ladder(new_ladder: dict, actor: str = "admin") -> dict:
    """Validate, bump version, persist and record history. Raises ValueError on invalid input."""
    errors = validate_ladder(new_ladder)
    if errors:
        raise ValueError("; ".join(errors))
    current = await get_ladder()
    ladder = _normalize_ladder(new_ladder)
    try:
        ladder["version"] = int(current.get("version", 0)) + 1
    except (TypeError, ValueError):
        ladder["version"] = 2
    if not ladder.get("effective_from"):
        ladder["effective_from"] = date.today().isoformat()
    # First customisation: keep the baseline in history too, so v1 is auditable.
    if not await db.get_offer_ladder_history(limit=1):
        await db.add_offer_ladder_history(int(current.get("version", 1)), json.dumps(current, ensure_ascii=False), "system:default")
    await db.set_setting(LADDER_SETTING_KEY, json.dumps(ladder, ensure_ascii=False))
    await db.add_offer_ladder_history(ladder["version"], json.dumps(ladder, ensure_ascii=False), actor)
    logger.info("kol_offer_ladder saved v%s by %s", ladder["version"], actor)
    return ladder


async def render_current_ladder_prompt(volume_claimed=None) -> str:
    return render_offer_ladder_prompt(await get_ladder(), volume_claimed)
