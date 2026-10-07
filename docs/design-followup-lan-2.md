# Design Doc — Follow-up lần 2 cho KOL không reply

> **Mục tiêu:** Thiết kế cơ chế nhắc lại (nudge) lần 2 cho KOL/lead đã được AI Agent DM outreach nhưng **KHÔNG reply**, để lead không bị "nguội" vì KOL miss tin nhắn.
> **Trạng thái:** Design only — **CHƯA viết code sản phẩm**. Mọi khẳng định về code hiện có đều kèm `file:line` đã đọc đối chiếu thật.
> **Ngày:** 2026-10-04 · **Task:** t_1b3ecd3a (T6) · **Phụ thuộc:** chạy sau T4 (cùng đụng `dm_reply_tracker.py`).

---

## 0. TL;DR cho founder

- **Tin tốt:** hệ thống đã có sẵn ~70% hạ tầng cần thiết — một **drip engine** chạy nền 5 phút/lần (`dm_reply_tracker.py:1255`), một **global send lock + random delay** chống spam (`keyword_watcher.py:208-366`), một **per-chat send lock** dùng chung cho mọi đường gửi (`telegram_forum_inbox.py:49`), **AI remix** đa provider (`ai_remix.py:494`), và **daily DM limit + PeerFlood cooldown** (`database.py:2245`, `keyword_watcher.py:142/237`).
- **Vấn đề thật sự:** drip hiện có **chỉ chạy cho lead ĐÃ reply** (bảng `ai_followup_chats` chỉ được tạo khi có tin inbound — `dm_reply_tracker.py:1048,1118`), lại còn **gate vào Tier A/B hoặc intent ≥ 50** (`dm_reply_tracker.py:1479`). KOL **chưa từng reply** = không có row = **không bao giờ được nhắc**. Đây đúng là lỗ hổng founder mô tả.
- **Đề xuất v1 (nhỏ, an toàn):** thêm một "outreach nudge engine" **bám vào log gửi đầu** (`watcher_dm_logs` / `dm_campaign_logs`), nhắc **đúng 1 lần sau 24h im lặng**, nội dung **remix mới** (không copy tin 1), đi qua **đúng pipeline an toàn hiện có**, và **re-check reply trước khi gửi** để chống race.
- **Chi phí ước tính:** ~1 cột DB mới (hoặc tận dụng `sent_at`), 1 hàm worker, 1 endpoint thủ công, 1 badge UI. Xem §7.
- **Rủi ro spam:** THẤP nếu giữ đúng 4 hàng rào ở §4 (xem ước lượng §4.5).

---

## 1. NGUỒN SỰ KIỆN — bám vào đâu để biết "đã DM lần 1 mà chưa có reply"?

### 1.1 Hiện trạng (đã đọc code)

| Sự thật | Bằng chứng (file:line) |
|---|---|
| Watcher DM ghi log **mỗi lần gửi** vào `watcher_dm_logs`, có `status`, `sent_at`, `account_id`, `target_user_id` | `database.py:350-366` (schema), `keyword_watcher.py:528,643,665,682,696,735,743,778,794,803,820` (`add_watcher_dm_log`) |
| Campaign DM ghi log tương tự vào `dm_campaign_logs` (cùng shape: `status/sent_at/account_id/target_user_id`) | `database.py:736-750`, `database.py:3314` (`add_dm_campaign_log`) |
| **Tin inbound của lead** ghi vào `dm_replies` (bảng riêng) **và** append vào `ai_followup_chats.history_json` role `user` | `database.py:540-552` (`dm_replies`), `dm_reply_tracker.py:1057` (`add_dm_reply`), `dm_reply_tracker.py:1160` (`append_followup_chat_message(..., "user", ...)`) |
| `ai_followup_chats` **chỉ được tạo khi có reply** (hoặc chat vô tình tồn tại) — **không** tạo lúc gửi DM | `dm_reply_tracker.py:1048` & `1118` (`get_or_create_followup_chat` **trong handler inbound**); không có lời gọi nào từ `keyword_watcher.py` (đã grep: `keyword_watcher.py` chỉ dùng `add_watcher_dm_log`) |
| `append_followup_chat_message` set `last_user_message_at = datetime('now')` **chỉ khi role='user'** — cột này là mốc "im lặng thật", không bị tin mình gửi làm reset | `database.py:4523-4535` |
| Drip engine hiện có query chỉ trên `ai_followup_chats`, gate `lead_tier IN ('Tier A','Tier B') OR intent_score >= 50`, `last_drip_stage < total_stages` | `dm_reply_tracker.py:1476-1484` |

