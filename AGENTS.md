# tg-scheduler — quy tắc cho trợ lý AI

## Cách làm việc
- Trả lời bằng tiếng Việt, ngắn gọn, nói thật: chưa kiểm chứng thì nói rõ là chưa.
- Việc nhỏ, rõ ràng: làm luôn. Việc đổi hành vi gửi tin, quyền hạn, dữ liệu
  hoặc có nhiều hướng: tóm tắt phương án (bảng ưu/nhược) và chờ duyệt.
- Khi tìm lỗi: đọc file thật và dẫn file:dòng làm bằng chứng, không đoán.
- Chỉ báo "xong" sau khi đã chạy test liên quan và nêu kết quả.

## Quy chuẩn kỹ thuật
- Frontend: dùng App.toast(msg, 'success'|'error'). App.showToast không tồn tại.
- Chèn dữ liệu vào HTML: esc(); trong onclick="f('...')": escJs().
- Đổi file JS thì tăng ?v= của file đó trong static/index.html.
- Giữ UTF-8 cho mọi file (tiếng Việt có dấu).
- Database: luôn `async with get_db() as db:`; không gọi aiosqlite.connect trực tiếp.
- Nick Telegram: trước khi cho nick gửi/hành động, gọi flood_state.blocked_reason(acc_id)
  (tạm dừng, đang nghỉ FloodWait, PeerFlood). Gặp FloodWait thì flood_state.mark().
- Chọn nhà cung cấp AI: chỉ dùng ai_remix.resolve_provider(agent).
- AI gửi cho khách: không lộ nhãn nội bộ, nguồn tài liệu, con số ngoài bảng hoa hồng.

## Trước khi bàn giao
- `ruff check .` và `python run_tests.py` phải sạch (xem skill `release`).
- Commit với message mô tả đúng thay đổi; không commit .env, sessions/, data/.
