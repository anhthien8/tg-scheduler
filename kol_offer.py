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
from functools import lru_cache

import database as db
import kol_region_offer

logger = logging.getLogger("kol_offer")

LADDER_SETTING_KEY = "kol_offer_ladder"

# The ladder below is WEEX's. Chats run by another exchange's agent (BloFin,
# Bitget, PU Prime...) quote only the terms written in that agent's own
# instructions / knowledge base (see offer_block_for / find_off_offer_figures).
LADDER_EXCHANGE = "WEEX"

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
        "upgrade": "Tiers are reviewed per calendar month. Reaching a threshold this month unlocks the new tier from NEXT month.",
        "starting_tier": "The starting tier follows the KOL's PROVEN volume on their current exchange: the average "
                         "monthly volume of the last 3 months in a dashboard screen recording, mapped to the tier "
                         "table. No proof = Tier 1. The team verifies the recording before the tier applies; if real "
                         "volume on WEEX is lower, the tier is adjusted at the quarterly review.",
        "downgrade": "Downgrades happen only at the quarterly review, if volume stays below the tier threshold.",
        "threshold_basis": "Tier thresholds use TOTAL monthly volume (maker + taker).",
        "bonus_basis": "The per-1M bonus is computed on TOTAL futures volume (maker + taker).",
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

# Rule texts replaced by the owner's deal rules (2026-11-10). A saved ladder that still
# carries the old default wording gets the new one; an admin-written text is kept.
_LEGACY_RULE_TEXT = {
    # Owner, 2026-11-18: the volume bonus counts ALL futures volume, no longer taker only.
    "bonus_basis": "The per-1M bonus is computed on TAKER volume only.",
    "upgrade": "Tiers are reviewed per calendar month. Reaching a threshold this month unlocks the new tier "
               "from NEXT month. The first month is always the starting tier.",
}


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
    for key, old in _LEGACY_RULE_TEXT.items():
        if rules.get(key) == old:
            rules[key] = DEFAULT_LADDER["rules"][key]
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

_VOLUME_UNIT = r"(billion|million|thousand|bn|b|mn|m|k)"
_VOLUME_RANGE_RE = re.compile(
    rf"(\d+(?:\.\d+)?)\s*{_VOLUME_UNIT}?\s*(?:-|–|~|to)\s*(\d+(?:\.\d+)?)\s*{_VOLUME_UNIT}?")


def stated_volume(profile: dict | None):
    """Monthly volume used to predict the starting tier: the admin-entered volume
    (`volume_claimed`) wins, else the volume the KOL stated in chat (`monthly_volume`,
    extracted by the AI). The value estimate stays on `volume_claimed` only."""
    p = profile or {}
    return p.get("volume_claimed") or p.get("monthly_volume") or None

