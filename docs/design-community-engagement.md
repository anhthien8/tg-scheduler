# Thiết kế: Community Engagement Engine (TG Scheduler)

- **Trạng thái:** Design **vòng 5** (đã sửa 5 lỗi CHẶN của review T9 + sửa cơ chế claim phạm vi vòng 4/5 + **vòng 5: counter atomic (quota>1), scope group/hour, rollback theo claim_id**) — CHỜ FOUNDER DUYỆT (chưa code, chưa commit)
- **Task Kanban:** T8 (`t_072b67b5`) → review T9 (`t_0e20acb0`) → **sửa vòng 2–5 T10 (`t_ba3819b0`)** → review vòng 3 T11 (`t_f688f734`)
- **Ngày:** 04/10/2026 (bản T8) · **cập nhật 05/10/2026 (bản T14 vòng 5, xem §12)**
- **Người viết:** architect
- **Phiên bản đề xuất khi build:** v3.8.0
- **Line number tham chiếu:** chụp/verify lại ngày 05/10/2026 trên nhánh `main` (repo `H:\Hermes\tg-scheduler`). Trước khi build phải verify lại vì file có thể đã dịch dòng.

---

## 0. Tóm tắt điều hành (đọc 30 giây)

**Mục tiêu:** account Telegram chỉ định → join nhóm chỉ định (vd nhóm support của sàn crypto) → tương tác **có giá trị thật** trong nhóm theo knowledge (nhận định TA, trả lời câu hỏi) → user tự tìm đến DM → funnel AI hiện có tiếp quản → dẫn về nhóm signal của founder.

**Triết lý (từ founder):** KHÔNG spam. Giá trị trước, kéo lead sau.

**Kết luận kỹ thuật:** ~75% hạ tầng đã có sẵn và tái dùng được. Phần phải viết mới **gồm: 3 bảng (campaign, logs, dm_map) + 1 nhánh reply trong watcher + 1 cơ chế chọn account + bộ rate-limit có claim atomic + registry trạng thái bận per-account + 1 hàm gửi công khai mới `_send_group_message`**.

**Điểm KHÔNG tái dùng được nguyên trạng (đã sửa khỏi bản T8 — xem §12):**
- `_send_group_reply` (`keyword_watcher.py:860-903`) **KHÔNG phải** public community sender độc lập: nó là helper của DM watcher, **bắt buộc** tham số `used_dm_acc_id` (nếu `None` → `return` ngay, `:875-876`), có delay cứng 1.5–4s (`:882`) và chỉ nhằm gửi 1 câu ngắn "check DM". ⇒ Phải viết hàm mới `_send_group_message` (§4.1.3/T3), không thể "tái dùng nguyên trạng".
- `_dm_global_lock` (`:202-213`) là lock **global không mang account ID** — không trả lời được câu "nick nào đang bận". ⇒ Phải thêm registry bận per-account (§4.5).
- Dedup `(chat_id, msg_id)` (`:1125-1136`) và PeerFlood/admin/self primitives (`:162-323`) **tái dùng được nguyên trạng** (đã verify).

Toàn bộ phần còn lại (join, AI, PeerFlood cooldown, dedup msg, admin-exclusion, self-id guard) tái dùng có kiểm chứng.

**Phân tầng bắt buộc:** v1 = **REACTIVE** (chỉ trả lời khi có người hỏi) — an toàn, được duyệt để thử nghiệm. v2 = **PROACTIVE** (tự đăng nhận định TA) — **rủi ro ban cao, CHƯA được duyệt**, chỉ làm sau khi v1 chạy ổn ≥2 tuần.

**Điểm vi phạm ToS cần founder biết (mục 6b & 11):** cả v1 và v2 đều vi phạm ToS Telegram ở mức độ khác nhau (v1: "unsolicited reply trong nhóm"; v2: "spam quảng cáo hàng loạt"). Không có thiết kế nào miễn nhiễm ban — chỉ có thiết kế giảm xác suất. Bắt buộc chạy trên **account phụ đã warmup**, không dùng account chính.

---

## 1. MÔ HÌNH DỮ LIỆU — entity "Engagement Campaign"

### 1.1. Quyết định: bảng MỚI `engagement_campaigns` (không nhồi vào `keyword_watchers`)

| Phương án | Ưu | Nhược | Chọn? |
|---|---|---|---|
| A. Dùng lại `keyword_watchers` + thêm cột (`mode`, `ai_agent_id`, `signal_group_link`…) | Không phải viết CRUD mới | `keyword_watchers` đã 20 cột + 4 migration ALTER; watcher = "ai nhắn gì → DM người đó", engagement = "tương tác công khai trong nhóm". Nhồi chung làm `_make_handler` phình thêm nhánh, phá vỡ ngữ nghĩa `messages[]`/`watcher_messages` | ❌ |
| B. Bảng mới `engagement_campaigns` + bảng log mới; tái dùng engine watcher làm "xe tải" bên dưới | Schema sạch, đúng ngữ nghĩa, CRUD riêng, dễ tắt/bật, không đụng code watcher hiện có (chỉ thêm 1 nhánh) | Thêm ~2 bảng + ~8 hàm DB | ✅ |
| C. Chỉ dùng `settings` (JSON blob) | Không migration | Không query được theo campaign, không log được, không UI bảng | ❌ |

→ **Chọn B.** Lý do cốt lõi: campaign cần `status` (draft/active/paused), `schedule_window`, và **join chậm (stagger)** — những thứ watcher hiện tại không có. Nhưng **engine bên dưới vẫn là `keyword_watcher`**, campaign chỉ là lớp cấu hình.

### 1.2. Schema tối thiểu

```sql
-- database.py, thêm sau khối ai_agents/agent_learned_knowledge (~dòng 950)
CREATE TABLE IF NOT EXISTS engagement_campaigns (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,

    -- Nhóm mục tiêu (nhóm support sàn crypto, nhóm community…)
    group_ids           TEXT NOT NULL DEFAULT '[]',   -- JSON list chat_id âm
    group_titles        TEXT DEFAULT '[]',            -- JSON list để hiện UI, khỏi join lại để đọc tên

    -- Account
    observer_account_ids TEXT NOT NULL DEFAULT '[]',  -- nick Ở TRONG nhóm (nhận event)
    reply_account_ids   TEXT DEFAULT '[]',            -- nick được phép trả lời (rỗng = = observer)

    -- Knowledge / AI
    ai_agent_id         INTEGER DEFAULT NULL,         -- FK logic -> ai_agents.id
    knowledge_extra     TEXT DEFAULT '',              -- knowledge bổ sung riêng campaign (TA template, chart cache)
    reply_mode          TEXT DEFAULT 'reactive',      -- 'reactive' (v1) | 'proactive' (v2, chưa mở)

    -- Trigger từ khóa (JSON list; cột này KHÔNG dùng cho proactive)
    trigger_keywords    TEXT DEFAULT '[]',

    -- Rate limit (số cụ thể — xem mục 4.1)
    max_replies_per_hour_per_group INTEGER DEFAULT 4,
    max_replies_per_day_per_group  INTEGER DEFAULT 12,
    min_gap_seconds     INTEGER DEFAULT 180,          -- khoảng cách tối thiểu giữa 2 reply cùng nhóm
    participation_min   INTEGER DEFAULT 1,            -- số nick tham gia tối thiểu mỗi event
    participation_max   INTEGER DEFAULT 1,            -- KHÔNG bao giờ cho 2 nick cùng nhóm reply 1 tin

    -- Lịch hoạt động (giờ vàng)
    schedule_start      TEXT DEFAULT '13:00',         -- HH:MM, giờ VN
    schedule_end        TEXT DEFAULT '23:00',
    active_days         TEXT DEFAULT '[1,2,3,4,5,6,7]', -- 1=Mon..7=Sun (khớp format days_of_week của schedules)
    schedule_jitter_min INTEGER DEFAULT 0,            -- PHÚT cộng ngẫu nhiên (v2 proactive mới dùng)

    -- Funnel (mục 5)
    signal_group_link   TEXT DEFAULT '',              -- link nhóm signal của founder
    signal_group_title  TEXT DEFAULT '',

    -- Trạng thái
    status              TEXT DEFAULT 'draft',         -- draft | active | paused | auto_paused
    pause_reason        TEXT DEFAULT '',               -- lý do auto-pause (mute/ban) để founder biết
    platform            TEXT DEFAULT 'telegram',


    -- CONSENT GATE (§4.8 R5) — FAIL-CLOSED, mặc định DENY
    operator_consent_confirmed INTEGER DEFAULT 0,      -- 0 = DENY: campaign KHÔNG start được
    operator_consent_at        TEXT DEFAULT NULL,      -- thời điểm operator tick checkbox
    operator_consent_by        TEXT DEFAULT '',        -- ai tick (username/label trên UI) — audit

    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS engagement_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER NOT NULL,
    group_id        INTEGER,
    group_title     TEXT,
    account_id      INTEGER,        -- nick đã reply
    reply_account_ids TEXT DEFAULT '[]', -- các nick CÓ MẶT trong nhóm tại thời điểm đó (để chống trùng)
    trigger_user_id INTEGER,        -- user đã hỏi
    trigger_msg_id  INTEGER,
    trigger_text    TEXT,           -- nội dung câu hỏi (để AI trả lời đúng ngữ cảnh)
    reply_text      TEXT,
    reply_msg_id    INTEGER,        -- message_id của reply (để nhận biết bị xoá = mute/ban)
    kind            TEXT DEFAULT 'reply',  -- reply | proactive_post | skipped | paused | join
    status          TEXT DEFAULT 'success', -- success | failed | skipped | reserved
    skip_reason     TEXT,           -- không_admin, rate_limit, khung_giờ, peerflood, trùng_nick, consent_denied…
    latency_ms      INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Bảng đếm/sổ claim (§4.1) — nguồn sự thật cho rate-limit, ghi TRƯỚC khi gửi.
-- ⚠️ TÁCH 2 BẢNG vì 2 LOẠI cửa sổ có cơ chế khác hẳn nhau (lỗi CHẶN vòng 3: 1 bảng + UNIQUE
--    trên (account_id, window_key) khiến LIMIT chỉ chứa ĐÚNG 1 claim/cửa sổ ⇒ limit 4/12/25
--    KHÔNG BAO GIỜ đạt được >1). Xem §12 vòng 5.
--
-- (A) engagement_claims — SỔ CHI TIẾT, mỗi lần claim = 1 dòng. Phục vụ cả 2 loại.
-- (B) engagement_window_counters — BỘ ĐẾM cho cửa sổ LOẠI limit; PK đúng phạm vi của cửa sổ.
--
-- Phân loại cửa sổ theo (scope, loại) — bảng ma trận đầy đủ ở §4.1.1:
--   • LOẠI "limit" = đếm số lần trong cửa sổ, ngưỡng N > 1 ⇒ BẮT BUỘC dùng COUNTER (B)
--     với atomic conditional increment: UPDATE ... SET count=count+1 WHERE count<limit;
--     dựa trên rowcount (số dòng UPDATE) để biết thắng/thua. PK của counter PHẢI khớp ĐÚNG
--     phạm vi: theo NHÓM cho trần nhóm, theo ACCOUNT cho trần toàn cục nick.
--   • LOẠI "gap" = cửa sổ TĨNH, đúng 1 dòng/key ⇒ hợp lệ với UNIQUE(account_id, window_key)
--     + so SELECT max(created_at), KHÔNG dùng COUNT (xem claim_gap() §4.1.1).
CREATE TABLE IF NOT EXISTS engagement_claims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    group_id    INTEGER,
    account_id  INTEGER NOT NULL,
    window_key  TEXT NOT NULL,      -- xem 5 loại ở §4.1.1
    created_at  TEXT DEFAULT (datetime('now')),
    kind        TEXT DEFAULT 'reply'
);

-- (B) BỘ ĐẾM cho cửa sổ LOẠI limit. PK chính là ĐỊNH DANH PHẠM VI của cửa sổ:
--   • acct_key   = account_id  ⇒ cửa sổ TOÀN CỤC THEO NICK (acct:*, PHẠM VI ACCOUNT)
--   • group_key  = group_id    ⇒ cửa sổ THEO NHÓM (group:*, PHẠM VI GROUP — tổng mọi account)
--   • acct_key   = account_id  ⇒ cửa sổ theo NHÓM+LỚP PHỤ (groupacct:*, PHẠM VI ACCOUNT)
--   • acct_key = -1 (NULL trong SQLite unique) ⇒ cửa sổ THEO NHÓM THUẦN (group:*)
-- Cột group_key/acct_key NULL được vì SQLite coi NULL khác nhau trong UNIQUE ⇒ dùng -1 thay NULL
-- khi INSERT để khoá chặt. Counter row KHÔNG xoá khi rollback, chỉ decrement (by_claim_id §4.1.1).
CREATE TABLE IF NOT EXISTS engagement_window_counters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    window_kind TEXT NOT NULL,       -- 'acct' | 'group' | 'groupacct'  (khớp tiền tố window_key)
    acct_key    INTEGER NOT NULL,    -- account_id cho acct/groupacct; -1 cho group
    group_key   INTEGER NOT NULL,    -- group_id cho group/groupacct; -1 cho acct
    window_key  TEXT NOT NULL,       -- 'acct:{a}:{day}' | 'group:{g}:{hour}' | 'groupacct:{g}:{a}:{day}'
    count       INTEGER NOT NULL DEFAULT 0,
    limit_      INTEGER NOT NULL,    -- trần áp cho cửa sổ này (chụp lại để audit)
    updated_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(window_kind, acct_key, group_key, window_key)
);

-- Cửa sổ LOẠI gap (gap:{g}:{a} và gapacct:{a}) — 1 key = 1 dòng duy nhất, chặn 2 reply sát nhau.
-- ⚠️ LIKE 'gap:%' KHÔNG khớp 'gapacct:...' (ký tự thứ 4 là 'a' chứ không phải ':') ⇒ phải liệt kê cả hai.
CREATE UNIQUE INDEX IF NOT EXISTS idx_engagement_claim_gap_unique
    ON engagement_claims(account_id, window_key)
    WHERE window_key LIKE 'gap:%' OR window_key LIKE 'gapacct:%';
CREATE INDEX IF NOT EXISTS idx_engagement_claim_acct_time
    ON engagement_claims(account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_engagement_claim_group_time
    ON engagement_claims(group_id, created_at);

-- Bộ đếm lookup nhanh theo từng phạm vi
CREATE INDEX IF NOT EXISTS idx_engagement_counter_acct
    ON engagement_window_counters(acct_key, window_key);
CREATE INDEX IF NOT EXISTS idx_engagement_counter_group
    ON engagement_window_counters(group_key, window_key);

CREATE INDEX IF NOT EXISTS idx_engagement_logs_campaign_time
    ON engagement_logs(campaign_id, created_at);
CREATE INDEX IF NOT EXISTS idx_engagement_logs_account_time
    ON engagement_logs(account_id, created_at);
```

### 1.3. Tái dùng bảng có sẵn (KHÔNG tạo bảng song song)

| Nhu cầu | Dùng bảng/hàm có sẵn | Vị trí |
|---|---|---|
| Cấu hình AI (prompt, knowledge, provider, keys, handover) | `ai_agents` | `database.py:895-914`, CRUD `routes/ai_agents.py:20-363` |
| Lịch sử hội thoại DM + funnel + tier/lead score | `ai_followup_chats` (+ `history_json`, `reply_count`, `status`, `human_takeover_at`, `stage`) | `database.py:798-812`, engine `dm_reply_tracker.py:503-877` |
| Profile lead (UID, link affiliate, vùng) | `kol_profiles` | `database.py:917-925` |
| Bài học admin dạy AI | `agent_learned_knowledge` | `database.py:941-950`, nạp tại `dm_reply_tracker.py:742-747` |
| PeerFlood cooldown (sống qua restart) | `accounts.peerflood_until` | `database.py:405` |
| Blacklist | `dm_blacklist` | `database.py:394-401` |

