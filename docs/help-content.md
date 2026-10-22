# Hướng dẫn sử dụng TG Scheduler

**Phiên bản:** v3.10.1 (22/10/2026) — LATEST

TG Scheduler là công cụ tự động hóa Telegram (userbot) giúp lên lịch gửi tin nhắn, DM tự động theo từ khóa, tăng tương tác, cào thành viên, KOL outreach, AI follow-up và quản lý nhiều tài khoản.

**Cảnh báo quan trọng:** Sử dụng userbot có rủi ro bị Telegram khóa tài khoản nếu gửi spam hoặc vi phạm điều khoản. Bắt đầu với số lượng nhỏ để test.

---

## 1. Giới thiệu

TG Scheduler giúp:

- Quản lý nhiều tài khoản Telegram (multi-account).
- Lên lịch gửi tin nhắn tự động (text, ảnh, video, file, poll) đến group/channel.
- Tự động DM khi phát hiện từ khóa (Keyword Watcher) hoặc thành viên mới join (Join Watcher).
- Tăng tương tác (reaction, view boost) trên kênh.
- Cào thành viên, chạy chiến dịch DM hàng loạt (Campaign), KOL outreach.
- AI Agent hỗ trợ trả lời tự động và remix tin nhắn.
- Forum Inbox (Sales War Room): đưa lead vào group Forum (Topics) để xử lý.
- Discord watcher + reaction.
- Analytics, lịch sử, backup, cảnh báo.

**Nguồn tham chiếu:** `README.md:1-86`, `routes/changelog.py:1-1707`.

---

## 2. Cài đặt & Khởi động

### Yêu cầu

- Python 3.11+ (khuyến nghị).
- Tài khoản Telegram (số điện thoại quốc tế).
- API ID & API Hash lấy tại https://my.telegram.org.
- `.env` chứa `DASHBOARD_SECRET_KEY` (bắt buộc).

### Biến môi trường (.env)

Tạo file `.env` từ `.env.example`:

```env
API_ID=2040
API_HASH=b18441a1ff607e10a989891a5462e627
PHONE_NUMBER=+849xxxxxxx
HOST=127.0.0.1
PORT=8888
TIMEZONE=Asia/Ho_Chi_Minh
DASHBOARD_SECRET_KEY=your_secret_key_here   # BẮT BUỘC
DISABLE_AUTH=0                               # Chỉ dùng dev
TG_RUNTIME_MODE=combined                     # combined | web
DATA_DIR=data
```

- `DASHBOARD_SECRET_KEY`: Khóa API (header `X-API-Key`). Sinh bằng: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `DISABLE_AUTH=1`: Tắt xác thực (chỉ dev).
- `TG_RUNTIME_MODE=web`: Chỉ chạy HTTP (không nạp Telethon). Dùng worker riêng.
- `DATA_DIR`: Thư mục chứa DB và lock (singleton).

**Nguồn:** `main.py:41-43,74-88`, `.env.example:1-16`, `SPLIT_RUNTIME.md:1-41`.

### Chạy

```bash
pip install -r requirements.txt
python main.py
```

Mở http://127.0.0.1:8888. Nhập `X-API-Key` vào localStorage (Settings → API Key).

**Log:** `startup_*.log`, `srv*.log`.

**Nguồn:** `main.py:315-318`, `engine_lifecycle.py:24-82`.

---

## 3. Đăng nhập tài khoản Telegram

### Thêm tài khoản

1. Vào tab **Tài khoản** → **+ Thêm tài khoản**.
2. Nhập:
   - **Số điện thoại** (+84xxxxxxxxx).
   - Proxy (tùy chọn, `socks5://user:pass@host:port`).
3. Nhấn **Gửi OTP** → Nhập mã OTP (6 số).
4. Nếu 2FA: Nhập mật khẩu.

**Ghi chú:** Không cần nhập API ID/API Hash trên giao diện — hệ thống tự dùng cấu hình mặc định (Telegram Desktop). Chỉ cần đổi khi muốn dùng API riêng (qua API/SQL).