def parse_volume_m(value) -> float | None:
    """Parse a stated monthly volume into millions (USD).

    Accepts 15, '15', '15M', '15m', '15 million', '15,000,000', '$15M', '1.5B'.
    A range ('18-20M', '18k-20k', '18 to 20 million') counts as its LOWER bound,
    with the unit shared when only the upper bound carries it.
    Returns None when nothing numeric is found.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        # Bare large numbers are raw USD, small ones are already millions.
        return v / 1_000_000 if v >= 100_000 else v
    s = str(value).strip().lower().replace(",", "").replace("$", "").replace("usdt", "").replace("usd", "")
    rng = _VOLUME_RANGE_RE.search(s)
    if rng:
        num = float(rng.group(1))
        unit = (rng.group(2) or rng.group(4) or "").strip()
    else:
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
    """Likely starting tier for the volume they stated (applies once the team verifies
    their last-3-months dashboard recording)."""
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


def _first_pitch_rule(ladder: dict) -> str:
    """The first offer must carry enough substance to close — a bare headline reads like spam."""
    t0 = ladder["tiers"][0]
    why = (ladder["rules"].get("why_us") or [""])[0]
    return (
        "FIRST PITCH (the first time the KOL agrees to hear the offer, or asks what you offer): ONE message, 3-5 short lines, "
        f"covering: the headline; that they start at {_fmt_num(t0['revshare_pct'])}% revshare + "
        f"${_fmt_num(t0['bonus_per_1m_taker_usd'])} per 1M futures volume — or directly at a higher tier when they prove "
        "their current monthly volume — and move up the ladder as volume grows; "
        "commission is paid daily; " + (f"one reason to choose us ({why}); " if why else "")
        + "then recommend ONE currently running campaign that fits their community and ask if they want to go ahead "
        "(if their last-3-months performance is still unknown, ask for it instead). "
        "Never send the headline alone, never send two messages in a row."
    )


def _starting_tier_map(ladder: dict) -> str:
    """'under 10M → Tier 1 (55%); 10-20M → Tier 2 (60%); …; 100M+ → Tier 5 (80%)'."""
    tiers = ladder["tiers"]
    parts = []
    for i, t in enumerate(tiers):
        lo = float(t["min_volume_m"])
        nxt = float(tiers[i + 1]["min_volume_m"]) if i + 1 < len(tiers) else None
        if nxt is None:
            rng = f"{_fmt_num(lo)}M+"
        elif lo <= 0:
            rng = f"under {_fmt_num(nxt)}M"
        else:
            rng = f"{_fmt_num(lo)}-{_fmt_num(nxt)}M"
        parts.append(f"{rng} → Tier {t['tier']} ({_fmt_num(t['revshare_pct'])}%)")
    return "; ".join(parts)


def render_starting_tier_rule(ladder: dict) -> str:
    """Rule 1 (owner, 2026-11-10): the starting tier follows the KOL's proven volume elsewhere."""
    return (
        "STARTING TIER (proven volume — replaces 'everyone starts at Tier 1'): the starting tier follows the KOL's PROVEN "
        "volume on the exchange they work with now = the AVERAGE monthly volume of the LAST 3 MONTHS in a screen "
        f"recording of their exchange dashboard. Map it to the table: {_starting_tier_map(ladder)}. "
        "No recording → Tier 1. Ask for the recording at most ONCE (it is the same ask as their last-3-months "
        "performance; they may hide UID, balance and email). When they share or state a volume, say it LIKELY qualifies "
        "them for that tier ONCE OUR TEAM VERIFIES the recording — never confirm a starting tier as final yourself. "
        "If their real volume on WEEX turns out lower, the tier is adjusted at the quarterly review."
    )


# Figures outside the ladder the AI may still quote: the knowledge base's own concession ladder.
_KB_LADDER_EXCEPTION = (
    "the marketing fee bonus / trial-fund amounts written in your knowledge base's MARKETING FEE & UPFRONT LADDER "
    "(only in its order, when the UPFRONT rule applies)"
)


