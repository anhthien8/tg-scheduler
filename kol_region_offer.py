"""Region offer for WEEX KOLs outside the core Asian markets (owner rules, 2026-11-09).

Covered: Southeast Asia (except Vietnam), LATAM, MENA, Europe (EU + EEA + Switzerland),
the UK, the US, Canada, South Asia, Africa, the CIS outside Russia (Ukraine, Belarus,
Kazakhstan, Uzbekistan), Turkey, Japan, Australia and New Zealand. Not covered (they keep
the tiered ladder): Vietnam, Korea, Russia (incl. "Russian-speaking" communities, owner
rule 2026-11-10), the China market (mainland, Hong Kong, Taiwan, Macau, and leads who
write in Chinese / Korean / Vietnamese) and sanctioned countries (Iran, Syria, Cuba,
North Korea). A country missing from both tables is not recognised, so the AI asks the
origin question / keeps the ladder.

Covered leads do NOT get the tiered ladder (55-80%). They get:
  * a flat 60% revshare, paid daily, and
  * the DEPOSIT BONUS campaign for their community, which is the main hook:
    10% deposit bonus; 20% when the KOL proves ~30M+ monthly volume over the
    last 3 months on their current exchange, or when their first run performs well.

Where the lead is from (owner rule): the KOL's OWN country decides first, the
country of the community they serve only when the KOL's own country is unknown.
The AI asks both directly (`ORIGIN_QUESTION_RULE`); the profile extractor stores
them as `kol_country` / `community_country`. `detect_region(history, profile)`
reads those first, then falls back to other profile fields and the lead's own
messages (never our messages). kol_offer.offer_block_for(..., region=...)
swaps the ladder block for `render_prompt` and narrows the off-ladder guard
to the region figures (`guard_ladder`).

The AI never suggests VPNs or other ways around country restrictions; such
questions go to a human ([HANDOVER_REQUIRED]).
"""
from __future__ import annotations

import copy
import json
import re

OFFER = {
    "revshare_pct": 60,
    "deposit_bonus_pct": 10,
    "deposit_bonus_strong_pct": 20,
    # "strong performance" = proven monthly volume over the last 3 months at or above this (in M USD).
    "strong_volume_m": 30,
    # Image sent with the first deposit pitch (data/kol_banners/<key>.jpg).
    "banner_key": "deposit10",
    # Never sent to region leads: double_gain still shows July 2026 dates; tier images show ladder %.
    "banner_blocked": ["double_gain", "tier_s", "tier_a", "tier_b", "tier_c"],
}