**Bulk:** Chế độ **Hàng loạt** — mỗi dòng `số điện thoại | proxy` (proxy tùy chọn), OTP xác nhận lần lượt từng số.

**Proxy rotate:** Cài đặt `proxy_rotate_on_ban=1` (tự động đổi proxy khi PeerFlood).

**Giới hạn:** Không có chặn cứng số account trong code hiện tại (README cũ ghi "tối đa 10").

**Nguồn:** `routes/auth.py:58-154`, `static/index.html:48-96,1677-1693`, `telegram_client.py:27,65-82`.

### Quản lý session

> ⚠️ **CẢNH BÁO BẢO MẬT:**
> - **KHÔNG chia sẻ / KHÔNG commit / KHÔNG backup công khai** thư mục `sessions/`.
> - Mỗi file `.session` = quyền truy cập đầy đủ vào tài khoản Telegram tương ứng; lộ file = mất tài khoản.
> - Thư mục `sessions/` phải nằm trong `.gitignore` (repo này đã có sẵn dòng `sessions/`).

- Session lưu tại `sessions/account_xxxxxxxx.session`.
- Xóa account → xóa session + disconnect.
- `routes/auth.py:89-116`.

---

## 4. Tạo lịch gửi tin

### Tạo lịch mới

Tab **Lịch gửi** → **+ Tạo lịch mới**.

Các trường:

- **Tên lịch**.
- **Tài khoản gửi**.
- **Loại lịch**:
  - `hourly`: Mỗi giờ (phút trong giờ).
  - `daily`: Hàng ngày (HH:MM).
  - `weekly`: Thứ (1-7, Mon-Sun), HH:MM.
  - `monthly`: Ngày trong tháng (1-31), HH:MM.
  - `once`: Ngày cụ thể (YYYY-MM-DD), HH:MM.
- **Giờ gửi** (HH:MM, múi giờ GMT+7).
- **Giới hạn số lần gửi** (`max_sends`, null = không giới hạn).
- **Tin nhắn** (thêm nhiều, thứ tự `msg_order`):
  - Text, Photo, Video, Document, Poll.
  - Poll: câu hỏi + options (JSON array), multiple.
- **Đích** (group/channel ID hoặc username).

**Preview:** Nút **Preview** gửi vào **Saved Messages** của account (không gửi thật).

**Test ngay:** Nút **Gửi ngay** → enqueue.

**Reset count:** Reset bộ đếm `current_sends`.

**Blocked targets:** Sau 3+ lỗi liên tiếp → block 2 giờ (tự reset). Xem `/blocked-targets`, unblock thủ công.

**Nguồn:** `routes/schedules.py:15-143`, `scheduler.py:48-101`, `message_queue.py:32-90`, `database.py:2144-2179`.

### Multi-media & Bulk

- Hỗ trợ nhiều tin nhắn / lịch.
- Upload media qua `/api/upload` (lưu `media/`).
- Bulk: Nhiều target, nhiều account.

---

## 5. Bulk sending + Rate limiting

### Hàng đợi & Giới hạn

- Mọi lịch → enqueue `message_queue`.
- Delay ngẫu nhiên 1.5–3.5 giây giữa các tin.
- **FloodWait:** Ngủ đúng `e.seconds + jitter(5-15)`, tối đa 900s. Retry ≤3 lần → fail.
- **PeerFlood:** Tự động pause account + cảnh báo (`alerts.flood_guard`).
- **Block target:** Sau 3+ lỗi → block 2 giờ.
- **Daily limit campaign:** Premium mặc định 60 DM/ngày, Normal tối đa 10 DM/ngày (cứng).

**Nguồn:** `message_queue.py:20-22,116-187,221-240`, `database.py:2212-2253,2246-2262`.

### An toàn

- Bắt đầu nhỏ.
- Dùng proxy xoay.
- Theo dõi Logs + Account health.

---

## 6. Keyword Watcher

Tab **Keyword DM** → **+ Tạo rule mới**.