def render_offer_ladder_prompt(ladder: dict | None = None, volume_claimed=None, volume_stated=None) -> str:
    """Build the prompt block the AI receives every turn.

    `volume_claimed` (admin-entered profile field) drives the tier prediction AND the
    value estimate; `volume_stated` (volume the KOL stated in chat, AI-extracted) only
    drives the tier prediction. With neither, the AI is told not to guess a tier.
    """
    ladder = _normalize_ladder(ladder or DEFAULT_LADDER)
    r = ladder["rules"]
    lines = [
        f"--- OFFICIAL WEEX KOL COMMISSION LADDER (v{ladder['version']}, effective {ladder['effective_from']}) ---",
        f"Headline you may use: \"{ladder.get('headline') or 'up to 80% revshare'}\". When the KOL asks for details, use EXACTLY this ladder. "
        "It is the SAME for every KOL, every region and every language (KOLs outside Vietnam, Korea and the China market get their own region offer block instead) — there is no other offer.",
        _first_pitch_rule(ladder),
        "| Tier | Condition (monthly) | RevShare | Bonus per 1M FUTURES volume |",
        "|---|---|---|---|",
        *_tier_rows(ladder),
        "RULES:",
        f"- Upgrade: {r['upgrade']}",
        *([f"- Starting tier: {r['starting_tier']}"] if r.get("starting_tier") else []),
        f"- Downgrade: {r['downgrade']}",
        f"- Threshold: {r['threshold_basis']} Bonus: {r['bonus_basis']}",
        f"- Payout: {r['commission_payout']} {r['bonus_payout']}",
        f"- Valid volume: {r['exclusions']} {r['sub_affiliate']}",
        f"- If asked why the bonus drops at the top tiers: {r['bonus_dip_explanation']}",
        "- When the KOL compares us with other exchanges, use ONLY these reasons: " + "; ".join(r.get("why_us") or []) + ".",
        "  NEVER name another exchange. NEVER use the phrase \"B-book\" or any variant.",
        "YOU MAY SETTLE ON YOUR OWN: " + "; ".join(ladder.get("ai_may_close") or []) + ".",
        "YOU MUST END YOUR REPLY WITH [HANDOVER_REQUIRED] FOR: " + "; ".join(ladder.get("must_handover") or []) + ".",
        render_starting_tier_rule(ladder),
        CURRENT_DEAL_RULE,
        UPFRONT_REVIEW_RULE,
        "NEVER quote any % or $ figure that is not in this ladder (no internal estimates, no 'total payout' maths, "
        "no ranges like 50-60%), EXCEPT " + _KB_LADDER_EXCEPTION + ".",
    ]
    pred = predicted_tier(ladder, volume_claimed or volume_stated)
    est = value_estimate(ladder, volume_claimed)
    if est is not None:
        lines[-1] = ("NEVER quote any % or $ figure that is not in this ladder, EXCEPT the pre-computed figures in the "
                     "VALUE ESTIMATE block below (copy them exactly; never compute new totals yourself) and "
                     + _KB_LADDER_EXCEPTION + ".")
        lines.append(render_value_estimate(est))
    if pred is not None:
        lines.append(
            f"TIER PREDICTION ALLOWED: the KOL stated about {_fmt_num(pred['volume_m'])}M monthly volume on their current "
            f"exchange → with a verified last-3-months dashboard recording they LIKELY start at Tier {pred['tier']} "
            f"({_fmt_num(pred['revshare_pct'])}% revshare + ${_fmt_num(pred['bonus_per_1m_taker_usd'])} per 1M futures volume) "
            "instead of Tier 1. Tell them it applies once our team verifies the recording; never confirm it as final."
        )
    else:
        lines.append(
            "TIER PREDICTION: the KOL has NOT stated their monthly volume yet — do not guess a tier; without proof they "
            "start at Tier 1. Do not run a separate volume interview: their last-3-months performance (asked once) is "
            "enough. If they state a volume in their latest message, map it with the STARTING TIER table above "
            "(as 'likely, once our team verifies the recording')."
        )
    return "\n".join(lines)


# ── Value estimate (what the KOL earns with WEEX at their current volume) ───

# Fee rate implied by WEEX's SABC table (100,000 USDT fees ≈ 166.67M volume): SABC tier only.
SABC_FEE_RATE = 0.0006
# Earnings estimate: futures fees are 0.02% maker / 0.08% taker, assumed half and half (owner, 2026-11-18).
EST_FEE_RATE = 0.0005
# SABC channel tiers by monthly fees (USDT), highest first.
# Monthly marketing-fee bonus cap (owner-set, 2026-11-07).
MARKETING_FEE_MAX = 200
SABC_TIERS = (("S", 100_000), ("A", 10_000), ("B", 1_000), ("C", 100))


def sabc_tier(fees_usd: float) -> str | None:
    for name, floor in SABC_TIERS:
        if fees_usd >= floor:
            return name
    return None


def _round_money(v: float) -> int:
    """Round to a clean figure so the reply reads naturally (and matches the guard exactly)."""
    step = 10 if v < 1_000 else 50 if v < 10_000 else 100 if v < 100_000 else 1_000
    return int(round(v / step) * step)