# code | display name | Latin-script terms (regex, word-bounded) | other-script terms
# Group codes (SEA / LATAM / MENA / EU) catch answers like "Latin America" or "Europe".
_ELIGIBLE_TABLE = r"""
ID|Indonesia|indonesia|indonesian|indonesians|indo|jakarta|surabaya|bandung|medan|bali|yogyakarta|jogja|semarang|makassar|palembang|batam|bekasi|tangerang|depok|bogor|malang|denpasar|pekanbaru|balikpapan|
AE|UAE|uae|u\.a\.e|emirates|emirati|emiratis|dubai|abu\s?dhabi|sharjah|ajman|ras\s?al\s?khaimah|fujairah|al\s?ain|الإمارات|دبي|أبوظبي|ابوظبي
MY|Malaysia|malaysia|malaysian|malaysians|kuala\s?lumpur|penang|johor|sabah|sarawak|ماليزيا
SG|Singapore|singapore|singaporean|singaporeans|
TH|Thailand|thailand|thai|bangkok|phuket|chiang\s?mai|pattaya|ประเทศไทย|ไทย
PH|the Philippines|philippines|philippine|filipino|filipinos|pinoy|manila|cebu|davao|quezon\s?city|
KH|Cambodia|cambodia|cambodian|khmer|phnom\s?penh|siem\s?reap|
LA|Laos|laos|lao\s?pdr|vientiane|
MM|Myanmar|myanmar|burma|burmese|yangon|mandalay|
BN|Brunei|brunei|
TL|Timor-Leste|timor[\s-]?leste|east\s?timor|
SEA|Southeast Asia|south[\s-]?east\s?asia|southeast\s?asian|asean|
MX|Mexico|mexico|mexican|mexicans|ciudad\s?de\s?m[eé]xico|cdmx|guadalajara|monterrey|méxico|
BR|Brazil|brazil|brasil|brazilian|brazilians|brasileiro|brasileira|s[aã]o\s?paulo|rio\s?de\s?janeiro|bras[ií]lia|
AR|Argentina|argentina|argentine|argentinian|buenos\s?aires|c[oó]rdoba|rosario|
CO|Colombia|colombia|colombian|columbia|bogot[aá]|medell[ií]n|cali|barranquilla|
CL|Chile|chile|chilean|santiago\s?de\s?chile|valpara[ií]so|
PE|Peru|peru|per[uú]|peruvian|lima|
VE|Venezuela|venezuela|venezuelan|caracas|
EC|Ecuador|ecuador|ecuadorian|quito|guayaquil|
BO|Bolivia|bolivia|bolivian|la\s?paz|santa\s?cruz\s?de\s?la\s?sierra|
PY|Paraguay|paraguay|paraguayan|asunci[oó]n|
UY|Uruguay|uruguay|uruguayan|montevideo|
CR|Costa Rica|costa\s?rica|costa\s?rican|
PA|Panama|panama|panam[aá]|panamanian|
GT|Guatemala|guatemala|guatemalan|
HN|Honduras|honduras|honduran|tegucigalpa|
SV|El Salvador|el\s?salvador|salvadoran|
NI|Nicaragua|nicaragua|nicaraguan|managua|
DO|the Dominican Republic|dominican\s?republic|rep[uú]blica\s?dominicana|santo\s?domingo|
PR|Puerto Rico|puerto\s?rico|puerto\s?rican|
LATAM|Latin America|latam|latin\s?america|latin\s?american|latinoam[eé]rica|south\s?america|south\s?american|central\s?america|hispanoam[eé]rica|
SA|Saudi Arabia|saudi|ksa|riyadh|jeddah|jiddah|mecca|makkah|medina|dammam|السعودية|الرياض|جدة
QA|Qatar|qatar|qatari|doha|قطر|الدوحة
KW|Kuwait|kuwait|kuwaiti|الكويت
BH|Bahrain|bahrain|bahraini|manama|البحرين
OM|Oman|oman|omani|muscat|عُمان|مسقط
JO|Jordan|jordanian|amman|الأردن|عمّان
LB|Lebanon|lebanon|lebanese|beirut|لبنان|بيروت
IQ|Iraq|iraq|iraqi|baghdad|erbil|basra|العراق|بغداد
EG|Egypt|egypt|egyptian|egyptians|cairo|alexandria|giza|مصر|القاهرة
MA|Morocco|morocco|moroccan|casablanca|rabat|marrakech|marrakesh|tangier|المغرب
DZ|Algeria|algeria|algerian|algiers|oran|الجزائر
TN|Tunisia|tunisia|tunisian|tunis|تونس
LY|Libya|libya|libyan|tripoli|benghazi|ليبيا
PS|Palestine|palestine|palestinian|gaza|ramallah|فلسطين
IL|Israel|israel|israeli|tel\s?aviv|jerusalem|ישראל
YE|Yemen|yemen|yemeni|sanaa|aden|اليمن
MENA|the Middle East / North Africa|mena|middle\s?east|middle\s?eastern|gulf\s?region|gcc|north\s?africa|arab\s?world|arab|arabic|الشرق\s?الأوسط|الخليج
GB|the UK|uk|u\.k\.|united\s?kingdom|britain|great\s?britain|british|england|scotland|scottish|wales|welsh|northern\s?ireland|london|manchester|birmingham|liverpool|leeds|glasgow|edinburgh|
US|the US|usa|u\.s\.a\.?|u\.s\.|united\s?states|american|americans|new\s?york|nyc|los\s?angeles|california|texas|florida|miami|chicago|san\s?francisco|las\s?vegas|seattle|boston|atlanta|houston|dallas|
DE|Germany|germany|german|deutschland|deutsch|berlin|munich|m[uü]nchen|hamburg|frankfurt|cologne|k[oö]ln|
FR|France|france|paris|marseille|lyon|toulouse|bordeaux|
ES|Spain|spain|spanish|españa|espana|madrid|barcelona|valencia|sevilla|seville|m[aá]laga|
IT|Italy|italy|italian|italia|rome|roma|milan|milano|naples|napoli|turin|torino|
PT|Portugal|portugal|portuguese|lisbon|lisboa|porto|
NL|the Netherlands|netherlands|dutch|holland|amsterdam|rotterdam|the\s?hague|utrecht|
BE|Belgium|belgium|belgian|brussels|antwerp|
LU|Luxembourg|luxembourg|
IE|Ireland|ireland|irish|dublin|
AT|Austria|austria|austrian|vienna|wien|
PL|Poland|poland|polish|polska|warsaw|warszawa|krak[oó]w|
CZ|Czechia|czech|czechia|prague|praha|
SK|Slovakia|slovakia|slovak|bratislava|
HU|Hungary|hungary|hungarian|budapest|
RO|Romania|rom[aâ]nia|romanian|bucharest|bucure[sș]ti|
BG|Bulgaria|bulgaria|bulgarian|
GR|Greece|greece|greek|athens|thessaloniki|
HR|Croatia|croatia|croatian|zagreb|
SI|Slovenia|slovenia|slovenian|ljubljana|
EE|Estonia|estonia|estonian|tallinn|
LV|Latvia|latvia|latvian|riga|
LT|Lithuania|lithuania|lithuanian|vilnius|
FI|Finland|finland|finnish|helsinki|
SE|Sweden|sweden|swedish|stockholm|gothenburg|
DK|Denmark|denmark|danish|copenhagen|
CY|Cyprus|cyprus|cypriot|limassol|nicosia|
MT|Malta|malta|maltese|
NO|Norway|norway|norwegian|oslo|
IS|Iceland|iceland|icelandic|reykjavik|
LI|Liechtenstein|liechtenstein|
CH|Switzerland|switzerland|swiss|zurich|z[uü]rich|geneva|gen[eè]ve|
EU|Europe|europe|european|europeans|eu|e\.u\.|european\s?union|eea|
CA|Canada|canada|canadian|canadians|toronto|vancouver|montreal|montr[eé]al|calgary|ottawa|
IN|India|india|indian|indians|bharat|mumbai|delhi|new\s?delhi|bangalore|bengaluru|kolkata|chennai|pune|ahmedabad|jaipur|hyderabad|भारत
PK|Pakistan|pakistan|pakistani|pakistanis|karachi|lahore|islamabad|rawalpindi|peshawar|faisalabad|پاکستان
BD|Bangladesh|bangladesh|bangladeshi|dhaka|chittagong|বাংলাদেশ
LK|Sri Lanka|sri\s?lanka|sri\s?lankan|colombo|
NP|Nepal|nepal|nepali|nepalese|kathmandu|
SOUTHASIA|South Asia|south\s?asia|south\s?asian|desi|
NG|Nigeria|nigeria|nigerian|nigerians|naija|lagos|abuja|
KE|Kenya|kenya|kenyan|nairobi|
GH|Ghana|ghana|ghanaian|accra|
ZA|South Africa|south\s?africa|south\s?african|johannesburg|cape\s?town|durban|pretoria|
UG|Uganda|uganda|ugandan|kampala|
TZ|Tanzania|tanzania|tanzanian|dar\s?es\s?salaam|
ET|Ethiopia|ethiopia|ethiopian|addis\s?ababa|
CM|Cameroon|cameroon|cameroonian|douala|yaound[eé]|
SN|Senegal|senegal|senegalese|dakar|
CI|Ivory Coast|ivory\s?coast|c[oô]te\s?d.ivoire|abidjan|
AFRICA|Africa|africa|african|africans|
UA|Ukraine|ukraine|ukrainian|ukrainians|kyiv|kiev|kharkiv|odesa|odessa|україна|украина
BY|Belarus|belarus|belarusian|minsk|беларусь
KZ|Kazakhstan|kazakhstan|kazakh|almaty|astana|казахстан
UZ|Uzbekistan|uzbekistan|uzbek|tashkent|
CIS|the CIS|cis|снг
TR|Turkey|turkey|turkish|t[uü]rkiye|istanbul|ankara|izmir|türkiye
JP|Japan|japan|japanese|tokyo|osaka|kyoto|日本
AU|Australia|australia|australian|australians|aussie|sydney|melbourne|brisbane|perth|
NZ|New Zealand|new\s?zealand|kiwi|auckland|wellington|
"""