### Cấu hình

- **Tên rule**.
- **Tài khoản gửi DM** (nhiều, ưu tiên thứ tự).
- **Nick theo dõi nhóm** (observer — nick phải ở trong nhóm, khác sender).
- **Loại trigger**: `keyword` | `join`.
- **Từ khóa** (keyword watcher).
- **Loại trừ username** (không DM).
- **Nhóm theo dõi** (ID âm, hoặc dán link → tự join + lấy ID).
- **Cooldown** (giờ, mặc định 24).
- **DM 1 lần** (`dm_once`).
- **Delay Join DM** (3–15 phút, max 120).
- **Kịch bản DM** (nhiều tin nhắn, hỗ trợ AI remix).
- **Reply trong nhóm** (kèm text + account gửi).

**Join tự động:** Khi tạo watcher, nick observer/sender tự join group.

**Nguồn:** `routes/watchers.py:20-79`, `keyword_watcher.py:38-47,1150-1219`, `database.py:1675-1839,1902-1950`.

### Hành vi an toàn

- Dedup DB + in-memory.
- Bỏ qua admin, bot, self-add.
- Giới hạn DM/ngày theo account (premium/normal).
- AI remix tên + chủ đề nhóm nguồn.

---

## 7. Join Watcher

Cùng form Keyword Watcher, chọn `watch_type=join`.

- Trigger khi thành viên mới join.
- Delay 3–15 phút (anti-spam).
- Bỏ qua admin/bot/self-add.
- Cần ít nhất 1 sender account.

**Nguồn:** `routes/watchers.py:44-59`, `keyword_watcher.py:38`.

---

## 8. Reaction engine ("Tăng Tương Tác")

Tab **Tăng Tương Tác**.

### Thêm kênh

- Link kênh (`t.me/...` hoặc `@username`).
- Tài khoản tham gia & react.
- Cảm xúc (multi-select, mặc định 👍).
- Delay (5–30s mặc định).
- **Tăng view**: bật + tỷ lệ (25/50/75/100%).
- **Số acc thả mỗi bài**: 0 = tất cả; ví dụ 6–12 = random 6–12 acc.

**Auto-join:** Khi thêm target.

**Private link:** Join trước → resolve entity sau.

**Nguồn:** `routes/reactions.py:22-145`, `reaction_watcher.py`.

### Lịch sử

Tab Logs xem reaction thành công/thất bại.

---

## 9. Invite engine + Warmup

### Invite Campaign

Tab **Cào & DM** → **Invite Campaign**.

- Tạo từ scrape job.
- Invite mode: `direct` | `link`.
- Daily limit, delay 45–120s.
- DM kèm link invite.

**Nguồn:** `routes/invite.py:18-181`, `invite_engine.py`.

### Warmup

Tab **Warmup Nhóm** (ẩn trong sidebar, cần navigate thủ công).

- Tạo group + script (text, AI remix).
- Job: interval 30–120 phút, daily_post_limit 10, 09:00–22:00.
- Chạy job → post script ngẫu nhiên.

**Nguồn:** `routes/warmup.py:20-145`, `warmup_engine.py`.

---

## 10. Member / Campaign, KOL outreach, AI follow-up, AI agents

### Cào & DM (Members)

Tab **Cào & DM**.

- **Scrape**: group → deep crawl (depth) → lưu members.
- **Campaign DM**:
  - Nguồn: scrape job.
  - Tài khoản gửi (Premium ưu tiên).
  - Delay 30–90s mặc định.
  - Daily limit premium 60 / normal 10.
  - AI remix, exclude previous DMs.
  - Min lead score (ICP).
  - Auto-resume, scheduled.
- **Deep crawl queue**: pause/resume/stop.

**Nguồn:** `routes/members.py:238-1667`, `database.py:2966-3129`.

### KOL Outreach & AI Follow-up

Tab **AI Agent** (ai-followup).