def value_estimate(ladder: dict | None, volume_claimed) -> dict | None:
    """Monthly earnings estimate at the KOL's stated monthly volume, or None when unknown."""
    ladder = _normalize_ladder(ladder or DEFAULT_LADDER)
    vol_m = parse_volume_m(volume_claimed)
    if not vol_m or vol_m <= 0:
        return None
    vol = vol_m * 1_000_000
    fees = vol * EST_FEE_RATE

    def _at(t: dict) -> dict:
        com = _round_money(fees * float(t["revshare_pct"]) / 100)
        bonus = _round_money(vol_m * float(t["bonus_per_1m_taker_usd"]))  # all futures volume
        return {"tier": t["tier"], "revshare_pct": t["revshare_pct"], "commission": com,
                "bonus": bonus, "total": com + bonus}

    first = ladder["tiers"][0]
    target = tier_for_volume(ladder, vol_m) or first
    return {"volume_m": vol_m, "fees": _round_money(fees), "sabc": sabc_tier(vol * SABC_FEE_RATE),
            "month1": _at(first), "steady": _at(target)}


def estimate_dollars(est: dict | None) -> set[float]:
    if not est:
        return set()
    out = {float(est["fees"])}
    st = est["steady"]
    out |= {float(st["commission"]), float(st["bonus"]), float(st["total"]), float(MARKETING_FEE_MAX)}
    return out


def render_value_estimate(est: dict) -> str:
    st = est["steady"]
    sabc = est["sabc"]
    fmt = lambda v: f"${v:,}"
    return "\n".join([
        "--- VALUE ESTIMATE FOR THIS KOL (pre-computed by the system; use these exact figures) ---",
        f"Stated volume ≈ {_fmt_num(est['volume_m'])}M/month → trading fees ≈ {fmt(est['fees'])}/month "
        "(rough estimate at their current volume, assuming about half maker and half taker orders).",
        f"SABC channel tier: {sabc or 'below C (no tier yet)'}"
        + (f" — pitch the tier-{sabc} benefits from the knowledge base and send [[BANNER:tier_{sabc.lower()}]]." if sabc else "."),
        f"PERFORMANCE SYNC: once our team verifies their last-3-months dashboard recording, they likely START at the "
        f"matching ladder tier instead of Tier 1: Tier {st['tier']}, {_fmt_num(st['revshare_pct'])}% revshare → commission ≈ "
        f"{fmt(st['commission'])} + volume bonus ≈ {fmt(st['bonus'])} = ≈ {fmt(st['total'])}/month EXTRA income "
        "if they ADD WEEX alongside what they run now (frame it as additional, not a replacement).",
        "When the KOL has shared their numbers, present the FULL PACKAGE in one message (short lines):",
        f"1) ≈ {fmt(st['total'])}/month extra from commission + volume bonus (commission paid daily).",
        "2) Marketing campaign support: ONE exclusive campaign for their channel (best fit; rotates every ~2 weeks, never stacked).",
        "3) Marketing accounts provided so they can build consistent WEEX content.",
        "4) Bonus: offline events in the future, and trial-fund bonuses for their community.",
        "Do NOT mention the marketing fee bonus in this package: it is a RETENTION CARD, used only when the KOL "
        "refuses / objects or asks for upfront (see the UPFRONT rule and the knowledge base).",
        "Then ask ONE question to move forward (e.g. which campaign they prefer or their WEEX UID).",
    ])


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
    r = ladder["rules"]
    only = r.get("percents_only")  # region offer (kol_region_offer): its figures replace the tier %
    for t in ladder["tiers"]:
        if only is None:
            pcts.add(float(t["revshare_pct"]))
        dollars.add(float(t["bonus_per_1m_taker_usd"]))
        vol = float(t["min_volume_m"])
        if vol > 0:
            # '10M' / '$10M' volume thresholds are legitimate dollar figures.
            dollars.add(vol * 1_000_000)
    dollars.add(1_000_000)  # 'per 1M futures volume'
    for p in list(only or []) + list(r.get("extra_allowed_percents") or []):
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