# Markets that never get the region offer. Mentioning one blocks the offer for text-only
# signals (the AI asks the origin question instead). Vietnam / Korea / Russia / China market
# are the owner's exclusions; OTHER = sanctioned countries.
_OTHER_TABLE = r"""
RU|russia|russian|russians|russian[\s-]?speaking|moscow|saint\s?petersburg|st\.?\s?petersburg|rossiya|росси[яиюей]|российск|москв|русскоязычн|питер
VN|vietnam|viet\s?nam|vietnamese|hanoi|ha\s?noi|saigon|s[aà]i\s?g[oò]n|ho\s?chi\s?minh|hcmc|da\s?nang|hai\s?phong|việt\s?nam|hà\s?nội
KR|korea|korean|koreans|seoul|busan|incheon|한국|대한민국|서울
CN|china|chinese|mainland|prc|beijing|shanghai|shenzhen|guangzhou|hangzhou|chengdu|hong\s?kong|hongkong|hk|taiwan|taiwanese|taipei|macau|macao|中国|中國|大陆|大陸|香港|台湾|台灣|澳门|澳門|华人|華人
OTHER|iran|iranian|iranians|tehran|persia|ایران|syria|syrian|damascus|سوريا|cuba|cuban|havana|north\s?korea|pyongyang|dprk
"""
_EXCLUDED_CODES = ("VN", "KR", "RU", "CN")
_GROUP_CODES = ("SEA", "LATAM", "MENA", "EU", "SOUTHASIA", "AFRICA", "CIS", "OTHER")


