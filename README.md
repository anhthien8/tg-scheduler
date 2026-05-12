# TG Scheduler

Telegram Userbot Message Scheduler — lên lịch gửi tin nhắn tự động đến groups/channels bằng tài khoản cá nhân.

Donate and get more sass : 0xC68cF353119bE63D44f5252B47Da2B4F9152f7B6 ( bep20 )

## Tính năng

- 🔐 **Multi-Account** — quản lý nhiều tài khoản Telegram (lên đến 10)
- ⏰ **Đa dạng lịch gửi** — hàng giờ, hàng ngày, hàng tuần, hàng tháng, một lần
- 🔢 **Giới hạn số lần gửi** — tự động tắt khi đủ số lần
- 📨 **Multi-media** — text, ảnh, video, file, poll
- 🚀 **Bulk sending** — gửi nhiều tin nhắn đến nhiều group/channel
- 🛡️ **Rate limiting** — jitter + FloodWait handling + auto retry
- 👁 **Preview** — test gửi đến Saved Messages trước khi kích hoạt
- 🌏 **Timezone Vietnam** (GMT+7)
- 📊 **Dashboard** — KISS dark theme, quản lý trực quan

## Tech Stack

- **Backend**: Python, FastAPI, Telethon (MTProto), APScheduler, SQLite
- **Frontend**: HTML/CSS/JS thuần (Single Page App)

## Cài đặt

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/tg-scheduler.git
cd tg-scheduler

# Install dependencies
pip install -r requirements.txt

# Chạy
python main.py
```

## Sử dụng

1. Mở `http://localhost:8888`
2. Thêm tài khoản Telegram (cần API ID & Hash từ [my.telegram.org](https://my.telegram.org))
3. Nhập OTP để đăng nhập
4. Tạo lịch gửi → chọn account, group/channel, thêm tin nhắn → Lưu

## Cấu trúc

```
tg-scheduler/
├── main.py              # Entry point
├── database.py          # SQLite CRUD
├── models.py            # Pydantic models
├── telegram_client.py   # Multi-account Telethon wrapper
├── message_queue.py     # Async queue + rate limiter
├── scheduler.py         # APScheduler (hourly/daily/weekly/monthly/once)
├── routes/              # FastAPI endpoints
├── static/              # Frontend (HTML/CSS/JS)
├── sessions/            # Telethon session files (gitignored)
└── data/                # SQLite database (gitignored)
```

## ⚠️ Lưu ý

- **Không share** thư mục `sessions/` — chứa session đã đăng nhập
- Sử dụng userbot có rủi ro bị Telegram khóa nếu gửi spam
- Bắt đầu với số lượng nhỏ để test

## License

MIT
