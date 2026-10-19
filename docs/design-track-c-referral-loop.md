# Design Doc — C5: Referral / Viral Loop

> **Mục tiêu:** Tạo hệ thống referral cho KOL đã converted — khuyến khích họ giới thiệu KOL khác, tạo hiệu ứng viral cho outreach.
> **Trạng thái:** Design → Implement
> **Ngày:** 2026-10-18

---

## 0. TL;DR

- **Hiện trạng:** Zero referral tracking. Không có bảng, field, hay logic nào liên quan đến referral trong toàn bộ codebase.
- **Vấn đề:** KOL đã signed deal có network KOL khác nhưng hệ thống không leverage.
- **Đề xuất:** Referral tracking system với unique referral codes, attribution, và reward tracking.

---

## 1. Referral Flow

```
KOL_A (đã signed) 
  → nhận referral link/code
  → chia sẻ cho KOL_B
  → KOL_B DM bot hoặc click link
  → Hệ thống attribute KOL_B → KOL_A
  → KOL_B vào funnel bình thường
  → Khi KOL_B signed → KOL_A nhận reward notification
```

---

## 2. Schema

### 2.1 Table `referral_codes` (NEW)

```sql
CREATE TABLE IF NOT EXISTS referral_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,      -- Account gửi referral
    referrer_user_id INTEGER NOT NULL, -- KOL_A user_id
    code TEXT NOT NULL UNIQUE,         -- Unique code: "REF-ABCD1234"
    max_uses INTEGER DEFAULT 10,       -- Giới hạn sử dụng
    uses_count INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT                     -- Optional expiry
);

CREATE UNIQUE INDEX idx_referral_code ON referral_codes(code);
CREATE INDEX idx_referral_referrer ON referral_codes(referrer_user_id);
```

### 2.2 Table `referral_attributions` (NEW)

```sql
CREATE TABLE IF NOT EXISTS referral_attributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referral_code_id INTEGER NOT NULL,
    referrer_user_id INTEGER NOT NULL,  -- KOL_A
    referred_user_id INTEGER NOT NULL,  -- KOL_B
    referred_username TEXT,
    status TEXT DEFAULT 'pending',      -- 'pending', 'contacted', 'replied', 'signed', 'expired'
    attributed_at TEXT DEFAULT (datetime('now')),
    converted_at TEXT,                  -- When KOL_B reaches 'signed' stage
    FOREIGN KEY (referral_code_id) REFERENCES referral_codes(id)
);

CREATE INDEX idx_attribution_referrer ON referral_attributions(referrer_user_id);
CREATE INDEX idx_attribution_referred ON referral_attributions(referred_user_id);
CREATE UNIQUE INDEX idx_attribution_unique ON referral_attributions(referral_code_id, referred_user_id);
```

### 2.3 Table `referral_rewards` (NEW)

```sql
CREATE TABLE IF NOT EXISTS referral_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id INTEGER NOT NULL,
    attribution_id INTEGER NOT NULL,
    reward_type TEXT NOT NULL,          -- 'notification', 'bonus_tier', 'custom'
    reward_detail TEXT,                 -- JSON: {"message": "...", "bonus": "..."}
    is_claimed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (attribution_id) REFERENCES referral_attributions(id)
);
```

---

## 3. Core Logic

### 3.1 Referral Code Generation

```python
import secrets
import string

def generate_referral_code() -> str:
    """Generate unique 8-char alphanumeric code."""
    chars = string.ascii_uppercase + string.digits
    return "REF-" + "".join(secrets.choice(chars) for _ in range(8))
```

### 3.2 Auto-Generate Referral Code

Khi KOL đạt stage `signed` trong `ai_followup_chats`:
```python
async def on_kol_signed(account_id: int, user_id: int):
    """Auto-generate referral code khi KOL signed deal."""
    async with get_db() as db:
        # Check if already has code
        existing = await db.get_referral_code_by_user(user_id)
        if existing:
            return existing
        
        code = generate_referral_code()
        await db.create_referral_code(account_id, user_id, code)
        
        # Send referral code to KOL via DM
        msg = (
            f"🎉 Chúc mừng bạn đã gia nhập WEEX!\n\n"
            f"Giới thiệu bạn bè KOL khác và nhận thưởng:\n"
            f"📌 Code giới thiệu: {code}\n"
            f"Chia sẻ code này cho KOL muốn hợp tác với WEEX."
        )
        await tg.send_text_message(account_id, user_id, msg)
```