def find_off_ladder_figures(text: str, ladder: dict | None = None,
                            allowed: tuple[set[float], set[float]] | None = None) -> list[str]:
    """Return every %/$ figure (and banned phrase) in `text` that the ladder does not allow.

    `allowed` = (percents, dollars) replaces the ladder's figures (non-WEEX agents);
    banned phrases still come from the ladder rules.
    An empty list means the reply is safe to send.
    """
    if not text:
        return []
    ladder = _normalize_ladder(ladder or DEFAULT_LADDER)
    pcts_ok, dollars_ok = allowed if allowed is not None else allowed_figures(ladder)
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


# ── Exchange-aware offer (non-WEEX agents) ──────────────────────────────────

# '100% organic' style wording and 'per 1M volume' are fine for any exchange.
_ANY_AGENT_PERCENTS = frozenset({100.0})
_ANY_AGENT_DOLLARS = frozenset({1_000_000.0})


def uses_ladder(exchange: str | None) -> bool:
    """True when the chat sells WEEX (the ladder applies); unknown = WEEX (historic default)."""
    return (exchange or LADDER_EXCHANGE) == LADDER_EXCHANGE


@lru_cache(maxsize=32)
def _figures_cached(text: str) -> tuple[frozenset, frozenset]:
    pcts: set[float] = set()
    dollars: set[float] = set()
    for m in _PCT_RANGE_RE.finditer(text):
        pcts.add(float(m.group(1)))
        pcts.add(float(m.group(2)))
    for m in _PCT_RE.finditer(text):
        pcts.add(float(m.group(1)))
    for m in _DOLLAR_RE.finditer(text):
        dollars.add(_to_number(m.group(1), m.group(2)))
    for m in _USD_SUFFIX_RE.finditer(text):
        dollars.add(_to_number(m.group(1), m.group(2)))
    return frozenset(pcts), frozenset(dollars)


def figures_in(text: str | None) -> tuple[set[float], set[float]]:
    """(percents, dollar amounts) written in `text`, parsed exactly like the guard parses replies."""
    if not text:
        return set(), set()
    pcts, dollars = _figures_cached(text)
    return set(pcts), set(dollars)


def agent_figures(agent: dict | None) -> tuple[set[float], set[float]]:
    """Figures a non-WEEX agent may quote: the ones in its own system prompt + knowledge base."""
    a = agent or {}
    pcts, dollars = figures_in(f"{a.get('system_prompt') or ''}\n{a.get('knowledge_base') or ''}")
    return pcts | _ANY_AGENT_PERCENTS, dollars | _ANY_AGENT_DOLLARS


def find_off_offer_figures(text: str, exchange: str | None = None, agent: dict | None = None,
                           ladder: dict | None = None) -> list[str]:
    """Off-offer guard for the exchange the chat sells: the WEEX ladder for WEEX,
    the replying agent's own figures for any other exchange."""
    if uses_ladder(exchange):
        own = f"{(agent or {}).get('system_prompt') or ''}\n{(agent or {}).get('knowledge_base') or ''}"
        if LADDER_EXCHANGE.lower() in own.lower():  # the WEEX agent's own KB figures (campaign prizes, tier APR)
            lp, ld = allowed_figures(ladder)
            kp, kd = figures_in(own)
            return find_off_ladder_figures(text, ladder, allowed=(lp | kp, ld | kd))
        return find_off_ladder_figures(text, ladder)
    return find_off_ladder_figures(text, ladder, allowed=agent_figures(agent))