### 1.2 Kết luận nguồn sự kiện

> **Nguồn sự kiện đúng = `watcher_dm_logs` + `dm_campaign_logs` (log gửi lần 1), KHÔNG phải `ai_followup_chats`.**

Vì `ai_followup_chats` là "chat có tương tác", nó bỏ sót đúng nhóm mục tiêu của task (KOL không reply). Điều kiện "chưa reply" xác định bằng **NOT EXISTS** trong `dm_replies` (và double-check qua `ai_followup_chats.history_json` nếu row tồn tại).

**Query nháp (đã kiểm chứng cột tồn tại):**

```sql
-- Ứng viên nudge: DM lần 1 SUCCESS, đủ già (>= N giờ), và CHƯA có inbound nào.
SELECT w.watcher_id, w.account_id, w.target_user_id, w.target_username, w.sent_at
FROM watcher_dm_logs w
WHERE w.status = 'success'
  AND w.sent_at <= datetime('now', '-24 hours')          -- đủ im lặng (cấu hình)
  AND w.sent_at >= datetime('now', '-120 hours')          -- trần tuổi: bỏ lead quá cũ
  AND NOT EXISTS (                                          -- chưa reply
        SELECT 1 FROM dm_replies r
        WHERE r.sender_user_id = w.target_user_id
          AND r.received_at >= w.sent_at
      )
  AND NOT EXISTS (                                          -- chưa nudge (dedup DB)
        SELECT 1 FROM nudges n
        WHERE n.account_id = w.account_id
          AND n.target_user_id = w.target_user_id
      )
ORDER BY w.sent_at ASC
LIMIT 20;
```

> Ghi chú: `dm_replies` không có index trên `sender_user_id` hiện tại (có index `idx_dm_replies_unread` cho `is_read`). Xem §7 — nên thêm index `(sender_user_id, received_at)` khi làm bước backend.

---

## 2. ĐIỀU KIỆN KÍCH HOẠT

| Tham số | Giá trị v1 đề xuất | Cấu hình qua | Ghi chú |
|---|---|---|---|
| **Ngưỡng im lặng** | **24 giờ** (mặc định) | setting `outreach_nudge_delay_hours` (mới), fallback `ai_followup_drip_cadence[0]` | 24h theo gợi ý đề bài; đọc bằng pattern `_load_drip_cadence()` hiện có (`dm_reply_tracker.py:1309`) |
| **Số lần nhắc tối đa** | **1 lần** cho v1 | setting `outreach_nudge_max` = `1` | v2 có thể dùng cadence `[24, 72]`; v1 giữ 1 cho an toàn |
| **Trần tuổi** | 120 giờ (5 ngày) | hard-code v1 | Tránh đào mộ lead 1 tháng trước → vừa spam vừa vô nghĩa |
| **Batch/chu kỳ** | 20/batch, chạy mỗi 5 phút | dùng lại `_followup_periodic_loop` (`dm_reply_tracker.py:1255`) | Không cần worker mới, chỉ thêm 1 lời gọi |
| **Cửa sổ gửi (giờ VN)** | 09:00–21:00 (tuỳ chọn v1.1) | — | Không gửi 3h sáng → giảm report/spam |

### 2.1 Loại trừ (bắt buộc — fail closed)

Không nhắc nếu **bất kỳ** điều kiện sau đúng:

1. **Đã reply** — `EXISTS(dm_replies...)` (§1.2) **hoặc** `ai_followup_chats.history_json` có role `user` sau `sent_at`.
2. **Blacklist** — `db.is_user_blacklisted(user_id, username)` (`database.py:2395`, pattern dùng ở `dm_reply_tracker.py:482,1501`).
3. **Đã handover người thật** — `ai_followup_chats.status IN ('needs_human','paused_admin')` **hoặc** `stage IN ('paused','signed')` (stage là nguồn sự thật theo operator, xem `database.py:840-843` và gate hành vi ở `dm_reply_tracker.py:1155-1176`). Nếu row chat **chưa tồn tại** thì coi như chưa handover.
4. **Account paused** — `db.is_account_paused(account_id)` (`database.py:2493`; watcher đã dùng ở `keyword_watcher.py:551`).
5. **Account PeerFlood cooldown** — `_is_peerflood_blocked(acc_id)` (`keyword_watcher.py:230`), restore từ DB (`keyword_watcher.py:216`).
6. **Account offline** — `client is None or not client.is_connected()` (pattern `dm_reply_tracker.py:1504-1506`).
7. **Daily DM limit** — `db.is_account_dm_limit_reached(account_id)` (`database.py:2245`).
8. **User là nick nội bộ** — `is_internal_account_user(user_id)` (`dm_reply_tracker.py:920`, đã có HARD GUARD ở `_send_ai_message` `dm_reply_tracker.py:469`).

