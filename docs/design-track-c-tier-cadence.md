# Design Doc — C1: Tier-Differentiated Drip Cadence

> **Mục tiêu:** Thay đổi drip cadence hiện tại (đồng đều [24h, 72h, 168h] cho mọi lead) thành cadence riêng theo `lead_tier`, giúp liên hệ lead chất lượng cao nhanh hơn và không spam lead chất lượng thấp.
> **Trạng thái:** Design → Implement
> **Ngày:** 2026-10-18

---

## 0. TL;DR

- **Hiện trạng:** `dm_reply_tracker.py:1308-1334` có `DEFAULT_DRIP_CADENCE_HOURS = [24, 72, 168]` và setting `ai_followup_drip_cadence` — áp dụng **đồng nhất** cho Tier A, B, C.
- **Vấn đề:** Tier A (intent 80-100, khả năng chốt deal cao) bị follow-up chậm như Tier C. Ngược lại, Tier C vốn ít quan tâm lại bị spam nếu hạ thời gian.
- **Đề xuất:** Thêm 3 settings cho cadence theo tier, giữ fallback về cadence toàn cục hiện có.

---

## 1. Cadence Matrix

| Lead Tier | Cadence (hours) | Lý do |
|-----------|-----------------|-------|
| Tier A (GOLD) | `[12, 24, 48, 96]` | 4 stages, follow-up nhanh hơn — lead HOT |
| Tier B (SILVER) | `[24, 72, 168]` | Giữ nguyên default — balanced |
| Tier C (BRONZE) | `[48, 168]` | 2 stages, chậm hơn — tránh spam lead cold |

> **Lưu ý:** Tier C hiện bị gate out hoàn toàn (`lead_tier IN ('Tier A','Tier B') OR intent_score >= 50` tại line 1484). Cần mở gate cho Tier C nhưng với cadence chậm.

---

## 2. DB Settings

3 settings mới trong bảng `settings`:

```
ai_followup_drip_cadence_tier_a  →  "[12, 24, 48, 96]"
ai_followup_drip_cadence_tier_b  →  "[24, 72, 168]"
ai_followup_drip_cadence_tier_c  →  "[48, 168]"
```

Fallback: nếu setting theo tier không tồn tại → dùng `ai_followup_drip_cadence` toàn cục (hiện tại).

---

## 3. Code Changes

### 3.1 `dm_reply_tracker.py` — `_load_drip_cadence()`

**Hiện tại (line 1308-1334):**
```python
def _load_drip_cadence() -> list[int]:
    # loads ai_followup_drip_cadence, returns DEFAULT_DRIP_CADENCE_HOURS
```

**Đề xuất:**
```python
DEFAULT_TIER_CADENCE = {
    "Tier A": [12, 24, 48, 96],
    "Tier B": [24, 72, 168],
    "Tier C": [48, 168],
}

async def _load_drip_cadence_for_tier(tier: str) -> list[int]:
    """Load cadence cho tier cụ thể, fallback về global."""
    tier_key = {
        "Tier A": "ai_followup_drip_cadence_tier_a",
        "Tier B": "ai_followup_drip_cadence_tier_b",
        "Tier C": "ai_followup_drip_cadence_tier_c",
    }.get(tier)
    
    if tier_key:
        async with get_db() as db:
            raw = await db.get_setting(tier_key, None)
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(x, (int,float)) and x > 0 for x in parsed):
                return parsed
    
    # Fallback: tier-specific default, then global
    if tier in DEFAULT_TIER_CADENCE:
        return DEFAULT_TIER_CADENCE[tier]
    return _load_drip_cadence()  # existing global loader
```

### 3.2 `dm_reply_tracker.py` — `process_drip_followups()`

**Hiện tại (line 1484):**
```python
WHERE (lead_tier IN ('Tier A','Tier B') OR intent_score >= 50)
  AND last_drip_stage < ?   -- total_stages from global cadence
```

**Đề xuất:**
1. Bỏ gate Tier C — cho phép tất cả tier vào pipeline.
2. Thay vì pre-calculate `total_stages` từ global cadence, tính per-row dựa trên `lead_tier`:

```python
# Thay đổi query: bỏ filter lead_tier, chỉ giữ intent_score fallback
WHERE status = 'active' 
  AND (lead_tier IS NOT NULL OR intent_score >= 30)  -- hạ threshold
  AND last_user_message_at IS NOT NULL

# Trong loop xử lý mỗi chat:
tier = chat["lead_tier"] or "Tier C"
cadence = await _load_drip_cadence_for_tier(tier)
total_stages = len(cadence)

if chat["last_drip_stage"] >= total_stages:
    continue  # đã hết stages cho tier này

threshold_hours = cadence[chat["last_drip_stage"]]
silence = _silence_hours(chat)
if silence < threshold_hours:
    continue  # chưa đủ im lặng
```

### 3.3 `dm_reply_tracker.py` — `_drip_instruction_for_stage()`

Hiện tại mỗi stage có instruction khác nhau. Cần extend cho 4 stages (Tier A):

```python
def _drip_instruction_for_stage(stage: int, total: int, tier: str) -> str:
    if stage == 0:
        return "Nhắc nhẹ nhàng, hỏi thăm..."
    elif stage == total - 1:
        return "Lần cuối: tóm tắt offer + CTA rõ ràng."
    elif tier == "Tier A" and stage == 1:
        return "Chia sẻ thêm 1 case study/testimonial cụ thể."
    elif tier == "Tier A" and stage == 2:
        return "Gợi ý demo/call — giá trị exclusive."
    else:
        return "Chia sẻ thêm giá trị, hỏi feedback."
```

### 3.4 UI — `static/js/ai_followup.js`

Thêm section "Cadence theo Tier" trong settings panel:
- 3 input fields cho Tier A / B / C cadence (JSON array)
- Validation: strictly increasing, positive integers
- Save → `POST /api/ai-followup/settings`

### 3.5 API — `routes/ai_followup.py`

Thêm 3 keys vào settings handler: `ai_followup_drip_cadence_tier_a`, `_tier_b`, `_tier_c`.

---

## 4. Test Plan

| Test | Expected |
|------|----------|
| `test_tier_a_cadence_faster` | Tier A chat gets drip at 12h, not 24h |
| `test_tier_b_cadence_default` | Tier B unchanged behavior |
| `test_tier_c_now_receives_drips` | Tier C (with intent >= 30) gets drip at 48h |
| `test_custom_tier_cadence_from_settings` | DB override works |
| `test_fallback_to_global_cadence` | Missing tier setting → global |
| `test_stage_count_per_tier` | Tier A has 4 stages, B has 3, C has 2 |

---

## 5. Migration

Không cần migration DB. Chỉ thêm settings rows lần đầu nếu muốn override default.

---

## 6. Rollback

Xóa 3 settings → hệ thống fallback về `DEFAULT_TIER_CADENCE` hardcoded → fallback về global `ai_followup_drip_cadence`.
