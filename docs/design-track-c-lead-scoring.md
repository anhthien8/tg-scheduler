# Design Doc — C2: Enhanced Lead Scoring (8+ signals)

> **Mục tiêu:** Mở rộng hệ thống lead scoring từ 5 tín hiệu tĩnh (pre-send) lên 8+ tín hiệu bao gồm cả conversational signals, để phân tier chính xác hơn và tự động re-score theo thời gian.
> **Trạng thái:** Design → Implement
> **Ngày:** 2026-10-18

---

## 0. TL;DR

- **Hiện trạng:** `lead_scoring.py` chỉ dùng 5 Telegram profile signals (username, first_name, premium, activity, profile completeness). Không có tier assignment. `intent_score` và `lead_tier` được LLM gán riêng trong `dm_reply_tracker.py` — hai hệ thống **tách rời hoàn toàn**.
- **Vấn đề:** Pre-send score không cập nhật khi lead tương tác. LLM-assigned tier không nhất quán (phụ thuộc prompt, không deterministic).
- **Đề xuất:** Hợp nhất thành 1 scoring engine 2 pha: (1) Static score pre-send, (2) Dynamic re-score post-conversation.

---

## 1. Signal Matrix (8+ signals)

### Phase 1: Static Signals (Pre-Send — giữ nguyên + bổ sung)

| Signal | Weight | Source | Ghi chú |
|--------|--------|--------|---------|
| `has_username` | +15 | Telegram profile | Giảm từ 25→15 |
| `has_first_name` | +10 | Telegram profile | Giữ nguyên |
| `is_premium` | +15 | Telegram profile | Giữ nguyên |
| `activity_recency` | 0-30 | Telegram `last_seen` | Giữ nguyên |
| `profile_completeness` | 0-15 | first+last+username+premium | Giảm từ 20→15 |
| `throwaway_penalty` | -30 | No username AND no name | Giữ nguyên |
| **NEW: `group_membership_count`** | +10 | Số group/channel mà account join (nếu có) | Proxy cho influence |
| **NEW: `bio_keywords`** | +15 | Bio chứa trading/crypto keywords | trader, analyst, KOL, influencer |

### Phase 2: Dynamic Signals (Post-Conversation)

| Signal | Weight | Source | Ghi chú |
|--------|--------|--------|---------|
| `reply_velocity` | 0-20 | Thời gian reply trung bình | <1h: +20, <6h: +15, <24h: +10, <72h: +5 |
| `reply_count` | 0-15 | Số tin nhắn đã reply | 1: +3, 2-3: +8, 4+: +15 |
| `asked_trading_question` | +10 | LLM detect trong conversation | "phí bao nhiêu", "leverage", "copy trade" |
| `provided_uid` | +20 | UID WEEX đã cung cấp | Tín hiệu mạnh nhất — intent cụ thể |
| `community_size` | 0-15 | LLM extract từ profile | 1k-5k: +5, 5k-20k: +10, 20k+: +15 |
| `current_exchange` | +5 | Đang dùng CEX khác | Có nhu cầu switch |
| `negative_sentiment` | -20 | Phản hồi tiêu cực, chửi, block | Auto-downgrade |
| `silence_days` | -5/-10/-15 | Không reply sau DM | >3d: -5, >7d: -10, >14d: -15 |

---

## 2. Tier Assignment Logic

```python
def assign_tier(total_score: int) -> str:
    """Deterministic tier assignment from composite score."""
    if total_score >= 70:
        return "Tier A"   # GOLD — high intent, high influence
    elif total_score >= 45:
        return "Tier B"   # SILVER — moderate, nurturing
    elif total_score >= 20:
        return "Tier C"   # BRONZE — low but not dead
    else:
        return "Cold"     # COLD — deprioritize
```

> **Giữ nguyên naming convention `Tier A/B/C`** cho backward compatibility. Không đổi sang GOLD/SILVER/BRONZE để tránh migration.

---

## 3. Architecture

### 3.1 Scoring Engine (enhance `lead_scoring.py`)