> **Nguyên tắc:** nếu bất kỳ check nào raise/lỗi → **bỏ qua lead đó** (fail closed), không gửi.

---

## 3. NỘI DUNG NHẮC LẠI — AI remix "gentle nudge"

### 3.1 Yêu cầu
- **KHÔNG copy y nguyên** tin lần 1 (Telegram spam filter + fingerprint trùng lặp).
- Giọng **nhẹ nhàng, 1–2 câu**, không ép, gợi mở giá trị mới (khác góc tiếp cận).

### 3.2 Tái dùng hàm nào (chính xác)

| Việc | Hàm tái dùng | Vị trí |
|---|---|---|
| Sinh nội dung chat đa lượt theo provider | **`ai_remix.generate_chat_response(messages_history, system_prompt, provider, api_keys, **kwargs)`** | `ai_remix.py:494` (đã được drip engine dùng ở `dm_reply_tracker.py:1531`) |
| Remix 1 đoạn text có sẵn (biến thể nhẹ) | `ai_remix.remix_message(original_text, provider, api_keys, custom_instruction=...)` | `ai_remix.py:403` |
| Làm sạch HTML/parse mode trước khi gửi | `dm_reply_tracker.sanitize_telegram_html(text)` | `dm_reply_tracker.py:410` (dùng ở `dm_reply_tracker.py:1533`) |
| Chặn lộ AI ("as an AI...") | `dm_reply_tracker.contains_ai_disclosure(text)` | `dm_reply_tracker.py:97` (dùng ở `dm_reply_tracker.py:1534`) |
| Lấy agent config (provider/keys/prompt) | `db.get_ai_agent(agent_id)` + `account_obj.get("ai_agent_id")` | `dm_reply_tracker.py:1509-1510` |

### 3.3 Prompt "gentle nudge" (đề xuất, dùng lại cấu trúc của `_drip_instruction_for_stage`)

```
[BỐI CẢNH]: Đây là TIN NHẮC LẠI LẦN 2 sau khi chúng ta đã gửi tin nhắn giới thiệu
đầu tiên (tin gốc: "<nội dung lần 1>") nhưng người nhận chưa phản hồi trong 24 giờ.

[NHIỆM VỤ]: Viết MỘT tin nhắn follow-up ngắn (1–2 câu), giọng thân thiện, KHÔNG hối
thúc, KHÔNG lặp lại nguyên văn tin đầu. Mở một góc MỚI (ưu đãi phí Maker/VIP tier,
hoặc hỏi nhẹ xem họ có quan tâm không). Kết thúc bằng câu hỏi mở để dễ trả lời.
KHÔNG nêu bạn là AI. KHÔNG dùng từ ngữ gây áp lực.
```

> Tái dùng đúng pattern ghép prompt đã có: `combined = sys_p + "\n\n[...INSTRUCTION]: " + instruction` (`dm_reply_tracker.py:1514-1515`).
> **Tái dùng `_drip_instruction_for_stage(stage=1, ...)` (`dm_reply_tracker.py:1362`)** cũng chấp nhận được cho v1 nếu muốn ship nhanh — prompt này đã là bản "1 day, friendly, non-pushy".

---

## 4. AN TOÀN / CHỐNG SPAM (ƯU TIÊN #1)

Đây là điểm sống-còn vì userbot gửi thừa → **PeerFlood → ban**. Mọi hàng rào đã có sẵn, chỉ cần **đi qua đúng**, không tự mở đường gửi mới.

### 4.1 Serialize qua global send lock / queue hiện có