- Cấu hình: system prompt, knowledge base, max replies, handover keywords, drip cadence (mặc định 24/72/168 giờ).
- Trạng thái AI (`status`): active / paused_admin / needs_human / bot_ignored. `onboarded` chỉ còn là alias cũ → chuyển bước **Gắn link** + tạm dừng AI.
- Gửi tay → pause AI + ghi history.
- Drip follow-up tự động (>48h im lặng) cho lead **chưa đồng ý deal**.
- KOL profile: weex_uid, affiliate_link, vip_code, volume_claimed, selected_campaign, distribution regions/campaigns.
- KOL channel auto-forward.

#### Bước onboarding KOL (kol_step) — spec `docs/spec-kol-starter-package.md`

Mỗi lead có **1 bước** duy nhất, nhớ xuyên suốt AI / Forum Inbox / Command Bot / web:

| Bước | Ý nghĩa | KOL còn thiếu | Team còn nợ |
|---|---|---|---|
| 0 prospect | chưa đồng ý | deal_agreement | — |
| 1 deal_agreed | đã OK deal | weex_uid | — |
| 2 uid_received | đã có UID | — | vip_code, affiliate_link |
| 3 link_confirmed | đã gắn link | selected_campaign, first_post_link | — |
| 4 first_post | đã có bài | — | volume_verified |
| 5 active | đang chạy | — | — |

Terminal: `declined` (KOL từ chối / AI trả `[STOP]`), `dormant`.

- Tự động (AI, hệ thống) chỉ tiến **đúng 1 bước**; nhảy cóc bị từ chối. Admin override mọi hướng qua panel **Bước** trên web, mọi thay đổi ghi vào `kol_step_events`.
- UID chỉ được nhận khi **2 nguồn khớp**: số xuất hiện trong tin KOL **và** LLM trích ra. Chỉ 1 nguồn → lưu `weex_uid_candidate`, không chuyển bước, `missing_items` vẫn có `weex_uid`.
- Follow-up theo bước (`process_step_reminders`): chỉ lead ≥ deal_agreed, nhắc đúng item KOL đang nợ, ≥48h, tối đa 2 lần/bước (reset khi sang bước mới), tôn trọng giờ hoạt động account, bỏ qua needs_human/declined. Setting: `kol_step_reminder_gap_hours` (48), `kol_step_reminder_max_per_step` (2). Trigger tay: `POST /api/ai-followup/trigger-step-reminders`.
- Từ deal_agreed trở đi AI **không** tự bật lại sau 12h; admin bật lại thủ công.
- Migration v1 chạy 1 lần khi khởi động (có backup): stage=signed → deal_agreed, status=onboarded → link_confirmed, có UID → uid_received, còn lại prospect.

#### Thang offer chính thức (kol_offer.py)

- Mặc định v1 (hiệu lực 21/10/2026): 55% ngay → ≥10M 60% → ≥20M 65% → ≥50M 70% → ≥100M 80% (tổng volume/tháng dương lịch); bonus $100/1M taker (bậc 4 $50, bậc 5 $10). Lên bậc theo tháng, review hạ bậc theo quý; commission hàng ngày, bonus mùng 10; loại tự trade/wash, sub-affiliate được tính.
- Chỉnh trong Tab AI Agent → **Thang offer** (preview đúng khối prompt). Lưu = version mới + ghi `kol_offer_ladder_history`.
- **Guard số lạ**: reply AI có % / $ / USDT ngoài thang hoặc cụm cấm (B-book) → thay bằng câu an toàn, chat chuyển needs_human, thông báo nick chính + Forum Inbox. Kiểm tra tay: `POST /api/ai-followup/offer-ladder/check {"text": ...}`.
- AI chỉ dự đoán bậc khi KOL đã nói volume (`volume_claimed`).

**Nguồn:** `routes/ai_followup.py`, `kol_offer.py`, `dm_reply_tracker.py` (render_kol_step_prompt, apply_kol_step_tags, process_step_reminders), `database.py` (set_kol_step, migrate_kol_steps_v1), `static/js/ai_followup.js`.

#### Guard AI — drip, nudge và self-learning (v3.10.1)