def render_agent_offer_prompt(exchange: str) -> str:
    """Offer block for a non-WEEX chat: the terms live in the agent's own instructions / KB."""
    ex = exchange or LADDER_EXCHANGE
    return "\n".join([
        f"--- OFFICIAL {ex} KOL COMMISSION TERMS ---",
        f"This chat is about {ex}. The ONLY commission terms, tiers, rewards, thresholds and links you may quote are the ones "
        "written in YOUR OWN instructions and knowledge base above. Lead with the starting offer they define and explain "
        "the tiers only when the KOL asks for details.",
        "Other exchanges' ladders, campaigns, links and committees do NOT apply to this chat — never quote or borrow them.",
        "If the KOL asks for a tier, %, bonus, threshold, fee or exception your instructions do not cover, say you will "
        "confirm the exact numbers with the team and end your reply with [HANDOVER_REQUIRED].",
        "TIER PREDICTION: only when your instructions define the volume thresholds AND the KOL stated their monthly volume; "
        "otherwise do not predict a tier.",
        "NEVER use the phrase \"B-book\" or any variant.",
        UPFRONT_PROOF_RULE,
        "NEVER quote any % or $ figure that is not written in your own instructions / knowledge base "
        "(no internal estimates, no new 'total payout' maths beyond the examples given there).",
    ])


async def offer_block_for(exchange: str | None, volume_claimed=None,
                          region: str | None = None, volume_stated=None) -> tuple[str, dict | None]:
    """(prompt block, ladder or None) for the exchange the chat sells.

    `region` ('ID' / 'AE', from kol_region_offer.detect_region) swaps the WEEX ladder
    for the region offer (flat revshare + deposit bonus campaign) and its guard figures.
    `volume_stated` (AI-extracted volume from chat) only feeds the ladder's tier prediction."""
    if uses_ladder(exchange):
        import kol_banners
        ladder = await get_ladder()
        camp = kol_banners.campaign_prompt_block()
        if region in kol_region_offer.REGIONS:
            # Deal rules 2 + 3 apply to region leads too; rule 1 (starting tier) does not — no ladder here.
            block = "\n".join(filter(None, [kol_region_offer.render_prompt(region, ladder, volume_claimed),
                                            CURRENT_DEAL_RULE, UPFRONT_REVIEW_RULE, camp]))
            return block, _allow_campaign_figures(kol_region_offer.guard_ladder(ladder), camp)
        block = "\n".join(filter(None, [render_offer_ladder_prompt(ladder, volume_claimed, volume_stated), camp]))
        est_d = estimate_dollars(value_estimate(ladder, volume_claimed))
        if est_d:  # let the guard pass the pre-computed estimate figures for this KOL only
            ladder = copy.deepcopy(ladder)
            rules = ladder.setdefault("rules", {})
            rules["extra_allowed_dollars"] = list(rules.get("extra_allowed_dollars") or []) + sorted(est_d)
        return block, _allow_campaign_figures(ladder, camp)
    return render_agent_offer_prompt(exchange), None


def _allow_campaign_figures(ladder: dict, camp_block: str) -> dict:
    """Let the guard pass the figures printed in the active campaign list (and nothing else new)."""
    if not camp_block:
        return ladder
    pcts, dollars = figures_in(camp_block)
    ladder = copy.deepcopy(ladder)
    rules = ladder.setdefault("rules", {})
    rules["extra_allowed_dollars"] = list(rules.get("extra_allowed_dollars") or []) + sorted(dollars)
    rules["extra_allowed_percents"] = list(rules.get("extra_allowed_percents") or []) + sorted(pcts)
    return ladder


def get_safe_offer_fallback_reply(lang: str = "en") -> str:
    """Reply sent instead of an AI message that quoted off-ladder figures."""
    if (lang or "").lower().startswith("vi"):
        return (
            "Để mình check lại con số chính xác với team rồi báo bạn ngay nhé, "
            "không muốn nói sai cho bạn 🙏"
        )
    if (lang or "").lower().startswith("ru"):
        return (
            "Уточню точные цифры у команды и сразу вернусь к вам — "
            "не хочу ошибиться 🙏"
        )
    return (
        "Let me double-check the exact numbers with the team and get right back to you — "
        "don't want to misquote anything for you 🙏"
    )


