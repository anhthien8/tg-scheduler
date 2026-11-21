"""Affiliate Portal guide sent right after our side shares a KOL's portal login.

When a BD sends a KOL their WEEX Affiliate Portal login (typed in Telegram, in
the app's chat box or in the lead's forum topic), the KOL gets a short how-to in
their own language, once per chat: change the password, where the referral link
is, Campaigns, commission stats, the user list and the Event center.

Menu names match the live portal (checked 2026-11-21: Dashboard, Users > User
list, Commissions > Commission stats, Campaigns, Event center, avatar menu >
Change password / Google Authenticator) and WEEX's own help article.
"""
import re

import lang_guard

PORTAL_HOST = "affiliates.weex.com"
HELP_ARTICLE = "https://www.weex.com/help/articles/vmpq6xjr1zgg886sj3rshbsq"
SEND_DELAY_SECS = 20  # let the BD finish what they are typing before the guide follows

_PORTAL_RE = re.compile(r"affiliates\.weex\.com", re.IGNORECASE)
_PASSWORD_RE = re.compile(
    r"(?:\bpass(?:word)?\b|\bpwd\b|\bpw\b|mật\s*khẩu|\bmk\b|密码|密碼|비밀번호|şifre|kata\s*sandi|\bsandi\b"
    r"|пароль|รหัสผ่าน)\s*[:：=]", re.IGNORECASE)
_ACCOUNT_RE = re.compile(
    r"(?:\baccount\b|\busername\b|\buser\s*name\b|\blogin\b|tài\s*khoản|\btk\b|账号|帳號|账户|아이디"
    r"|kullanıcı\s*adı|\bakun\b|логин|บัญชี)\s*[:：=]", re.IGNORECASE)


def login_kind(text) -> str:
    """'portal' (login + affiliates.weex.com link), 'generic' (login labels only) or ''."""
    t = str(text or "")
    if not _PASSWORD_RE.search(t):
        return ""
    if _PORTAL_RE.search(t):
        return "portal"
    return "generic" if _ACCOUNT_RE.search(t) else ""


# ── Which template the KOL reads ─────────────────────────────────────────────

_FAMILY_TO_LANG = {"zh": "zh", "ko": "ko", "cyr": "ru", "th": "th"}
_TR_STRONG = set("ğıİĞ")          # letters only Turkish uses
_ROMANIAN = set("ăâţțșȘȚ")
_TR_WORDS = {"merhaba", "teşekkürler", "tesekkurler", "teşekkür", "evet", "hayır", "hayir", "için", "icin",
             "değil", "degil", "nasıl", "nasil", "hocam", "şimdi", "simdi", "çok", "güzel", "kardeşim", "kardesim"}
# Words shared with English / Malay / slang: never count them as Indonesian here.
_ID_GUIDE_WEAK = {"bang", "kita", "kami", "dong", "gan", "mau", "lu", "yang"}
_TAGALOG = {"po", "ang", "mga", "yung", "naman", "nyo", "pwede", "lang", "ba", "ng", "salamat", "kayo"}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
# Our own long message quoted back inside the KOL's reply is not their language.
_QUOTE_RE = re.compile(r"\n?\[Quoting (?:your|their) earlier message: \".*?\"\]\s*$", re.DOTALL)


def _own_texts(history) -> list[str]:
    return [_QUOTE_RE.sub(" ", str(m.get("content") or "")) for m in (history or [])[-30:]
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str)]


