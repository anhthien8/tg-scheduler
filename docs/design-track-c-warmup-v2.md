# Design Doc — C6: Warmup Engine v2 (Diverse Behaviors)

> **Mục tiêu:** Nâng cấp warmup engine từ 1 hành vi (post script messages) lên 6+ hành vi tự nhiên, thêm graduation stages, và tích hợp `account_busy.py`.
> **Trạng thái:** Design → Implement
> **Ngày:** 2026-10-18

---

## 0. TL;DR

- **Hiện trạng:** `warmup_engine.py` chỉ có 1 action: gửi tin nhắn script vào group. Không join/leave group, không react, không đọc history, không graduate account.
- **Vấn đề:** Telegram detect pattern "chỉ gửi tin, không làm gì khác" = bot behavior → FloodWait/ban.
- **Đề xuất:** Thêm 5 hành vi mới + hệ thống graduation 4 giai đoạn.

---

## 1. Warmup Behaviors

### 1.1 Behavior Matrix

| # | Behavior | Method | Frequency | Stage |
|---|----------|--------|-----------|-------|
| 1 | **Post message** (existing) | `send_text_message()` | 2-5/day | Day 3+ |
| 2 | **Read history** | `get_messages(limit=20)` | 5-10/day | Day 1+ |
| 3 | **React emoji** | `send_reaction(emoji)` | 3-8/day | Day 1+ |
| 4 | **Join channel/group** | `join_chat()` | 1-2/day | Day 1-7 |
| 5 | **View profile** | `get_entity()` | 2-4/day | Day 1+ |
| 6 | **Forward message** | `forward_messages()` | 0-1/day | Day 5+ |

### 1.2 Behavior Weights by Stage

```python
BEHAVIOR_WEIGHTS = {
    "stage_1": {  # Day 1-2: Passive only
        "read_history": 50,
        "react_emoji": 30,
        "view_profile": 20,
    },
    "stage_2": {  # Day 3-4: Start engaging
        "read_history": 30,
        "react_emoji": 25,
        "view_profile": 10,
        "post_message": 25,
        "join_channel": 10,
    },
    "stage_3": {  # Day 5-7: Active participation
        "read_history": 15,
        "react_emoji": 20,
        "post_message": 35,
        "join_channel": 10,
        "forward_message": 10,
        "view_profile": 10,
    },
    "stage_4": {  # Day 8+: Ready for graduation
        "post_message": 40,
        "react_emoji": 25,
        "read_history": 15,
        "forward_message": 10,
        "view_profile": 10,
    },
}
```

---

## 2. Graduation System

### 2.1 Stages

```
Stage 1 (Day 1-2): PASSIVE — chỉ read + react
Stage 2 (Day 3-4): WARMING — bắt đầu post
Stage 3 (Day 5-7): ACTIVE — đa dạng hành vi
Stage 4 (Day 8+):  READY — sẵn sàng graduate
```

### 2.2 Graduation Criteria

Account graduate khi **tất cả** điều kiện thỏa:
- Đã hoạt động >= 7 ngày
- Đã gửi >= 10 messages thành công
- Đã react >= 15 lần
- Đã join >= 3 groups/channels
- Không bị FloodWait >= 600s trong 3 ngày gần nhất
- Không bị PeerFlood trong 7 ngày gần nhất

### 2.3 Post-Graduation

Account tự động:
1. Đánh dấu `warmup_stage = 'graduated'` trong DB
2. Thêm vào pool `sender_account_ids` của active watchers
3. Gửi notification cho admin

---

## 3. Schema Changes

### 3.1 Table `warmup_progress` (NEW)

```sql
CREATE TABLE IF NOT EXISTS warmup_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL UNIQUE,
    warmup_job_id INTEGER,
    stage INTEGER DEFAULT 1,
    started_at TEXT DEFAULT (datetime('now')),
    messages_sent INTEGER DEFAULT 0,
    reactions_sent INTEGER DEFAULT 0,
    channels_joined INTEGER DEFAULT 0,
    history_reads INTEGER DEFAULT 0,
    forwards_sent INTEGER DEFAULT 0,
    profiles_viewed INTEGER DEFAULT 0,
    last_flood_at TEXT,
    graduated_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_warmup_progress_stage ON warmup_progress(stage);
CREATE INDEX idx_warmup_progress_account ON warmup_progress(account_id);
```

### 3.2 Table `warmup_action_log` (NEW)

```sql
CREATE TABLE IF NOT EXISTS warmup_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,  -- 'post', 'react', 'read', 'join', 'forward', 'view'
    target_chat_id INTEGER,
    target_message_id INTEGER,
    status TEXT DEFAULT 'success',  -- 'success', 'flood', 'error'
    error_detail TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_warmup_log_account ON warmup_action_log(account_id, created_at DESC);
```