**Cột mới cần alter (1 dòng duy nhất, có thể bỏ qua nếu dùng bảng log riêng):**
```sql
-- Chỉ cần nếu muốn join 1 câu "lead này từ nhóm nào"
ALTER TABLE ai_followup_chats ADD COLUMN engagement_campaign_id INTEGER DEFAULT NULL;
```
→ **Quyết định: KHÔNG alter bảng đang chạy production.** `ai_followup_chats.campaign_id` **đã tồn tại sẵn** (`database.py:804`, verify trong `CREATE TABLE ai_followup_chats` tại `:798-812`) và được dùng cho **KOL campaign** — **KHÔNG** dùng nó cho engagement, và **KHÔNG** nhồi ID âm giả (bản T8 từng đề xuất ID âm — đã bỏ, xem §12). Cách đúng: dùng **bảng map riêng** `engagement_dm_map(campaign_id, account_id, user_id, created_at)` (§5.2) để gán campaign cho lead đến từ nhóm công khai, không đụng schema bảng cũ và không lẫn với KOL campaign.

### 1.4. Migration

Theo đúng pattern repo (`try/except ALTER TABLE` — xem `database.py:296-314`): tạo bảng mới bằng `CREATE TABLE IF NOT EXISTS` trong `init_db()`, không cần migration vì là bảng trắng.

---

## 2. CHẾ ĐỘ TƯƠNG TÁC — phân tầng rủi ro

### 2a. REACTIVE (v1 — KHUYẾN NGHỊ LÀM TRƯỚC)

**Định nghĩa:** Engine **chỉ** sinh reply khi một tin nhắn trong nhóm mục tiêu khớp `trigger_keywords` (vd `btc`, `eth`, `phân tích`, `chart`, `vào lệnh`, `target`, `sl`, tên coin trong list). Không có trigger → không có tin nào được gửi.

**Luồng (tái dùng `keyword_watcher._make_handler`):**
1. `events.NewMessage` trên observer nick → `chat_id` phải nằm trong `engagement_campaigns.group_ids`.
2. Khớp `trigger_keywords` → **dedup `(chat_id, msg_id)` atomic** (đã có: `_seen_msg_ids`, `keyword_watcher.py:1132-1136`) ⇒ nhiều nick trong cùng nhóm không xử lý 2 lần.
3. Lọc bỏ: bot (`keyword_watcher.py:945`), **admin/bot của nhóm** (`_get_group_admin_ids`, `keyword_watcher.py:279-323`), account nội bộ của mình (`_get_self_user_ids`, `:162-197`), blacklist (`:990`), ngoài khung giờ.
4. Kiểm rate limit mới (§4.1) → nếu vượt, ghi log `status='skipped'`, `skip_reason='rate_limit'`, **không gửi**.
5. Chọn **1 nick** để trả lời bằng `random.choice(reply_account_ids đang online)` — xem §4.3.
6. Gọi AI: `ai_rmx.generate_chat_response([system=agent.system_prompt + knowledge + format_rules_community, user=nội dung câu hỏi + context 5 tin gần nhất], agent.provider, keys)` (hàm có sẵn `ai_remix.py:494-568`, kèm fallback đa provider sẵn có).
7. Delays & typing: `random` 20–90s trước khi gửi, typing simulation có sẵn (`keyword_watcher.py:627-635` dùng `client.action(peer, "typing")`).
8. Gửi reply **trong nhóm, reply_to đúng msg_id người hỏi** — **hàm MỚI `_send_group_message(event, text, account_id, reply_to)`** (viết trong T3, §12). **KHÔNG tái dùng `_send_group_reply`**: đó là helper của DM watcher, **bắt buộc** `used_dm_acc_id` (None ⇒ return ngay, `keyword_watcher.py:875-876`), delay cứng 1.5–4s (`:882`), và chỉ để gửi 1 câu "check DM" — không phải public community sender. Acceptance criteria của `_send_group_message` xem §4.1.3.
9. Ghi `engagement_logs`.
10. **KHÔNG DM user trong v1.** User tự bấm vào profile nick → nhắn riêng → funnel hiện có tiếp quản (§5).

**Vì sao an toàn hơn:**
- Nội dung do người khác khởi xướng → không phải "unsolicited broadcast".
- Tần suất tự nhiên bằng tần suất câu hỏi thật trong nhóm.
- Reply có `reply_to` → Telegram và admin nhìn thấy đây là trả lời, không phải spam rải.

**Guardrails riêng v1:** xem §4 (đủ 7 nhóm guardrail, mọi ngưỡng là số cụ thể).

### 2b. PROACTIVE (v2 — RỦI RO CAO, CHƯA DUYỆT)

**Định nghĩa:** Engine **tự đăng** 1 nhận định TA mới vào nhóm theo lịch thưa (1–2 post/ngày/nhóm), không cần ai hỏi.

**Guardrails bắt buộc nếu founder vẫn muốn làm (không có ngoại lệ):**
1. **Trần cứng 2 post/ngày/nhóm/account** và **1 post/2 giờ** tối thiểu khoảng cách (`max_replies_per_day_per_group` dùng chung).
2. **Giờ ngẫu nhiên trong cửa sổ** `schedule_start..schedule_end` + `schedule_jitter_min` (vd post rơi vào 14:07, 20:43 chứ không phải 14:00, 20:00). **KHÔNG BAO GIỜ** post đúng phút tròn.
3. **Tối đa 1 nick của mình post trong 1 nhóm/ngày** — 2 nick cùng đăng nhận định trong 1 nhóm = dấu hiệu bot rõ nhất. Ép bằng `participation_max=1`/ngày.
4. **Nội dung phải thực sự khác nhau**: bắt buộc remix qua AI + chèn số liệu/kline thật tại thời điểm post (nếu chỉ đổi vài chữ → Telegram fingerprint giống nhau). Bắt buộc đọc lại 20 tin gần nhất của nhóm và **bơm vào prompt** để AI không lặp ý người khác vừa nói.
5. **Chỉ chạy trên nick đã warmup ≥ 14 ngày** (xem `warmup_engine.py`, `routes/warmup.py:34-41` về job warmup theo `daily_post_limit`) và **nick không dùng cho DM mass**.
6. **Kill-switch toàn cục** trong Settings: `engagement_proactive_enabled` (mặc định `0`) + kill-switch theo campaign.
7. **Dry-run mode bắt buộc 3 ngày đầu**: sinh nội dung, ghi `engagement_logs(kind='proactive_post', status='skipped', skip_reason='dry_run')`, founder đọc trong UI rồi mới bật thật.
8. Auto-pause theo §4.7 + nếu tỷ lệ tin bị xoá/flag > 0 thì tự tạm dừng campaign và gửi alert.

**Khuyến nghị của architect:** **KHÔNG build v2 trong đợt này.** v1 đã đủ để bắt đầu thu lead. v2 để riêng một task sau khi có số liệu v1 (tỷ lệ lead về, tỷ lệ account bị mute).

---

## 3. KNOWLEDGE / AI

### 3.1. Agent nào?

Dùng lại **đúng hệ `ai_agents`** (`database.py:895-914`). Campaign trỏ `ai_agent_id` → 1 agent, ví dụ tạo agent mới tên **"Community TA Analyst"**, khác hoàn toàn với agent "Crypto Exchange BD Pro" (agent BD nói chuyện chốt deal 1-1, KHÔNG phù hợp nói trong nhóm công khai — sẽ lộ giọng "sales" ngay).

| Trường agent | Giá trị cho Community TA Analyst |
|---|---|
| `provider` / `model` / `api_keys_json` | Gemini/Groq/OpenAI-compatible (dùng chung key hệ thống; `_get_system_ai_config()` `routes/ai_agents.py:57-97`) |
| `tone` | `analytical` |
| `max_replies` | dùng cho funnel DM, không ảnh hưởng group reply |
| `handover_keywords` | `["gặp admin", "tư vấn riêng", "inbox riêng", "số điện thoại"]` |
| `knowledge_base` | Kiến thức TA + disclaimer + quy tắc cấm shill (§3.2) |

### 3.2. Khung prompt (output = nhận định giá trị, KHÔNG shill)

Khác với `format_rules` của funnel DM (rất dài, 11 mục, ở `dm_reply_tracker.py:679-721`), prompt group phải **ngắn, khô, có số**:

```
Bạn là trader phân tích kỹ thuật (TA) có kinh nghiệm, đang trả lời trong một nhóm
cộng đồng crypto công khai. Người đọc là trader bình thường, không phải khách hàng.

QUY TẮC BẮT BUỘC:
1. Trả lời ĐÚNG câu hỏi được nêu. Không mở bài dài dòng, không chào hỏi xã giao.
2. Đưa NHẬN ĐỊNH có thể kiểm chứng: vùng hỗ trợ/kháng cự cụ thể, khung thời gian
   (4H/1D), xu hướng, mốc invalidation. Nếu thiếu dữ liệu thì nói rõ "cần xem
   lại khung X" chứ KHÔNG bịa số.
3. TUYỆT ĐỐI KHÔNG: quảng cáo sàn/broker, xin link, mời vào nhóm, khoe lợi nhuận,
   hứa hẹn lợi nhuận, dùng từ "cam kết", "chắc chắn", "x100", "all-in".
4. KHÔNG nêu tên sàn, mã giới thiệu, link affiliate, hay link nhóm riêng trong
   câu trả lời trong nhóm công khai.
5. Luôn đóng bằng 1 câu rủi ro ngắn, khô: "Đây là góc nhìn kỹ thuật, không phải
   lời khuyên đầu tư."  (biến thể ngẫu nhiên, không lặp y hệt cùng câu chữ)
6. Trả lời NGẮN: tối đa 3-5 câu, không markdown nặng, không bullet dài.
7. Ngôn ngữ: khớp ngôn ngữ câu hỏi (Việt → Việt, Anh → Anh).
8. Nếu câu hỏi không liên quan crypto/TA (chào hỏi, drama, hỏi giá, hỏi admin):
   KHÔNG trả lời (trả về đúng token <SKIP>).
```

**Nguồn dữ liệu TA:** prompt chỉ có knowledge tĩnh + câu hỏi + 5-10 tin gần nhất. Muốn "nhận định có số" đúng thì phải **nhét giá thật** vào prompt. Đề xuất: dùng `ai_remix`/`httpx` gọi 1 endpoint giá công khai (Binance/CoinGecko public, không cần key) với timeout 3s, cache 5 phút/coin, chèn `BTC 62,340 | 24h -1.2%` vào prompt. **Nếu không có dữ liệu giá → trả về `<SKIP>`** thay vì bịa.

### 3.3. Chống lặp & chống 2 account tự nói chuyện với nhau

Đây là rủi ro **đặc thù và nguy hiểm nhất** của tính năng này. 4 lớp phòng vệ:

| Lớp | Cơ chế | Vị trí |
|---|---|---|
| L1 — Không bao giờ 2 nick reply 1 tin | Dedup `(chat_id, msg_id)` đã có sẵn, **claim atomic không await** trước mọi await ⇒ handler của nick B thấy key đã claim → return | `keyword_watcher.py:1126-1136` (tái dùng y nguyên) |
| L2 — Nick mình không bao giờ là "người hỏi" | `_get_self_user_ids()` lọc bỏ sender là account nội bộ — đã có | `keyword_watcher.py:952-957`, `dm_reply_tracker.py:885-927` |
| L3 — Chống trùng nội dung | Lưu hash (sha256 16 byte) của 5 reply gần nhất **per nhóm** (in-mem TTL 24h, mirror vào `engagement_logs`). Trước khi gửi: so similarity (tỉ lệ token chung ≥ 0.65) với 5 reply gần nhất ⇒ sinh lại tối đa 2 lần ⇒ vẫn trùng thì `<SKIP>` | **MỚI** — ~60 dòng, dùng `difflib.SequenceMatcher` (stdlib) |
| L4 — Cooldown per (nick, nhóm) | 1 nick trả lời cùng 1 nhóm tối đa `max_replies_per_hour_per_group`; và `min_gap_seconds` (mặc định 180s) giữa 2 reply cùng nhóm của **cùng nick** | **MỚI** — bộ đếm in-mem + đọc `engagement_logs` khi start |

**Bổ sung L3b — "không tự tiếp lời chính mình":** nếu tin nhắn khớp keyword **là tin của một trong các account của mình** (đã bị L2 chặn) hoặc **reply_to một tin của account mình** → skip. Điều này chặn trường hợp nick A post "BTC đang test 62k", nick B (cũng của mình) "phân tích BTC". Rule: **không reply nếu message đích là con của tin do mình gửi** — cần thêm 1 check `event.reply_to_msg_id` tra `engagement_logs.reply_msg_id`.

---

## 4. CHỐNG PHÁT HIỆN & AN TOÀN (ƯU TIÊN #1)

### 4.1. Rate limit — SỐ CỤ THỂ

| Ngưỡng | Giá trị mặc định | Ghi chú |
|---|---|---|
| Reply/nhóm/giờ | **≤ 4** (`max_replies_per_hour_per_group`) | Trần cứng trong code, không cho UI đặt > 6 |
| Reply/nhóm/ngày | **≤ 12** | Trần cứng 20 |
| Reply toàn cục/ngày (mọi nhóm, 1 nick) | **≤ 25** | MỚI — chống "nick này chỉ đi trả lời khắp nơi" |
| Khoảng cách 2 reply cùng nhóm | **≥ 180s** (`min_gap_seconds`) | random 180–600s |
| Khoảng cách 2 reply cùng nick (toàn cục) | **≥ 120s** | random 120–300s |
| Số nick tham gia 1 event | **đúng 1** (`participation_min = participation_max = 1`) | KHÔNG random nhiều nick cho cùng 1 tin |
| Join account mới vào nhóm | **stagger 15–45 phút/account** (xem §6) | không join đồng loạt |

Lý do chọn các số này: nhóm crypto support sôi động có ~50–300 tin/giờ; 1 nick reply 4 lần/giờ ≈ 1.3–8% lượng tin → hoàn toàn tự nhiên. Nếu > 6/giờ, nick bắt đầu giống bot trả lời tự động (nhất là khi mọi reply đều sâu 3-5 câu).

#### 4.1.1. CLAIM ATOMIC — sửa lỗi race (lỗi CHẶN #1 của review T9)

**Vấn đề của bản T8:** thiết kế cũ "đọc log khi start + counter RAM, ghi log SAU send". Khi nhiều handler (nhiều observer nick cùng nhóm, hoặc nhiều event dồn) chạy song song, tất cả cùng đọc `count=3 < 4`, cùng qua check, rồi mới ghi log → **vượt trần**. Tương tự cho trần ngày và trần account-global.

**Nguyên tắc sửa: CLAIM/RESERVE trước, SEND sau, luôn ở cùng 1 chỗ (bảng `engagement_claims`, §1.2).**

Mọi ngưỡng đều quy về **1 cửa sổ (window_key)**. **Lỗi CHẶN vòng 3 đã sửa:** một câu `INSERT` + `UNIQUE(account_id, window_key)` **chỉ** biểu diễn được quota = 1 (mỗi cửa sổ đúng 1 claim) ⇒ **không bao giờ** đạt limit 4/12/25. Vì vậy cơ chế **tách theo LOẠI cửa sổ**:

- **Cửa sổ LOẠI `limit`** (4/giờ/nhóm · 12/ngày/nhóm/nick · 25/ngày/nick-toàn-cục): dùng **counter row + atomic conditional increment**. Mỗi lần claim chạy `UPDATE engagement_window_counters SET count=count+1 WHERE <đúng phạm vi> AND window_key=? AND count < limit`, rồi đọc **rowcount** (`cursor.rowcount`): `1` ⇒ thắng (đã giữ slot), `0` ⇒ đã chạm trần ⇒ `rate_limit`. Counter chưa tồn tại thì `INSERT` row `count=0` trước (nằm trong **cùng transaction** với UPDATE, `BEGIN IMMEDIATE`, busy-timeout 5000 + WAL — pattern repo đã dùng cho `ai_followup_chats` upsert). **Không** dùng `UNIQUE(account_id, window_key)` cho loại limit nữa.
- **Cửa sổ LOẠI `gap`** (`gap:{g}:{a}` ≥180s · `gapacct:{a}` ≥120s): key **TĨNH**, đúng 1 dòng/key ⇒ giữ `UNIQUE(account_id, window_key)` (`idx_engagement_claim_gap_unique`, §1.2) và chặn bằng `SELECT max(created_at)` — **KHÔNG** dùng COUNT, **KHÔNG** dùng counter.

Mọi claim (cả 2 loại) vẫn ghi thêm 1 dòng vào **sổ chi tiết `engagement_claims`** để truy vết; counter là nguồn sự thật của trần, `engagement_claims` là log + nguồn gap. RAM không phải nguồn sự thật — RAM chỉ là cache đọc nhanh cho UI.