Ba đường gửi tin AI (reply trực tiếp, **drip follow-up**, **outreach nudge**) nay dùng chung một bộ guard:

- **Cả 3 đều nhận khối thang offer chính thức** trong system prompt (render từ `kol_offer`), không còn con số viết tay trong code.
- **Drip** (lead chưa đồng ý deal, tier cadence 12/24/48/96h · 24/72/168h · 48/168h): nếu tin AI có % / $ ngoài thang → **không gửi**, log `[DripEngine] 🚫 Blocked off-ladder figures`.
- **Nudge** (follow-up lần 2, >24h không reply): cùng quy tắc, và ghi vào bảng `nudges` với `error_message = "off_ladder:75%,$200"` để admin xem lại.
- Prompt drip **không còn** hard-code "$50,000 prize pool" — câu dẫn chỉ nhắc "a currently running trading campaign or the daily commission payout" và yêu cầu AI không tự chế %.
- **Self-learning**: rule học từ câu trả lời tay của admin mà chứa số ngoài thang được lưu `status = "pending"` (không nằm trong prompt approved-only) kèm WARNING log; rule đúng thang vẫn `approved`. Cần rà lại các rule `approved` cũ trong DB.

#### Schema self-check (v3.10.1)

Lúc khởi động, `init_db()` tự kiểm tra 7 bảng / 30+ cột mà phễu KOL phụ thuộc (`REQUIRED_SCHEMA`):

- Cột thiếu → `repair_schema()` tự thêm lại; không sửa được → log ERROR `[Schema] SELF-CHECK FAILED` và **bỏ qua** migration (`[KOLStep] migration SKIPPED`) thay vì chạy trên schema hỏng.
- Kiểm tra bằng tay: `python -c "import asyncio, database; print(asyncio.run(database.verify_schema()))"` — rỗng là sạch.

#### An toàn DM campaign (v3.10.1)

- **Reserve trước khi gửi**: bảng `dm_campaign_reservations` (khóa `campaign_id` + `target_user_id`). `sent` ⇒ không gửi lại; `pending` ⇒ giữ 24h rồi mới thử lại; `failed` ⇒ retry được. Crash giữa "Telegram đã nhận" và "ghi log success" không còn gửi trùng.
- **Hạn mức ngày đếm chung cho cả account**: đọc `get_account_daily_dm_count_live()` = watcher + campaign + reservation đang bay (giờ VN). 2 campaign dùng chung một nick không còn cùng vượt hạn mức.
- **account_busy**: mỗi lượt gửi giữ `account_busy(kind='dm', TTL 180s)` để không trùng với reaction / warmup / engagement trên cùng nick; nick đang bận thì bỏ qua và thử nick khác.

### AI Agents

Tab **Cấu hình AI Agents**.

- Tạo agent: name, emoji, provider (gemini/deepseek/openai/chatgpt_oauth/groq/9router), model, base_url, keys (round-robin), system_prompt, remix_instruction, knowledge_base, handover_keywords, max_replies, tone.
- Mặc định: Crypto Exchange BD Pro (WEEX).
- Test agent, duplicate, reset chats, learned rules (tự học từ admin).
- Fallback đa tầng provider.
- System prompt thực tế gửi AI = prompt agent + format rules + KB + **thang offer chính thức** + **KOL ONBOARDING STATE** (bước hiện tại, đã có / còn thiếu / team còn nợ). Tag điều khiển bước AI có thể trả: `[DEAL_AGREED]`, `[FIRST_POST]`, `[STOP]` (bị strip trước khi gửi).

**Nguồn:** `routes/ai_agents.py:20-363`, `static/index.html:1005-...`.

---

## 11. Forum Inbox (Sales War Room)

Tab **Cài đặt AI** → phần **Forum Inbox**.

### Yêu cầu

- Group đã bật **Topics**.
- Command Bot (từ @BotFather) là admin của group.
- Cấu hình Command Bot trước (token + admin IDs).

### Cấu hình