```python
@dataclass
class LeadScore:
    static_score: int       # Phase 1 signals
    dynamic_score: int      # Phase 2 signals  
    total_score: int        # Clamped [0, 100]
    tier: str               # "Tier A" / "Tier B" / "Tier C" / "Cold"
    reasons: list[str]      # Human-readable signal breakdown
    signals: dict[str, int] # Signal → weight for debug/UI

def score_lead_static(member: dict) -> LeadScore:
    """Pre-send scoring — chỉ dùng Telegram profile data."""
    ...

async def score_lead_dynamic(user_id: int, account_id: int) -> LeadScore:
    """Post-conversation re-scoring — kết hợp static + dynamic signals."""
    # 1. Load static signals from scraped_members
    # 2. Load conversation data from ai_followup_chats  
    # 3. Load KOL profile from kol_profiles
    # 4. Calculate composite score
    # 5. Assign tier
    ...

async def rescore_and_update(user_id: int, account_id: int) -> LeadScore:
    """Re-score, update DB, return new score."""
    score = await score_lead_dynamic(user_id, account_id)
    async with get_db() as db:
        await db.update_followup_lead_metrics(
            account_id=account_id,
            user_id=user_id,
            intent_score=score.total_score,
            lead_tier=score.tier,
            summary="; ".join(score.reasons[:5])
        )
    return score
```

### 3.2 Re-Score Triggers

| Trigger | When | Action |
|---------|------|--------|
| Post-reply | Mỗi khi lead reply trong `dm_reply_tracker.py` | `rescore_and_update()` |
| Drip sent | Sau mỗi drip stage | Re-evaluate silence_days |
| Periodic | Cron mỗi 6h trong `_followup_periodic_loop` | Batch re-score active chats |
| Manual | Admin click "Re-score" trong UI | API endpoint |

### 3.3 LLM Integration Cleanup

**Thay đổi LLM prompt** trong `dm_reply_tracker.py`:
- **Bỏ** yêu cầu LLM output `[METRICS: {...}]` cho `intent_score` và `lead_tier`
- **Giữ** yêu cầu LLM extract trading signals (uid, community, exchange) thành structured tags
- Scoring engine sẽ **compute** score từ signals, không để LLM quyết định

```
# OLD prompt instruction:
[METRICS: {"intent_score": 75, "lead_tier": "Tier B", "summary": "..."}]

# NEW prompt instruction:  
[SIGNALS: {"provided_uid": "12345", "community_size": "8000", "current_exchange": "Binance", "asked_about": "copy_trade"}]
```

---

## 4. DB Changes

### 4.1 Table `lead_score_history` (NEW)

```sql
CREATE TABLE IF NOT EXISTS lead_score_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    static_score INTEGER DEFAULT 0,
    dynamic_score INTEGER DEFAULT 0,
    total_score INTEGER DEFAULT 0,
    tier TEXT DEFAULT 'Cold',
    signals_json TEXT DEFAULT '{}',
    scored_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_score_history_user ON lead_score_history(user_id, scored_at DESC);
```

### 4.2 `ai_followup_chats` — thêm cột

```sql
ALTER TABLE ai_followup_chats ADD COLUMN score_signals_json TEXT DEFAULT '{}';
ALTER TABLE ai_followup_chats ADD COLUMN last_scored_at TEXT;
```

---

## 5. UI Changes

### 5.1 Lead Score Panel trong AI Follow-up

Mỗi chat card hiện thêm:
- Score gauge: `75/100` với color (green/yellow/orange/red)
- Tier badge: 🥇 Tier A / 🥈 Tier B / 🥉 Tier C / ❄️ Cold
- Signal breakdown tooltip (hover để xem chi tiết từng signal)
- Button "Re-score" (manual trigger)

### 5.2 Settings Panel

- Configurable weights cho mỗi signal (advanced mode)
- Tier thresholds (Tier A >= ?, Tier B >= ?, Tier C >= ?)

---

## 6. Test Plan

| Test | Expected |
|------|----------|
| `test_static_score_premium_active` | Premium + recent activity = high static |
| `test_dynamic_reply_velocity_boost` | Fast reply → +20 |
| `test_uid_provided_highest_signal` | UID → +20, auto Tier A if combined |
| `test_negative_sentiment_downgrade` | Negative reply → -20 |
| `test_silence_penalty` | No reply >7d → -10 |
| `test_tier_assignment_boundaries` | 70→A, 45→B, 20→C, 19→Cold |
| `test_rescore_updates_db` | After reply, tier upgraded in DB |
| `test_backward_compat` | Old chats with LLM-assigned tier still work |

---

## 7. Migration

- ADD COLUMN safe operations
- New table `lead_score_history` — no existing data impact
- LLM prompt change: gradual — keep parsing old `[METRICS]` format as fallback for 1 version