def guide_lang(history) -> str | None:
    """Template language for this chat, or None (nothing is sent; the BD writes it by hand).

    Decided from the KOL's own messages only. Chinese, Korean, Cyrillic and Thai
    writers get their template; Vietnamese only with real Vietnamese letters;
    Japanese / Arabic / Hindi writers and chats with no evidence get nothing.
    Latin writers get Turkish or Indonesian when their words show it, else English.
    """
    own = _own_texts(history)
    scripts = lang_guard.conversation_scripts([{"role": "user", "content": t} for t in own])
    if not scripts:
        return None
    if "ja" in scripts:
        return None  # a kanji-only "了解" must not turn a Japanese chat Chinese
    if any(lang_guard.text_script(t) == "vi" for t in own):
        return "vi"
    fam = scripts[0]
    if fam in _FAMILY_TO_LANG:
        return _FAMILY_TO_LANG[fam]
    if fam not in (lang_guard.LATIN, "vi"):
        return None
    text = " ".join(own)
    words = {w.lower() for w in _WORD_RE.findall(text)}
    tr_words = len(words & _TR_WORDS)
    if not any(ch in _ROMANIAN for ch in text) and (
            any(ch in _TR_STRONG for ch in text) or tr_words >= 2 or ("ş" in text.lower() and tr_words >= 1)):
        return "tr"
    import kol_region_offer
    if (len((words & kol_region_offer._ID_WORDS) - kol_region_offer._ID_WEAK - _ID_GUIDE_WEAK) >= 3
            and not words & _TAGALOG):
        return "id"
    return "en"


# ── Templates (one per language, same content) ───────────────────────────────

