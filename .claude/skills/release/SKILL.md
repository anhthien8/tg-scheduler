---
name: release
description: Kiểm tra, gộp và bàn giao một thay đổi của tg-scheduler. Dùng khi vừa sửa code xong và cần đưa lên main, hoặc khi người dùng nói "gộp", "deploy", "cập nhật server".
---

# Bàn giao một thay đổi

App chạy trên laptop của chủ dự án (không phải máy cloud). Phiên AI không deploy được; việc của bạn là đưa code sạch lên `main` và nói rõ bước người dùng phải làm.

1. **Kiểm tra**
   - `ruff check .` phải sạch (CI chạy đúng lệnh này).
   - `python run_tests.py` (Python + JS). Chạy nhanh một phần: `python -m pytest tests/<file> -q -p no:cacheprovider`.
   - Sửa JS thì `node --check static/js/<file>.js`.
2. **Giao diện**: file JS nào đổi thì tăng `?v=` của file đó trong `static/index.html`, nếu không trình duyệt giữ bản cũ tới 1 giờ.
3. **Commit** với message mô tả đúng thay đổi và lý do. Không commit `.env`, `sessions/`, `data/`.
4. **Gộp**: chỉ tạo PR / gộp vào `main` khi người dùng yêu cầu.
5. **Báo người dùng** đúng 3 việc họ phải làm trên laptop:
   ```
   git checkout main
   git pull
   ```
   rồi khởi động lại app (`run_split.cmd` hoặc `start_background.vbs`) và tải lại dashboard bằng Ctrl+F5.
6. Nói thật về phần chưa kiểm chứng (ví dụ: chưa bấm thử trên dashboard thật, chưa thử với Telegram thật).
