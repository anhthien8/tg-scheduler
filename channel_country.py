"""Best-effort country/region guess for a Telegram channel from its title,
description and username. Pure-Python, no network: scripts first (cheap and
reliable), then language/country keywords. Returns an ISO-ish code or "".
"""
import re

COUNTRY_LABELS = {
    "VN": "🇻🇳 Việt Nam", "ID": "🇮🇩 Indonesia", "RU": "🇷🇺 Nga / CIS", "UA": "🇺🇦 Ukraine",
    "CN": "🇨🇳 Trung Quốc", "KR": "🇰🇷 Hàn Quốc", "JP": "🇯🇵 Nhật", "TH": "🇹🇭 Thái Lan",
    "AR": "🇸🇦 Ả Rập", "IR": "🇮🇷 Iran", "TR": "🇹🇷 Thổ Nhĩ Kỳ", "IN": "🇮🇳 Ấn Độ",
    "PK": "🇵🇰 Pakistan", "NG": "🇳🇬 Nigeria", "PH": "🇵🇭 Philippines", "MY": "🇲🇾 Malaysia",
    "BR": "🇧🇷 Brazil / BĐN", "ES": "🇪🇸 Tây Ban Nha / LatAm", "DE": "🇩🇪 Đức", "FR": "🇫🇷 Pháp",
    "IT": "🇮🇹 Ý", "EN": "🌐 Quốc tế (EN)",
}

_SCRIPTS = [
    ("KR", re.compile(r"[가-힯]")),
    ("JP", re.compile(r"[぀-ヿ]")),
    ("CN", re.compile(r"[一-鿿]")),
    ("TH", re.compile(r"[฀-๿]")),
    ("IR", re.compile(r"[پچژگ]")),
    ("AR", re.compile(r"[؀-ۿ]")),
    ("IN", re.compile(r"[ऀ-ॿ]")),
]
_UA_CHARS = re.compile(r"[іїєґІЇЄҐ]")
_CYR = re.compile(r"[Ѐ-ӿ]")
_VI = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệịỉĩọỏốồổỗộớờởỡợụủũứừửữựỳỷỹỵ]", re.I)
_TR = re.compile(r"[ğışĞİŞ]")

# (code, keyword regex) checked on lowercase text; word-ish boundaries.
_KW = [
    ("VN", r"việt nam|vietnam|\bvn\b|tiếng việt"),
    ("ID", r"indonesia|\bindo\b|bahasa|\byang\b|\bdan\b|\buntuk\b|\bdengan\b|\bgratis\b|\bkami\b|\bterbaru\b"),
    ("MY", r"malaysia|\bmalay\b"),
    ("PH", r"philippines|pinoy|filipino|tagalog"),
    ("NG", r"nigeria|naija|lagos|\bghana\b|africa"),
    ("PK", r"pakistan|urdu"),
    ("IN", r"\bindia\b|\bindian\b|hindi|\binr\b"),
    ("TR", r"türk|turkey|türkiye|\bve\b.*\bbir\b"),
    ("BR", r"brasil|brazil|português|\bpara\b.*\bvocê\b|\bnão\b"),
    ("ES", r"español|\bespaña\b|latam|méxico|argentina|\bpara\b.*\blos\b|\bseñales\b"),
    ("DE", r"deutsch|germany|\bund\b.*\bder\b"),
    ("FR", r"français|france|\bpour\b.*\bles\b"),
    ("IT", r"italia|italiano"),
    ("IR", r"\biran\b|persian|farsi"),
    ("AR", r"\barab|\buae\b|dubai|saudi"),
]
_KW = [(c, re.compile(p)) for c, p in _KW]
_LATIN_WORD = re.compile(r"[a-z]{3,}")


def guess_country(title: str = "", description: str = "", username: str = "") -> str:
    text = f"{title or ''} {description or ''} {username or ''}"
    for code, rx in _SCRIPTS:
        if len(rx.findall(text)) >= 2:
            return code
    cyr = len(_CYR.findall(text))
    if cyr >= 4:
        return "UA" if _UA_CHARS.search(text) else "RU"
    if len(_VI.findall(text)) >= 3:
        return "VN"
    low = text.lower()
    for code, rx in _KW:
        if rx.search(low):
            return code
    if len(_TR.findall(text)) >= 2:
        return "TR"
    if len(_LATIN_WORD.findall(low)) >= 3:
        return "EN"
    return ""