def _flag(code: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code) if len(code) == 2 else ""


def _compile(terms: list[str], code: str) -> re.Pattern:
    latin = [t for t in terms if re.fullmatch(r"[a-z0-9\\\s?\[\]\-\.()|áàâãéèêíóôõúüçñșš]+", t, re.I)]
    other = [t for t in terms if t not in latin]
    parts = []
    if latin:
        parts.append(r"(?<![A-Za-z])(?:" + "|".join(latin) + r")(?![A-Za-z])")
    parts += other
    flags = [_flag(c) for c in ([code] if code not in _GROUP_CODES else [])]
    if code == "GB":
        flags.append(_flag("UK"))
    if code == "CN":
        flags += [_flag("HK"), _flag("TW"), _flag("MO")]
    parts += [re.escape(f) for f in flags if f]
    return re.compile("|".join(parts), re.IGNORECASE)


def _parse(table: str, named: bool) -> tuple[dict, dict]:
    names, pats = {}, {}
    for line in table.strip().splitlines():
        cells = [c for c in line.split("|")]
        code = cells[0]
        if named:
            names[code], terms = cells[1], cells[2:]
        else:
            terms = cells[1:]
        pats[code] = _compile([t for t in terms if t], code)
    return names, pats


REGIONS, _PLACES = _parse(_ELIGIBLE_TABLE, named=True)
_, _OTHER_PLACES = _parse(_OTHER_TABLE, named=False)

