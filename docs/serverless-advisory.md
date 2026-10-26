# Cố vấn triển khai "serverless" cho tg-scheduler (v3.10.2)

> Tài liệu cố vấn — KHÔNG phải kế hoạch deploy. Quyết định cuối thuộc founder.
> Nguồn sự thật kỹ thuật: `telegram_worker.py`, `engine_lifecycle.py`, `runtime_commands.py`, `main.py`.

## 1. Kết luận ngắn

**tg-scheduler KHÔNG chạy được trên serverless thuần (Lambda/Cloud Functions/Vercel).**
Lý do kỹ thuật không thể né:

| Ràng buộc | Vì sao serverless thuần chết |
|---|---|
| 19 session Telethon (MTProto) | Kết nối TCP dài hạn, cần process sống 24/7. Function timeout 15 phút là hết. |
| SQLite + WAL trên disk cục bộ | Serverless không có disk bền; hai instance cùng mở một file DB là hỏng. |
| APScheduler + drip/nudge/warmup engines | Cron nội bộ trong process — function không có "trong process". |
| `engine.lock` (khóa file OS) | Vô nghĩa khi mỗi request một container. |

"Serverless" đúng nghĩa cho dự án này = **managed VM/container chạy 24/7, trả tiền theo tháng**, không phải FaaS.

## 2. Topology khuyến nghị: dùng split runtime ĐÃ CÓ SẴN

Split runtime (web ↔ worker + IPC queue qua SQLite, heartbeat 15s, lease 120s) đã viết xong và có test (`standalone_tests/test_split_runtime.py` 16 test, `test_ipc_vertical.py`, `test_runtime_commands.py`). Production hiện vẫn chạy combined qua `start_background.vbs` — đó là khoảng cách duy nhất.

```
┌─────────────────────┐         ┌──────────────────────────────┐
│  WEB (FastAPI)      │  SQLite │  WORKER (telegram_worker.py) │
│  - dashboard/API    │  queue  │  - 19 Telethon sessions      │
│  - đọc DB read-only │ ──────► │  - engines: drip/nudge/warm  │
│  - enqueue command  │  lease  │  - APScheduler               │
│  - restart tự do    │  120s   │  - heartbeat 15s             │
└─────────────────────┘         └──────────────────────────────┘
         cùng 1 máy, cùng DATA_DIR (bắt buộc — SQLite cục bộ)
```

Lợi ích thực tế:
- Deploy/restart web **không** rớt 19 phiên Telegram (nguồn FloodWait + mất warmup hiện tại).
- Web crash không giết engines; worker crash được phát hiện qua heartbeat (stale > 60s).
- Lệnh nguy hiểm (send, join) đi qua queue có lease — không mất lệnh khi worker chết giữa chừng.

## 3. Lộ trình 3 bước (không bước nào là deploy hôm nay)

### Bước 1 — Chạy split ngay trên máy Windows hiện tại (0 đồng, 1 buổi)
1. Process 1: `python -m uvicorn main:app` với `RUNTIME_MODE=web` (web không acquire engine.lock).
2. Process 2: `python telegram_worker.py` (acquire lock, chạy engines).
3. Sửa `start_background.vbs` thành 2 lệnh. Smoke test: web restart ↛ worker restart.
   *Điều kiện*: hiện mới ~5 lệnh đi qua IPC queue — kiểm kê lệnh web→engine còn gọi trực tiếp trước khi cắt (xem `ipc_dispatcher.py`).

### Bước 2 — Chuyển lên 1 VPS Linux (~$5–10/tháng, khi cần uptime)
- 1 VPS (Hetzner CX22 / Vultr / DigitalOcean), systemd 2 unit: `tgs-web.service`, `tgs-worker.service` (`Restart=always`).
- SQLite + sessions nằm trên disk VPS; backup = `sqlite3 .backup` + rsync folder `sessions/` (đã có `data/backups`).
- Secrets qua env (`COMMAND_BOT_TOKEN`, `DASHBOARD_SECRET_KEY`) — v3.10.2 đã hỗ trợ.
- KHÔNG đổi DB engine. SQLite WAL dư sức cho 19 account + 1 admin. Postgres chỉ cần khi có nhiều máy.

### Bước 3 — (chỉ khi scale nhiều người vận hành) container hóa
- Docker compose 2 service + volume chung. Fly.io/Railway chạy được nhưng disk bền + always-on làm giá ngang VPS mà thêm ràng buộc. VPS vẫn là điểm dừng hợp lý.

## 4. Những thứ KHÔNG nên làm

| Ý tưởng | Vì sao không |
|---|---|
| Lambda/Cloud Functions cho engines | Telethon session chết theo function; FloodWait tăng vì reconnect liên tục. |
| Tách DB sang Postgres ngay | Thêm 1 dịch vụ phải vận hành, 0 lợi ích ở quy mô hiện tại; mất `BEGIN IMMEDIATE` pattern đã test kỹ. |
| 2 worker cùng DATA_DIR | `engine_lifecycle.acquire_data_lock()` chặn đúng — đây là feature, không phải bug. |
| Chạy web ở cloud, worker ở nhà | Hai máy không chia được SQLite; IPC queue nằm trong DB. Phải cùng máy. |

