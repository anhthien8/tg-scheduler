# Split Runtime — Phase 1 Foundation & Phase 2 IPC Vertical Slice

## Tóm tắt trạng thái

- **Mặc định**: `TG_RUNTIME_MODE=combined` (giữ nguyên toàn bộ hành vi cũ, backward-compatible).
- **Web mode**: `TG_RUNTIME_MODE=web` chỉ khởi động FastAPI + database + IPC command queue, không nạp bất kỳ Telethon/background engines nào.
- **Worker mode**: `telegram_worker.py` sở hữu toàn bộ Telethon, watchers, scheduler, Command Bot và chạy command consumer loop.
- **IPC Command Queue**: SQLite-backed queue (`runtime_commands.py`) hỗ trợ enqueue, atomic claim, lease, dedup (idempotency key), và crash recovery (`unknown` status).
- **Phạm vi đã nối IPC (Phase 2)**:
  - `chats.refresh`: Enqueue yêu cầu tải lại danh sách chat qua worker.
  - `campaign.start` / `campaign.stop`: Enqueue lệnh start/stop DM campaign qua worker.
  - `watchers.reload`: Enqueue lệnh reload keyword watcher handlers qua worker.
  - Read APIs (`/api/ipc/accounts`, `/api/ipc/campaigns`, `/api/ipc/scrape-jobs`, `/api/ipc/watchers`): Truy vấn trực tiếp từ SQLite, redact credentials.
- **Frontend runtime-aware (`static/js/api.js`, `static/js/members.js`)**:
  - Combined mode: Gọi direct endpoints như trước.
  - Web mode: Enqueue command qua `/api/ipc/commands/...`, poll kết quả với `API.pollCommand()`, không tự động resend khi gặp `unknown`.

## Các file thành phần

| File | Vai trò |
|---|---|
| `engine_lifecycle.py` | Shared startup/shutdown sequence, singleton file lock (`engine.lock`), heartbeat writer. |
| `telegram_worker.py` | Entrypoint cho background process: chạy engines, heartbeat (15s), và IPC command consumer. |
| `runtime_commands.py` | SQLite queue: enqueue, atomic claim, lease timeout, complete, fail, crash recovery. |
| `routes/ipc.py` | FastAPI router: enqueue endpoints, read endpoints, status query, X-API-Key auth. |
| `ipc_dispatcher.py` | Worker-side command dispatcher: ánh xạ `chats.refresh`, `campaign.start/stop`, `watchers.reload` tới engine functions thật. |
| `main.py` | Hỗ trợ `TG_RUNTIME_MODE=combined|web`. Khởi tạo schema `runtime_commands`. |
| `static/js/api.js` | Runtime mode detection (`/api/runtime/mode`), enqueue + polling wrappers. |
| `static/js/members.js` | Tích hợp runtime-aware start/stop campaign. |
| `test_split_runtime.py` | 16 offline tests cho lifecycle, mode switching, lock acquisition, heartbeat. |
| `test_runtime_commands.py` | 29 offline tests cho queue semantics, concurrency, leasing, idempotency. |
| `test_ipc_vertical.py` | 6 offline ASGI integration tests cho IPC endpoints và worker consumer roundtrip. |
| `test_reaction_watcher_safety.py` | 6 offline tests kiểm chứng an toàn reaction watcher (channel_id guard, deduplication). |
| `test_api_runtime.mjs` | 13 JavaScript tests cho `api.js` runtime detection, enqueuing, polling. |

## Giới hạn & Lưu ý

1. **Watcher CRUD chưa chuyển sang IPC**: Tạo/sửa/xóa watcher vẫn yêu cầu `combined` mode; trong `web` mode trả về 503.
2. **Không tự động retry lệnh `unknown`**: Khi worker crash giữa chừng, lệnh chuyển sang `unknown` để tránh gửi trùng tin nhắn cho lead.
3. **Singleton lock**: Chỉ bảo vệ 2 tiến trình trên cùng 1 máy dùng chung `DATA_DIR`.
4. **Vận hành production**: Hiện tại hệ thống vẫn đang chạy `TG_RUNTIME_MODE=combined` mặc định.