**Bảng ma trận cửa sổ (nguồn sự thật cho scope/loại/ngưỡng):**

| window_key | scope | loại | ngưỡng | cơ chế claim | vị trí |
|---|---|---|---|---|---|
| `acct:{account_id}:{day}` | **ACCOUNT** (mọi nhóm) | limit | ≤25/ngày/nick | counter PK `(window_kind='acct', acct_key=account_id, group_key=-1, window_key)` | §4.1.1 |
| `groupacct:{group_id}:{account_id}:{day}` | **ACCOUNT** (trong 1 nhóm) | limit | ≤12/ngày/nhóm/nick | counter PK `(window_kind='groupacct', acct_key=account_id, group_key=group_id, window_key)` | §4.1.1 |
| `group:{group_id}:{hour}` | **GROUP** (tổng mọi account trong nhóm) | limit | ≤4/giờ/**nhóm** | counter PK `(window_kind='group', acct_key=-1, group_key=group_id, window_key)` | §4.1.1 |
| `gap:{group_id}:{account_id}` | ACCOUNT (trong 1 nhóm) | gap | ≥180s | `max(created_at)` + UNIQUE `(account_id, window_key)` | §4.1.1 |
| `gapacct:{account_id}` | **ACCOUNT** (mọi nhóm) | gap | ≥120s | `max(created_at)` + UNIQUE `(account_id, window_key)` | §4.1.1 |

> **Phân biệt scope — cốt lõi lỗi CHẶN vòng 3 #2:** trần `group:` là **tổng của MỌI account trong nhóm**, không phải "mỗi account 4/giờ". Nếu counter của `group:` lại khoá theo `account_id` thì 5 account cùng nhóm có 5 ngân sách riêng (⇒ 20 reply/giờ/nhóm) — **SAI**. Vì vậy counter `group:` **BẮT BUỘC** khoá theo `group_id` (`acct_key=-1`, `group_key=group_id`), bất kể account nào claim. Ngược lại `acct:`/`gapacct:` là trần **toàn cục theo nick** ⇒ khoá theo `account_id`, không quan tâm group.

```python
# Cửa sổ (window_key) — sinh từ timestamp UTC, KHÔNG phụ thuộc RAM.
# build_windows() trả list[(window_key, kind, scope, limit, min_gap)]:
#   kind : 'limit' (counter) | 'gap' (max(created_at))
#   scope: 'account' | 'group'   ← QUYẾT ĐỊNH khoá counter (lỗi vòng 3 #2)
#   acct_key/group_key suy ra từ scope, xem _counter_key()
WINDOW_HOUR = "group:{group_id}:{hour}"                     # [limit, GROUP,  ≤4]
WINDOW_DAY_GROUP_ACCT = "groupacct:{group_id}:{account_id}:{day}"  # [limit, ACCOUNT, ≤12]
WINDOW_ACCT_GLOBAL = "acct:{account_id}:{day}"              # [limit, ACCOUNT, ≤25]
WINDOW_GAP_GROUP = "gap:{group_id}:{account_id}"            # [gap, ≥180s]
WINDOW_GAP_ACCT = "gapacct:{account_id}"                    # [gap, ≥120s]

def build_windows(campaign, group_id, account_id, now=None):
    """Trả list[(window_key, kind, scope, limit, min_gap)] — ĐÚNG thứ tự kiểm (xem dưới)."""
    now = now or time.time()
    hour = time.strftime("%Y%m%dT%H", time.gmtime(now))
    day  = time.strftime("%Y%m%d",     time.gmtime(now))
    return [
        # (window_key,                     kind,    scope,     limit, min_gap)
        (f"acct:{account_id}:{day}",                "limit", "account", 25, 0),   # #1 toàn cục nick
        (f"groupacct:{group_id}:{account_id}:{day}","limit", "account", 12, 0),   # #2 nhóm×nick
        (f"group:{group_id}:{hour}",                "limit", "group",    4, 0),   # #3 NHÓM (tổng!)
        (f"gap:{group_id}:{account_id}",            "gap",   "account", None, campaign["min_gap_seconds"]),
        (f"gapacct:{account_id}",                   "gap",   "account", None, 120),
    ]

def _counter_key(scope, group_id, account_id, kind):
    """Định danh PHẠM VI cho counter row. ĐÂY là chỗ sửa lỗi vòng 3 #2 (scope group/hour)."""
    if scope == "group":                 # group:{g}:{hour}  ⇒ tổng MỌI account trong nhóm
        return dict(window_kind="group",     acct_key=-1,          group_key=group_id)
    if kind == "acct":                   # hiện chỉ acct: dùng nhánh này
        return dict(window_kind="acct",      acct_key=account_id,  group_key=-1)
    return dict(window_kind="groupacct", acct_key=account_id,  group_key=group_id)

async def claim_limit(campaign_id, group_id, account_id, window_key,
                      limit, scope) -> tuple[bool, str, int | None]:
    """Cửa sổ LOẠI limit — COUNTER + atomic conditional increment (KHÔNG UNIQUE 1-dòng).

    Trả (True, "", counter_id) nếu THẮNG (đã giữ 1 slot), (False, "rate_limit", None) nếu chạm trần.
    Toàn bộ trong 1 transaction ⇒ 2 handler đua nhau chỉ 1 UPDATE có rowcount=1.
    """
    key = _counter_key(scope, group_id, account_id, window_key.split(":")[0])
    async with db.transaction():                      # BEGIN IMMEDIATE, busy_timeout=5000
        # 1) đảm bảo counter row tồn tại (idempotent, CÙNG transaction)
        await db.execute(
            "INSERT OR IGNORE INTO engagement_window_counters"
            "(window_kind, acct_key, group_key, window_key, count, limit_) "
            "VALUES(?,?,?,?,0,?)",
            key["window_kind"], key["acct_key"], key["group_key"], window_key, limit)
        # 2) tăng CÓ ĐIỀU KIỆN — chỉ thành công khi còn slot. rowcount quyết thắng/thua.
        cur = await db.execute(
            "UPDATE engagement_window_counters SET count=count+1, updated_at=datetime('now') "
            "WHERE window_kind=? AND acct_key=? AND group_key=? AND window_key=? "
            "  AND count < ?",                            # ← atomic guard duy nhất
            key["window_kind"], key["acct_key"], key["group_key"], window_key, limit)
        if cur.rowcount != 1:                          # đã chạm trần ⇒ đua thua
            return False, "rate_limit", None
        # 3) ghi SỔ CHI TIẾT (truy vết + nguồn rollback theo claim_id)
        claim_id = await db.execute_insert(
            "INSERT INTO engagement_claims(campaign_id,group_id,account_id,window_key) "
            "VALUES(?,?,?,?)", campaign_id, group_id, account_id, window_key)
    return True, "", claim_id

async def claim_gap(campaign_id, group_id, account_id, window_key, min_gap) -> tuple[bool, str, int | None]:
    """Cửa sổ LOẠI gap: key TĨNH (không thành phần thời gian). Lưu trong `engagement_claims`
    (KHÔNG dùng counter): partial UNIQUE (account_id, window_key) + so max(created_at).
    Nếu lần claim gần nhất cách đây < min_gap ⇒ chặn; ngược lại UPSERT claim mới (reset đồng hồ).
    KHÔNG dùng COUNT, KHÔNG để key tĩnh chặn vĩnh viễn (§12 vòng 3).

    TRẢ VỀ `(ok, reason, claim_id)`: khi THẮNG, `claim_id` = id dòng gap VỪA INSERT — nguồn duy
    nhất để rollback theo ID (§4.1.4, vòng 6). Khi THUA (chưa qua min_gap hoặc đua UNIQUE):
    `claim_id=None` ⇒ KHÔNG có gì để nhả, handler không cần release."""
    async with db.transaction():
        row = await db.fetchone(
            "SELECT max(created_at) AS last FROM engagement_claims "
            "WHERE account_id=? AND window_key=?", account_id, window_key)
        now = time.time()
        if row and row["last"]:
            last_ts = datetime.strptime(row["last"], "%Y-%m-%d %H:%M:%S")
            last_ts = last_ts.replace(tzinfo=timezone.utc).timestamp()
            if now - last_ts < min_gap:
                return False, "rate_limit", None
            # đã qua min_gap ⇒ claim mới: xoá dòng gap cũ của key (giữ 1 dòng/key) rồi INSERT.
            await db.execute(
                "DELETE FROM engagement_claims WHERE account_id=? AND window_key=?",
                account_id, window_key)
        try:
            claim_id = await db.execute_insert(   # ← BẮT id dòng VỪA insert (dùng để rollback theo ID)
                "INSERT INTO engagement_claims(campaign_id,group_id,account_id,window_key) "
                "VALUES(?,?,?,?)", campaign_id, group_id, account_id, window_key)
        except sqlite3.IntegrityError:            # đua thua partial UNIQUE (acct_id,window_key)
            return False, "rate_limit", None
    return True, "", claim_id
```

**Thứ tự claim trong handler (đặt TRƯỚC mọi `await` network / trước `_send_group_message`):**

```python
claimed = []   # list[(claim_id, counter_id|None, is_gap)] — ĐỦ ĐỂ ROLLBACK THEO ID (§4.1.4)
try:
    for wkey, kind, scope, lim, min_gap in build_windows(campaign, group_id, account_id):
        if kind == "gap":                                             # cửa sổ gap
            ok, reason, gap_id = await claim_gap(
                campaign["id"], group_id, account_id, wkey, min_gap)
            if ok:
                claimed.append((gap_id, None, True))                  # gap: giữ ID dòng vừa insert để nhả theo ID
            # nếu thua (chưa qua min_gap / đua UNIQUE): gap_id=None ⇒ không có gì để nhả
        else:                                                         # cửa sổ limit → COUNTER
            ok, reason, claim_id = await claim_limit(
                campaign["id"], group_id, account_id, wkey, lim, scope)
            if ok:
                claimed.append((claim_id, _counter_row_id(wkey, scope, group_id, account_id), False))
        if not ok:
            log_skip(reason)
            release_claims(claimed)          # nhả ĐÚNG những gì lượt này đã giữ (§4.1.4)
            return
except Exception:
    release_claims(claimed); return          # lỗi giữa chừng ⇒ nhả đúng claim của lượt này
```

Chỉ sau khi **cả 5 cửa sổ đều claim thành công** mới đi tiếp: chọn nick, gọi AI, delay, `_send_group_message`. Nếu gửi lỗi → `UPDATE engagement_logs` bản ghi claim sang `status='failed'`; **slot KHÔNG được nhả** khi đã gửi ra ngoài (giữ trần an toàn), nhưng nếu lỗi **trước khi** gọi network thì nhả claim **theo đúng claim_id** (`release_claims()`, §4.1.4) để không tự bóp chết campaign.

**5 cửa sổ claim cho 1 reply (đúng thứ tự + scope):**
1. `acct:{account}:{day}` — LOẠI limit, **scope ACCOUNT**, ≤25 — trần toàn cục per nick/mọi nhóm (§4.1.2)
2. `groupacct:{group}:{account}:{day}` — LOẠI limit, **scope ACCOUNT**, ≤12
3. `group:{group}:{hour}` — LOẠI limit, **scope GROUP (tổng mọi account trong nhóm)**, ≤4
4. `gap:{group}:{account}` — **LOẠI gap**, `min_gap_seconds` (≥180s) cùng nhóm/cùng nick
5. `gapacct:{account}` — **LOẠI gap**, 120s cùng nick trên MỌI nhóm (§4.1.2; trước đây chỉ có filter RAM ở §4.3 — nay đưa vào claim để sống qua restart)

> **Vì sao cửa sổ gap KHÔNG dùng COUNT (đây là lỗi reviewer T9 vòng 2 bắt được):** key gap là **tĩnh**, nếu chặn bằng `COUNT(*) >= 1` trên UNIQUE index thì sau reply ĐẦU TIÊN trong `(group, account)`, key đó **không bao giờ insert lại được** ⇒ mọi reply sau bị chặn **VĨNH VIỄN**, không phải "chỉ trong min_gap_seconds". Cách đúng: `claim_gap()` đọc `max(created_at)` của key, chỉ chặn khi `now - max(created_at) < min_gap`; khi đã qua gap thì **xoá dòng gap cũ của key rồi INSERT lại** (key tĩnh được TÁI DÙNG hợp lệ). Dòng gap chỉ dọn như claim cũ ở §4.1.2 (job >2 ngày) — nhưng nó **không** tự nhả sau 2 ngày như trần; nó nhả **ngay** ở lần claim kế khi đã qua `min_gap`, vì `claim_gap()` tự xoá+insert.

> **Vì sao cửa sổ LIMIT dùng COUNTER chứ KHÔNG dùng UNIQUE 1-dòng (lỗi CHẶN vòng 3 #1):** `UNIQUE(account_id, window_key)` buộc mỗi cửa sổ có **đúng 1** dòng ⇒ quota biểu diễn được **chỉ là 1**. Limit 4/12/25 là quota **> 1** ⇒ phải đếm. `claim_limit()` dùng `UPDATE ... SET count=count+1 WHERE count < limit` rồi đọc `rowcount` (`1`=thắng, `0`=chạm trần) — **atomic**, không có khe hở đọc-rồi-ghi. Test "25 lần thành công" (§4.1.1) nay **khớp** schema: counter `acct:7:{day}` đạt `count=25`, lần 26 `rowcount=0` ⇒ chặn.

> **Vì sao `group:{group}:{hour}` khoá counter theo `group_id`, KHÔNG theo `account_id` (lỗi CHẶN vòng 3 #2):** yêu cầu là **tổng ≤4 reply/giờ/NHÓM** (mọi nick cộng lại). Nếu counter lại khoá theo `account_id` thì 5 nick cùng nhóm có 5 ngân sách riêng ⇒ tối đa 20 reply/giờ/nhóm — trái yêu cầu. Vì vậy counter `group:` dùng `acct_key=-1, group_key=group_id`; mọi nick trong nhóm cùng đập vào **1 dòng** ⇒ đúng tổng. Hai trần còn lại (`groupacct:` ≤12/ngày/nhóm/nick và `acct:` ≤25/ngày/nick-toàn-cục) là **scope ACCOUNT** ⇒ khoá theo `account_id`. Xem bảng ma trận ở đầu §4.1.1.

> **Hệ quả cố ý (an toàn):** vì counter `group:` chỉ khoá theo `group_id` (không campaign), cửa sổ `group:{g}:{hour}` **dùng chung** cho MỌI campaign chạy trong cùng nhóm đó ⇒ 2 campaign cùng nhóm chia sẻ ngân sách ≤4/giờ (bảo thủ, đúng ý "1 nhóm không nhận quá 4 reply/giờ từ cụm nick của mình"). Nếu sau này muốn tách ngân sách theo campaign thì thêm `campaign_id` vào `window_key`/`_counter_key` (vd `group:{campaign}:{group}:{hour}`), KHÔNG đổi cơ chế counter.

#### 4.1.4. ROLLBACK THEO CLAIM ID (lỗi CHẶN vòng 3 #3)

**Vấn đề:** bản vòng 2–4 nhả slot bằng `DELETE FROM engagement_claims WHERE ... window_key ...`. Sau khi chuyển sang **nhiều claim/cửa sổ**, xoá theo `window_key` có thể **xoá mất claim hợp lệ của lượt khác** (vd 2 lượt cùng `(account, group, giờ)` khi trần > 1 — xoá theo key là xoá cả 2, trong đó 1 cái đã gửi thành công). Với counter thì càng nguy hiểm: `DELETE` counter row sẽ **reset cả bộ đếm** của cửa sổ đó.

**Luật rollback (chỉ áp dụng cho lỗi TRƯỚC khi gọi network):**
1. `claim_limit()` **trả về `claim_id`** (id dòng vừa INSERT vào `engagement_claims`) và ghi nhớ `counter_id` của dòng counter đã tăng.
2. Handler giữ list `claimed = [(claim_id, counter_id, is_gap)]` **chỉ của lượt này**; `release_claims(claimed)` xử lý:
   - **Counter (loại limit):** `UPDATE engagement_window_counters SET count=count-1 WHERE id=? AND count>0` — **decrement, KHÔNG xoá row** (giữ row để window khác của cùng scope không bị reset). Chỉ decrement counter **mà lượt này đã tăng** (`counter_id`), không đụng counter khác.
   - **Sổ chi tiết (loại limit):** `DELETE FROM engagement_claims WHERE id=?` — **đúng `claim_id`** của lượt này, không `WHERE window_key`.
   - **Gap:** `DELETE FROM engagement_claims WHERE id=?` — **đúng `claim_id`** của dòng gap lượt này vừa insert (`claim_gap()` trả ID, §4.1.1 vòng 6). **KHÔNG xoá theo `(account, window_key)`** (đó vẫn là xoá theo key — bug bản vòng 5 #3 còn sót). Vì mỗi lượt thắng gap tạo **đúng 1 dòng mới** với ID riêng, xoá theo ID là an toàn tuyệt đối kể cả khi `claim_gap()` đã DELETE+INSERT lại key.
3. **KHÔNG BAO GIỜ** rollback bằng `DELETE ... WHERE window_key ...` (mọi họ khoá, cả limit lẫn gap) — đó chính là lỗi làm mất claim hợp lệ.
4. Nếu **đã gọi network** (đã `_send_group_message`): **KHÔNG nhả slot dù gửi lỗi** (giữ trần an toàn) — chỉ ghi `engagement_logs(status='failed')`.

```python
async def release_claims(claimed: list) -> None:
    """Nhả ĐÚNG những gì lượt này đã giữ (theo claim_id/counter_id), KHÔNG đụng lượt khác.
    Chỉ gọi khi lỗi TRƯỚC khi gọi network. Mọi nhánh xoá CHỈ theo id vừa tạo — không theo key."""
    async with db.transaction():
        for claim_id, counter_id, is_gap in claimed:
            if is_gap:
                # gap: xoá ĐÚNG id dòng vừa insert; nếu claim thua thì claim_id=None ⇒ không có gì để nhả
                if claim_id:
                    await db.execute("DELETE FROM engagement_claims WHERE id=?", claim_id)
                continue
            # loại limit: xoá ĐÚNG dòng sổ của lượt này, rồi decrement ĐÚNG counter đã tăng
            if claim_id:
                await db.execute("DELETE FROM engagement_claims WHERE id=?", claim_id)
            if counter_id:
                await db.execute(
                    "UPDATE engagement_window_counters SET count=count-1, updated_at=datetime('now') "
                    "WHERE id=? AND count>0", counter_id)          # decrement, KHÔNG DELETE row
```

**Test concurrency bắt buộc (bổ sung vào T3/T4):** `asyncio.gather(*(handle_same_event(i) for i in range(10)))` với 10 event **cùng nhóm, cùng nick, cùng giờ** → assert **đúng 1** claim thành công và **9** bản ghi log `skip_reason='rate_limit'`; tổng reply gửi = 1. Lặp cho 5 nick khác nhau cùng nhóm → tổng ≤4/giờ. Test đua 2 tiến trình (`multiprocessing`) trên SQLite để chứng minh khoá không chỉ đúng trong 1 event loop.

**Test concurrency CÔ LẬP COUNTER — còn đúng 1 slot (bắt buộc — kiểm #c, vòng 6):** test toàn pipeline ở trên **chưa chứng minh counter atomic**: kết quả "1 thắng" có thể chỉ do **partial UNIQUE của cửa sổ gap** (2 lượt cùng `(account, group)` chạm `gap:`/`gapacct:` thì UNIQUE chặn một cái, bất kể counter có đúng hay không), hoặc do cửa sổ khác chặn chứ không phải `claim_limit()`. Phải có **một test chỉ đập vào `claim_limit()`**, tách hoàn toàn khỏi gap:

- **Cô lập:** gọi **trực tiếp** `claim_limit(...)`; **KHÔNG** đi qua handler, **KHÔNG** build/call `claim_gap()`, **KHÔNG** tạo dòng gap nào. Dùng 1 cửa sổ limit đơn lẻ, vd `acct:{account}:{day}` (scope ACCOUNT, ≤25) — nơi duy nhất chen nhau là `claim_limit`.
- **Seed về đúng 1 slot:** `INSERT INTO engagement_window_counters(window_kind,acct_key,group_key,window_key,count,limit_) VALUES('acct',7,-1,'acct:7:{day}',24,25)` (tức `count = limit - 1 = 24`). Assert tiền đề `count` = 24 trước khi chạy.
- **Đồng thời nhiều coroutine:** `await asyncio.gather(*(claim_limit(campaign, group, 7, 'acct:7:'+day, 25, 'account') for _ in range(10)))` — 10 coroutine **cùng gọi đồng thời** trên **cùng 1 slot**.
- **Assert kết quả:** **ĐÚNG 1** trả `(True, "", claim_id)`; **9** còn lại trả `(False, "rate_limit", None)`. `len(rows_success) == 1`.
- **Assert counter không vượt:** `SELECT count FROM engagement_window_counters WHERE window_key='acct:7:'||{day}` = **25** (== limit, **KHÔNG** 26+). Đây là **assert cốt lõi**: chỉ `UPDATE ... WHERE count<limit` + `rowcount` mới bảo đảm điều này; nếu là read-then-write (hoặc thiếu guard trong transaction) thì ≥2 coroutine cùng đọc 24 rồi cùng ghi 25 ⇒ count=25 nhưng **cả 2 thắng** ⇒ test bắt được (đếm được 2 claim).
- **Assert sổ chi tiết có đúng 1 dòng mới:** `SELECT COUNT(*) FROM engagement_claims WHERE window_key='acct:7:'||{day}` = 1.
- **Bản 2 tiến trình:** lặp kịch bản này với 2 process (`multiprocessing`) cùng trỏ 1 file SQLite, seed 24, mỗi process 1 `claim_limit` → **chỉ 1 process thắng**, final `count=25` (chứng minh transaction/UPDATE atomic vắt qua ranh giới process, không chỉ trong 1 event loop).
- **Phân biệt:** nếu bỏ `AND count < ?` khỏi `UPDATE` (hoặc tách đọc/ghi) ⇒ test fail (2 thắng / count=26) — đúng bản chất lỗi reviewer T15 nêu. Test này **độc lập** với test gap ở §4.1.1 (e)/(a)-(d); kết quả của nó **không thể** do UNIQUE gap giải thích.

**Test trần TOÀN CỤC theo nick (bắt buộc — kiểm #a "25 qua, 26 chặn"):** dựng **3 nhóm khác nhau**, cùng 1 nick 7, cùng ngày → gửi lần lượt 25 reply (rải qua 3 nhóm) → cả 25 **thành công**; reply thứ **26** (ở nhóm bất kỳ) bị chặn `skip_reason='rate_limit'` bởi cửa sổ `acct:7:{day}`. Assert `SELECT count FROM engagement_window_counters WHERE window_kind='acct' AND acct_key=7 AND window_key='acct:7:'||{day}` = **25** (chứng minh counter đếm gộp mọi nhóm). Đây là test **phân biệt** bản sửa vòng 5: (i) nếu còn dùng UNIQUE 1-dòng thì chỉ 1 reply qua (test fail ở bước 2); (ii) nếu counter lọc thêm `group_id` thì ra 3 dòng nhỏ thay vì 1 counter = 25.

**Test trần NHÓM/giờ theo hợp nhất nhiều account (bắt buộc — kiểm #b "5 account, chỉ 4 tổng"):** 1 nhóm G, **5 nick khác nhau** (7,8,9,10,11) cùng nhóm, cùng giờ → cho **mỗi nick** reply 1 lần (5 lượt). Assert **đúng 4 lượt qua**, lượt thứ 5 (bất kỳ nick nào) bị chặn `rate_limit` bởi cửa sổ `group:{G}:{hour}`. Assert `SELECT count FROM engagement_window_counters WHERE window_kind='group' AND acct_key=-1 AND group_key={G} AND window_key='group:{G}:'||{hour}` = **4** (MỘT dòng counter cho cả nhóm, KHÔNG phải 5 dòng theo account). Đây là test **phân biệt** bản sửa vòng 5 #2: bản cũ khoá counter theo `account_id` ⇒ 5 dòng ⇒ cả 5 qua (test fail).

**Test ROLLBACK theo claim_id (bắt buộc — kiểm #d "rollback không xoá claim thành công trước"):** nick 7 đã gửi thành công 1 reply trong giờ H (⇒ counter `acct:7:{day}` =1, `group:{G}:{H}` =1). Dựng lượt 2 của cùng nick/nhóm/giờ: claim thành công cửa sổ #1 (`acct`) rồi **cố tình lỗi** **trước** khi gọi network (vd raise trong bước build prompt) ⇒ `release_claims()` chạy. Assert: (i) counter `acct:7:{day}` trở lại **= 1** (decrement đúng lượt này, KHÔNG về 0, KHÔNG mất row); (ii) dòng `engagement_claims` của **lượt 1 vẫn còn** (rollback xoá theo `claim_id` của lượt 2, không theo `window_key`); (iii) lượt 3 (sau đó, lỗi đã hết) reply lại **thành công** (slot đã được nhả đúng).

**Test cơ chế gap (bắt buộc, cover đúng vòng 3 — bổ sung T3/T4):**
- (a) 2 reply cùng `(group, account)` cách nhau **<180s** → cái thứ 2 bị chặn `skip_reason='rate_limit'` (nhánh `claim_gap`).
- (b) 2 reply cùng `(group, account)` cách nhau **>180s** → cái thứ 2 **qua được** (chứng minh KHÔNG bị chặn vĩnh viễn — chính là bug bản vòng 2).
- (c) **Restart engine giữa 2 reply**: reply 1 lúc T, restart, reply 2 lúc T+60s → vẫn bị chặn `rate_limit` (gap đọc từ `created_at` trong DB, không phải RAM); reply 3 lúc T+200s → qua.
- (d) 2 reply cùng nick khác nhóm cách nhau **<120s** → cái thứ 2 bị chặn bởi cửa sổ `gapacct:{account}` (kể cả sau restart).
- (e) Assert `claim_gap()` **có** nhánh đọc `max(created_at)` (không phải `COUNT>=limit`) — test khẳng định số dòng `gap:{g}:{a}` sau 3 reply cách xa nhau vẫn = **1** (đã xoá+insert lại), không tăng thành 2/3.

**Ghi chú — bộ lọc RAM `_last_reply_at` ở §4.3 KHÔNG phải nguồn sự thật:** filter `time.time() - _last_reply_at.get(a,0) >= 120` (§4.3) chỉ là **tối ưu chọn nick** (tránh chọn nhầm nick vừa gửi). Nó **KHÔNG** sống qua restart vì là in-mem; trần ≥120s/account/global **thật sự được enforce** bởi cửa sổ `gapacct:{account}` (claim #5 ở trên). Mọi test rate-limit phải assert trên DB (`engagement_claims`), **không** assert trên `_last_reply_at`.

#### 4.1.2. Giới hạn ACCOUNT-GLOBAL — persist + recovery (lỗi CHẶN #3 của review T9)

Trần **≤25 reply/ngày cho 1 nick trên MỌI nhóm** và **gap ≥120s giữa 2 reply cùng nick (toàn cục)** phải có cơ chế riêng, không thể suy từ trần per-group:

- **Nguồn sự thật:** `engagement_window_counters` cho 2 cửa sổ **scope ACCOUNT** — `window_key='acct:{account_id}:{day}'` (counter `(window_kind='acct', acct_key=account_id, group_key=-1)`, ≤25, §4.1.1) và `engagement_claims` cho `window_key='gapacct:{account_id}'` (LOẠI gap, ≥120s, `claim_gap()`) + **đếm chéo** từ `engagement_logs` (`idx_engagement_logs_account_time`, §1.2) để đối chiếu.
- **Query đếm chéo toàn cục theo account (mọi campaign, mọi group):**
  ```sql
  SELECT COUNT(*) FROM engagement_logs
  WHERE account_id=? AND kind IN ('reply')
    AND created_at > datetime('now','-1 day');
  ```
  Dùng để **khôi phục** khi start engine (§4.1.1) và để đối chiếu với counter (`engagement_window_counters.count` của `acct:{account}:{day}`).
- **Recovery sau restart:** `_restore_engagement_counters()` chạy khi start engine (đặt cùng chỗ `_restore_peerflood_from_db()` `keyword_watcher.py:216-227`): **không cần rebuild** — counter đã nằm trong `engagement_window_counters` (persist) nên restart **không** reset trần. Hàm chỉ **đối chiếu** counter với `engagement_logs` để phát hiện lệch (vd crash trước khi ghi log) và log cảnh báo. Vì counter là persistent, restart **không** reset trần → khắc phục hẳn bug "restart xong vượt trần" mà bản T8 chỉ ghi chú. **Gap cũng sống qua restart:** `gap:{g}:{a}` và `gapacct:{a}` đọc `max(created_at)` từ `engagement_claims`, nên reply ngay sau restart vẫn tôn trọng ≥180s/≥120s (test (c)/(d) §4.1.1).
- **Dọn claim cũ:** job nhỏ mỗi giờ xoá `engagement_claims` có `created_at < datetime('now','-2 day')` (giữ bảng nhỏ) **và** xoá `engagement_window_counters` có `window_key` trỏ ngày cũ (`updated_at < datetime('now','-2 day')`) — an toàn vì counter là theo-ngày/theo-giờ, không cần giữ quá 1 ngày. **Lưu ý:** job dọn **chỉ** là dọn rác — cửa sổ gap **không** phụ thuộc nó để nhả; `claim_gap()` đã tự xoá+insert lại key khi đã qua `min_gap` (§4.1.1). Vì vậy key gap không bao giờ "chặn vĩnh viễn tới khi job 2 ngày chạy".
- **Test restart (bổ sung T4):** giả lập đã gửi 24 reply/ngày cho nick 7 → restart engine (khởi tạo lại state) → reply thứ 25 OK, thứ 26 bị `skip_reason='rate_limit'` ngay sau restart. Test concurrency: 2 tiến trình cùng claim `acct:7:{day}` ở mốc 24 → chỉ 1 vượt được. Thêm: sau restart, reply cách reply trước <120s bị chặn bởi `gapacct:7` (chứng minh gap không chỉ ở RAM).

#### 4.1.3. Acceptance criteria hàm gửi MỚI `_send_group_message` (lỗi CHẶN #5 của review T9)

Đây là phần **phải viết mới** (không tái dùng `_send_group_reply` — xem §0/§2a/§12). Signature đề xuất:

```python
async def _send_group_message(event, text: str, account_id: int,
                              reply_to: int | None = None,
                              *, dry_run: bool = False) -> dict:
    """Gửi 1 tin công khai vào nhóm bằng account_id. Trả {ok, msg_id, error, kind}."""
```

Acceptance criteria bắt buộc (mỗi dòng là 1 assert trong test T3/T4):

| # | Tiêu chí | Chi tiết |
|---|---|---|
| AC1 | **reply_to** | Gửi với `reply_to=trigger_msg_id` (khác `_send_group_reply` vốn `reply_to=event.id`). Nếu `reply_to=None` thì gửi tin thường. Verify `send_message` được gọi đúng `reply_to`. |
| AC2 | **typing + delay** | Trước khi gửi: `client.action(peer, "typing")` trong `min(len(text)*0.012, 3.5)`s (tái dùng `keyword_watcher.py:627-635`) **sau** delay `random.uniform(20,90)`s (§4.2). **Không** dùng delay cứng 1.5–4s của hàm cũ. |
| AC3 | **PeerFlood handling** | Trước gửi: `_is_peerflood_blocked(account_id)` → trả `{ok:False, error:'peerflood_blocked'}` không gọi network. Khi `send_message` raise `FloodWaitError` → `_mark_peerflood(account_id)` (tái dùng `:237-255`) và trả `{ok:False, kind:'peerflood'}`. |
| AC4 | **mute/ban classification** | `ChatWriteForbiddenError`/`UserBannedInChannelError` → `{kind:'muted'}`; `UserNotParticipantError`/`ChannelPrivateError` → `{kind:'kicked'}`. Handler dùng `kind` này để cập nhật §4.7 (loại nick / auto-pause). |
| AC5 | **auto-pause hook** | Hàm trả `kind` để lớp gọi **gọi hook** `_on_group_send_failure(campaign_id, account_id, kind)`: mute ≥2 nick / kicked → `status='auto_paused'` + `alerts.send_alert` (§4.7). Bản thân hàm **không** tự pause (giữ testable), nhưng phải có hook và test gọi hook. |
| AC6 | **busy registry** | Bọc `account_busy.acquire(account_id, 'engagement')` (§4.5.1); nếu busy → `{ok:False, kind:'busy'}`. |
| AC7 | **strip/guard output** | Chặn link affiliate/tên sàn/`cam kết` (tái dùng `contains_ai_disclosure` + `get_safe_fallback_reply`, `dm_reply_tracker.py:97-138`) trước khi gửi; `parse_mode='html'` như hàm cũ `:896`. |
| AC8 | **dry_run** | `dry_run=True` → **không** gọi `send_message`, trả `{ok:True, msg_id:None, dry_run:True}` (dùng cho kiểm thử §2b.7 và test không cần mạng). |

### 4.2. Delay + typing simulation

- **Delay trước khi reply:** `random.uniform(20, 90)` giây. **Lý do 20s tối thiểu:** trả lời trong 3 giây một câu hỏi TA = dấu hiệu bot rõ nhất (và cả 4 nick cùng online).
- **Typing simulation:** gọi `async with client.action(peer, "typing")` trong `min(len(reply)*0.012, 3.5)` giây — đúng code đã có ở `keyword_watcher.py:627-635`, tái dùng nguyên.
- **Đọc "human reading time":** `min(delay*0.4, 6.0)` giây trước khi hiện typing (tái dùng pattern `dm_reply_tracker.py:837-846`).
- **KHÔNG** reply khi nick vừa chạy xong 1 tác vụ khác (DM mass/invite) — thêm check "nick đang bận" theo §4.5.

### 4.3. Chọn 1 nick ngẫu nhiên (chống "uniform participation fingerprint")

Nếu 4 nick cùng trong nhóm, **mọi nick đều reply mỗi lần** = chữ ký bot kinh điển (đã ghi trong skill `telegram-multiaccount-automation`, mục Anti-spam). Với v1 vấn đề này **đã tự giải** vì L1 chỉ cho 1 nick/event — nhưng phải đảm bảo nick được chọn **ngẫu nhiên**, không phải luôn nick đầu tiên:

```python
# Chọn 1 nick, ưu tiên nick "rảnh" (không bị PeerFlood, không trong cooldown 120s)
candidates = [a for a in reply_accounts
              if client_online(a)
              and not _is_peerflood_blocked(a)
              and not account_busy.is_busy(a)                 # §4.5.1
              and time.time() - _last_reply_at.get(a, 0) >= 120]
if not candidates:
    log_skip("không có nick rảnh"); return
acc = random.choice(candidates)
```

> ⚠️ `_last_reply_at` ở trên là **cache RAM chỉ để chọn nick nhanh** — **KHÔNG** phải nguồn sự thật và **KHÔNG** sống qua restart. Trần thật ≥120s/account (toàn cục), ≥180s/nhóm-per-nick được **enforce ở tầng claim** (§4.1.1 cửa sổ `gapacct:{account}` và `gap:{group}:{account}`, đọc `created_at` từ `engagement_claims`). Nghĩa là kể cả `_last_reply_at` rỗng sau restart, claim vẫn chặn đúng gap; và khi bộ lọc RAM lệch với DB thì **DB thắng**. Vì vậy test rate-limit assert trên `engagement_claims`, không assert trên dict RAM này. §4.5.1 cũng dùng filter này nhưng thay/bổ sung bằng `account_busy.is_busy(a)`.

**Cân bằng tải theo tuần:** để không có nick nào "im lặng" quá lâu (cũng là dấu hiệu), thêm trọng số nhẹ: `random.choices(candidates, weights=[1/(1+replies_last_24h(a)) for a in candidates])`.

### 4.4. Không reply admin / bot / account mình

Tái dùng **nguyên trạng**:
- `_get_group_admin_ids(client, group_id)` — cache 24h, gồm cả bot và anonymous admin: `keyword_watcher.py:279-323`.
- Check admin trong handler: `keyword_watcher.py:970-982`.
- Lọc bot: `tg.is_bot_account(sender, username)` (`telegram_client.py:42`), dùng tại `keyword_watcher.py:945`.
- Lọc account nội bộ: `_get_self_user_ids()` (`keyword_watcher.py:162-197`) / `is_internal_account_user()` (`dm_reply_tracker.py:920-927`).

**Bổ sung mới:** hộp "whitelist user được phép trả lời" (mặc định rỗng = trả lời tất cả trừ admin/bot). Một số nhóm support có moderator trả lời nhanh — mình reply chồng lên moderator cũng gây khó chịu.

### 4.5. Tôn trọng PeerFlood cooldown + registry trạng thái bận PER-ACCOUNT (lỗi CHẶN #2)

**Vấn đề của bản T8:** §4.5 cũ viết "nếu `_dm_global_lock.locked()` **và** nick đó đang trong lượt DM → chọn nick khác". Điều này **không khả thi**: `_dm_global_lock` (`keyword_watcher.py:202-213`, dùng tại `:347-352`) là `asyncio.Lock` **global, không mang account ID** — `locked()` chỉ trả `True/False` chung chung, **không** cho biết nick nào đang DM. Vì vậy không thể "biết nick đó đang bận".

- `_is_peerflood_blocked(acc_id)` (`keyword_watcher.py:230-234`) + `_mark_peerflood` (`:237-255`, persist DB `accounts.peerflood_until`, khôi phục khi start `:216-227`). **Tái dùng y nguyên.**
- **Global send lock cho group reply:** v1 **KHÔNG dùng** `_get_dm_lock()` vì lock đó gồm delay 2–20 phút (`DM_DELAY_MIN_SECS/MAX_SECS` `:204-205`, áp dụng `:353-358`) — dùng chung sẽ khiến reply nhóm trễ vô hạn. Dùng **lock riêng `_engagement_send_lock`** (serialize reply nhóm 1 tại 1 thời điểm).

#### 4.5.1. Registry bận per-account (MỚI — khắc phục lỗi T8)

Thay vì dựa vào global lock, thêm **1 registry trạng thái bận duy nhất theo account** trong module mới `account_busy.py`:

```python
# account_busy.py — nguồn sự thật duy nhất cho "nick nào đang bận"
_busy_until: dict[int, float] = {}      # account_id -> monotonic deadline
_busy_kind:  dict[int, str]   = {}      # account_id -> 'dm'|'funnel'|'invite'|'warmup'|'engagement'
_busy_lock:  dict[int, asyncio.Lock] = {}   # per-account lock cho tác vụ độc quyền

@contextlib.asynccontextmanager
async def acquire(account_id: int, kind: str, ttl_secs: float = 90):
    """Đánh dấu 1 nick đang bận. Raise AccountBusy nếu nick đã bận bởi tác vụ khác."""
    if is_busy(account_id):
        raise AccountBusy(account_id, _busy_kind.get(account_id, "?"))
    _busy_until[account_id] = time.monotonic() + ttl_secs
    _busy_kind[account_id] = kind
    try:
        yield
    finally:
        _busy_until.pop(account_id, None)
        _busy_kind.pop(account_id, None)

def is_busy(account_id: int) -> bool:
    until = _busy_until.get(account_id, 0)
    return until > time.monotonic()
```

**Điểm móc vào từng luồng** (đây là phần T8 thiếu — phải nêu rõ chèn ở đâu):

| Luồng | Điểm móc hiện có | Cách bọc bận |
|---|---|---|
| DM watcher (keyword) | `_send_dm_with_fallback` `keyword_watcher.py:328-366` (đã `async with dm_lock` `:349`) | Bọc `async with account_busy.acquire(acc_id, 'dm', ttl_secs=DM_DELAY_MAX_SECS)` quanh phần gửi của **từng** `acc_id` trong `account_ids` (không bọc cả list) |
| Funnel AI (DM trả lời lead) | `dm_reply_tracker._send_ai_message` (`:465-500`, dùng `get_send_lock`) | Trong `_send_ai_message`, `account_busy.acquire(account_id, 'funnel', ttl=120)` |
| Invite engine | `invite_engine.py` chỗ gọi `send_message` | `account_busy.acquire(acc_id, 'invite', ttl=180)` |
| Warmup | `warmup_engine.py` chỗ gửi post | `account_busy.acquire(acc_id, 'warmup', ttl=120)` |
| **Engagement reply** | hàm mới `_send_group_message` (§4.1/T3) | `account_busy.acquire(acc_id, 'engagement', ttl=90)` |

- **Cách dùng khi chọn nick (§4.3):** candidate chỉ hợp lệ nếu `not account_busy.is_busy(a)` **và** `not _is_peerflood_blocked(a)` **và** `time.time() - _last_reply_at.get(a,0) >= 120`. Nếu **tất cả** nick bận → hoãn reply, retry sau 60s, tối đa 3 lần rồi skip (`skip_reason='tất_cả_nick_bận'`).
- **Vì sao dùng TTL chứ không chỉ Lock:** luồng DM/invite có thể chết giữa chừng (task bị cancel/session rớt) → nếu chỉ dùng Lock sẽ **kẹt vĩnh viễn**; TTL tự nhả. Lock per-account (`_busy_lock`) chỉ dùng cho tác vụ cần độc quyền tuyệt đối; trạng thái đọc UI dùng `_busy_until`.
- **Test (bổ sung T3/T4):** (a) giả lập nick 7 đang DM (`account_busy.acquire(7,'dm')`) → handler engagement chọn nick khác, **không** chọn 7; (b) cả 3 nick đều busy → reply bị hoãn rồi skip đúng `skip_reason`; (c) giữ busy quá TTL → tự nhả, không kẹt; (d) 2 coroutine cùng `acquire(7,...)` → 1 thắng, 1 nhận `AccountBusy`.

### 4.6. Khung giờ + random hóa lịch

- Chỉ hoạt động trong `schedule_start..schedule_end` (mặc định 13:00–23:00 giờ VN — giờ VN là giờ vàng của nhóm crypto châu Á/global).
- Kiểm tra `active_days` (1=Mon..7=Sun, khớp format `days_of_week` của `schedules` — `scheduler.py:68-73`).
- **Bot không bao giờ hành động đúng phút tròn:** mọi delay đều `random.uniform` (đã có ở trên). Với v2 proactive: bắt buộc `schedule_jitter_min ≥ 7`.
- **Tránh pattern "reply ngay khi có tin":** delay 20–90s đảm bảo không phải phản xạ 1 giây.

### 4.7. Auto-pause khi bị mute / ban khỏi nhóm

Cơ chế phát hiện (MỚI, ~50 dòng):

| Dấu hiệu | Cách phát hiện | Hành động |
|---|---|---|
| Bị mute (không gửi được trong nhóm) | `send_message` raise `ChatWriteForbiddenError` / `UserBannedInChannelError` (import từ `telethon.errors`) | Ghi `engagement_logs(status='failed', skip_reason='muted')`, **tạm loại nick đó khỏi campaign** (in-mem 24h), và nếu **≥ 2 nick cùng bị** → `status='auto_paused'`, `pause_reason='mute'`, gửi alert |
| Bị kick khỏi nhóm | `GetParticipantRequest`/`get_permissions` raise `UserNotParticipantError` (pattern đã có ở `keyword_watcher.py:1250-1255`) — chạy health-check mỗi 30 phút cho observer nick | Tạm loại nick, nếu hết nick → auto_pause |
| Reply bị admin xoá | Kiểm tra tồn tại `reply_msg_id` sau 10 phút (`client.get_messages(group, ids=[id])` → None = đã xoá) | Đếm; **1 nick bị xoá 2 lần/24h** → tạm loại nick 24h |
| Account bị Telegram hạn chế gửi | `PeerFloodError` trên group send | `_mark_peerflood(acc)` (tái dùng) |
| Nhóm đổi thành read-only / channel | `ChatWriteForbiddenError` | auto_pause campaign đó |

**Điều kiện auto-pause toàn campaign:** ≥ 2 nick bị mute/ban, HOẶC ≥ 3 tin bị xoá trong 24h, HOẶC `PeerFloodError` từ 2 nick khác nhau. Khi auto-pause: ghi `status='auto_paused'`, `pause_reason`, và gửi cảnh báo qua `alerts.send_alert("engagement", text)` (`alerts.py:30-55`, dedup 1h, gửi vào Saved Messages) **+ hiển thị banner đỏ trên tab Cộng Đồng**.

`status='auto_paused'` **không tự bật lại** — founder phải bấm "Bật lại" trong UI (giống tinh thần `/aion` sau `/aioff` ở v3.7.9). Phải đồng thời `_unregister` toàn bộ handler của campaign để không lãng phí event.

### 4.8. Bảng rủi ro ban + mitigation

| # | Rủi ro | Xác suất | Hậu quả | Mitigation trong design |
|---|---|---|---|---|
| R1 | Reply trong nhóm bị coi là spam/unsolicited | Trung bình (v1), Cao (v2) | Nick bị mute trong nhóm, mất kênh tiếp cận | Chỉ REACTIVE ở v1; ≤4 reply/nhóm/giờ; delay 20–90s; typing sim; reply_to đúng người hỏi; giá trị thật từ knowledge |
| R2 | Nhiều nick của mình bị phát hiện là cùng người | Trung bình | Ban cả cụm account | 1 nick/event (L1); chỉ 1 nick post/ngày/nhóm (v2); IP/proxy khác nhau (đã có `proxy_manager.py`); không dùng chung ảnh/hash (đã có `image_randomizer.py`) |
| R3 | Nội dung lặp giữa các nick → fingerprint | Trung bình | Nick bị report | L3 similarity ≥0.65 → regenerate; L3b không tự tiếp lời |
| R4 | PeerFlood do gửi quá nhanh | Trung bình | Không gửi được 45 phút | Tái dùng `_mark_peerflood` persist DB; rate limit per-account; chọn nick khác khi 1 nick bị |
| R5 | Bị admin nhóm report (vì nói chuyện dù không được mời) | Trung bình–Cao | Kicked + ban | **Consent gate fail-closed (§4.8.1):** campaign không start được nếu operator chưa tick xác nhận; không reply admin; không reply tin không khớp keyword; không gửi link/CTA trong nhóm (v1) |
| R6 | Vi phạm ToS Telegram (§4.9) | Cao (v2), Trung bình (v1) | Ban account, không kháng được | Cảnh báo rõ ở §4.9; chỉ chạy account phụ đã warmup; không dùng account chính |
| R7 | Lead kéo về không chất lượng → founder mất uy tín | Trung bình | Nhóm signal nhiễu | Không đẩy link nhóm signal trong nhóm công khai; chỉ AI funnel DM mới dẫn (và dẫn theo ngữ cảnh quan tâm — §5) |
| R8 | Nhóm đổi thành read-only / bị tắt | Thấp | Lỗi log rác | Health-check 30 phút + auto-pause |
| R9 | Account bị "flagged" (không ban) | Trung bình | Giảm reach, tin không tới | Tái dùng `accounts.is_flagged`/`check_spam_status` (`telegram_client.py:1590`) — chạy trước khi add account vào campaign |
| R10 | Rò rỉ link nhóm signal / lộ automation | Thấp | Lộ "hệ thống bot" → founder mất mặt | Không nói AI/automation (đã có `contains_ai_disclosure` + `get_safe_fallback_reply` ở `dm_reply_tracker.py:97-138`, tái dùng cho group reply) |

#### 4.8.1. Consent gate — FAIL-CLOSED (lỗi CHẶN #4 của review T9)

**Vấn đề của bản T8:** §4.8 cũ chỉ ghi "tôn trọng no-solicit nếu nhóm có rule **→ tắt campaign**" như một câu ghi chú — **không có field, không có bước bắt operator xác nhận**, nên mặc định là **fail-open** (campaign vẫn chạy nếu không ai để ý rule). Yêu cầu review: đảo thành **fail-closed — mặc định DENY**.

**Schema (đã thêm ở §1.2):** `engagement_campaigns.operator_consent_confirmed INTEGER DEFAULT 0`, `operator_consent_at`, `operator_consent_by`.

**Luật fail-closed:**
1. **Mặc định 0 = DENY.** Campaign `status='draft'` **không thể** chuyển sang `active` khi `operator_consent_confirmed=0`. `POST /api/engagement/campaigns/{id}/activate` trả **409** kèm `error='consent_required'` nếu chưa tick.
2. **Engine double-check lúc start:** ở `_register_watcher`/engine start, campaign có `operator_consent_confirmed=0` → **không đăng ký handler**, ghi `engagement_logs(kind='skipped', skip_reason='consent_denied')`. Đây là chốt thứ 2 (không tin UI), tránh trường hợp bật bằng API trực tiếp.
3. **UI checkbox bắt buộc** (khối B, §7.2) — không tick thì nút "Lưu & Bật" disabled:
   > ☐ **Tôi xác nhận: nhóm này cho phép thảo luận công khai, HOẶC tôi có quan hệ/được admin đồng ý.** Tôi hiểu tính năng này vi phạm ToS Telegram và tôi tự chịu rủi ro ban account.
4. **Audit log:** khi tick, ghi `operator_consent_at=now`, `operator_consent_by=<username/label>`, và một dòng `engagement_logs(kind='paused', skip_reason='consent_confirmed')` — để truy được **ai xác nhận, khi nào**.
5. **Thu hồi:** bỏ tick (update về 0) → campaign tự `status='paused'` + `_unregister` handler ngay (giống nút pause §7.2-F).

**Test (bổ sung T2/T3):** activate khi consent=0 → 409 + không có handler; tick consent → activate OK; sửa DB consent về 0 rồi start engine → không đăng ký handler + có log `consent_denied`; bỏ tick trên campaign đang chạy → pause tức thì.

### 4.9. Điểm vi phạm ToS Telegram (nêu thẳng cho founder)

| Hành vi trong design | Điều khoản Telegram | Mức độ |
|---|---|---|
| Reply tin nhắn trong nhóm bằng nick "dân thường" nhưng thực chất là automation | Telegram ToS §"Automation" — dùng userbot cho mục đích không phải cá nhân/spam | **Vi phạm** (v1, v2) — Telegram có quyền ban không cần cảnh báo |
| Tự đăng nội dung quảng bá/định hướng lead trong nhóm người khác | §"Spam" — unsolicited advertising | **Vi phạm nặng** (v2). v2 gần như chắc chắn bị report trong 1-3 ngày |
| Kéo lead bằng cách bắt user DM trước rồi dẫn sang nhóm khác | Vùng xám — không vi phạm nếu user tự nguyện DM; vi phạm nếu có dấu hiệu "mồi" | Xám (v1) |
| Mass DM | §"Bulk messaging" | Không có trong v1/v2 (không DM ai) ✓ |

**Khuyến nghị cứng:** (a) chỉ chạy v1 trên **account phụ đã warmup ≥14 ngày**, (b) không chạy ở nhóm có nội quy cấm quảng cáo/bot (đích thị là nhóm support của chính sàn — nếu là sàn mà founder không có quan hệ, admin sẽ report ngay), (c) **KHÔNG BAO GIỜ dùng account chính / account đang chạy funnel DM KOL**.

---

## 5. FUNNEL — user DM mình thì sao?

### 5.1. Luồng hiện có (không viết mới)

Khi user nhắn riêng, `dm_reply_tracker._make_handler` (`dm_reply_tracker.py:930-1228`) xử lý:
1. Bỏ qua non-private (`:935-936`), lọc bot (`:1040-1056`), blacklist (`:1082`).
2. Ghi `dm_replies` + tra campaign/watcher log để biết lead thuộc đâu (`:1058-1060`, `database.py:3888-3905`).
3. Nếu `ai_followup_enabled` (`:1110-1111`): tạo/đọc `ai_followup_chats` (`database.py:4441`), auto-resume sau 12h (`:1129-1157`), append history (`:1160`).
4. Gác theo `status`/`stage` (`:1162-1175`), gác `handover_keywords` + `max_replies` (`:1194-1213`).
5. Gọi `generate_and_send_ai_reply_for_chat()` (`:503-877`) → chọn agent (campaign → account → agent active đầu tiên), build prompt, gọi `ai_rmx.generate_chat_response` (`ai_remix.py:494`), simulate đọc + typing (`:819-851`), gác lại takeover (`:853-864`), gửi qua `_send_ai_message` với send-lock từ `telegram_forum_inbox.get_send_lock` (`:465-500`).

### 5.2. Chỗ cần config "link nhóm signal"

Hiện tại **không có** cơ chế nào dẫn user về nhóm signal. Có 2 chỗ để gắn:

**Cách A (khuyến nghị — không sửa code funnel):** bỏ `signal_group_link` vào **`knowledge_base` của agent**, kèm 1 câu hướng dẫn trong prompt:
> "Khi user đã hỏi ≥2 câu về TA/kèo/nhận định VÀ tỏ ra muốn theo dõi thường xuyên, mời họ vào nhóm cập nhật tín hiệu: {link}. Không paste link ở bất kỳ tin đầu tiên nào. Không mời nếu user chỉ hỏi giá."
→ Ưu: 0 dòng code, không cần migration, founder tự sửa được trong UI AI Agents.

**Cách B (chuẩn hơn, cần code — đưa vào kế hoạch):** thêm `engagement_campaigns.signal_group_link` → engine funnel tự nối vào `combined_prompt` (`dm_reply_tracker.py:722-724`) khi biết lead đến từ campaign (tra bảng map ở §1.3). Kèm điều kiện chống "mời link quá sớm": chỉ chèn khi `chat.reply_count >= 3` và user đã hỏi ≥1 câu liên quan coin/TA.

**Cách C (đo conversion):** tái dùng `/api/ai-followup/stats` (`routes/ai_followup.py:219-224`) + đọc `kol_profiles` (`database.py:917`) — nếu user nói UID/link affiliate trong DM, AI tự trích (đã có `extract_kol_profile`, `ai_remix.py:706`). Muốn biết "user vào nhóm signal chưa" thì phải kiểm tra membership bằng account là member nhóm signal (thêm endpoint `POST /api/engagement/check-signal-membership` — MỚI, ~30 dòng, tái dùng `check_accounts_in_groups` `telegram_client.py:840`).

**Bối cảnh attribution:** user DM "từ nhóm nào"? Cơ chế có sẵn là `find_campaign_log_for_user` / `find_watcher_log_for_user` — nhưng lead từ engagement **chưa** có log DM nên sẽ ra NULL. **Giải pháp:** khi ghi `engagement_logs`, đồng thời ghi 1 bản ghi "mồi" vào `watcher_dm_logs` với `status='success'`? KHÔNG nên (bẩn log). Thay vào đó: bảng map nhỏ `engagement_dm_map(account_id, trigger_user_id, campaign_id, created_at)` — khi `add_dm_reply` (`dm_reply_tracker.py:1062`), tra map theo `(account_id, sender_id)` trong 7 ngày gần nhất để gán campaign. Đây là hạng mục MỚI nhỏ (bảng + 2 hàm DB).

---

## 6. LUỒNG JOIN

**UI:** form "Thêm nhóm" của tab Cộng Đồng → dán link (hoặc @username, hoặc chat_id âm) → chọn accounts.

**Trình tự (tái dùng) :**
1. `POST /api/watchers/resolve-link` (`routes/watchers.py:319-335`) → `tg.join_channel` + `tg.resolve_chat_link` (`telegram_client.py:1012`, `:1074`) cho **1 account đại diện**, để đọc `chat_id`/`title`/`type`.
   - Lưu ý đã có trong docstring: link invite riêng tư chỉ resolve được **sau khi** account đã là member — đúng như code làm.
2. `POST /api/watchers/auto-join` (`routes/watchers.py:305-311`) → `tg.auto_join_accounts_to_groups(account_ids, group_ids)` (`telegram_client.py:1053-1071`).

**⚠️ Vấn đề của `auto_join_accounts_to_groups` hiện tại:** vòng lặp **tuần tự và KHÔNG delay** (`for acc in accounts: for gid in groups: await join_channel(...)`) ⇒ 5 account join 5 nhóm = 25 request join dồn dập trong vài giây. Đây là dấu hiệu bot rõ ràng nhất và cũng là 1 trong các nguyên nhân phổ biến của `PeerFloodError`/ban khi join hàng loạt.

**Đề xuất (MỚI, ~40 dòng):** hàm `auto_join_staggered(account_ids, group_ids, delay_min_secs, delay_max_secs, on_progress)` **testable thuần**, có thể đặt trong `telegram_client.py` cạnh hàm cũ (KHÔNG sửa hàm cũ để không phá caller hiện có: `routes/watchers.py:77`, `:223` gọi fire-and-forget qua `asyncio.create_task`).

| Tham số | Giá trị đề xuất | Lý do |
|---|---|---|
| Delay giữa 2 lần join **của cùng account** | `random.uniform(90, 240)` giây | Join 3 nhóm/account là ngưỡng hợp lý |
| Delay giữa **2 account khác nhau join cùng nhóm** | `random.uniform(900, 2700)` giây (15–45 phút) | Đây là ngưỡng quan trọng để không lộ cụm account. Có thể dài hơn ở lần chạy đầu |
| Tối đa | 3 nhóm mới/account/ngày, 5 account join cùng nhóm/ngày | Trần cứng |
| Trước khi join | `check_spam_status(acc_id)` (`telegram_client.py:1590`) + kiểm tra `is_flagged`/`PeerFlood` | Không join bằng nick đang bị hạn chế |
| Sau khi join | ghi `engagement_logs(kind='join', status, skip_reason)` + cập nhật tiến độ UI theo từng bước | UI hiện "3/5 nick đã vào nhóm" |
| Khi lỗi `FloodWaitError` | dừng join của nick đó, `_mark_peerflood`, chuyển nick khác | Không retry mù |
| Join thất bại `InviteHashExpired` / link hết hạn | báo rõ trên UI, không retry | |

**Join xong thì có DM member không?** **KHÔNG.** v1 không DM ai trong nhóm. Muốn DM member mới join thì đó là tính năng **Join Watcher** đã có (v3.3.0, `keyword_watchers.watch_type='join'`) — tách biệt hoàn toàn, founder tự bật nếu muốn, và **không nên gộp** với Engagement Campaign (gộp sẽ biến chiến dịch "tạo giá trị" thành "cào member" = đúng thứ founder nói KHÔNG làm).

**Warmup trước khi join:** bắt buộc nick phải có ≥ N ngày warmup. `warmup_engine.py` + `routes/warmup.py:34-41` (job với `daily_post_limit`, `schedule_start/end`) + `warmup_jobs`/`warmup_logs` (`database.py:1103`, `:1121`). UI nên chặn (disable checkbox) nick chưa warmup, kèm dòng chữ giải thích.

---

## 7. UI

### 7.1. Quyết định: **tab riêng** "Cộng Đồng" (nav-item mới)

Lý do:
- Tab Watcher (`static/index.html:125`) đã gánh 2 loại (keyword + join) và có form khá dài. Nhồi thêm "campaign" làm 3 mode trong 1 form, mỗi mode ẩn/hiện nửa số field ⇒ dễ bug UI và khó hiểu.
- Engagement có entity riêng (§1) ⇒ bảng riêng, log riêng, analytics riêng.
- Nhưng **backend vẫn tái dùng** engine watcher, nên chi phí thấp.

Nav: thêm `<button class="nav-item" data-page="engagement" onclick="App.navigate('engagement')">` cạnh `data-page="reactions"` (`index.html:128`) hoặc nhóm với `ai-followup` (`:146`).

### 7.2. Màn hình tối thiểu (chỉ những gì cần thiết để vận hành)

**A. Khối "Nhóm mục tiêu" (form: name, link/nhóm đã chọn, group_titles)**
- Ô dán link + nút **Thêm nhóm** → gọi `/api/watchers/resolve-link` → hiện tên nhóm + số member (nếu lấy được).
- Bảng nhóm đã thêm (tên, type, nút xoá).
- **Checkbox chọn accounts sẽ join** + badge cảnh báo đỏ cho nick **chưa warmup đủ 14 ngày** hoặc **đang bị flagged** (chặn chọn).
- Nút **Join dần dần** → gọi endpoint stagger, hiện progress "3/5 nick đã vào nhóm (ước tính còn 32 phút)".

**B. Khối "Tương tác"**
- `reply_mode`: radio **REACTIVE (khuyến nghị)** — `PROACTIVE (rủi ro cao, chưa khuyến nghị)` — nếu chọn proactive và `engagement_proactive_enabled=0` thì disable + tooltip lý do.
- `trigger_keywords`: input tags (mặc định: `btc, eth, sol, bnb, phân tích, chart, target, sl, entry, hỗ trợ, kháng cự`).
- `ai_agent_id`: dropdown agents.
- `knowledge_extra`: textarea (nhận định riêng, view thị trường của founder).
- Rate limit (3 ô số, có placeholder = mặc định nêu ở §4.1, hard cap hiển thị trong tooltip).
- `schedule_start` / `schedule_end` / checkboxes ngày trong tuần.
- Toggle: **"Cho phép reply"**, **"Bật auto-pause khi bị mute/ban"** (mặc định ON, không cho tắt ở bản đầu để tránh tự sát).
- **Checkbox consent BẮT BUỘC (mặc định OFF — fail-closed, §4.8.1):** *"Tôi xác nhận: nhóm này cho phép thảo luận công khai, HOẶC tôi có quan hệ/được admin đồng ý. Tôi hiểu tính năng này vi phạm ToS Telegram và tự chịu rủi ro ban account."* → Không tick thì nút **"Lưu & Bật"** disabled; API activate trả 409 `consent_required`. Khi tick ghi `operator_consent_at`/`operator_consent_by` (audit).

**C. Khối "Funnel"**
- `signal_group_link` + `signal_group_title` + toggle "cho phép AI dẫn link sau ≥3 lượt" + nút **Check membership** (xem bao nhiêu lead vào nhóm signal).

**D. Bảng "Log tương tác"** (đọc `/api/engagement/logs`)
Cột: `Thời gian | Nhóm | Nick | User hỏi | Câu hỏi (rút gọn) | Reply (rút gọn, click để xem đầy đủ) | Trạng thái | Lý do skip`.
- Filter: campaign, nhóm, nick, status, kind, khoảng ngày.
- Badge đỏ ở tab khi `status='auto_paused'` + banner lý do.

**E. Khối "Sức khoẻ chiến dịch"** (trên cùng tab)
- 4 số: reply hôm nay / giờ vừa rồi (so với trần), số nick bị mute 24h, số tin bị xoá 24h.
- Nếu chạm ≥ 75% trần bất kỳ → badge vàng. Nếu auto_paused → badge đỏ.

**F. Nút nguy hiểm:** "Tạm dừng" (pause) — 1 click, unregister handler ngay, không gửi gì thêm.

### 7.3. File UI cần đụng

- `static/index.html` — nav item (`:128` khu vực) + section mới (đặt sau section reactions ~`:987` hoặc cuối) + modal xác nhận join.
- `static/js/` — theo pattern repo, mỗi trang có file riêng (`ai_followup.js`). Cần tạo `static/js/engagement.js` với object `Engagement` (giống `Members.switchSubTab` `index.html:1140`).
- `static/css/style.css` — chỉ dùng class có sẵn (`.table-wrap`, `.table`, `.tab-bar`, `.nav-item`, `.data-table`); không thêm CSS mới trừ khi cần banner đỏ.
- Kiểm tra cú pháp: `node --check static/js/engagement.js` (đúng pattern repo).

---

## 8. LOG & ANALYTICS

### 8.1. Đo gì

| Nhóm chỉ số | Chỉ số | Nguồn | Ý nghĩa |
|---|---|---|---|
| **Hoạt động** | Số reply/ngày (theo nhóm, theo nick, theo giờ) | `engagement_logs` | Kiểm soát rate limit |
| | Số `skipped` theo `skip_reason` (rate_limit / ngoài_khung_giờ / không_admin / trùng_nick / muted) | `engagement_logs.skip_reason` | Phát hiện cấu hình sai (vd 90% skip vì `ngoài_khung_giờ` = khung giờ lệch với hoạt động thật của nhóm) |
| | Tỷ lệ reply bị xoá / tổng reply | `reply_msg_id` kiểm tra sau 10 phút | Dấu hiệu sớm nhất của việc bị coi là spam |
| | Số nick bị mute/ban | `skip_reason='muted'` | Sức khoẻ account |
| **Chất lượng** | Reply **có số** (regex có số/chữ số + đơn vị giá) / tổng reply | `reply_text` | Đo "giá trị thật" — nếu tỷ lệ thấp nghĩa là AI đang nói chung chung |
| | Reply chứa từ bị cấm (shill: "cam kết", "x100", link, tên sàn) / tổng | `reply_text` | Kiểm soát guardrail §3.2 (nên = 0) |
| | Tỷ lệ reply bị regenerate do trùng nội dung (L3) | counter in-mem | Nếu cao → knowledge/prompt chưa đủ đa dạng |
| **Funnel** | Số user DM vào nick sau khi thấy reply | `dm_replies` join `engagement_dm_map` | Chỉ số quan trọng nhất của v1 |
| | Số lead từ engagement đi tới tier A/B | `ai_followup_chats.lead_tier` | Chất lượng lead |
| | Số lead vào nhóm signal | endpoint check membership | Conversion cuối (mục tiêu kinh doanh) |
| | Thời gian trung bình từ reply → DM đầu tiên | timestamp diff | Đo hiệu lực |

### 8.2. Attribution (bắt buộc để đo)

Không có attribution thì "số lead về" vô nghĩa vì lẫn với KOL campaign. Cách làm: `engagement_dm_map(account_id, trigger_user_id, campaign_id, created_at)` (mục 5.2) + tra map khi ghi `dm_replies`. Trong analytics, chỉ số engagement = số DM có map khớp trong 7 ngày.

### 8.3. UI analytics

Tái dùng khung có sẵn: `routes/analytics.py` (`/api/analytics/*`, có `analytics_cache.get_or_fetch` — `analytics.py:243-259`). Thêm 1 endpoint `GET /api/engagement/stats?campaign_id=&days=7` + 1 thẻ trong tab Analytics. Không cần chart mới ở v1 — bảng số + 1 sparkline đủ.

---

## 9. KẾ HOẠCH TRIỂN KHAI — chia bước nhỏ

Thứ tự bắt buộc: schema → engine → API → UI → test → **changelog + HDSD (bước cuối, KHÔNG được bỏ)**.

| Bước | Nội dung | File đụng tới | Test cần viết | Ước lượng |
|---|---|---|---|---|
| **T1** | Schema: 2 bảng mới `engagement_campaigns`, `engagement_logs` + bảng map `engagement_dm_map` + **2 bảng rate-limit: `engagement_claims` (sổ chi tiết + gap) và `engagement_window_counters` (bộ đếm limit, §1.2)**; CRUD (create/get_all/get/update/delete/toggle) | `database.py` (~dòng 950, cạnh `agent_learned_knowledge`) | `tests/test_engagement_db.py`: CRUD, JSON parse, default values, migration chạy 2 lần không lỗi; **counter UNIQUE theo (window_kind, acct_key, group_key, window_key)**; tách `acct`/`group`/`groupacct` | S |
| **T2** | API CRUD + join stagger + logs. Reuse `resolve-link`/`check-membership` | `routes/engagement.py` (MỚI), đăng ký trong `main.py` (~`:274` cạnh `watchers.router`); hàm `auto_join_staggered` trong `telegram_client.py` (cạnh `:1053`, KHÔNG sửa hàm cũ) | `tests/test_engagement_api.py`: validation (rate limit ≤ trần, delay_max ≥ delay_min, giờ hợp lệ), 404, toggle, join stagger gọi đúng thứ tự + delay (mock `join_channel`) | M |
| **T3** | Engine react: nhánh `watch_type='engagement'` trong `keyword_watcher` (`_make_handler`, `_register_watcher` `:1353`, thêm hàm **MỚI** `_send_group_message` (§4.1.3) — **KHÔNG** tái dùng `_send_group_reply` vì hàm đó là helper DM, bắt buộc `used_dm_acc_id`), `account_busy.py` (MỚI), claim atomic `claimer.py` (MỚI — `claim_limit()` COUNTER atomic + `claim_gap()`, **`release_claims()` theo claim_id**, §4.1.1/§4.1.4) | `keyword_watcher.py`, `account_busy.py` (MỚI), `claimer.py` (MỚI), `engine_lifecycle.py:237-271` (start engine) | `tests/test_engagement_engine.py`: khớp keyword, lọc admin/bot/self/blacklist, **claim concurrency (10 event đồng thời → 1 claim)**, **claim_limit() CÔ LẬP counter còn đúng 1 slot: seed count=limit-1, 10 coroutine (+2 process) → đúng 1 thắng, 9 rate_limit, final count=limit (kiểm #c, §4.1.1)**, 1-nick-per-event (dedup), **rate limit 4/giờ/nhóm đạt được (kiểm #a/b)**, **gap <180s chặn / >180s qua** (§4.1.1 test (a)(b)), **trần toàn cục ≤25/ngày/nick đếm GỘP nhiều nhóm (§4.1.1 test trần toàn cục — kiểm #a)**, **5 account cùng nhóm giờ ⇒ chỉ 4 tổng (kiểm #b)**, **rollback theo claim_id — cả limit LẪN gap, xoá CHỈ theo id vừa tạo (§4.1.4 — kiểm #d)**, ngoài khung giờ, chọn nick khi 1 nick PeerFlood, chọn nick bỏ qua nick **busy per-account**, L3 trùng nội dung → regenerate, L3b không tự tiếp lời; 8 AC của `_send_group_message` (§4.1.3) | L |
| **T4** | Guardrail nâng cao: **recovery rate-limit từ `engagement_window_counters` + `engagement_claims` sau restart** (§4.1.2), similarity L3, health-check mute 30p, auto-pause + `alerts.send_alert`; consent double-check ở engine start (§4.8.1) | `keyword_watcher.py`, `alerts.py`, `claimer.py` | `tests/test_engagement_safety.py`: counter phục hồi sau restart (25/26), claim concurrency 2 tiến trình; **gap tôn trọng sau restart** (§4.1.1 test (c)(d)), gap toàn cục `gapacct` chặn 2 reply cùng nick khác nhóm <120s; 2 nick bị mute → auto_paused; reply bị xoá 2 lần → loại nick; auto-pause không tự bật lại; **consent=0 → không đăng ký handler + log `consent_denied`** | M |
| **T5** | AI nhận định: prompt khung community, chèn giá thật (cache 5 phút, fail → `<SKIP>`), parser `<SKIP>`, L3 similarity, phát hiện shill trong output | `ai_remix.py` (hàm mới `generate_community_reply`), hoặc `agents/community_analyst.py` (MỚI, tách riêng cho dễ test) | `tests/test_engagement_ai.py`: prompt chứa số liệu giá; không có giá → `<SKIP>`; output chứa "cam kết" → bị chặn; trùng 0.7 similarity → regenerate | M |
| **T6** | Funnel: map attribution + chèn `signal_group_link` vào prompt khi reply_count ≥ 3 + endpoint check membership nhóm signal | `dm_reply_tracker.py:722-724` (chèn prompt), `:1058-1062` (ghi map), `routes/engagement.py` | `tests/test_engagement_funnel.py`: lead từ nhóm có map; link không xuất hiện ở tin đầu; có ở tin thứ ≥3 | S |
| **T7** | UI tab Cộng Đồng (form + bảng log + banner auto-pause + progress join) | `static/index.html`, `static/js/engagement.js` (MỚI), `static/css/style.css` | Thủ công + `node --check`; không có test JS tự động trong repo | M |
| **T8** | Analytics: endpoint stats + thẻ trong tab Analytics | `routes/engagement.py`, `routes/analytics.py`, `static/js/` | `tests/test_engagement_stats.py`: aggregation đúng (rate limit, tỷ lệ xoá, conversion) | S |
| **T9** | **BẮT BUỘC:** Changelog `routes/changelog.py` (`CHANGELOG_DATA` — entry mới đầu danh sách, `is_latest: True`, `badge: "LATEST"`, hạ entry `v3.7.12` `:11-16` xuống `is_latest: False`) + HDSD `docs/help-content.md` (thêm mục mới `## 18. Cộng Đồng (Engagement)` **sau** `## 17` `:503`, cập nhật `docs/help-toc.json`, kiểm tra số heading khớp) + `README.md` | `routes/changelog.py`, `docs/help-content.md`, `docs/help-toc.json`, `README.md` | Kiểm tra thủ công: JSON hợp lệ, heading count khớp TOC; chạy full suite | S |

**Tổng:** 9 bước, S=4, M=4, L=1. **T3 là bước nặng nhất** (nên tách thành 2 task: T3a dedup+filter+rate limit, T3b AI reply + L3), và **T3 phụ thuộc T1+T2**; T4/T5/T6 phụ thuộc T3.

**Nguyên tắc khi build (theo skill `telegram-multiaccount-automation`):**
- Prefix test theo convention repo (xem `tests/` — dùng `test_27_create_watcher_empty_keywords` style khi cần giữ thứ tự tên cũ).
- Chạy full suite bằng `terminal(background=true, notify=true)` (suite > 300s).
- QC: `git status`/`git diff --stat` xác nhận chỉ đúng file dự kiến bị đụng.
- Bug pre-existing đã biết: `tests/test_pool_starvation.py::test_cancellation_no_permit_leak` fail do teardown asyncio — **báo là pre-existing, không sửa trong task này**.
- Changelog + HDSD + QC + commit do **orchestrator** làm ở cổng release cuối (worker KHÔNG commit, không bỏ changelog).

---

## 10. LAZIER ALTERNATIVE (1 dòng)

> **Tạo 1 watcher keyword bình thường trỏ vào nhóm mục tiêu, đổi `group_reply_text` thành nội dung AI sinh từ agent knowledge, và KHÔNG DM ai** — tái dùng 100% code hiện có, chạy được trong **~2 giờ** (chủ yếu là sửa `_send_group_reply` để gọi AI thay vì text tĩnh), đạt khoảng **60–70% nhu cầu v1**: có (a) giá trị thật trong nhóm, (b) user tự DM về, (c) funnel AI tiếp quản. **Thiếu:** rate limit/giờ theo nhóm (chỉ có cooldown DM hiện có), auto-pause khi bị mute, chống trùng nội dung giữa các nick, log riêng + attribution + analytics — tức chưa đủ an toàn để chạy nhiều nick ở nhiều nhóm lớn.

> **⚠️ ĐÍNH CHÍNH (vòng T10):** câu "tái dùng 100% code hiện có" ở trên là **không chính xác** — `_send_group_reply` (`keyword_watcher.py:860-903`) là helper của DM watcher, **bắt buộc `used_dm_acc_id`** (None ⇒ `return` ngay, `:875-876`), có **delay cứng 1.5–4s** (`:882`) và chỉ để gửi 1 câu ngắn "check DM". Nó **không** phải public community sender và **không** gọi AI. Phương án "lazier" trên thực tế **vẫn phải viết hàm mới** `_send_group_message` (AC §4.1.3) cho phần gửi, dù vẫn tiết kiệm được phần schema/claim/UI. Xem §12 mục 5.

**Đề xuất của architect:** nếu founder muốn thấy kết quả trong tuần này → chạy Lazier Alternative trên **1 nhóm + 1 nick đã warmup, tối đa 5 reply/ngày**, song song build v1 đầy đủ. Nếu founder muốn đúng chuẩn an toàn ngay → làm T1→T9 ở trên (ước tính 6–9 ngày công build + QC).

---

## 11. Cảnh báo tổng thể cho founder (đọc trước khi duyệt)

1. **ToS:** tính năng này vi phạm ToS Telegram ở tầng "automation + unsolicited content" (chi tiết §4.9). Telegram có thể ban bất kỳ lúc nào, không kháng cáo. → **Chỉ chạy trên account phụ đã warmup, không bao giờ dùng account chính.**
2. **Đây không phải "chắc chắn an toàn", đây là "giảm rủi ro xuống mức chấp nhận được".** v1 với ≤4 reply/nhóm/giờ + 1 nick/tin + delay 20–90s vẫn có thể bị mute nếu nội dung bị coi là quảng cáo. → Bắt buộc bật auto-pause và theo dõi tab sức khoẻ chiến dịch trong 3 ngày đầu.
3. **v2 (tự đăng bài) gần như chắc chắn bị report** trong 1–3 ngày nếu chạy ở nhóm có admin tích cực. → Không khuyến nghị build.
4. **Nhóm đích quan trọng:** nếu là nhóm support của chính sàn mà founder KHÔNG có quan hệ → admin có quyền và động cơ report ngay. Nếu founder có quan hệ đối tác với sàn → **xin phép admin trước** là cách an toàn nhất và hợp pháp nhất.
5. **Không cam kết về số lead.** Giá trị thật phụ thuộc chất lượng knowledge/nhận định. Nếu knowledge sơ sài, các reply sẽ là "nhận định chung chung" và không ai DM — lúc đó tăng số lượng reply là **đúng sai lầm chết người**. Cách đúng: cải thiện chất lượng nhận định (giá thật, mốc cụ thể, khung thời gian).

---

## 12. Changelog review

Vòng 2 (T10) sửa 5 lỗi CHẶN do reviewer T9 (`t_0e20acb0`) chỉ ra. Mọi `file:line` mới đã đọc lại code thật để xác minh.

| # | Lỗi CHẶN (T9) | Đã sửa ở đâu |
|---|---|---|
| 1 | **Race rate-limit** — đọc log khi start + counter RAM, ghi log SAU send ⇒ nhiều handler cùng qua check ⇒ vượt trần. | §1.2 thêm bảng `engagement_claims` (+ UNIQUE index); **§4.1.1 mới**: claim/reserve atomic (INSERT trong transaction, thua UNIQUE ⇒ skip) đặt **trước** await/send; test concurrency 10 event → 1 claim, + test 2 tiến trình. |
| 2 | **Busy-account không khả thi** — `_dm_global_lock` (`keyword_watcher.py:202-213`) global, không mang account ID; `locked()` không biết nick nào bận. | **§4.5.1 mới**: registry bận per-account `account_busy.py` (`_busy_until`/`_busy_kind` + TTL + per-account lock), bảng điểm móc vào DM (`:328-366`)/funnel/invite/warmup/engagement, + 4 test. §4.5 nêu rõ vì sao bỏ cách cũ. |
| 3 | **Account-global limit thiếu cơ chế** — §4.1 nêu ≤25/ngày, ≥120s nhưng schema + T3/T4 không có query/claim/persist/recovery. | **§4.1.2 mới**: claim `acct:{account}:{day}`, query đếm chéo `engagement_logs` (`idx_engagement_logs_account_time` §1.2), `_restore_engagement_counters()` khi start (cạnh `_restore_peerflood_from_db` `:216-227`), job dọn claim, test restart 25/26 + concurrency. |
| 4 | **Consent gate chưa fail-closed** — §4.8 chỉ ghi "tắt campaign nếu có rule", không field/procedure bắt operator xác nhận. | §1.2 thêm `operator_consent_confirmed` (default 0 = DENY) + `operator_consent_at/by`; **§4.8.1 mới**: 5 luật fail-closed (409 `consent_required`, engine double-check, checkbox UI §7.2-B, audit log, thu hồi), + test. |
| 5 | **Tuyên bố sai "`_send_group_reply` tái dùng nguyên trạng"** — thực tế là helper DM, bắt buộc `used_dm_acc_id` (`:875-876`), delay cứng 1.5–4s (`:882`). | Sửa **§0** (kết luận kỹ thuật), **§2a bước 8**, **§10** (đính chính "lazier"): ghi rõ phải viết hàm MỚI `_send_group_message`. **§4.1.3 mới**: 8 acceptance criteria (reply_to, typing/delay, PeerFlood, mute/ban classification, auto-pause hook, busy registry, guard output, dry_run). §9 T3/T4 cập nhật tương ứng. |

**Đồng thời sửa theo thông tin reviewer đã xác nhận (giữ nguyên các điểm reviewer nói ĐÚNG):**
- §1.3: **bỏ đề xuất "ID âm giả"** — `ai_followup_chats.campaign_id` đã tồn tại (`database.py:798-812`) và dùng cho KOL campaign; engagement dùng bảng map riêng.
- Giữ nguyên (đã verify khớp): `routes/watchers.py:305-335`; `telegram_client.py:1053-1071` (join tuần tự, không delay); dedup `keyword_watcher.py:1125-1136`; admin/self/PeerFlood primitives `:162-323`.

**Không đổi cấu trúc:** vẫn giữ 11 mục gốc (§0–§11) + thêm §12; không sửa code, không commit.

### Vòng 3 (T10 re-review) — sửa lỗi CHẶN còn lại trong fix #1 (cửa sổ gap)

Reviewer vòng 2 (T9) chấp nhận 4.75/5 lỗi đã sửa, nhưng phát hiện **§4.1.1 chưa build được**: cửa sổ `gap:{group}:{account}` là **key TĨNH** (không thành phần thời gian) mà lại chặn bằng `COUNT(*)>=limit` + UNIQUE index ⇒ sau reply ĐẦU TIÊN trong `(group, account)`, key **không bao giờ insert lại được** ⇒ chặn **VĨNH VIỄN**. Ngoài ra trần gap toàn cục/theo nick (≥120s) không có window_key tương ứng.

| Điểm sửa | Vị trí | Nội dung |
|---|---|---|
| Gap dùng `max(created_at)`, KHÔNG dùng COUNT | §4.1.1 (thay `claim_slot()` bằng `claim_limit()` + **`claim_gap()` mới**) | `claim_gap()` đọc `SELECT max(created_at)` của key, chặn chỉ khi `now - last < min_gap`; đã qua gap thì **xoá dòng gap cũ + INSERT lại** ⇒ key tĩnh được tái dùng hợp lệ, không chặn vĩnh viễn. |
| Bổ sung cửa sổ gap toàn cục per-account | §1.2 (`window_key` ghi rõ 5 loại + partial UNIQUE index cho gap), §4.1.1 (`build_windows()` thêm `gapacct:{account}` ≥120s, danh sách nâng **4 → 5 cửa sổ**), §4.1.2, §4.3 | Trần ≥120s/account/toàn cục nay là **claim thật trong DB** (sống qua restart), không chỉ là filter RAM `_last_reply_at`; §4.3 ghi rõ RAM chỉ là tối ưu chọn nick, DB thắng khi lệch. |
| Test đúng cơ chế gap | §4.1.1 (test (a)–(e)), §4.1.2, §9 T3/T4 | <180s chặn `rate_limit`; >180s qua; restart giữa 2 reply vẫn tôn trọng gap; gap toàn cục <120s chặn; số dòng key gap luôn = 1. |
| Sửa phụ (pre-existing T8) | §9 T9 | `v3.7.11 :11-14` → `v3.7.12 :11-16`; `help-content.md :501` → `:503`. |

### Vòng 4 (T10) — sửa lỗi phạm vi đếm của `claim_limit()` (trần toàn cục theo nick bị vô hiệu)

Khi tự soát lại vòng 3, phát hiện **lỗi thiết kế thứ 2 nằm trong chính fix blocker #1** (chưa reviewer nào bắt, nhưng cùng bản chất với lỗi gap: "có claim nhưng cơ chế đếm sai phạm vi"):

**Vấn đề:** `claim_limit()` (bản vòng 3) đếm `COUNT(*) ... WHERE campaign_id=? AND group_id IS ? AND account_id=? AND window_key=?`, và `idx_engagement_claim_unique` là UNIQUE trên `(campaign_id, group_id, account_id, window_key)`. Với cửa sổ **toàn cục theo nick** `acct:{account}:{day}` — key **cố ý không chứa group** — việc lọc/unique thêm `group_id` khiến **mỗi nhóm sinh một dòng riêng** cùng `window_key` ⇒ trần **≤25 reply/ngày/nick (mọi nhóm)** *không bao giờ chạm tới* (mỗi nhóm chỉ đếm được 1–2). Tức là lỗi CHẶN #3 của T9 ("account-global limit thiếu cơ chế") **vẫn còn**, chỉ núp dưới dạng đếm sai.

| Điểm sửa | Vị trí | Nội dung |
|---|---|---|
| `claim_limit()` đếm chỉ theo `(account_id, window_key)` | §4.1.1 | Bỏ `campaign_id`/`group_id` khỏi `WHERE` của `COUNT(*)`. `window_key` đã mã hoá đầy đủ phạm vi (group/giờ/ngày); cửa sổ toàn cục không được tách theo group. Giải thích "hệ quả cố ý" (2 campaign cùng nhóm chia sẻ ≤4/giờ) nêu ngay dưới. |
| Tách partial UNIQUE index cho 2 họ khoá limit | §1.2 | `idx_engagement_claim_group_unique (account_id, window_key) WHERE window_key LIKE 'group:%' OR 'groupacct:%'` **và** `idx_engagement_claim_acct_unique (account_id, window_key) WHERE window_key LIKE 'acct:%'` — thay `idx_engagement_claim_unique (campaign_id, group_id, account_id, window_key)`. Đảm bảo UNIQUE chống đua **đúng phạm vi** cho cả 2 họ. |
| Test phân biệt | §4.1.1 (test "trần toàn cục"), §9 T3 | 3 nhóm khác nhau, 1 nick, 25 reply qua cả 3 nhóm → 25 thành công, cái thứ 26 bị chặn; `COUNT(*) WHERE account_id=7 AND window_key='acct:7:{day}'` = 25 (bản cũ lọc `group_id` sẽ ra 3 dòng nhỏ ⇒ test fail). |
| Sửa tiền tố `LIKE` cho index gap | §1.2 (`idx_engagement_claim_gap_unique`) | `LIKE 'gap:%'` **KHÔNG** khớp `gapacct:...` (ký tự thứ 4 là 'a' chứ không phải ':') ⇒ phải ghi rõ `WHERE window_key LIKE 'gap:%' OR window_key LIKE 'gapacct:%'`, nếu không cửa sổ `gapacct` không có UNIQUE ⇒ mất bảo vệ chống đua của trần gap toàn cục. Tương tự `groupacct:` phải liệt kê riêng cạnh `group:`. |

**Giữ nguyên §0–§12, cấu trúc 11 mục gốc + §12; chỉ sửa doc, không code, không commit.**

### Vòng 5 (T14) — sửa 3 lỗi CHẶN của review vòng 3 (T11 `t_f688f734`)

Reviewer T11 KHÔNG đồng ý build, nêu **3 lỗi CHẶN** còn lại trong cơ chế claim rate-limit. Vòng 5 sửa cả 3, kèm 4 phép kiểm (a)–(d) bắt buộc mô tả trong doc.

| # | Lỗi CHẶN (T11 vòng 3) | Đã sửa ở đâu | Cách sửa |
|---|---|---|---|
| 1 | **Rate-limit không biểu diễn được quota > 1** (doc:120-162, 319-367) — `UNIQUE(account_id, window_key)` buộc mỗi cửa sổ **đúng 1** claim ⇒ limit 4/12/25 không thể có nhiều claim thành công; test "25 lần thành công" (:428-430) mâu thuẫn schema. | §1.2 (bảng `engagement_window_counters` mới), §4.1.1 (`claim_limit()` viết lại = **counter + atomic conditional increment**), §4.1.2, §4.1.4, §4.1.1 test "trần toàn cục" (đổi assert sang đọc counter), §9 T1/T3 | Cửa sổ **LOẠI limit** dùng counter row + `UPDATE ... SET count=count+1 WHERE count<limit`, đọc `rowcount` (`1`=thắng, `0`=chạm trần) trong **1 transaction**. `UNIQUE(account_id, window_key)` **chỉ còn** dùng cho cửa sổ **LOẠI gap** (đúng 1 dòng, `claim_gap()` tự xoá+insert — giữ nguyên cách cũ). Counter là **nguồn sự thật của trần**; `engagement_claims` là sổ chi tiết + nguồn gap. |
| 2 | **Trần group/hour sai scope** (doc:147-151, 324-340, 347-358) — key `group:{g}:{hour}` không chứa account nhưng UNIQUE/COUNT theo account ⇒ 5 account cùng nhóm có 5 ngân sách riêng, trái yêu cầu **tổng ≤4/giờ/NHÓM**. | §4.1.1 (**bảng ma trận window** mới + `_counter_key()` + note "vì sao khoá theo group_id"), §1.2 (PK counter chứa `window_kind/acct_key/group_key`), §4.1.1 test "trần NHÓM/giờ hợp nhất" (kiểm #b), §9 T3 | Counter của cửa sổ `group:` khoá theo **`group_id`** (`window_kind='group'`, `acct_key=-1`, `group_key=group_id`) — mọi account trong nhóm đập vào **1 dòng** ⇒ đúng tổng. Trần `acct:`/`groupacct:` là **scope ACCOUNT** ⇒ khoá theo `account_id`. Doc ghi rõ **ma trận**: window_key | scope (group/account) | loại (limit/gap) | ngưỡng. |
| 3 | **Rollback theo window key** (doc:399-410) — xoá theo key có thể xoá mất claim hợp lệ của lượt khác sau khi chuyển sang nhiều claim/window. | §4.1.4 (**mục mới** — luật rollback + `release_claims()`), §4.1.1 (vòng claim giữ `claim_id`/`counter_id`), §4.1.1 test "ROLLBACK theo claim_id" (kiểm #d), §9 T3 | `claim_limit()` **trả về `claim_id`**; handler giữ `claimed=[(claim_id, counter_id, is_gap)]`. Rollback (chỉ khi lỗi **TRƯỚC** network): xoá **đúng `claim_id`** (`DELETE ... WHERE id=?`), counter thì **decrement** (`UPDATE ... SET count=count-1`, **không xoá row**), gap thì xoá theo key tĩnh (mỗi lượt 1 dòng). **KHÔNG** `DELETE ... WHERE window_key` cho cửa sổ limit. |

**4 phép kiểm bắt buộc (reviewer yêu cầu) — nay mô tả tường minh trong doc:**

| Kiểm | Mô tả | Vị trí mô tả |
|---|---|---|
| (a) cùng account/window limit 25: **25 lần qua, lần 26 bị chặn** | 3 nhóm, 1 nick, 25 reply ok; lần 26 `rate_limit`; assert counter `acct:7:{day}` = 25 | §4.1.1 "Test trần TOÀN CỤC theo nick" |
| (b) 5 account cùng group/hour: **chỉ 4 TỔNG qua**, account thứ 5 bị chặn | 5 nick cùng nhóm/giờ, mỗi nick 1 lượt → đúng 4 qua; assert counter `group:{G}:{hour}` = 4 (1 dòng nhóm) | §4.1.1 "Test trần NHÓM/giờ hợp nhất" |
| (c) concurrency còn 1 slot: **đúng 1 claim thắng**, còn lại `rate_limit` | 10 event đồng thời cùng nhóm/nick/giờ → 1 claim thắng, 9 skip; + test 2 tiến trình | §4.1.1 "Test concurrency" |
| (d) rollback 1 reservation **KHÔNG xoá claim đã thành công trước** | lượt 1 đã gửi ok; lượt 2 lỗi trước network → counter về đúng giá trị cũ (không về 0), claim lượt 1 còn nguyên | §4.1.1 "Test ROLLBACK theo claim_id" + §4.1.4 |

**Giữ nguyên (đã reviewer xác nhận ĐÚNG, không sửa):** BUSY-ACCOUNT per-account đủ luồng DM/funnel/invite/warmup (§4.5.1); CONSENT default-deny + operator/audit (§4.8.1); tuyên bố `_send_group_reply` (§0/§2a/§10) + acceptance criteria `_send_group_message` (§4.1.3). Cấu trúc §0–§12 giữ nguyên (thêm §4.1.4); **chỉ sửa doc, không code, không commit**.

### Vòng 6 (T16) — vòng CUỐI: sửa 2 lỗi CHẶN của review vòng 5 (T15 `t_026f2088`)

Reviewer T15 xác nhận counter quota>1, scope GROUP 4/giờ, và 3 lỗi vòng trước ĐẠT; còn **2 lỗi CHẶN cục bộ**. Vòng 6 sửa cả 2 — **chỉ sửa doc, không code, không commit**.

| # | Lỗi CHẶN (T15 vòng 5) | Đã sửa ở đâu | Cách sửa |
|---|---|---|---|
| 1 | **Rollback gap còn xoá theo key** (doc:413-438, 443-461, 485-503) — `claim_gap()` không trả `claim_id`; handler lưu `(None, None, True)` cho gap; `release_claims()` nhánh gap dùng `_delete_gap_row(...)`/`DELETE ... WHERE (account, window_key)` ⇒ **vẫn còn nhánh xoá theo window key**, đúng lỗi #3 vòng 5 chưa sửa hết cho họ gap. | §4.1.1 (`claim_gap()` đổi signature trả `claim_id` + `db.execute_insert`), §4.1.1 (handler: `ok, reason, gap_id = await claim_gap(...)`; `claimed.append((gap_id, None, True))`), §4.1.4 (luật #2 nhánh gap + `release_claims()` bỏ hẳn `_delete_gap_row`), §9 T3 | `claim_gap()` nay trả `(ok, reason, claim_id)` với `claim_id` = id dòng gap **VỪA INSERT** (bắt qua `db.execute_insert`); khi **thua** (chưa qua `min_gap`/đua UNIQUE) trả `claim_id=None`. Handler lưu ID thật vào tuple `claimed` (không còn `None`). `release_claims()` nhánh gap: **chỉ** `DELETE FROM engagement_claims WHERE id=?` theo ID vừa tạo; **bỏ hẳn** `_delete_gap_row(...)`/nhánh xoá theo `(account, window_key)`. Ghi rõ: nếu `claim_gap` **thua** thì `claim_id=None` ⇒ **không có gì để nhả**, handler bỏ qua. Luật #3 §4.1.4 nâng lên: **KHÔNG BAO GIỜ** rollback bằng `DELETE ... WHERE window_key` (mọi họ khoá, cả limit lẫn gap). |
| 2 | **Test concurrency chưa cô lập counter** (doc:514) — test chạy toàn pipeline; kết quả "1 thắng" có thể do **UNIQUE của cửa sổ gap**, chưa chứng minh `claim_limit()` atomic khi còn đúng 1 slot. | §4.1.1 (**test mới** "Test concurrency CÔ LẬP COUNTER — còn đúng 1 slot"), §12 (hàng kiểm #c), §9 T3/T4 | Thêm test **chỉ gọi trực tiếp `claim_limit()`**, tách hẳn khỏi gap: seed counter `count = limit-1 = 24`; `asyncio.gather` 10 coroutine cùng claim trên **cùng 1 slot**; assert **đúng 1** thắng, **9** nhận `rate_limit`, final `count = 25` (== limit, **không** vượt), sổ chi tiết đúng 1 dòng; + bản **2 process** SQLite. Nêu rõ test **phân biệt**: bỏ `AND count < limit` (tách đọc/ghi) ⇒ 2 thắng/count=26 ⇒ fail. |

**4 phép kiểm bắt buộc — nay mô tả tường minh (siết #c):**

| Kiểm | Mô tả | Vị trí mô tả |
|---|---|---|
| (a) cùng account/window limit 25: **25 lần qua, lần 26 bị chặn** | 3 nhóm, 1 nick, 25 reply ok; lần 26 `rate_limit`; assert counter `acct:7:{day}` = 25 | §4.1.1 "Test trần TOÀN CỤC theo nick" |
| (b) 5 account cùng group/hour: **chỉ 4 TỔNG qua** | 5 nick cùng nhóm/giờ, mỗi nick 1 lượt → đúng 4 qua; assert counter `group:{G}:{hour}` = 4 (1 dòng nhóm) | §4.1.1 "Test trần NHÓM/giờ hợp nhất" |
| (c) concurrency còn **đúng 1 slot**: **đúng 1 thắng**, phần còn lại `rate_limit` | **cô lập `claim_limit`**: seed `count=limit-1=24`; 10 coroutine (+ bản 2 process) cùng claim; assert 1 thắng/9 thua; final `count=25` không vượt; 1 dòng sổ | §4.1.1 "Test concurrency CÔ LẬP COUNTER — còn đúng 1 slot" |
| (d) rollback 1 reservation **KHÔNG xoá claim đã thành công trước** | lượt 1 đã gửi ok; lượt 2 lỗi trước network → counter về đúng giá trị cũ (không về 0), claim lượt 1 còn nguyên; **rollback theo ID** (cả limit lẫn gap) | §4.1.1 "Test ROLLBACK theo claim_id" + §4.1.4 |

**Giữ nguyên (đã reviewer T15 xác nhận ĐÚNG):** counter quota>1 (§1.2/§4.1.1); scope GROUP tổng 4/giờ (§4.1.1); 3 lỗi vòng trước; phép kiểm (a),(b),(d). Cấu trúc §0–§12 giữ nguyên (thêm §4.1.4 từ vòng 5); **chỉ sửa doc, không code, không commit**.

