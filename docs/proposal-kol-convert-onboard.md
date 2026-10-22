# Đề xuất: hoàn thiện tiến trình Convert + Onboard KOL

Ngày: 2026-10-21 · Trạng thái: **ĐỀ XUẤT — chờ founder chốt, chưa code**
Nguồn: code `tg-scheduler` (đọc trực tiếp), DB `data/scheduler.db` (read-only), Obsidian `1-Projects/tg-scheduler/*`, `threads-kol-outreach/*`.

## 1. Hiện trạng (bằng chứng)

Phễu thật trong DB (398 lead đã reply): Tier A 22 · B 62 · C 314; status: active 272 · bot_ignored 111 · needs_human 15 · **onboarded 0**; stage: new 79 · nurture 208 · paused 111 · **signed 0**; verification: none 397 · requested 1. `kol_profiles` 77 hồ sơ, chỉ **11 có weex_uid**, 5 có chọn campaign. `referral_*` = 0 dòng.

Outreach → Reply → AI follow-up → Handover đã tốt (Forum Inbox, Command Bot, drip theo tier, SLA alert). **Từ handover trở đi mọi thứ là nhãn thủ công**, `@weexwill` làm tay.

## 2. Lỗ hổng đã xác minh

| # | Lỗ hổng | Vị trí |
|---|---|---|
| G1 | Hai máy trạng thái chồng nhau (`status` vs `stage`); `onboarded` chỉ là nút web, `signed` chỉ là lệnh `/stage` | database.py:4970, routes/ai_followup.py:291 |
| G2 | `[ONBOARDED]` là nhánh chết (không prompt nào sinh ra) | dm_reply_tracker.py:770 |
| G3 | `stage` không bao giờ tự tiến | chỉ `set_followup_stage` từ command bot |
| G4 | UID chỉ là text LLM trích; không validate, không đổi trạng thái | kol_profiles.weex_uid |
| G5 | `verification_status` không ai đọc (không analytics, không logic) | database.py:5151 |
| G6 | Dynamic lead score hỏng: gọi `db_conn.get_followup_chat` trên connection thô + cột `first_user_message_at` không tồn tại → luôn rơi về điểm tĩnh | lead_scoring.py:215,233,260 |
| G7 | Referral: `update_attribution_status`/`create_referral_reward` không ai gọi; stats đếm `'signed'` không bao giờ set → conversions luôn 0 | database.py:6376,6391,6411 |
| G8 | Không có funnel thật trên UI; backtest coi `intent≥70` là "converted" | backtest/analytics.py:18 |
| G9 | Không lưu volume proof, hợp đồng/commission đã chốt, payout | không có |
| G10 | `needs_human` tự resume AI sau 12h → lead đang chốt deal có thể bị AI chen | dm_reply_tracker.py:1146 |
| G11 | Active-hours chỉ áp ở watcher/warmup, không áp ở drip/nudge/campaign | keyword_watcher.py:564 |

## 3. Đề xuất — một pipeline duy nhất

Hợp nhất thành **`kol_stage`** (một nguồn sự thật), `status` chỉ còn điều khiển AI (active/paused/bot_ignored):

`new → engaged → qualified(Tier A/B) → handover → uid_submitted → verified → onboarded → active_kol` (+ `declined`, `dormant`)

Mỗi bước có **sự kiện vào** rõ ràng, ghi vào bảng log `kol_stage_events (lead, from, to, actor, reason, ts)` để đo thời gian từng bước và audit.

### Giai đoạn triển khai

| Pha | Nội dung | Giải quyết | Rủi ro |
|---|---|---|---|
| **P0 – sửa nền (nhỏ, nên làm trước)** | Fix `score_lead_dynamic`; bỏ/đấu nối `[ONBOARDED]`; bỏ đếm `'signed'` sai; test E2E UID→stage | G2 G6 G7(một phần) | Thấp |
| **P1 – state machine + funnel** | Cột `kol_stage` + bảng events; migrate từ status/stage cũ; auto-advance (handover→`handover`, UID trích được→`uid_submitted`); endpoint `/api/analytics/kol-funnel` + card Dashboard (reply → qualified → UID → verified → onboarded, thời gian TB mỗi bước) | G1 G3 G5 G8 G10 | Trung bình (migration) |
| **P2 – Convert** | Hàng đợi "Cần xác minh UID" cho Will: nút Verify/Reject, lưu volume proof (ảnh/link) + ghi chú; nếu WEEX có affiliate API thì tự check UID (**Không có thông tin** — cần founder xác nhận có API không); SLA alert theo bước | G4 G9 | Phụ thuộc WEEX |
| **P3 – Onboard** | Checklist onboarding mỗi KOL (UID ✔, vipCode ✔, campaign chọn ✔, link affiliate ✔, vào group KOL ✔, bài đăng đầu tiên ✔); wizard trong modal; khi đủ checklist → `onboarded` + bật `distribution_enabled` + gửi welcome pack; nối referral (attribution→converted, reward) | G7 G9 | Trung bình |
| **P4 – Vận hành** | Active-hours cho drip/nudge/campaign; backtest dùng stage thật; cập nhật HDSD + changelog | G8 G11 | Thấp |

## 4. Quyết định cần founder chốt

1. WEEX có affiliate API/export để check UID tự động không? Nếu không → Will verify tay qua hàng đợi P2.
2. Điều kiện `verified`: chỉ UID hợp lệ, hay UID + volume proof tối thiểu (bao nhiêu)?
3. Commission/offer chuẩn theo tier KOL (Obsidian không có số — "Không có thông tin"); fixed-fee Tier A (đã ghi chờ duyệt ở Decision Log 28/08).
4. Chính sách `needs_human`: giữ auto-resume 12h hay khoá AI đến khi Will đóng bước?
5. Có build chung với Task-121 (Community Engagement) không, hay tách?

## 5. Kiểm thử bắt buộc (theo AGENTS.md)

Test mới: chuyển stage từng bước + từ chối bước nhảy cóc, migration idempotent, funnel khớp số đếm DB, E2E (reply có UID → uid_submitted → verify → checklist đủ → onboarded → xuất hiện trong `get_kols_for_distribution`). Chạy `python run_tests.py` trước khi chốt; cập nhật `routes/changelog.py` + `docs/help-content.md`.