# Words that look like places but are not, in their typical context.
_FALSE_PLACE_RE = re.compile(
    r"\bemirates\s+(?:airline|airlines|flight|stadium)\b|\bcali\s+(?:weed|kush)\b|\bthai\s+(?:food|massage)\b|"
    r"\bamerican\s+(?:express|dollar)\b|\bjordan\s+(?:shoes|sneakers)\b|"
    r"\b(?:canadian|australian|aussie)\s+dollars?\b|\bturkey\s+(?:sandwich|dinner|meat)\b|\bindian\s+ocean\b|"
    r"\bjapanese\s+(?:food|anime|candlesticks?)\b|\bkiwi\s+(?:fruit|juice)\b|\bsouth\s+african\s+rand\b",
    re.IGNORECASE)

# Bahasa Indonesia words (informal ones first). Several distinct hits = an Indonesian speaker.
_ID_WORDS = frozenset((
    "gak nggak enggak ngga banget dong sih gimana udah aja kalo gue lu bang gan kak mas mbak "
    "saya kami kita bisa tidak sudah belum terima kasih makasih komunitas yang dengan untuk "
    "dan ini itu juga ada mau berapa bagaimana boleh sama tapi karena kalau sekarang nanti "
    "grup saudara selamat pagi siang malam halo bro"
).split())
# Words that are also common English / other-language tokens — never count them.
_ID_WEAK = frozenset({"bro", "halo", "ada", "dan", "sama", "mas", "kak", "aja"})
# Malay markers: Malay and Bahasa Indonesia share many words.
_MALAY_RE = re.compile(r"\b(?:malaysia|malaysian|kuala\s?lumpur|nak|tak|kat|awak|korang)\b|🇲🇾", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-zA-Z]+")
_THAI_RE = re.compile(r"[฀-๿]")
# Writing in Chinese / Korean / Vietnamese = China / Korea / Vietnam market (owner exclusion).
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_HANGUL_RE = re.compile(r"[가-힯ᄀ-ᇿ]")
_KANA_RE = re.compile(r"[぀-ヿ]")
_VIET_RE = re.compile(r"[Ạ-ỹơƠƯư]")