TEMPLATES = {
    "en": (
        "Quick tour of the portal so you don't have to dig around:\n\n"
        "1. Log in at affiliates.weex.com with the account above, then click your avatar (top right) → "
        "Change password. Worth turning on Google Authenticator there too.\n"
        "2. Your referral link and code are right on the Dashboard, in the \"Referral link\" box.\n"
        "3. Want a separate link for Telegram, X or YouTube? Make one per channel under \"Campaigns\". That's also "
        "where you can give part of your commission back to your members as cashback.\n"
        "4. The Dashboard shows your Today / 30-day / Total commission, plus how many people in your team "
        "deposited and traded.\n"
        "5. Commissions → Commission stats has the day-by-day breakdown, and you can export it. "
        "Users → User list shows everyone who signed up with your link.\n"
        "6. Event center lists the exchange's running events, handy for posts to your community.\n\n"
        f"WEEX's full guide: {HELP_ARTICLE}\n"
        "If anything looks off, just message me here."
    ),
    "vi": (
        "Mình gửi bạn hướng dẫn nhanh để dùng portal cho tiện nhé:\n\n"
        "1. Đăng nhập affiliates.weex.com bằng tài khoản ở trên, rồi bấm vào avatar (góc trên bên phải) → "
        "Change password để đổi mật khẩu. Tiện thì bật luôn Google Authenticator ở đó cho an toàn nha.\n"
        "2. Link và mã giới thiệu của bạn nằm ngay ở Dashboard, mục \"Referral link\".\n"
        "3. Muốn mỗi kênh (Telegram, X, YouTube…) có link riêng thì tạo trong \"Campaigns\". Ở đó bạn cũng chia lại "
        "được một phần hoa hồng cho member dưới dạng cashback.\n"
        "4. Dashboard hiện hoa hồng Today / 30-day / Total, kèm số người đã nạp tiền và đã giao dịch trong team "
        "của bạn.\n"
        "5. Vào Commissions → Commission stats để xem hoa hồng theo từng ngày, có thể xuất file. "
        "Users → User list là danh sách người đăng ký qua link của bạn.\n"
        "6. Event center có các sự kiện sàn đang chạy để bạn đẩy cho cộng đồng.\n\n"
        f"Hướng dẫn đầy đủ của WEEX: {HELP_ARTICLE}\n"
        "Có gì chưa rõ cứ nhắn mình ở đây nhé."
    ),
    "zh": (
        "给你一份后台快速上手说明，省得到处找：\n\n"
        "1. 用上面的账号登录 affiliates.weex.com，然后点右上角头像 → Change password 修改密码，"
        "建议顺手开启 Google Authenticator。\n"
        "2. 你的邀请链接和邀请码就在 Dashboard 的 \"Referral link\" 板块。\n"
        "3. 想给 Telegram、X、YouTube 各用一个链接，可以在 \"Campaigns\" 里按渠道分别创建；"
        "也可以在那里把一部分佣金以返现的形式让利给你的成员。\n"
        "4. Dashboard 能看到 Today / 30-day / Total 佣金，以及你团队里入金和交易的人数。\n"
        "5. Commissions → Commission stats 可以按天查看佣金明细，并支持导出；"
        "Users → User list 是通过你的链接注册的用户名单。\n"
        "6. Event center 里是交易所正在进行的活动，可以推给你的社群。\n\n"
        f"WEEX 官方完整指南：{HELP_ARTICLE}\n"
        "有任何不清楚的地方，直接在这里找我就行。"
    ),
    "id": (
        "Ini panduan singkat biar gampang pakai portalnya:\n\n"
        "1. Login di affiliates.weex.com pakai akun di atas, lalu klik avatar (kanan atas) → Change password "
        "untuk ganti kata sandi. Sekalian aktifkan Google Authenticator di situ biar lebih aman.\n"
        "2. Link dan kode referral kamu ada di Dashboard, bagian \"Referral link\".\n"
        "3. Mau link terpisah untuk Telegram, X atau YouTube? Buat satu per channel di \"Campaigns\". Di situ kamu "
        "juga bisa bagi sebagian komisi ke member sebagai cashback.\n"
        "4. Dashboard menampilkan komisi Today / 30-day / Total, plus berapa orang di tim kamu yang sudah deposit "
        "dan trading.\n"
        "5. Commissions → Commission stats untuk rincian komisi per hari, bisa di-export. "
        "Users → User list berisi semua yang daftar lewat link kamu.\n"
        "6. Event center berisi event exchange yang sedang jalan yang bisa kamu promosikan ke komunitas.\n\n"
        f"Panduan lengkap dari WEEX: {HELP_ARTICLE}\n"
        "Kalau ada yang kurang jelas, langsung chat saya di sini ya."
    ),
    "tr": (
        "Panelde kaybolmaman için kısa bir rehber:\n\n"
        "1. Yukarıdaki hesapla affiliates.weex.com'a giriş yap, sonra sağ üstteki profil resmine tıkla → "
        "Change password ile şifreni değiştir. Orada Google Authenticator'ı da açman iyi olur.\n"
        "2. Referans linkin ve kodun Dashboard'da, \"Referral link\" bölümünde.\n"
        "3. Telegram, X veya YouTube için ayrı link mi istiyorsun? \"Campaigns\" bölümünden her kanal için bir tane "
        "oluşturabilirsin. Komisyonunun bir kısmını üyelerine cashback olarak da orada verebilirsin.\n"
        "4. Dashboard'da Today / 30-day / Total komisyonun ve ekibinde para yatıran ve işlem yapan kişi sayısı "
        "görünür.\n"
        "5. Commissions → Commission stats günlük komisyon dökümünü gösterir, dışa aktarabilirsin. "
        "Users → User list linkinle kayıt olan herkesi listeler.\n"
        "6. Event center'da borsanın şu an devam eden etkinlikleri var, topluluğunla paylaşabilirsin.\n\n"
        f"WEEX'in tam rehberi: {HELP_ARTICLE}\n"
        "Takıldığın bir yer olursa buradan yazman yeterli."
    ),
    "ko": (
        "포털 사용하실 때 헤매지 않으시도록 간단히 정리해 드릴게요:\n\n"
        "1. 위 계정으로 affiliates.weex.com에 로그인하신 뒤, 오른쪽 위 프로필 사진 → Change password에서 "
        "비밀번호를 바꿔 주세요. 같은 메뉴에서 Google Authenticator도 켜 두시는 걸 추천드려요.\n"
        "2. 추천 링크와 추천 코드는 Dashboard의 \"Referral link\" 영역에 있어요.\n"
        "3. 텔레그램, X, 유튜브처럼 채널별로 링크를 나누고 싶으시면 \"Campaigns\"에서 채널마다 하나씩 만드시면 돼요. "
        "거기서 커미션 일부를 멤버분들께 캐시백으로 돌려드리는 설정도 할 수 있어요.\n"
        "4. Dashboard에서는 Today / 30-day / Total 커미션과 팀에서 입금·거래한 인원을 볼 수 있어요.\n"
        "5. Commissions → Commission stats에서 일별 커미션 내역을 보고 내보낼 수 있어요. "
        "Users → User list에서는 링크로 가입한 분들을 확인하실 수 있어요.\n"
        "6. Event center에는 커뮤니티에 소개하실 수 있는 거래소의 진행 중인 이벤트가 있어요.\n\n"
        f"WEEX 공식 가이드: {HELP_ARTICLE}\n"
        "궁금한 점 있으시면 여기로 편하게 메시지 주세요."
    ),
    "ru": (
        "Коротко, как пользоваться порталом, чтобы ничего не искать:\n\n"
        "1. Войдите на affiliates.weex.com с данными выше, затем нажмите на аватар (справа вверху) → "
        "Change password и смените пароль. Там же стоит включить Google Authenticator.\n"
        "2. Ваша реферальная ссылка и код — на Dashboard, в блоке «Referral link».\n"
        "3. Нужны отдельные ссылки для Telegram, X или YouTube? Создайте по одной на канал в разделе «Campaigns». "
        "Там же можно вернуть часть комиссии вашим подписчикам в виде кэшбэка.\n"
        "4. На Dashboard видно комиссию за Today / 30-day / Total, а также сколько людей в вашей команде "
        "пополнили счёт и торговали.\n"
        "5. Commissions → Commission stats — комиссия по дням, можно выгрузить. "
        "Users → User list — все, кто зарегистрировался по вашей ссылке.\n"
        "6. В Event center — текущие ивенты биржи, которые можно продвигать в вашем сообществе.\n\n"
        f"Полная инструкция WEEX: {HELP_ARTICLE}\n"
        "Если что-то непонятно, пишите сюда."
    ),
    "th": (
        "สรุปวิธีใช้พอร์ทัลสั้นๆ ไว้ให้ จะได้ไม่ต้องหาเอง:\n\n"
        "1. ล็อกอิน affiliates.weex.com ด้วยบัญชีด้านบน แล้วกดรูปโปรไฟล์ (มุมขวาบน) → Change password "
        "เพื่อเปลี่ยนรหัสผ่าน แนะนำให้เปิด Google Authenticator ไว้ด้วย\n"
        "2. ลิงก์และรหัสแนะนำของคุณอยู่ที่ Dashboard ในส่วน \"Referral link\"\n"
        "3. ถ้าอยากแยกลิงก์สำหรับ Telegram, X หรือ YouTube สร้างแยกตามช่องทางได้ในเมนู \"Campaigns\" "
        "และยังแบ่งคอมมิชชันบางส่วนคืนให้สมาชิกเป็นแคชแบ็กได้ที่นั่นด้วย\n"
        "4. Dashboard จะแสดงคอมมิชชัน Today / 30-day / Total พร้อมจำนวนคนในทีมที่ฝากเงินและเทรดแล้ว\n"
        "5. Commissions → Commission stats ดูรายละเอียดคอมมิชชันรายวันและ export ได้ "
        "ส่วน Users → User list คือรายชื่อคนที่สมัครผ่านลิงก์ของคุณ\n"
        "6. Event center มีกิจกรรมของ exchange ที่กำลังจัดอยู่ ให้คุณนำไปโปรโมตในคอมมูนิตี้ได้\n\n"
        f"คู่มือฉบับเต็มของ WEEX: {HELP_ARTICLE}\n"
        "ถ้ามีอะไรไม่ชัดเจน ทักมาที่นี่ได้เลย"
    ),
}


def render(lang: str) -> str:
    return TEMPLATES.get(lang) or TEMPLATES["en"]
