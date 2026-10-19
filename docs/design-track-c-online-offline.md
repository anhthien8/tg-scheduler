# Design Doc — C3: Online/Offline Cycle per Account

> **Mục tiêu:** Mỗi Telegram account có "giờ hoạt động" (working hours) riêng, ngoài giờ sẽ ngừng gửi DM/tham gia hoạt động — giảm rủi ro ban khi gửi tin lúc 3h sáng.
> **Trạng thái:** Design → Implement
> **Ngày:** 2026-10-18

---

## 0. TL;DR

- **Hiện trạng:** Accounts hoạt động 24/7, không có khái niệm "sleep". `keyword_watcher.py` chỉ check connected/paused/limit — không check giờ.
- **Vấn đề:** Gửi DM lúc 3h sáng giờ target = hành vi bot, tăng rủi ro report.
- **Đề xuất:** Thêm `active_hours_start` / `active_hours_end` + `timezone` cho mỗi account. Ngoài giờ → skip account trong mọi engine.

---

## 1. Schema Changes

### 1.1 Table `accounts` — thêm 3 cột migration

```sql
ALTER TABLE accounts ADD COLUMN active_hours_start TEXT DEFAULT '08:00';
ALTER TABLE accounts ADD COLUMN active_hours_end TEXT DEFAULT '22:00';  
ALTER TABLE accounts ADD COLUMN active_timezone TEXT DEFAULT 'Asia/Ho_Chi_Minh';
```

Giá trị mặc định: 08:00 - 22:00 ICT (giờ Việt Nam), phù hợp target thị trường VN/SEA.

### 1.2 Setting toàn cục (fallback)

```
account_default_active_start  →  "08:00"
account_default_active_end    →  "22:00"
account_default_timezone      →  "Asia/Ho_Chi_Minh"
```

---

## 2. Core Logic — `is_account_in_active_hours()`

Thêm hàm mới trong `database.py` hoặc utility module:

```python
import zoneinfo

def is_account_in_active_hours(
    start: str,   # "08:00"
    end: str,     # "22:00"  
    tz_name: str  # "Asia/Ho_Chi_Minh"
) -> bool:
    """Check nếu giờ hiện tại nằm trong working hours của account."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except (KeyError, zoneinfo.ZoneInfoNotFoundError):
        return True  # timezone lỗi → cho hoạt động (safe default)
    
    now = datetime.now(tz)
    current_time = now.strftime("%H:%M")
    
    if start <= end:
        # Normal range: 08:00 - 22:00
        return start <= current_time < end
    else:
        # Overnight range: 22:00 - 06:00 (night shift)
        return current_time >= start or current_time < end
```

---

## 3. Integration Points

### 3.1 `keyword_watcher.py` — `_do_send_dm_with_fallback` (line 543-570)

Thêm check vào account selection loop, **sau** PeerFlood check:

```python
# Line ~555, after _is_peerflood_blocked check:
acc_data = await db.get_account(acc_id)
if not is_account_in_active_hours(
    acc_data.get("active_hours_start", "08:00"),
    acc_data.get("active_hours_end", "22:00"),
    acc_data.get("active_timezone", "Asia/Ho_Chi_Minh"),
):
    logger.debug("Account %s outside active hours, skip", acc_id)
    continue
```

### 3.2 `dm_reply_tracker.py` — AI reply & Drip sends

Trong `generate_and_send_ai_reply_for_chat` (line 503), check trước khi gửi:

```python
acc_data = await db.get_account(account_id)
if not is_account_in_active_hours(...):
    logger.info("Account %s offline, defer AI reply", account_id)
    return  # reply sẽ được retry ở periodic loop tiếp theo
```

### 3.3 `warmup_engine.py` — `run_warmup_job` (line 58-63)

Warmup đã có `schedule_start/end` per-job. Thêm per-account check:

```python
# After job time window check:
acc_data = await db.get_account(current_account_id)
if not is_account_in_active_hours(...):
    logger.info("Account %s offline, skip warmup round", current_account_id)
    await asyncio.sleep(300)  # check lại sau 5 phút
    continue
```

### 3.4 `engagement_engine.py`

Thêm check trước khi reply trong group engagement.

---

## 4. UI Changes

### 4.1 Account Settings (trong trang quản lý accounts)

Mỗi account card thêm:
- Input "Giờ bắt đầu" (time picker, default 08:00)
- Input "Giờ kết thúc" (time picker, default 22:00)
- Dropdown "Timezone" (phổ biến: Asia/Ho_Chi_Minh, UTC, Asia/Singapore, Europe/London)
- Badge trạng thái: 🟢 Online / 🔴 Offline (real-time)

### 4.2 API Endpoints

```
PUT /api/accounts/{id}/active-hours
  body: { start: "08:00", end: "22:00", timezone: "Asia/Ho_Chi_Minh" }

GET /api/accounts/{id}/active-hours
  response: { start, end, timezone, is_active_now: bool }
```

---

## 5. Caching

Cache account active hours trong memory (TTL 5 phút) để tránh query DB mỗi lần check:

```python
_active_hours_cache: dict[int, tuple[str, str, str, float]] = {}

async def get_cached_active_hours(acc_id: int) -> tuple[str, str, str]:
    if acc_id in _active_hours_cache:
        start, end, tz, cached_at = _active_hours_cache[acc_id]
        if time.monotonic() - cached_at < 300:
            return start, end, tz
    # Re-fetch from DB
    ...
```

---

## 6. Test Plan

| Test | Expected |
|------|----------|
| `test_account_active_during_hours` | 10:00 in 08-22 window → True |
| `test_account_inactive_night` | 03:00 in 08-22 window → False |
| `test_overnight_window` | 22:00-06:00, check 23:00 → True |
| `test_bad_timezone_fallback` | Invalid tz → returns True |
| `test_dm_skips_offline_account` | DM send skips offline account |
| `test_warmup_respects_hours` | Warmup skips offline account |

---

## 7. Migration

- 3 ALTER TABLE trên `accounts` (safe, ADD COLUMN with DEFAULT)
- Không break existing behavior (default 08:00-22:00 bao phủ giờ làm việc bình thường)

---

## 8. Rollback

Set `active_hours_start = '00:00'`, `active_hours_end = '23:59'` → account hoạt động 24/7 như cũ.