def _profile_blob(profile: dict | None) -> str:
    if not profile:
        return ""
    try:
        return json.dumps(profile, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(profile)


def _matches(text: str) -> list[tuple[int, str, bool]]:
    """(position, code, eligible) for every place mention in `text`."""
    if not text:
        return []
    clean = _FALSE_PLACE_RE.sub(" ", text)
    found = []
    for table, ok in ((_PLACES, True), (_OTHER_PLACES, False)):
        for code, rx in table.items():
            m = rx.search(clean)
            if m:
                found.append((m.start(), code, ok))
    return sorted(found)


def _place_region(text: str) -> str | None:
    """Region code for the FIRST place named in `text`; None when it is not covered."""
    found = _matches(text)
    if not found:
        return None
    _, code, ok = found[0]
    return code if ok else None


def _decide(text: str) -> tuple[bool, str | None]:
    """(text names a place, region code or None) for a profile country field."""
    found = _matches(text)
    if not found:
        return False, None
    _, code, ok = found[0]
    return True, (code if ok else None)


def _country_field(profile: dict | None, key: str) -> str:
    v = (profile or {}).get(key)
    return str(v).strip() if v not in (None, "") else ""


def origin_known(profile: dict | None) -> bool:
    """True once the KOL said where they or their community are from."""
    return bool(_country_field(profile, "kol_country") or _country_field(profile, "community_country"))


def _excluded_language(history: list[dict] | None) -> bool:
    """The lead writes in Chinese, Korean or Vietnamese (China / Korea / Vietnam market)."""
    user = [str(m.get("content") or "") for m in (history or [])[-30:] if m.get("role") == "user"]
    # Japanese also uses Han characters: kana in the message means Japanese, not Chinese.
    hits = sum(1 for t in user
               if (len(_CJK_RE.findall(t)) >= 3 and not _KANA_RE.search(t))
               or len(_HANGUL_RE.findall(t)) >= 3 or len(_VIET_RE.findall(t)) >= 2)
    return bool(user) and hits * 3 >= len(user)


def detect_region(history: list[dict] | None, profile: dict | None = None) -> str | None:
    """Region code when this lead gets the region offer, else None (tiered ladder).

    Priority: China / Korea / Vietnam market by language → the KOL's own country
    (a KOL based in Vietnam gets the ladder even if their community is Thai) →
    the community's country → other profile fields → the lead's own messages."""
    if _excluded_language(history):
        return None
    for key in ("kol_country", "community_country"):
        named, region = _decide(_country_field(profile, key))
        if named:
            return region
    texts = [_profile_blob(profile)]
    user_msgs = [str(m.get("content") or "") for m in (history or [])[-30:] if m.get("role") == "user"]
    texts += user_msgs
    found = [f for t in texts for f in _matches(t)]
    if any(not ok for _, _, ok in found):
        return None  # a non-covered market is mentioned: wait for the origin answer
    if found:
        return found[0][1]
    if any(_THAI_RE.search(t) for t in user_msgs):
        return "TH"
    words: set[str] = set()
    malay = False
    for content in user_msgs:
        malay = malay or bool(_MALAY_RE.search(content))
        words |= {w.lower() for w in _WORD_RE.findall(content)} & _ID_WORDS
    if len(words - _ID_WEAK) >= 3:
        return "MY" if malay else "ID"
    return None


def _n(x) -> str:
    f = float(x)
    return str(int(f)) if f == int(f) else f"{f:g}"


def _banner_lines(o: dict, dep: str, dep_hi: str) -> list[str]:
    """Tell the agent to attach the deposit campaign image (only when the file exists)."""
    import kol_banners
    key = o.get("banner_key")
    if not key or not kol_banners.banner_path(key):
        return []
    blocked = ", ".join(f"[[BANNER:{k}]]" for k in o.get("banner_blocked") or [])
    return [
        f"CAMPAIGN IMAGE: in the reply where you first pitch the {dep}% deposit bonus, add the tag [[BANNER:{key}]] at the "
        "end — the system sends the official campaign image right after your text (once per chat). Let the image show the "
        "reward table; do not retype its USDT amounts. If the image was already sent ([banner sent: "
        f"{key}] in the history), do not add the tag again."
        + (f" Never send {blocked} to this lead (outdated or tier-based); describe the {dep_hi}% option in words only."
           if blocked else ""),
    ]


def render_prompt(region: str, ladder: dict, volume_claimed=None, offer: dict | None = None) -> str:
    """Offer block that REPLACES the ladder block for a lead from `region`."""
    o = {**OFFER, **(offer or {})}
    r = ladder.get("rules") or {}
    name = REGIONS.get(region, region)
    rev, dep, dep_hi, strong = (_n(o["revshare_pct"]), _n(o["deposit_bonus_pct"]),
                                _n(o["deposit_bonus_strong_pct"]), _n(o["strong_volume_m"]))
    why = r.get("why_us") or []
    lines = [
        f"--- OFFICIAL WEEX KOL OFFER FOR {name.upper()} (replaces the commission ladder for this lead) ---",
        f"This lead is from {name}. For {name} the tiered commission ladder does NOT apply: never show a tier table, "
        "never mention tiers or tier upgrades, never predict a tier, and never quote any other revshare %.",
        "THE OFFER:",
        f"1) DEPOSIT BONUS CAMPAIGN (the main hook — lead with it): their community members who deposit through the KOL's "
        f"link get a {dep}% deposit bonus. KOLs with strong trading volume / performance on the exchanges they work with now "
        f"get the stronger {dep_hi}% deposit bonus for their community.",
        f"2) Commission: a flat {rev}% revshare on their referrals' trading fees, paid daily.",
        f"WHO GETS {dep_hi}% (two ways only): (a) the KOL shows proof of their LAST 3 MONTHS on their current exchange "
        f"(screenshot or screen-recorded affiliate dashboard) with about {strong}M+ monthly trading volume — then offer "
        f"{dep_hi}% right away; or (b) they start with {dep}% and their FIRST deposit campaign run performs well — the team "
        f"reviews the results and unlocks {dep_hi}% for the next runs. Until (a) is in the chat, offer {dep}% and tell them "
        f"{dep_hi}% unlocks if the first run goes well (or straight away with last-3-months proof of {strong}M+ monthly volume). "
        "Never promise a fixed result threshold for (b); never make the proof a condition to start.",
        f"PUSH THE DEPOSIT CAMPAIGN HARD: in the first pitch open with the deposit bonus, then the {rev}% revshare paid daily, "
        + (f"one reason to choose us ({why[0]}), " if why else "")
        + "then ask if they want to launch the deposit campaign for their group. Bring the deposit bonus back whenever they "
        "hesitate, compare, ask what is special, or go quiet, and suggest how to run it: a pinned post / announcement with "
        "their link, a short video or livestream showing the deposit bonus, a deadline to create urgency. "
        "One message, 3-5 short lines; never send two messages in a row.",
        *_banner_lines(o, dep, dep_hi),
        f"If you already mentioned other commission figures earlier in this chat, say simply that for KOLs in {name} "
        "the program is the deposit campaign + flat revshare above, and continue with it.",
        "Some countries restrict derivatives trading. If the KOL asks whether members in a restricted country can "
        "join, or asks about VPNs or any way around country restrictions: never suggest a VPN, a different country "
        "setting or any workaround — say the team will confirm eligibility and end your reply with [HANDOVER_REQUIRED].",
        "Campaign details you do not have (bonus cap, bonus type, duration, withdrawal rules): say the team will send the "
        "exact campaign terms and end your reply with [HANDOVER_REQUIRED].",
    ]
    if r.get("exclusions") or r.get("sub_affiliate"):
        lines.append(f"Valid volume: {r.get('exclusions', '')} {r.get('sub_affiliate', '')}".strip())
    if why:
        lines.append("When the KOL compares us with other exchanges, use ONLY these reasons: " + "; ".join(why) + ".")
    lines.append("NEVER name another exchange. NEVER use the phrase \"B-book\" or any variant.")
    try:
        vol = float(volume_claimed) if volume_claimed not in (None, "") else None
    except (TypeError, ValueError):
        vol = None
    if vol is not None and vol >= float(o["strong_volume_m"]):
        lines.append(f"The KOL stated about {_n(vol)}M monthly volume: tell them this likely qualifies them for the "
                     f"{dep_hi}% deposit bonus right away once their last-3-months proof is verified.")
    lines.append(
        f"YOU MUST END YOUR REPLY WITH [HANDOVER_REQUIRED] FOR: any revshare above {rev}%, a deposit bonus above {dep_hi}%, "
        "any other %, bonus or threshold, upfront / fixed fees after the proof step, or any exception request."
    )
    lines.append(f"NEVER quote any % or $ figure except {rev}%, {dep}% and {dep_hi}% "
                 "(no tier figures, no internal estimates, no ranges, no 'total payout' maths).")
    return "\n".join(lines)


# Asked when the WEEX agent does not know yet where the KOL and their community are from.
ORIGIN_QUESTION_RULE = (
    "--- ORIGIN QUESTION (the offer depends on it) ---\n"
    "You do not know yet where this KOL is based or where their community members are from. Ask it directly, ONCE, "
    "in one short natural line (e.g. \"By the way, where are you based, and where are most of your community members "
    "from?\"), early in the chat — at the latest together with your first pitch question, before quoting any commission %. "
    "Do not ask again if they already answered or ignored it. Their OWN country decides the offer first; the "
    "community's country counts only when they do not say where they are based."
)


def guard_ladder(ladder: dict, offer: dict | None = None) -> dict:
    """Ladder copy whose allowed percents are the region figures only (see kol_offer.allowed_figures)."""
    o = {**OFFER, **(offer or {})}
    out = copy.deepcopy(ladder)
    rules = out.setdefault("rules", {})
    rules["percents_only"] = [o["revshare_pct"], o["deposit_bonus_pct"], o["deposit_bonus_strong_pct"]]
    return out
