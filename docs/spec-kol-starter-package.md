# Đặc tả: Gói Starter duyệt sẵn + Bộ nhớ trạng thái KOL (5 bước)

Ngày: 2026-10-21 · Trạng thái: **CHỜ FOUNDER CHỐT "ĐỒNG Ý" — chưa code**
Phạm vi: 2 việc ưu tiên đã thống nhất. Launch kit, đo activation, Pha 0 (fix nền) làm sau, không nằm trong đặc tả này.

---

## A. Gói Starter — thang commission chuẩn (đã founder chốt 21/10)

Áp dụng cho **mọi KOL**, mọi khu vực. Thay toàn bộ số đang nằm rải trong prompt/KB.

| Bậc | Điều kiện (tổng volume / tháng lịch) | RevShare | Bonus / 1M **taker** | Tổng trả ra ước tính* |
|---|---|---|---|---|
| 1 | Khởi điểm (cao hơn affiliate thường 5%) | 55% | $100 | ~71,7% |
| 2 | ≥ 10M | 60% | $100 | ~76,7% |
| 3 | ≥ 20M | 65% | $100 | ~81,7% |
| 4 | ≥ 50M | 70% | $50 | ~78,3% |
| 5 | ≥ 100M | 80% | $10 | ~81,7% |

\* tính trên taker 0.06% (1M taker = $600 phí); chỉ để nội bộ kiểm tra, AI không nói số này.

**Quy tắc vận hành:**
- **Lên bậc:** tính theo **tháng lịch**; đạt mốc tháng này → áp bậc mới từ **tháng sau**. Tháng đầu luôn 55%.
- **Hạ bậc:** chỉ khi **review theo quý**, nếu volume tụt dưới mốc.
- **Volume mốc** = tổng volume (không chỉ taker). **Bonus** tính theo volume taker.
- **Trả tiền:** commission **hàng ngày**; bonus trả **mùng 10** tháng sau.
- **Volume hợp lệ:** loại trừ tự trade và wash volume; volume từ **sub-affiliate được tính**.
- **Lý do AI được dùng** khi KOL so sánh với sàn khác: không trade ngược user, có KYT/compliance, có campaign thật, thưởng cho cộng đồng organic. **Không** nhắc tên sàn khác, **không** dùng "B-book".

**Ranh giới AI (chốt / handover):**

| AI được tự chốt | Bắt buộc `[HANDOVER_REQUIRED]` |
|---|---|
| Nguyên thang trên, cách tính, lịch trả, loại trừ | Tiền upfront / trading funds / CPA / fixed-fee |
| Giải thích vì sao bonus giảm ở bậc 4–5 ("ưu tiên revshare bền hơn") | Bậc ngoại lệ, % ngoài thang, đổi mốc |
| Chọn 1 campaign đang hiệu lực phù hợp | Yêu cầu hợp đồng/giấy tờ, thanh toán token khác |
| Nhận UID, xác nhận đã ghi | Tranh chấp payout, volume không khớp |

### A1. Lưu trữ
Setting mới `kol_offer_ladder` (JSON có cấu trúc, **không phải văn bản tự do**), chỉnh trong tab **Cấu hình AI** bằng form bảng (bậc / mốc / revshare / bonus) + các trường quy tắc. Có `version` + `effective_from` để biết KOL nào chốt theo bản nào. Lịch sử phiên bản lưu trong `kol_offer_ladder_history`.

### A2. Đưa vào AI
- `dm_reply_tracker.py:687-689` (rule 7, chia theo ngôn ngữ 70–80% / 50–60%): **xoá**, thay bằng khối sinh tự động từ `kol_offer_ladder` (render bảng + quy tắc + ranh giới).
- `routes/ai_agents.py:115-120` (KB agent mẫu Tier 1/2/3 theo volume): **thay** bằng cùng khối sinh tự động.
- Rule `ANTI-WIZARD` (`dm_reply_tracker.py:694`): sửa thành "tự nhiên, **một bước tiếp theo mỗi lượt**", bỏ cấm tuyệt đối.
- Hồ sơ KOL có `volume_claimed` → AI được nói "với X M bạn khai, bạn ở bậc N từ tháng sau"; không có → không dự đoán bậc.
- Guard code (ngoài prompt): regex bắt số % hoặc $ trong reply AI **không có trong thang** → thay bằng câu fallback + `needs_human`. Cùng cơ chế với `contains_ai_disclosure()` hiện có.

---

## B. Bộ nhớ trạng thái KOL — 5 bước