## 5. Checklist trước khi bấm nút (bước 1)

- [ ] Kiểm kê route web gọi thẳng engine (grep `tg.get_client|lifecycle.` trong `routes/`) → chuyển qua queue hoặc chấp nhận read-only.
- [ ] `RUNTIME_MODE` phân nhánh trong `main.py` lifespan (web mode: bỏ start_engines).
- [ ] Test: tắt worker → web vẫn trả dashboard, lệnh send trả "queued, worker offline".
- [ ] Backup `data/` + `sessions/` trước lần chạy split đầu tiên.

## 6. Checklist vận hành khi lên VPS (bước 2)

- [ ] Đặt `COMMAND_BOT_TOKEN`, `COMMAND_BOT_ADMIN_IDS`, `DASHBOARD_SECRET_KEY` qua env/secret manager — không để trong DB.
- [ ] Backup `data/` + `sessions/` và test restore MỘT lần trước khi đổi máy.
- [ ] Theo dõi heartbeat worker + queue 24h trước khi tắt hẳn combined mode.
- [ ] Không expose dashboard ra Internet khi chưa có reverse proxy HTTPS.

**Nâng cấp tương lai:** chỉ tách Postgres + queue riêng (Redis/NATS) khi cần nhiều máy/nhiều worker — không làm sớm hơn.

## 7. Cloud container trên Railway (founder chọn 27/10/2026)

Chạy **combined mode, 1 service, 1 replica** — đúng như máy Windows hiện tại, chỉ là máy do Railway quản lý. Không phải serverless thuần (Telethon cần kết nối giữ liên tục), nhưng không còn phải tự trông máy.

- [ ] Tạo service từ repo (Railway đọc `railway.json` → build `Dockerfile`).
- [ ] Thêm **Volume** mount path `/data`. Dockerfile đã trỏ `DATA_DIR=/data/db`, `SESSION_DIR=/data/sessions`, `MEDIA_DIR=/data/media`. Image KHÔNG chứa DB/session/.env (`.dockerignore`).
- [ ] Copy `data/scheduler.db` + toàn bộ `sessions/*.session` lên volume TRƯỚC lần start đầu — nếu không, mọi tài khoản phải đăng nhập lại. Tắt server ở máy nhà trước (2 nơi cùng dùng 1 session = Telegram có thể khóa phiên).
- [ ] Env vars: `DASHBOARD_SECRET_KEY`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, `COMMAND_BOT_TOKEN`, `COMMAND_BOT_ADMIN_IDS`. `HOST=0.0.0.0` đã có sẵn; Railway tự đặt `PORT`.
- [ ] **AI provider:** setting `ai_oai_compat_base_url` đang là `http://127.0.0.1:20128/v1` (proxy trên máy nhà) — trên cloud địa chỉ này không tồn tại. Đổi sang endpoint công khai (API key provider trực tiếp) trước khi chuyển.
- [ ] Replicas = 1. Không bật autoscale: 2 container cùng session Telegram sẽ đá nhau.
- [ ] Dashboard ra Internet: Railway có HTTPS sẵn, nhưng cổng chặn duy nhất là `X-API-Key`. Trước khi public: `DASHBOARD_SECRET_KEY` dài ngẫu nhiên, và nếu bật `/api/login` (username/password) thì phải thêm giới hạn số lần thử (slowapi đã có trong requirements) — hiện chưa có.


## 8. 9router trên laptop ở nhà → server cloud (Cloudflare Tunnel + Access)

Laptop chạy 9router ở `127.0.0.1:20128`. Tunnel chỉ mở kết nối **ra ngoài**, không cần mở port modem.

**Trên laptop (PowerShell, chạy 1 lần):**
```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel login
cloudflared tunnel create 9router
cloudflared tunnel route dns 9router ai.<domain-cua-ban>
```
Tạo `%USERPROFILE%\.cloudflared\config.yml`:
```yaml
tunnel: 9router
credentials-file: C:\Users\<user>\.cloudflared\<TUNNEL-ID>.json
ingress:
  - hostname: ai.<domain-cua-ban>
    service: http://127.0.0.1:20128
  - service: http_status:404
```
Cài thành service (tự chạy khi bật máy): `cloudflared service install` (PowerShell Admin).
Laptop: tắt Sleep khi cắm sạc, để 9router tự khởi động.

**Trên Cloudflare Zero Trust dashboard:**
1. Access → Service Auth → tạo Service Token → lưu Client ID + Secret (chỉ hiện 1 lần).
2. Access → Applications → Self-hosted `ai.<domain>` → Policy action **Service Auth**, include token vừa tạo.

**Trên Railway (env vars):**
```
CF_ACCESS_HOST=ai.<domain-cua-ban>
CF_ACCESS_CLIENT_ID=<id>.access
CF_ACCESS_CLIENT_SECRET=<secret>
```
Rồi trong app đổi base URL OpenAI-compatible thành `https://ai.<domain-cua-ban>/v1`.
App chỉ gửi header Access khi host khớp `CF_ACCESS_HOST` → token không lộ sang OpenAI.

**Kiểm tra:** `curl -H "CF-Access-Client-Id: ..." -H "CF-Access-Client-Secret: ..." https://ai.<domain>/v1/models` → 200; bỏ header → 403.