| Hàng rào | Cách tái dùng | Vị trí |
|---|---|---|
| **Per-chat send lock** (Forum confirm, AI reply, drip dùng chung 1 lock) | `from telegram_forum_inbox import get_send_lock` → `async with get_send_lock(account_id, user_id):` | `telegram_forum_inbox.py:49`; đã dùng trong `_send_ai_message` ở `dm_reply_tracker.py:467-468` |
| **Global DM lock + random 2–20 phút** (chỉ để lộ rõ cho DM hàng loạt) | `_get_dm_lock()` + `DM_DELAY_MIN_SECS/MAX` | `keyword_watcher.py:208-213, 328-366` |

**Đề xuất:** nudge đi qua **`_send_ai_message(account_id, user_id, text, drip_stage=...)`** (`dm_reply_tracker.py:465`) để **tự động thừa hưởng** per-chat lock + toàn bộ guard bên trong. **Đây là điểm tái dùng quan trọng nhất** — không viết đường `tg.send_text_message` mới.

> ⚠️ Nếu muốn chặt hơn (serialize toàn cục mọi đường DM như watcher), có thể wrap thêm `_get_dm_lock()` từ `keyword_watcher` — nhưng như vậy **couple module**; v1 khuyến nghị chỉ dùng `get_send_lock` per-chat + delay nhỏ ngẫu nhiên giữa các nudge trong batch (xem §4.3).

### 4.2 Delay ngẫu nhiên
- Trong batch nudge, chèn `await asyncio.sleep(random.uniform(60, 300))` giữa các lần gửi (1–5 phút) — tái dùng tinh thần `MIN_DELAY/MAX_DELAY` (`keyword_watcher.py:275-276`) và delay trong `_send_dm_with_fallback` (`keyword_watcher.py:350-358`).

### 4.3 Tôn trọng daily DM limit + PeerFlood cooldown
- Trước mỗi lần gửi: `limit_reached, count, limit = await db.is_account_dm_limit_reached(account_id)`; nếu `limit_reached` → skip (`database.py:2245`).
- Kiểm `_is_peerflood_blocked(acc_id)` (`keyword_watcher.py:230`); nếu bị block → skip toàn bộ lead dùng account đó trong chu kỳ.
- Khi `send_text_message` trả lỗi chứa `peerflood`/`floodwait` → để `telegram_client`/handler tự `_mark_peerflood` (đã có cơ chế ở `keyword_watcher.py:720`).

### 4.4 Dedup race-safe (in-mem + DB) + TOCTOU

> **TOCTOU (Time-Of-Check ↔ Time-Of-Use):** lead có thể reply **giữa lúc lên lịch và lúc gửi thật**. Query lúc quét đã "chưa reply" nhưng trước khi `send` KOL đã trả lời → nếu gửi nữa là spam + xấu hổ.

Cách chống (kết hợp cả 2, bắt buộc):