Thay hai máy trạng thái chồng nhau hiện nay (`ai_followup_chats.status` + `stage`, `PIPELINE_STAGES` tại `database.py:4970`). `status` chỉ còn điều khiển AI (`active` / `paused` / `bot_ignored`).

### B1. Bước và sự kiện vào

| # | `kol_step` | Sự kiện chuyển vào | Ai ghi |
|---|---|---|---|
| 0 | `prospect` | lead reply lần đầu (mặc định) | hệ thống |
| 1 | `deal_agreed` | KOL đồng ý thang (AI tag `[DEAL_AGREED]` **hoặc** admin bấm) | AI/admin |
| 2 | `uid_received` | UID hợp lệ xuất hiện (regex + LLM trích, cần 2 nguồn khớp) | hệ thống |
| 3 | `link_confirmed` | admin xác nhận đã gắn affiliate/vipCode (nút trong modal/Forum topic) | admin |
| 4 | `first_post` | KOL gửi link bài / admin xác nhận | AI tag `[FIRST_POST]` / admin |
| 5 | `active` | có volume hợp lệ kỳ đầu (admin nhập hoặc import từ WEEX nếu có) | admin |
| — | `declined` / `dormant` | từ chối rõ / không phản hồi > N ngày sau bước 1 | AI tag / cron |

Không được nhảy cóc quá 1 bước tiến bằng tự động; admin được set bất kỳ. Mỗi lần chuyển ghi `kol_step_events(lead_id, from, to, actor, reason, ts)`.

### B2. Bộ nhớ KOL có cấu trúc
Tách `kol_profiles.profile_json` thành cột rõ (giữ `profile_json` cho phần tự do):
`channel_link, platform, followers, weex_uid, volume_claimed, volume_verified, offer_version, selected_campaign, vip_code, affiliate_link, missing_items (JSON), last_kol_reply_at, last_our_reply_at`.

`missing_items` = tự tính từ bước hiện tại (vd ở `deal_agreed` mà thiếu UID → `["weex_uid"]`). AI nhận khối "ĐÃ CÓ / CÒN THIẾU" mỗi lượt → **không hỏi lại**, chỉ hỏi đúng thứ thiếu.

### B3. Follow-up theo bước (thay drip theo tier cho lead sau bước 1)
- Chỉ nhắc KOL **đã `deal_agreed`** trở lên, nội dung = **đúng 1 item thiếu**.
- Tối đa **2 nhắc/bước**, cách ≥ 48h, trong active-hours của account; dừng hẳn khi `declined` hoặc KOL nói phiền (tag `[STOP]`).
- Lead chưa tới bước 1 giữ drip theo tier hiện tại (không đổi trong phạm vi này).
- `needs_human` + bước ≥ 1: **tắt** auto-resume 12h — AI chỉ bật lại khi admin bấm.

### B4. Hiển thị
- Modal lead và header Forum topic: thanh 5 bước + danh sách còn thiếu + bậc hiện tại/dự kiến.
- Dashboard: card đếm KOL theo bước (thay card theo `status`). Command Bot `/pipeline` đọc `kol_step`.
- Analytics activation (bài đăng, volume, 7/30 ngày) **ngoài phạm vi** — chờ trả lời WEEX API.

### B5. Migration
`stage=signed` → `deal_agreed`; `status=onboarded` → `link_confirmed`; có `weex_uid` → ít nhất `uid_received`; còn lại `prospect`. Idempotent, có backup DB trước (cơ chế `alerts.py` sẵn có).

---

## C. Kiểm thử (bắt buộc trước khi bàn giao)
1. Render thang → chuỗi prompt khớp JSON; đổi setting → prompt đổi, version tăng.
2. Guard số lạ: reply có "75%" / "$200 upfront" → fallback + `needs_human`; reply đúng thang → đi qua.
3. Chuyển bước: hợp lệ / nhảy cóc bị từ chối / admin override / events ghi đủ.
4. UID: regex + LLM khớp → `uid_received`; chỉ 1 nguồn → không chuyển, ghi `missing_items`.
5. Follow-up: cap 2/bước, ≥48h, dừng khi `declined`/`[STOP]`, không gửi ngoài active-hours.
6. Migration idempotent trên bản sao `data/scheduler.db`.
7. Full suite `python run_tests.py` xanh; cập nhật `routes/changelog.py` + `docs/help-content.md`.

## D. Ngoài phạm vi / chờ
- WEEX affiliate API để verify UID & import volume (**Không có thông tin** — bước 5 tạm nhập tay).
- Launch kit (mẫu bài, banner), đo activation, Pha 0 fix nền → đợt sau.
- Test lại provider AI (lần 20/10 chưa có kết quả — cần log/endpoint từ founder).