### 3.3 Referral Attribution

Khi KOL mới DM bot hoặc gửi referral code:
```python
async def process_referral_code(
    code: str, 
    referred_user_id: int, 
    referred_username: str
) -> bool:
    """Process referral code from new KOL."""
    async with get_db() as db:
        ref_code = await db.get_referral_code(code)
        if not ref_code or not ref_code["is_active"]:
            return False
        if ref_code["uses_count"] >= ref_code["max_uses"]:
            return False
        if ref_code.get("expires_at") and ref_code["expires_at"] < datetime.utcnow().isoformat():
            return False
        
        # Check not self-referral
        if ref_code["referrer_user_id"] == referred_user_id:
            return False
        
        # Create attribution
        await db.create_referral_attribution(
            referral_code_id=ref_code["id"],
            referrer_user_id=ref_code["referrer_user_id"],
            referred_user_id=referred_user_id,
            referred_username=referred_username,
        )
        
        # Increment uses
        await db.increment_referral_uses(ref_code["id"])
        
        return True
```

### 3.4 Referral Code Detection in Messages

Trong `dm_reply_tracker.py`, khi processing inbound message:
```python
import re

REFERRAL_PATTERN = re.compile(r'\bREF-[A-Z0-9]{8}\b')

async def check_referral_in_message(text: str, user_id: int, username: str):
    """Detect and process referral codes in incoming messages."""
    match = REFERRAL_PATTERN.search(text)
    if match:
        code = match.group(0)
        success = await process_referral_code(code, user_id, username)
        if success:
            logger.info("Referral attributed: %s → user %s", code, user_id)
```

### 3.5 Conversion Tracking

Khi KOL_B (referred) đạt stage `signed`:
```python
async def on_referred_kol_signed(user_id: int):
    """Track conversion and notify referrer."""
    async with get_db() as db:
        attr = await db.get_attribution_by_referred(user_id)
        if attr and attr["status"] != "signed":
            await db.update_attribution_status(attr["id"], "signed")
            
            # Create reward for referrer
            await db.create_referral_reward(
                referrer_user_id=attr["referrer_user_id"],
                attribution_id=attr["id"],
                reward_type="notification",
                reward_detail=json.dumps({
                    "message": f"KOL bạn giới thiệu (@{attr['referred_username']}) đã ký hợp đồng! 🎊"
                })
            )
            
            # Notify referrer
            # (done by admin manually or auto via AI agent)
```

---

## 4. API Endpoints

```
POST   /api/referrals/codes              -- Admin tạo code cho KOL
GET    /api/referrals/codes              -- List all codes
GET    /api/referrals/codes/{code}       -- Get code details + attributions
DELETE /api/referrals/codes/{id}         -- Deactivate code

GET    /api/referrals/attributions       -- List all attributions
GET    /api/referrals/stats              -- Summary: total referrals, conversions, rate

POST   /api/referrals/process            -- Manually attribute a referral
```

---

## 5. UI — Referral Tab

### 5.1 Referral Dashboard
- Total referral codes issued
- Total KOLs referred
- Conversion rate (referred → signed)
- Top referrers leaderboard

### 5.2 Referral Code Management
- Create/deactivate codes
- View attributions per code
- Export referral data

---

## 6. Integration Points

| Integration | How |
|-------------|-----|
| `dm_reply_tracker.py` | Detect REF-code in inbound messages |
| `ai_followup_chats.stage` | On stage='signed' → auto-create code + track conversion |
| Command Bot | `/referral` command for KOL to check their code & stats |
| AI Agent prompt | Mention referral program when KOL reaches negotiating stage |

---

## 7. Test Plan

| Test | Expected |
|------|----------|
| `test_generate_unique_codes` | 100 codes all unique |
| `test_referral_attribution` | Valid code → attribution created |
| `test_self_referral_blocked` | Same user → rejected |
| `test_max_uses_enforced` | Exceeding max → rejected |
| `test_expired_code_rejected` | Expired code → rejected |
| `test_conversion_tracking` | Referred KOL signed → referrer notified |
| `test_referral_stats` | Stats endpoint returns correct counts |
| `test_detect_code_in_message` | "REF-ABCD1234" detected in free text |

---

## 8. Dependency

- Requires `ai_followup_chats.stage` to be reliably set (currently exists)
- B2 Community Engagement Engine should be stable before referral loop (done ✅)