- Bật toggle.
- Dán link nhóm (`t.me/c/4352266125/1` hoặc `t.me/+...`) → **Tự lấy ID**.
- Group ID (âm, ví dụ `-1004352266125`).
- General Topic ID (thường = 1).
- Lưu → bot lắng nghe lead mới.

### Tính năng v1

- Mỗi lead 1 topic riêng.
- Chỉ relay **text** (media chưa hỗ trợ).
- Admin gửi từ group → bot preview + confirm → gửi từ nick phụ.
- Backfill (tạo topic cho hội thoại đã trả lời, 1–90 ngày).
- Replay history (nạp lịch sử vào topic).
- Command Bot hiển thị tên account; nếu chưa có tên thì dùng nhãn `Acc #id`.
- Header topic và Command Bot `/status`, `/pipeline` hiển thị thanh bước KOL (`🪜 Bước: ●●○○○ 2/5 · Còn thiếu: ...`). Reply AI bị guard số lạ chặn cũng báo vào đây kèm lý do.
- Cảnh báo **KOL CAMPAIGN PENDING** vẫn gửi tới Command Bot và Forum Inbox, nhưng mặc định không gửi vào Saved Messages. Admin bật lại bằng setting `kol_pending_saved_msg_enabled` = `1`.

**Nguồn:** `routes/settings.py:504-702`, `static/index.html:729-805`, `command_bot.py`, `telegram_forum_inbox.py`, `README.md:61-75`.

---

## 12. Proxy manager + Blacklist

### Proxy

Tab **Cài đặt AI** → **Proxy**.

- **Webshare**: API key → fetch → test → assign round-robin.
- **Paste**: nhập danh sách proxy (http/socks5) → test → merge pool.
- Assign 1 account / reassign tất cả / clear.
- Rotate on ban (setting `proxy_rotate_on_ban`).

**Nguồn:** `routes/proxy.py:22-262`, `proxy_manager.py`.

### Blacklist

Tab **Blacklist**.

- Thêm username / user_id / reason.
- Bulk (mỗi dòng 1 username).
- Tìm kiếm + lọc bot.
- Áp dụng toàn hệ thống (watcher, campaign, AI, drip).

**Nguồn:** `routes/blacklist.py:14-47`, `static/index.html:832-880`.

---

## 13. Analytics / Dashboard / Changelog

### Dashboard (Tổng quan)

- KPI: DM hôm nay, trả lời hôm nay, lead cần người thật, số account.
- Biểu đồ 14 ngày.
- Việc cần làm, lịch sắp tới, sức khỏe account.

### Analytics

Tab **Analytics**.

- Overview, daily stats, account health, campaign performance, agent evolution.
- Export CSV (members, campaign logs, contacts).
- Template library + performance.
- Auto-reply rules.

**Nguồn:** `routes/analytics.py:138-359`.

### Changelog

Tab **Cập Nhật**.

- Timeline release notes (feature / improvement / fix).
- Lọc theo loại.
- Phiên bản hiện tại: v3.7.12.

**Nguồn:** `routes/changelog.py:9-1707`, `static/js/changelog.js:10-187`.

---

## 14. Discord watcher

Tab **Discord**.

- Thêm bot (token từ Discord Developer Portal).
- Kết nối / ngắt / xóa.
- Xem servers & channels.
- Tạo watcher (keywords, channels, cooldown, dm_once, messages).
- Reaction targets Discord.
- Stats bots/watchers.

**Nguồn:** `routes/discord.py:32-261`, `static/js/discord.js:7-281`.

---

## 15. Dành cho ADMIN

### Runtime mode

- `TG_RUNTIME_MODE=combined` (mặc định): mọi thứ trong 1 process.
- `web`: chỉ HTTP + IPC queue. Worker (`telegram_worker.py`) chạy engines + consumer.
- Web mode: watcher CRUD, auth, schedules, reactions, members, ai-followup trả 503.

**Nguồn:** `main.py:41-296`, `SPLIT_RUNTIME.md:1-41`.

### Singleton lock