1. **In-mem guard:** đặt `(account_id, user_id)` vào một set `_pending_nudges` **trước** khi gửi, xoá sau (tái dùng pattern `_pending_ai_sends` ở `dm_reply_tracker.py:486,1448` + `_remove_ai_send_after_delay` `dm_reply_tracker.py:161`).
2. **Re-check ngay trước send, TRONG cùng lock** — thứ tự bắt buộc:
   ```
   async with get_send_lock(acc, uid):          # 1. chốt lock per-chat
       if await _has_inbound_since(acc, uid, sent_at):   # 2. RE-CHECK reply
           return                                    #    → không gửi
       if (acc, uid) in _pending_nudges: return      # 3. in-mem dedup
       _pending_nudges.add((acc, uid))
       ... send ...
   ```
   Đây chính là logic "re-check before send" mà `_send_ai_message` **đã làm sẵn** (`dm_reply_tracker.py:471-485`: re-read chat, kiểm status/human_takeover/blacklist/**`last_drip_stage`**). Nếu nudge đi qua `_send_ai_message`, chỉ cần đảm bảo bước re-check inbound (`_has_inbound_since`) nằm **trong** lock.
3. **DB dedup (nguồn sự thật bền):** bảng `nudges` (hoặc cột `last_nudge_at`) — insert **sau** khi gửi thành công, kèm UNIQUE `(account_id, target_user_id)` để chống gửi trùng khi 2 batch chạy chồng (§5).

### 4.5 Ước lượng rủi ro spam

| Kịch bản | Xác suất | Tác động | Hàng rào |
|---|---|---|---|
| Gửi trùng 1 KOL do 2 batch chạy song song | Thấp | Trung bình (report) | UNIQUE index `nudges` + `_pending_nudges` + batch LIMIT 20 + worker đơn |
| Gửi cho lead vừa reply (race) | Thấp | Trung bình | Re-check trong `get_send_lock` (§4.4) |
| Vượt daily limit → ban | Rất thấp | Cao | `is_account_dm_limit_reached` trước mỗi send (§4.3) |
| PeerFlood do dồn dập | Thấp | Cao | random delay 1–5 phút + `_is_peerflood_blocked` + cooldown 45 phút (`keyword_watcher.py:142`) |
| Nội dung trùng tin 1 → spam fingerprint | Thấp | Trung bình | AI remix bắt buộc (§3), chặn copy |
| Gửi cho lead đã handover/blacklist | Rất thấp | Trung bình | §2.1 loại trừ |

> **Tổng thể: RỦI RO THẤP** với điều kiện giữ đủ 4 hàng rào. Nudge là **một lần duy nhất** (max=1) nên trần phát sinh = đúng số lead đã DM thành công chưa reply, đã trừ limit.

---

## 5. STATE / DB — đề xuất tối thiểu (ưu tiên tận dụng bảng có sẵn)

**Nguyên tắc: ưu tiên tái dùng.** Nhưng vì nhóm mục tiêu **chưa có row `ai_followup_chats`**, không thể nhét `nudge_count` vào đó cho trường hợp phổ biến nhất.

### Phương án A (KHUYẾN NGHỊ v1) — bảng `nudges` tối giản

```sql
CREATE TABLE IF NOT EXISTS nudges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL,
    target_user_id INTEGER NOT NULL,
    watcher_id     INTEGER,
    campaign_id    INTEGER,
    channel        TEXT NOT NULL,          -- 'watcher' | 'campaign'
    nudge_count    INTEGER DEFAULT 1,
    last_nudge_at  TEXT DEFAULT (datetime('now')),
    status         TEXT NOT NULL CHECK(status IN ('sent','skipped','failed')),
    error_message  TEXT,
    UNIQUE(account_id, target_user_id)     -- dedup race-safe ở tầng DB
);
CREATE INDEX IF NOT EXISTS idx_nudges_lookup ON nudges(account_id, target_user_id);
CREATE INDEX IF NOT EXISTS idx_nudges_last  ON nudges(last_nudge_at);
```

- Đặt chỗ tạo bảng trong `database.py` cùng khu vực `watcher_dm_logs` (`database.py:350`) / `ai_followup_chats` (`database.py:797`) theo đúng pattern `CREATE TABLE IF NOT EXISTS`.
- **Ưu điểm:** không đụng bảng lõi; `UNIQUE` là hàng rào chống trùng cứng.

### Phương án B — tận dụng cột có sẵn, không bảng mới
- Nếu KOL **đã** có row `ai_followup_chats` (đã reply rồi im): dùng cột `last_drip_stage` (`database.py:836`) như "đã nudge hay chưa" và `last_user_message_at` (`database.py:837`) làm mốc im lặng. **Không cần bảng mới cho nhóm này** (đây là drip hiện có).
- Nhóm **chưa reply** vẫn cần Phương án A (hoặc bảng riêng), vì không có row.

> **Kết luận:** v1 dùng **A** cho nhóm "chưa reply", **B** (sẵn có) cho nhóm "đã reply rồi im". Chỉ **1 bảng mới nhỏ**.

---

## 6. UI — hiển thị tối thiểu trạng thái "đã nhắc lần 2"

| Chỗ | Hiện có | Thêm gì (tối thiểu) |
|---|---|---|
| **Trang AI Agent / Leads** (`static/js/ai_followup.js`) | `STATUS_META` map 5 trạng thái (`static/js/ai_followup.js:18-24`), filter theo `status` (`static/js/ai_followup.js:193-227`) | Thêm badge nhỏ **"🔔 Đã nhắc lần 2"** cạnh tên lead nếu có row `nudges.status='sent'`. Có thể thêm filter chip `nudged` |
| **Endpoint dữ liệu** | `GET /api/ai-followup/chats` (`routes/ai_followup.py:227`) trả list chat | Bổ sung field `nudged: true/false` / `nudge_count` (join `nudges`) |
| **Analytics / Stats** | `GET /api/ai-followup/stats` (`routes/ai_followup.py:219`) đếm theo status | Thêm 1 dòng đếm `nudged_total` |
| **Nút thủ công (debug)** | đã có `POST /trigger-drip` (`routes/ai_followup.py:252`) | Thêm `POST /trigger-nudge` gọi worker nudge (giống pattern trigger-drip) |

> Tối thiểu đủ dùng: **1 badge + 1 field trong endpoint chats + 1 nút trigger**. Không cần màn hình mới.

---

## 7. KẾ HOẠCH TRIỂN KHAI (backend → test → UI, mỗi bước 1 task nhỏ)

| # | Task | File đụng tới | Việc làm | Test cần viết |
|---|---|---|---|---|
| **T7.1** | Migration bảng `nudges` + index | `database.py` (§5, cạnh `watcher_dm_logs`/`ai_followup_chats`) | `CREATE TABLE IF NOT EXISTS nudges` + index; hàm `get_nudge_candidates(limit)` (query §1.2), `record_nudge(...)`, `has_inbound_since(...)` | `tests/test_nudge_store.py`: tạo bảng idempotent; UNIQUE chặn trùng; `has_inbound_since` đúng |
| **T7.2** | Nhận diện ứng viên + re-check inbound | `database.py` (query), helper mới trong `dm_reply_tracker.py` | Hàm `_pick_nudge_candidates()` (bám `watcher_dm_logs`/`dm_campaign_logs` + NOT EXISTS `dm_replies` + dedup) | `tests/test_nudge_candidates.py`: lead đã reply → loại; đã blacklist → loại; quá 120h → loại; đã nudge → loại |
| **T7.3** | Worker nudge (core) | `dm_reply_tracker.py` | Hàm `process_outreach_nudges()`: chọn ứng viên → remix nội dung (§3) → **re-check trong `get_send_lock`** → `_send_ai_message(drip_stage=1)` → `record_nudge` | `tests/test_nudge_worker.py`: **không gửi** nếu reply giữa lúc quét & gửi (TOCTOU, mock); **không gửi** nếu daily limit; **không gửi** nếu PeerFlood; dedup in-mem chặn gửi lại |
| **T7.4** | Gắn worker vào loop nền + endpoint trigger | `dm_reply_tracker.py:1255` (`_followup_periodic_loop`), `routes/ai_followup.py:252` | Gọi `process_outreach_nudges()` trong loop 5 phút (bọc try/except); thêm `POST /trigger-nudge` | `tests/test_nudge_loop.py`: loop gọi không crash khi DB lỗi; endpoint trả `{status, result}` |
| **T7.5** | Setting cấu hình | `routes/ai_followup.py` (settings `150-216`), `dm_reply_tracker.py` (`_load_drip_cadence` pattern `1309`) | Setting `outreach_nudge_delay_hours` (default 24) + `outreach_nudge_max` (default 1) | `tests/test_nudge_settings.py`: invalid → fallback 24; max=0 → tắt |
| **T7.6** | UI badge + field + stats | `static/js/ai_followup.js:18-24,227,249`, `routes/ai_followup.py:219,227` | Thêm build `nudged`/`nudge_count` vào `/chats`; badge 🔔; đếm `nudged_total` ở `/stats` | `tests/test_nudge_ui_contract.py` (JS/JSON contract) hoặc mở rộng test route |
| **T7.7** | Index tối ưu (tuỳ chọn) | `database.py` (cạnh index `dm_replies` `554`) | Thêm `idx_dm_replies_sender ON dm_replies(sender_user_id, received_at)` | `tests/test_nudge_store.py` (assert index tồn tại) |

**Thứ tự bắt buộc:** T7.1 → T7.2 → (T7.3 ‖ T7.5) → T7.4 → T7.6 → T7.7.
**QA gate (theo `AGENTS.md`):** chạy `python run_tests.py` toàn bộ trước khi chốt; test nudge phải **kiểm hành vi thật** (mock `send_text_message` await_count), không snapshot source.

> Lưu ý phối hợp: T6 (task này) chạy **sau T4** vì cùng đụng `dm_reply_tracker.py`; T7.3/T7.4 cũng sẽ đụng file này nên phải serialize với các task khác trên cùng file.

---

## 8. LAZIER ALTERNATIVE (1 dòng)

> **Cách nhẹ nhất để thử nhanh:** tái dùng nguyên drip engine hiện có — trong lúc gửi DM lần 1 thành công ở `keyword_watcher.py`, gọi luôn `db.get_or_create_followup_chat(...)` để tạo row `ai_followup_chats` với `lead_tier='Tier B'`, rồi để drip tự động (`process_drip_followups`, `dm_reply_tracker.py:1383`) nhắc sau 24h — **không cần bảng mới, không cần worker mới**, chỉ cần bỏ điều kiện gate Tier nếu muốn phủ cả Tier C.

---

## 9. BẢNG: THÀNH PHẦN TÁI DÙNG vs PHẦN MỚI

| Hạng mục | ♻️ TÁI DÙNG | ➕ MỚI |
|---|---|---|
| Nguồn sự kiện | `watcher_dm_logs` (`database.py:350`), `dm_campaign_logs` (`database.py:736`), `dm_replies` (`database.py:540`) | — |
| Mốc im lặng | `last_user_message_at` (`database.py:837,4523`) | — |
| Send pipeline | `_send_ai_message` (`dm_reply_tracker.py:465`), `get_send_lock` (`telegram_forum_inbox.py:49`) | — |
| Chống spam lock/delay | `_get_dm_lock`+`DM_DELAY_*` (`keyword_watcher.py:208-213,350`), `_pending_ai_sends` (`dm_reply_tracker.py:486`) | `_pending_nudges` (biến thể) |
| Daily limit / PeerFlood | `is_account_dm_limit_reached` (`database.py:2245`), `_is_peerflood_blocked` (`keyword_watcher.py:230`) | — |
| AI remix | `ai_remix.generate_chat_response` (`ai_remix.py:494`), `sanitize_telegram_html` (`dm_reply_tracker.py:410`), `contains_ai_disclosure` (`dm_reply_tracker.py:97`) | Prompt "gentle nudge" (§3.3) |
| Loại trừ | `is_user_blacklisted` (`database.py:2395`), `is_account_paused` (`database.py:2493`), `is_internal_account_user` (`dm_reply_tracker.py:920`), stage gate (`dm_reply_tracker.py:1155`) | — |
| Trigger/loop | `_followup_periodic_loop` (`dm_reply_tracker.py:1255`), `POST /trigger-drip` (`routes/ai_followup.py:252`) | `POST /trigger-nudge` |
| DB state | cột `last_drip_stage`/`last_user_message_at` (cho nhóm đã reply) | **bảng `nudges`** + `has_inbound_since` (§5) |
| Worker | cấu trúc `process_drip_followups` (`dm_reply_tracker.py:1383`) làm khuôn | `process_outreach_nudges()` |
| UI | `STATUS_META` (`static/js/ai_followup.js:18`), `/chats`,`/stats` (`routes/ai_followup.py:219-236`) | badge 🔔 + field `nudged` |
| Settings | pattern `_load_drip_cadence` (`dm_reply_tracker.py:1309`) | `outreach_nudge_delay_hours`, `outreach_nudge_max` |

---

## 10. RỦI RO & LƯU Ý

1. **Không được tự mở đường gửi mới** — mọi nudge phải đi qua `_send_ai_message` / `get_send_lock` để thừa hưởng guard. Đây là điều kiện an toàn số 1.
2. **`ai_followup_chats` là "chat có tương tác"**, không phải "đã outreach" — đừng query nhầm (đây chính là bug tiềm ẩn làm KOL không reply không bao giờ được nhắc).
3. **Nhóm đã reply rồi im** đã được drip lo — nudge mới **chỉ nhắm nhóm chưa reply** để tránh double-message.
4. **Giới hạn batch 20 + LIMIT query** để không dồn dập (tái dùng đúng LIMIT 20 của drip `dm_reply_tracker.py:1483`).
5. **Cần index `dm_replies.sender_user_id`** khi dữ liệu lớn (§7 T7.7), nếu không query NOT EXISTS sẽ chậm dần.
6. **Test bắt buộc cho TOCTOU** (reply giữa chừng) — đây là kịch bản dễ vỡ nhất.

---

## 11. Đối chiếu tiêu chí nghiệm thu

- [x] Trả lời đủ 8 mục (1 §1, 2 §2, 3 §3, 4 §4, 5 §5, 6 §6, 7 §7, 8 §8).
- [x] Mọi khẳng định về code hiện có kèm `file:line` đã đọc.
- [x] Bảng tái dùng vs phần mới (§9) + ước lượng rủi ro spam (§4.5).
- [x] KHÔNG viết code sản phẩm — chỉ doc + kế hoạch.
- [x] Không sửa file code nào khác, không commit git.