---

## 4. Core Engine Changes

### 4.1 `warmup_engine.py` — Refactor `run_warmup_job()`

```python
async def run_warmup_job(job_id: int):
    """Main warmup loop — behavior-weighted per stage."""
    while _active_jobs.get(job_id):
        # 1. Check time window
        if not in_schedule_window(job):
            await asyncio.sleep(60)
            continue
        
        # 2. Pick account (round-robin)
        acc_id = pick_next_account(job)
        
        # 3. Check active hours (C3 integration)
        if not is_account_in_active_hours(acc_id):
            continue
        
        # 4. Acquire busy lock
        async with account_busy.acquire(acc_id, "warmup"):
            # 5. Determine stage
            progress = await get_warmup_progress(acc_id)
            stage = determine_stage(progress)
            
            # 6. Pick weighted random behavior
            behavior = pick_behavior(stage)
            
            # 7. Execute behavior
            await execute_warmup_behavior(acc_id, behavior, job)
            
            # 8. Update progress counters
            await update_warmup_progress(acc_id, behavior)
            
            # 9. Check graduation
            if should_graduate(progress):
                await graduate_account(acc_id)
        
        # 10. Random interval sleep
        await asyncio.sleep(random.uniform(
            job["interval_min"] * 60,
            job["interval_max"] * 60
        ))
```

### 4.2 Behavior Implementations

```python
async def _warmup_read_history(acc_id: int, chat_id: int):
    """Scroll through recent messages — mimics natural reading."""
    client = tg.get_client(acc_id)
    messages = await client.get_messages(chat_id, limit=random.randint(10, 30))
    # Mark as read
    if messages:
        await client.send_read_acknowledge(chat_id, messages[0])
    await asyncio.sleep(random.uniform(2, 8))  # "reading time"

async def _warmup_react_emoji(acc_id: int, chat_id: int):
    """React to a recent message with random emoji."""
    client = tg.get_client(acc_id)
    messages = await client.get_messages(chat_id, limit=10)
    if messages:
        msg = random.choice(messages)
        emoji = random.choice(["👍", "🔥", "❤️", "😂", "🤔", "💯", "👀"])
        try:
            await msg.react(emoji)
        except Exception:
            pass  # Some messages/chats don't allow reactions

async def _warmup_join_channel(acc_id: int):
    """Join a curated list of crypto-related channels."""
    # Use a pre-defined list of safe, public channels
    ...

async def _warmup_view_profile(acc_id: int, chat_id: int):
    """View random user profiles from recent messages."""
    client = tg.get_client(acc_id)
    messages = await client.get_messages(chat_id, limit=20)
    if messages:
        msg = random.choice([m for m in messages if m.sender_id])
        try:
            await client.get_entity(msg.sender_id)
        except Exception:
            pass

async def _warmup_forward_message(acc_id: int, from_chat: int, to_chat: int):
    """Forward an interesting message between groups."""
    client = tg.get_client(acc_id)
    messages = await client.get_messages(from_chat, limit=5)
    if messages:
        msg = random.choice(messages)
        try:
            await client.forward_messages(to_chat, msg)
        except Exception:
            pass
```

---

## 5. Safety Guards

| Guard | Implementation |
|-------|---------------|
| FloodWait handling | Catch `FloodWaitError`, log to `warmup_action_log`, pause account if > 600s |
| `account_busy` integration | Use `async with account_busy.acquire()` for ALL behaviors |
| Rate limits per behavior | Max reactions/day, max joins/day configurable |
| Error threshold | 5 consecutive errors → auto-pause warmup for account |
| Graduated check | Once graduated, warmup job auto-removes account |

---

## 6. UI Changes

### 6.1 Warmup Dashboard

- Progress bars per account: Stage 1→2→3→4→Graduated
- Action log table with filters
- Graduation checklist (✅/❌ for each criterion)
- Manual "Graduate" button (admin override)

### 6.2 Warmup Job Settings

Thêm:
- Toggle per behavior (enable/disable react, read, etc.)
- Behavior weight sliders
- Graduation criteria thresholds

---

## 7. Test Plan

| Test | Expected |
|------|----------|
| `test_stage_1_no_posting` | Stage 1 → only read/react/view |
| `test_stage_progression` | Day 3 → auto advance to stage 2 |
| `test_graduation_criteria` | All criteria met → graduated |
| `test_flood_blocks_warmup` | FloodWait → pause, no retry |
| `test_busy_prevents_collision` | account_busy blocks concurrent DM |
| `test_behavior_weight_distribution` | Random selection matches weights |