- File `data/engine.lock` (msvcrt/fcntl).
- Ngăn 2 combined hoặc combined + worker cùng DATA_DIR.

**Nguồn:** `engine_lifecycle.py:24-82`.

### Xác thực

- Luôn yêu cầu `X-API-Key` (trừ `DISABLE_AUTH=1`).
- Khóa sinh ngẫu nhiên 32 bytes.

**Nguồn:** `main.py:74-88`.

### Sao lưu & Vận hành

- `alerts.run_backup()`: backup SQLite giữ 7 bản.
- Heartbeat worker (15s).
- Log: `startup_*.log`, `srv*.log`.
- Health: `/api/health/telegram-worker`.
- Cập nhật: git pull + restart.

**Nguồn:** `alerts.py:3-151`, `engine_lifecycle.py:99-321`.

### engine.lock & DATA_DIR

- Mặc định `data/`.
- Biến `DATA_DIR` override.

---

## 16. Xử lý sự cố thường gặp (FAQ)

### FloodWait / PeerFlood

- FloodWait: Hệ thống tự ngủ đúng giây Telegram yêu cầu + jitter.
- PeerFlood: Account tự pause + cảnh báo. Đợi hoặc đổi proxy/IP.
- Xem Logs + Account health.

### Account bị hạn chế / PeerFlood

- Giảm tần suất.
- Dùng proxy xoay.
- Warmup trước khi chạy lớn.

### Dashboard không mở / 403 Unauthorized

- Kiểm tra `DASHBOARD_SECRET_KEY` trong `.env`.
- Header `X-API-Key` đúng (Settings → API Key).
- `DISABLE_AUTH=1` chỉ dev.

### Log "[Schema] SELF-CHECK FAILED" hoặc "[KOLStep] migration SKIPPED"

- Schema DB thiếu cột mà phễu KOL cần (thường do DB cũ từ trước v3.10.0).
- `init_db()` tự thử thêm lại (`repair_schema`). Nếu vẫn còn lỗi → kiểm tra quyền ghi file `data/scheduler.db` và file `scheduler.db-wal`.
- Kiểm tra tay: `python -c "import asyncio, database; print(asyncio.run(database.verify_schema()))"`.
- Đừng chạy migration trên schema hỏng — hệ thống cố tình bỏ qua để không đánh dấu "đã migrate" rồi bỏ rơi dữ liệu.

### KOL bị DM trùng / nhận 2 tin cùng lúc

- Reserve-before-send đã chặn ở bảng `dm_campaign_reservations`; nếu vẫn thấy trùng, kiểm tra 2 campaign có đang chạy song song trên cùng một nick (nên tách nick theo campaign).
- Xem `dm_campaign_reservations` để biết target nào đang `pending` (đang bay) và target nào `failed` (sẽ retry).

### AI trả lời số lạ (vd 75%) bị chặn

- Đây là guard đang hoạt động: reply/drip/nudge đều quét số ngoài thang commission.
- Nếu số đó **đúng** và bạn muốn cho phép: sửa thang ở Tab AI Agent → **Thang offer** (bản mới có version + lịch sử), hoặc thêm vào `extra_allowed_percents` / `extra_allowed_dollars` của thang.
- Rule học từ admin có số lạ bị giữ ở `status='pending'` — duyệt lại trong tab AI Agent.

### Watcher không chạy

- Kiểm tra account đã login + active.
- Reload watcher (nút trong form).
- Web mode: watcher CRUD không hỗ trợ.

### Session hết hạn / disconnect

- Logout → xóa session → login lại.
- Proxy lỗi → remove proxy → reconnect.

### Command Bot / Forum Inbox không hoạt động

- Bot phải là admin group.
- Admin IDs đúng.
- Chạy ở `combined` mode.
- Backfill/replay cần bot online.

### Lỗi 503 trong web mode

- Nhiều route chỉ hoạt động ở `combined`.
- Dùng `TG_RUNTIME_MODE=combined` cho full chức năng.

---

## 17. Bảng thuật ngữ ngắn