# ── Storage ─────────────────────────────────────────────────────────────────

# WEEX deal rules 2 + 3 (owner, 2026-11-10; upfront wording 2026-11-18): learn the KOL's current deal, never refuse
# upfront outright: it is "not yet activated for a first-time partnership" and is reviewed after month one.
CURRENT_DEAL_RULE = (
    "CURRENT DEAL: when the KOL mentions or compares another exchange, asks for a better deal, or asks for upfront, ask "
    "ONCE in one short message what their CURRENT deal is: revshare/rebate %, any volume bonus, any fixed/upfront fee, "
    "contract length or exclusivity, and payout schedule — plus a screenshot of their commission page (they may hide "
    "UID, balance and email). Skip whatever they already told you. Never name an exchange yourself and never promise "
    "to match or beat their deal: say our team will review it to see what we can do for them."
)
UPFRONT_REVIEW_RULE = (
    "UPFRONT / FIXED-FEE REQUESTS (overrides the handover list above for the FIRST ask): do NOT refuse outright, never "
    "say upfront is unavailable, and do NOT hand over yet. Say that upfront is not yet activated for KOLs on a "
    "first-time partnership with WEEX, that after the first month of working together our team will review their "
    "upfront request again based on their community's performance, and that in the meantime the cooperation can go "
    "the trial-fund / marketing-support route instead (amounts and conditions ONLY as written in the knowledge base, "
    "in its order). If a pre-computed monthly earnings total for this KOL is given below, add it as what they earn "
    "EVERY month, not once. "
    "In the same message ask ONCE for their current deal (see CURRENT DEAL), the last-3-months dashboard recording, "
    "and how much monthly futures trading volume their referred users are expected to generate in the first month "
    "(their own trading on the master account does not count) so our team can prepare the marketing-support / trial-fund request "
    "(skip what is already in the chat). Never state an upfront amount, a milestone or a date for the CASH upfront "
    "or its review, and never promise the review will approve one; the marketing fee bonus and trial-fund figures and "
    "conditions of the knowledge base ladder are allowed, exactly as written there. If they still refuse or insist "
    "after your first answer, follow the knowledge base MARKETING FEE & UPFRONT LADDER step by step (step 1 before "
    "step 2, each offered once); it decides when to hand over. Once they have shared their deal or recording, ask for "
    "a cash upfront amount, or that ladder is used up — say the team is reviewing their deal and will come back with "
    "a proposal, and end your reply with [HANDOVER_REQUIRED]."
)

# Upfront asks for non-WEEX agents: collect proof first, then answer with the agent's own upfront rule.
UPFRONT_PROOF_RULE = (
    "UPFRONT / FIXED-FEE REQUESTS (overrides the handover list above for the FIRST ask): do NOT hand over yet. "
    "First, ONCE (skip what is already in the chat), ask how much monthly futures volume their referred users are expected "
    "to generate in the first month (their own master-account trading does not count) so we can submit a trial-fund request, "
    "and ask for a screen-recorded VIDEO of their trading/referral performance for the LAST 3 MONTHS "
    "(affiliate dashboard: referred volume, active traders, commission), clearly showing the deal terms of the exchange "
    "they currently partner with (rate, any upfront/fixed fee they receive). They may hide UID, balance and email. "
    "Do not promise anything in return for the video. "
    "After the video is received, rank the KOL with it and offer the monthly MARKETING FEE BONUS instead of upfront "
    "(amount, 30-day pilot KPI and conditions as in the knowledge base). Only if they still refuse after that (or refuse / cannot "
    "provide the video): if the knowledge base defines a LAST-RESORT UPFRONT offer, offer exactly that once, and when the KOL "
    "accepts its terms append [HANDOVER_REQUIRED]; otherwise reply following your system prompt's upfront rule and append "
    "[HANDOVER_REQUIRED] so the team reviews it."
)


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