| Thuật ngữ       | Giải thích |
|-----------------|------------|
| **Watcher**     | Rule tự động DM khi phát hiện từ khóa hoặc thành viên mới join. |
| **Campaign**    | Chiến dịch DM hàng loạt từ danh sách thành viên đã cào. |
| **Lead**        | Người dùng đã tương tác (trả lời DM / join). |
| **FloodWait**   | Telegram yêu cầu chờ (thường  vài giây đến phút). |
| **PeerFlood**   | Tài khoản bị hạn chế tạm thời do gửi quá nhiều. |
| **Userbot**     | Ứng dụng Telegram dùng tài khoản cá nhân (không phải Bot API). |
| **Session**     | File `.session` chứa trạng thái đăng nhập Telethon. |
| **Join Watcher**| Watcher kích hoạt khi thành viên mới join nhóm. |
| **Forum Inbox** | Hệ thống đưa lead vào group Forum (Topics) để xử lý tập trung. |
| **Command Bot** | Bot Telegram nhận lệnh admin và relay tin nhắn từ Forum Inbox. |
| **ICP Score**   | Điểm chất lượng lead (dựa username, Premium, hoạt động gần đây). |
| **Drip Cadence**| Nhịp follow-up tự động sau N giờ im lặng. |
---

## 18. Cộng Đồng (Engagement)

### Tổng quan

Community Engagement Engine cho phép account Telegram join nhóm cộng đồng crypto (nhóm support sàn, nhóm trader) và tự động reply có giá trị khi có người hỏi câu hỏi liên quan. User tự tìm đến DM → funnel AI hiện có tiếp quản.

**Phiên bản v1 (REACTIVE):** chỉ reply khi có từ khóa trigger khớp. Không tự đăng bài.

### Tạo chiến dịch

1. Vào tab **🌐 Cộng Đồng** trong sidebar.
2. Điền thông tin: tên, nhóm mục tiêu (chat_id), accounts, từ khóa trigger, AI agent.
3. **Bắt buộc tick checkbox consent** trước khi bật chiến dịch.
4. Bấm "Tạo mới" hoặc "Lưu & Bật".

### Rate limit

| Ngưỡng | Giá trị mặc định |
|--------|------------------|
| Reply/nhóm/giờ | ≤ 4 (tối đa 6) |
| Reply/nhóm/ngày | ≤ 12 (tối đa 20) |
| Reply toàn cục/ngày/nick | ≤ 25 |
| Gap cùng nhóm | ≥ 180 giây |
| Gap cùng nick (toàn cục) | ≥ 120 giây |
| Số nick reply 1 tin | Đúng 1 |

### An toàn

- **Consent gate:** chiến dịch mặc định DENY. Phải tick xác nhận trước khi bật.
- **Auto-pause:** khi ≥2 nick bị mute/ban, hệ thống tự dừng chiến dịch.
- **Dedup:** 1 tin nhắn chỉ được 1 nick reply, không bao giờ 2 nick cùng reply.
- **Không reply admin/bot/nick mình:** tự động lọc bỏ.
- **Delay 20-90 giây:** trước khi reply, có typing simulation.

### Join nhóm

Dùng nút "Join dần dần" để account join nhóm với delay 15-45 phút giữa các account. Không join đồng loạt.

### Log & Sức khoẻ

- Panel sức khoẻ hiện reply hôm nay, reply giờ vừa rồi, nick bị mute.
- Bảng log chi tiết: thời gian, nhóm, nick, câu hỏi, reply, trạng thái, lý do skip.

**Nguồn tham chiếu:** `routes/engagement.py`, `claimer.py`, `account_busy.py`, `static/js/engagement.js`.

---

**Kết thúc.** Nội dung dựa hoàn toàn trên code thực tế (đã đối chiếu `routes/*.py`, `static/index.html`, `static/js/*.js`, engine files, changelog). Không có tính năng bịa đặt.

**Nguồn chính:** Toàn bộ repo H:/Hermes/tg-scheduler (đã đọc 40+ file).